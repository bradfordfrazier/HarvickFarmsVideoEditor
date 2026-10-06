"""
Retake detection for long-form talking-head footage.

How it works
------------
1. Whisper transcribes with WORD-level timestamps.
2. The word stream is split at "restart points": places where a speaker could
   plausibly begin again (after a pause, after sentence punctuation, after a
   cut-off dash; commas count as weak restart points).
3. For every restart point q we look back at earlier restart points p and ask:
   "does the speech starting at q re-say what was said from p up to q?"
   The two word streams are aligned (order-preserving, tolerant of inserted /
   dropped / changed words). If most of the earlier material is re-said, the
   earlier material [p, q) is a superseded take and is discarded.
4. The LAST take always wins. Chains (take 1 -> take 2 -> take 3 ...) collapse
   naturally because each restart only has to match what is still kept.
5. Discards are returned as precise time intervals and are SUBTRACTED from the
   silence-based speech segments (segments are split, not dropped whole).

Whisper's own segment boundaries are never used as the unit of comparison,
because they fall in different places on every take.
"""
import os
import re
import json
import difflib
import subprocess
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from backend.config import FFMPEG_PATH, OUTPUT_DIR

# "tiny.en" mis-hears enough words that identical takes stop looking identical.
# "base.en" is roughly 2x slower but noticeably more consistent. Override with
# the WHISPER_MODEL environment variable ("small.en" is better again).
DEFAULT_WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base.en")
_CACHE_VERSION = "w1"  # bump when the cached JSON shape changes

# ----------------------------------------------------------------------------
# Tunables
# ----------------------------------------------------------------------------
PAUSE_SPLIT_SECONDS = 0.40     # word gap that counts as a possible restart point
SOFT_LOOKBACK_SECONDS = 20.0   # comma-level restarts only look this far back
SHORT_TAKE_TOKENS = 8          # takes shorter than this need a stricter match
SHORT_TAKE_MIN_SCORE = 0.75
SOFT_MIN_SCORE = 0.80
MAX_ABANDONED_TAIL_WORDS = 10  # words a take may run on after the matched part
MAX_TAKE_TOKENS = 600
CUT_LEAD_SECONDS = 0.12        # breathing room kept before the winning take
CUT_TRAIL_SECONDS = 0.15       # breathing room kept after the last kept word
MIN_KEPT_PIECE_SECONDS = 0.25  # slivers shorter than this are dropped

_FILLERS = {"um", "uh", "uhm", "umm", "er", "erm", "ah", "eh", "hmm", "mm", "mhm"}
_STOPWORDS = {
    "a", "an", "the", "and", "or", "but", "so", "if", "then", "than", "that", "this",
    "these", "those", "to", "of", "in", "on", "at", "for", "with", "from", "by", "as",
    "is", "are", "was", "were", "be", "been", "being", "am", "do", "does", "did",
    "have", "has", "had", "it", "its", "i", "you", "we", "they", "he", "she", "me",
    "my", "our", "your", "their", "us", "them", "im", "youre", "were", "theyre",
    "ive", "weve", "ill", "well", "thats", "theres", "whats", "not", "no", "yes",
    "just", "like", "really", "very", "kind", "sort", "know", "okay", "ok", "right",
    "all", "there", "here", "what", "when", "where", "which", "who", "how", "why",
    "can", "will", "would", "could", "should", "going", "gonna", "get", "got",
    "about", "up", "out", "into", "over", "also", "now", "because", "cause",
}
_W_STOP, _W_CONTENT = 0.25, 1.0


# ----------------------------------------------------------------------------
# Text helpers
# ----------------------------------------------------------------------------
def clean_text(t: str) -> str:
    """Lowercase, strip punctuation, collapse whitespace."""
    t = t.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())


def _norm_token(word: str) -> Tuple[str, float]:
    """Return (comparison token, weight). Empty token means 'ignore this word'."""
    t = re.sub(r"[^\w]", "", word.lower().replace("'", "").replace("\u2019", ""))
    if not t or t in _FILLERS:
        return "", 0.0
    if t in _STOPWORDS:
        return t, _W_STOP
    # Very light stemming so "plant / plants / planted / planting" compare equal.
    if len(t) > 4:
        for suf in ("ing", "ed", "s"):
            if t.endswith(suf) and len(t) - len(suf) >= 3 and not t.endswith("ss"):
                t = t[: -len(suf)]
                break
    return t, _W_CONTENT


def text_similarity(t1: str, t2: str) -> float:
    """
    Word-level similarity of two snippets.
    Detects restarts, identical prefixes, and rephrased takes.
    """
    c1, c2 = clean_text(t1), clean_text(t2)
    if not c1 or not c2:
        return 0.0

    w1, w2 = c1.split(), c2.split()

    # Prefix overlap check (speakers restarting the same sentence)
    min_prefix = min(len(w1), len(w2), 4)
    if min_prefix >= 3:
        if w1[:min_prefix] == w2[:min_prefix]:
            return 0.95
        prefix_ratio = difflib.SequenceMatcher(None, " ".join(w1[:min_prefix]), " ".join(w2[:min_prefix])).ratio()
        if prefix_ratio >= 0.8:
            return 0.90

    a = [n for n, _ in map(_norm_token, t1.split()) if n]
    b = [n for n, _ in map(_norm_token, t2.split()) if n]
    seq_ratio = difflib.SequenceMatcher(None, a or w1, b or w2, autojunk=False).ratio()

    # Jaccard similarity for slight rephrasings
    s1, s2 = set(w1), set(w2)
    jaccard = len(s1 & s2) / max(len(s1 | s2), 1)

    return max(seq_ratio, jaccard)


def score_take_quality(segment: Dict[str, Any]) -> float:
    """
    Rough delivery-quality score, reported per take for the UI.
    Rewards finished sentences; penalizes fragments and stutters.
    (It does not pick the winner - the last take always wins.)
    """
    text = segment.get("text", "").strip()
    words = text.split()
    if not words:
        return 0.0
    score = 1.0
    finished = text.endswith((".", "!", "?")) and not text.endswith("...")
    if finished:
        score += 0.5
    if len(words) <= 3 and not finished:
        score -= 0.8
    elif len(words) < 5 and not finished:
        score -= 0.4
    lower = [w.lower().strip(".,!?") for w in words]
    for n in (2, 3, 4):
        grams = [tuple(lower[i:i + n]) for i in range(len(lower) - n + 1)]
        if len(grams) != len(set(grams)):
            score -= 0.6
            break
    return score


# ----------------------------------------------------------------------------
# Transcription
# ----------------------------------------------------------------------------
def transcribe_audio_whisper(
    file_path: str,
    model_name: Optional[str] = None,
    cache: bool = True
) -> List[Dict[str, Any]]:
    """
    Transcribe with local Whisper, including word-level timestamps.
    Each segment carries a "words" list: [{"word", "start", "end"}, ...].
    """
    model_name = model_name or DEFAULT_WHISPER_MODEL
    p = Path(file_path).resolve()
    cache_file = OUTPUT_DIR / (
        f"{p.stem}_{p.stat().st_mtime_ns}_{model_name}_{_CACHE_VERSION}_whisper.json"
    )

    if cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    temp_wav = OUTPUT_DIR / f"{p.stem}_whisper_temp.wav"
    try:
        cmd = [FFMPEG_PATH, "-y", "-i", str(p), "-vn", "-ac", "1", "-ar", "16000", str(temp_wav)]
        subprocess.run(cmd, check=True, capture_output=True)

        import whisper
        model = whisper.load_model(model_name)
        result = model.transcribe(
            str(temp_wav),
            fp16=False,
            # Must stay False: conditioning makes Whisper "tidy up" and skip repeats.
            condition_on_previous_text=False,
            word_timestamps=True,
            verbose=False
        )

        segments = []
        for s in result.get("segments", []):
            words = [
                {
                    "word": w.get("word", "").strip(),
                    "start": round(float(w["start"]), 3),
                    "end": round(float(w["end"]), 3),
                }
                for w in s.get("words", []) or []
                if w.get("word", "").strip()
            ]
            segments.append({
                "start": round(float(s["start"]), 3),
                "end": round(float(s["end"]), 3),
                "duration": round(float(s["end"]) - float(s["start"]), 3),
                "text": s.get("text", "").strip(),
                "confidence": round(float(1.0 - s.get("no_speech_prob", 0.0)), 3),
                "words": words,
            })

        if cache:
            cache_file.write_text(json.dumps(segments), encoding="utf-8")
        return segments
    finally:
        if temp_wav.exists():
            temp_wav.unlink(missing_ok=True)


# ----------------------------------------------------------------------------
# Word stream
# ----------------------------------------------------------------------------
def _flatten_words(segments: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """One flat, time-ordered word list. Falls back to interpolating word times
    inside a segment when word timestamps are missing."""
    words: List[Dict[str, Any]] = []
    for si, seg in enumerate(segments):
        items: List[Tuple[str, float, float]] = []
        raw = seg.get("words") or []
        if raw:
            for w in raw:
                txt = str(w.get("word", "")).strip()
                if txt:
                    items.append((txt, float(w["start"]), float(w["end"])))
        else:
            toks = seg.get("text", "").split()
            total = sum(len(t) for t in toks) or 1
            t0, t1 = float(seg["start"]), float(seg["end"])
            cur = t0
            for t in toks:
                d = (t1 - t0) * len(t) / total
                items.append((t, cur, cur + d))
                cur += d
        for txt, s, e in items:
            if words and s < words[-1]["start"]:
                s = words[-1]["start"]
            words.append({"text": txt, "start": s, "end": max(e, s), "seg": si})
    return words


def _boundary_kind(words: List[Dict[str, Any]], i: int) -> int:
    """0 = not a restart point, 1 = weak (comma), 2 = strong (pause / sentence end / cut-off)."""
    if i == 0:
        return 2
    prev = words[i - 1]["text"].rstrip("\"')")
    if prev.endswith((".", "!", "?", "-", "\u2014", "\u2013", "\u2026")):
        return 2
    if words[i]["start"] - words[i - 1]["end"] >= PAUSE_SPLIT_SECONDS:
        return 2
    if prev.endswith((",", ";", ":")):
        return 1
    return 0


# ----------------------------------------------------------------------------
# Alignment: "does stream B re-say take A?"
# ----------------------------------------------------------------------------
def _restart_match(
    a: List[str], a_w: List[float], b: List[str],
    threshold: float, soft: bool
) -> Optional[Dict[str, Any]]:
    """
    a = tokens of the earlier (candidate superseded) take, b = tokens that follow
    the restart point. Returns match info if b is a re-take of a, else None.

    The score is the weighted share of the earlier take that is re-said, in
    order. Function words count for little, so shared "and so we ..." openings
    do not create matches on their own. A take may run on a little after the
    matched part (the stumble that caused the restart) without being penalized
    in full, but a long unmatched stretch means real, unrepeated content and
    blocks the match.
    """
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs = [(m.a + k, m.b + k) for m in sm.get_matching_blocks() for k in range(m.size)]
    if len(pairs) < 2:
        return None

    a_hit = [False] * len(a)
    b_hit = [False] * len(b)
    for i, j in pairs:
        a_hit[i] = True
        b_hit[j] = True

    content_pairs = [(i, j) for i, j in pairs if a_w[i] >= _W_CONTENT]
    anchors = content_pairs or pairs
    first_a, first_b = anchors[0]
    last_a, last_b = anchors[-1][0], pairs[-1][1]

    n_match = len(pairs)
    lead_a = sum(1 for i in range(first_a) if not a_hit[i])
    lead_b = sum(1 for j in range(first_b) if not b_hit[j])
    # The retake must begin (almost) immediately at the restart point; if it
    # begins a few words later, a later restart point is the right one.
    if lead_a > max(2, int(0.2 * n_match)) or lead_b > max(1, int(0.15 * n_match)):
        return None  # the two do not start in the same place

    tail_idx = [i for i in range(last_a + 1, len(a)) if not a_hit[i]]
    tail_n = len(tail_idx)
    tail_w = sum(a_w[i] for i in tail_idx)
    total_w = sum(a_w)
    matched_w = sum(a_w[i] for i in range(len(a)) if a_hit[i])

    # Content words that were re-said but in a different position (rephrasing)
    b_set = set(b)
    matched_w += 0.5 * sum(
        a_w[i] for i in range(last_a + 1)
        if not a_hit[i] and a_w[i] >= _W_CONTENT and a[i] in b_set
    )

    short = len(a) < SHORT_TAKE_TOKENS
    if short:
        score = matched_w / total_w if total_w else 0.0
        need = max(threshold, SHORT_TAKE_MIN_SCORE)
    else:
        if tail_n > max(MAX_ABANDONED_TAIL_WORDS, 0.6 * n_match):
            return None
        score = matched_w / max(total_w - 0.5 * tail_w, 1e-6)
        need = threshold
    if soft:
        need = max(need, SOFT_MIN_SCORE)
    if not content_pairs and score < 0.9:
        return None
    if score < need:
        return None
    return {"score": min(1.0, score), "last_b": last_b}


def _check_aborted_match(
    a: List[str], a_w: List[float], b: List[str]
) -> Optional[Dict[str, Any]]:
    """Check if b is an aborted restart matching the beginning of completed take a."""
    if len(b) > 8:
        return None
    sm = difflib.SequenceMatcher(None, a, b, autojunk=False)
    pairs = [(m.a + k, m.b + k) for m in sm.get_matching_blocks() for k in range(m.size)]
    if len(pairs) < 2:
        return None
    first_a, first_b = pairs[0]
    if first_a > 1 or first_b > 0:
        return None
    last_b = pairs[-1][1]
    if len(pairs) < max(2, int(0.7 * len(b))):
        return None
    return {"score": len(pairs) / max(len(b), 1), "last_b": last_b}


# ----------------------------------------------------------------------------
# Main detector
# ----------------------------------------------------------------------------
def _empty_result(segments):
    return {"groups": [], "discarded_intervals": [], "kept_segments": segments, "total_retakes_removed": 0}


def detect_repeated_takes(
    segments: List[Dict[str, Any]],
    similarity_threshold: float = 0.55,
    max_gap_seconds: float = 150.0
) -> Dict[str, Any]:
    """
    Find superseded takes and return the time intervals to cut.

    similarity_threshold: share (0-1) of an earlier take that must be re-said
        for it to count as superseded. Lower = more aggressive consolidation.
    max_gap_seconds: longest allowed distance between the start of a take and
        the start of the retake that replaces it (i.e. the longest single take).
        Any number of takes can chain, so this is per take, not per paragraph.
    """
    words = _flatten_words(segments)
    if len(words) < 4:
        return _empty_result(segments)

    # Comparison tokens (fillers and pure punctuation removed)
    tok: List[str] = []
    tok_w: List[float] = []
    tok_word: List[int] = []
    for wi, w in enumerate(words):
        n, wt = _norm_token(w["text"])
        if n:
            tok.append(n)
            tok_w.append(wt)
            tok_word.append(wi)
    nt = len(tok)
    if nt < 4:
        return _empty_result(segments)
    tok_time = [words[wi]["start"] for wi in tok_word]

    # Restart points, expressed in token space.
    # start_info[token] = [kind, earliest word index that maps to this token]
    start_info: Dict[int, List[int]] = {}
    ti = 0
    for wi in range(len(words)):
        kind = _boundary_kind(words, wi)
        if not kind:
            continue
        while ti < nt and tok_word[ti] < wi:
            ti += 1
        if ti >= nt:
            break
        if ti in start_info:
            start_info[ti][0] = max(start_info[ti][0], kind)
        else:
            start_info[ti] = [kind, wi]
    starts = sorted(start_info)
    head_sets = [set(tok[s:s + 12]) for s in starts]

    kept_tok = [True] * nt
    kept_word = [True] * len(words)
    split_words = set()                 # word indices where one take ends and the next begins
    take_score: Dict[int, float] = {}   # first word of a discarded take -> match score
    winner_end: Dict[int, int] = {}     # first word of a winning take -> last matched word
    aborted_groups: List[Dict[str, Any]] = []

    def span_text(a: int, b: int) -> str:
        return " ".join(w["text"] for w in words[a:b])

    lo = 0
    for si in range(1, len(starts)):
        q = starts[si]
        q_hard = start_info[q][0] == 2
        q_time = tok_time[q]
        q_word = tok_word[q]
        while tok_time[starts[lo]] < q_time - max_gap_seconds:
            lo += 1

        front = q  # everything in [front, q) has already been discarded by this restart
        # Nearest earlier restart point first, then keep walking back: each
        # accepted match removes one older take, and the next comparison only
        # sees what is still kept.
        for pj in range(si - 1, lo - 1, -1):
            p = starts[pj]
            if not kept_tok[p]:
                continue
            soft = not (q_hard and start_info[p][0] == 2)
            if soft and q_time - tok_time[p] > SOFT_LOOKBACK_SECONDS:
                continue
            if len(head_sets[pj] & head_sets[si]) < 2:
                continue

            # Cheap check on the opening words before aligning the whole take
            head: List[int] = []
            t = p
            while t < front and len(head) < 16:
                if kept_tok[t]:
                    head.append(t)
                t += 1
            if len(head) < 2:
                continue
            b_win = tok[q:q + 24]
            hm = difflib.SequenceMatcher(None, [tok[i] for i in head], b_win, autojunk=False)
            hw = sum(tok_w[head[m.a + k]] for m in hm.get_matching_blocks() for k in range(m.size))
            b_win_w = sum(tok_w[q + j] for j in range(min(len(tok) - q, 24)))
            ref_w = min(sum(tok_w[i] for i in head), b_win_w)
            if hw < 0.5 * similarity_threshold * ref_w:
                continue

            a_idx = head if t >= front else head + [i for i in range(t, front) if kept_tok[i]]
            if len(a_idx) > MAX_TAKE_TOKENS:
                continue
            b = tok[q:q + int(len(a_idx) * 1.5) + 8]
            res = _restart_match([tok[i] for i in a_idx], [tok_w[i] for i in a_idx], b,
                                 similarity_threshold, soft)
            if res:
                p_word = start_info[p][1]
                for i in range(p, front):
                    kept_tok[i] = False
                for wi in range(p_word, q_word):
                    kept_word[wi] = False
                split_words.add(p_word)
                split_words.add(q_word)
                for sw in split_words:
                    if p_word <= sw < q_word:
                        take_score.setdefault(sw, res["score"])
                end_w = tok_word[min(q + res["last_b"], nt - 1)]
                winner_end[q_word] = max(winner_end.get(q_word, q_word), end_w)
                front = p
                continue

            # Check if b is an aborted restart of completed take a
            q_next_tok = starts[si + 1] if si + 1 < len(starts) else nt
            b_frag = tok[q:q_next_tok]
            ab_res = _check_aborted_match([tok[i] for i in a_idx], [tok_w[i] for i in a_idx], b_frag)
            if ab_res:
                p_word = start_info[p][1]
                p_text = span_text(p_word, q_word)
                q_next_word = start_info[starts[si + 1]][1] if si + 1 < len(starts) else len(words)
                q_text = span_text(q_word, q_next_word)
                p_q = score_take_quality({"text": p_text})
                q_q = score_take_quality({"text": q_text})
                if p_q >= 1.0 and q_q < 0.6 and (p_q - q_q > 0.5):
                    for wi in range(q_word, q_next_word):
                        kept_word[wi] = False
                    for i in range(q, q_next_tok):
                        kept_tok[i] = False
                    aborted_groups.append({
                        "p_word": p_word,
                        "p_end_word": q_word,
                        "p_text": p_text,
                        "p_score": p_q,
                        "q_word": q_word,
                        "q_end_word": q_next_word,
                        "q_text": q_text,
                        "q_score": q_q,
                        "sim": ab_res["score"]
                    })
                    break

    # ------------------------------------------------------------------
    # Turn discarded word runs into groups + cut intervals
    # ------------------------------------------------------------------
    def cut_in(wi: int) -> float:
        """Cut time just before word wi, when wi is the first KEPT word."""
        if wi <= 0:
            return words[0]["start"]
        return max(words[wi - 1]["end"], words[wi]["start"] - CUT_LEAD_SECONDS)

    def cut_out(wi: int) -> float:
        """Cut time just before word wi, when wi is the first DISCARDED word."""
        if wi <= 0:
            return words[0]["start"]
        return min(words[wi]["start"], words[wi - 1]["end"] + CUT_TRAIL_SECONDS)

    groups: List[Dict[str, Any]] = []
    discarded_intervals: List[Dict[str, Any]] = []
    nw = len(words)
    wi = 0
    while wi < nw:
        if kept_word[wi]:
            wi += 1
            continue
        if any(ag["q_word"] <= wi < ag["q_end_word"] for ag in aborted_groups):
            wi += 1
            continue
        rs = wi
        while wi < nw and not kept_word[wi]:
            wi += 1
        re_ = wi  # first word of the winning take
        if re_ >= nw:
            break
        gid = f"retake_group_{len(groups) + 1}"
        bounds = [rs] + sorted(s for s in split_words if rs < s < re_) + [re_]
        candidates = []
        for k in range(len(bounds) - 1):
            a, b = bounds[k], bounds[k + 1]
            t0 = cut_out(a) if k == 0 else cut_in(a)
            t1 = cut_in(b)
            if t1 <= t0:
                continue
            text = span_text(a, b)
            candidates.append({
                "index": len(candidates),
                "segment_index": words[a]["seg"],
                "start": round(t0, 3),
                "end": round(t1, 3),
                "text": text,
                "is_winner": False,
                "quality_score": round(score_take_quality({"text": text}), 2),
                "similarity": round(take_score.get(a, 0.0), 2),
            })
        if not candidates:
            continue
        w_end = min(max(winner_end.get(re_, re_), re_), nw - 1)
        while w_end + 1 < nw and _boundary_kind(words, w_end + 1) < 2:
            w_end += 1  # show the winner through to the end of its sentence
        w_text = span_text(re_, w_end + 1)
        candidates.append({
            "index": len(candidates),
            "segment_index": words[re_]["seg"],
            "start": round(cut_in(re_), 3),
            "end": round(words[w_end]["end"], 3),
            "text": w_text,
            "is_winner": True,
            "quality_score": round(score_take_quality({"text": w_text}), 2),
            "similarity": 1.0,
        })
        groups.append({
            "group_id": gid,
            "candidates": candidates,
            "winner_index": candidates[-1]["index"],
            "winner_reason": "last_take_default",
        })
        for c in candidates[:-1]:
            discarded_intervals.append({
                "start": c["start"],
                "end": c["end"],
                "duration": round(c["end"] - c["start"], 3),
                "reason": "Discarded repeated take (superseded by a later take)",
                "text": c["text"],
                "group_id": gid,
            })

    # Add aborted groups (where earlier complete take won over aborted take)
    for ag in aborted_groups:
        gid = f"retake_group_{len(groups) + 1}"
        p_c = {
            "index": 0,
            "segment_index": words[ag["p_word"]]["seg"],
            "start": round(words[ag["p_word"]]["start"], 3),
            "end": round(words[ag["p_end_word"] - 1]["end"], 3),
            "text": ag["p_text"],
            "is_winner": True,
            "quality_score": round(ag["p_score"], 2),
            "similarity": 1.0,
        }
        q_c = {
            "index": 1,
            "segment_index": words[ag["q_word"]]["seg"],
            "start": round(cut_out(ag["q_word"]), 3),
            "end": round(words[ag["q_end_word"] - 1]["end"], 3),
            "text": ag["q_text"],
            "is_winner": False,
            "quality_score": round(ag["q_score"], 2),
            "similarity": round(ag["sim"], 2),
        }
        groups.append({
            "group_id": gid,
            "candidates": [p_c, q_c],
            "winner_index": 0,
            "winner_reason": "superior_quality",
        })
        discarded_intervals.append({
            "start": q_c["start"],
            "end": q_c["end"],
            "duration": round(q_c["end"] - q_c["start"], 3),
            "reason": "Discarded repeated take (won by superior_quality)",
            "text": q_c["text"],
            "group_id": gid,
        })

    def _is_discarded(seg: Dict[str, Any]) -> bool:
        mid = (seg["start"] + seg["end"]) / 2.0
        return any(d["start"] <= mid <= d["end"] for d in discarded_intervals)

    return {
        "groups": groups,
        "discarded_intervals": discarded_intervals,
        "kept_segments": [s for s in segments if not _is_discarded(s)],
        "total_retakes_removed": len(discarded_intervals),
    }


# ----------------------------------------------------------------------------
# Applying the cuts to the silence-based speech segments
# ----------------------------------------------------------------------------
def filter_speech_segments_by_retakes(
    speech_segments: List[Dict[str, Any]],
    discarded_intervals: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Subtract discarded retake intervals from the speech segments.

    A speech segment (a stretch between silences) often holds a retake plus
    material worth keeping, so segments are trimmed / split at the retake
    boundaries rather than kept or dropped whole.

    The input dicts are tagged in place ("is_retake", "retake_group_id") for
    the timeline; trimmed pieces are returned as new dicts.
    """
    if not discarded_intervals:
        for s in speech_segments:
            s["is_retake"] = False
        return speech_segments

    cuts = sorted(discarded_intervals, key=lambda d: d["start"])
    out: List[Dict[str, Any]] = []
    for s in speech_segments:
        s_start, s_end = s["start"], s["end"]
        dur = max(s_end - s_start, 1e-9)
        pieces: List[Tuple[float, float]] = []
        cursor = s_start
        removed = 0.0
        best_overlap, best_gid = 0.0, None
        for d in cuts:
            if d["end"] <= cursor:
                continue
            if d["start"] >= s_end:
                break
            ov_start, ov_end = max(cursor, d["start"]), min(s_end, d["end"])
            if ov_end <= ov_start:
                continue
            if ov_start > cursor:
                pieces.append((cursor, ov_start))
            removed += ov_end - ov_start
            if ov_end - ov_start > best_overlap:
                best_overlap, best_gid = ov_end - ov_start, d.get("group_id")
            cursor = ov_end
        if cursor < s_end:
            pieces.append((cursor, s_end))

        if removed <= 1e-6:
            s["is_retake"] = False
            out.append(s)
            continue

        pieces = [(a, b) for a, b in pieces if b - a >= MIN_KEPT_PIECE_SECONDS]
        s["is_retake"] = not pieces or removed >= 0.5 * dur
        if best_gid:
            s["retake_group_id"] = best_gid
        for a, b in pieces:
            out.append({
                "start": round(a, 3),
                "end": round(b, 3),
                "duration": round(b - a, 3),
                "is_retake": False,
                "trimmed_by_retake": True,
                "retake_group_id": best_gid,
            })
    return out
