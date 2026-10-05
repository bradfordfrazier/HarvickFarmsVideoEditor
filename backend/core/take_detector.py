import os
import re
import json
import difflib
import subprocess
import shutil
from pathlib import Path
from typing import List, Dict, Any, Tuple, Optional
from backend.config import FFMPEG_PATH, OUTPUT_DIR

def clean_text(t: str) -> str:
    """Normalize text for phonetic/semantic comparison."""
    t = t.lower()
    t = re.sub(r"[^\w\s]", "", t)
    return " ".join(t.split())

def text_similarity(t1: str, t2: str) -> float:
    """
    Calculate textual and semantic similarity between two spoken segments.
    Detects restarts, identical prefixes, and rephrased takes.
    """
    c1, c2 = clean_text(t1), clean_text(t2)
    if not c1 or not c2:
        return 0.0
        
    w1, w2 = c1.split(), c2.split()
    
    # 1. Prefix overlap check (speakers restarting the same sentence)
    min_prefix = min(len(w1), len(w2), 4)
    if min_prefix >= 3:
        if w1[:min_prefix] == w2[:min_prefix]:
            return 0.95
        prefix_ratio = difflib.SequenceMatcher(None, " ".join(w1[:min_prefix]), " ".join(w2[:min_prefix])).ratio()
        if prefix_ratio >= 0.8:
            return 0.90

    # 2. SequenceMatcher ratio (order-preserving edit distance)
    seq_ratio = difflib.SequenceMatcher(None, c1, c2).ratio()
    
    # 3. Word token set overlap (Jaccard similarity for slight rephrasings)
    s1, s2 = set(w1), set(w2)
    jaccard = len(s1 & s2) / max(len(s1 | s2), 1)
    
    return max(seq_ratio, jaccard)

def score_take_quality(segment: Dict[str, Any]) -> float:
    """
    Evaluate candidate take quality.
    Rewards complete thoughts and terminal punctuation.
    Penalizes truncated fragments, mid-sentence stumbles, and repetitions.
    """
    text = segment.get("text", "").strip()
    words = text.split()
    if not words:
        return 0.0
        
    score = 1.0
    
    # Terminal punctuation indicates completed delivery
    if text.endswith((".", "!", "?")):
        score += 0.5
        
    # Penalty for short fragments (<= 3 words without terminal punctuation) indicating false starts
    if len(words) <= 3 and not text.endswith((".", "!", "?")):
        score -= 0.8
    elif len(words) < 5 and not text.endswith((".", "!", "?")):
        score -= 0.4
        
    # Penalty for internal repetitions/stuttering (e.g. "in 2022, in 2022")
    lower_words = [w.lower().strip(".,!?") for w in words]
    for n in (2, 3, 4):
        ngrams = [tuple(lower_words[i:i+n]) for i in range(len(lower_words) - n + 1)]
        if len(ngrams) != len(set(ngrams)):
            score -= 0.6
            break
            
    return score

def transcribe_audio_whisper(
    file_path: str,
    model_name: str = "tiny.en",
    cache: bool = True
) -> List[Dict[str, Any]]:
    """
    Transcribe media file using local Whisper.
    Uses cached JSON if available to avoid redundant computation.
    """
    p = Path(file_path).resolve()
    cache_file = OUTPUT_DIR / f"{p.stem}_{p.stat().st_mtime_ns}_whisper.json"
    
    if cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text(encoding="utf-8"))
        except Exception:
            pass

    # Extract 16kHz mono audio for Whisper
    temp_wav = OUTPUT_DIR / f"{p.stem}_whisper_temp.wav"
    try:
        cmd = [
            FFMPEG_PATH,
            "-y",
            "-i", str(p),
            "-vn",
            "-ac", "1",
            "-ar", "16000",
            str(temp_wav)
        ]
        subprocess.run(cmd, check=True, capture_output=True)
        
        import whisper
        model = whisper.load_model(model_name)
        result = model.transcribe(
            str(temp_wav),
            fp16=False,
            condition_on_previous_text=False,
            verbose=False
        )
        
        segments = []
        for s in result.get("segments", []):
            segments.append({
                "start": round(float(s["start"]), 3),
                "end": round(float(s["end"]), 3),
                "duration": round(float(s["end"]) - float(s["start"]), 3),
                "text": s.get("text", "").strip(),
                "confidence": round(float(1.0 - s.get("no_speech_prob", 0.0)), 3)
            })
            
        if cache:
            cache_file.write_text(json.dumps(segments, indent=2), encoding="utf-8")
            
        return segments
    finally:
        if temp_wav.exists():
            temp_wav.unlink(missing_ok=True)

def detect_repeated_takes(
    segments: List[Dict[str, Any]],
    similarity_threshold: float = 0.55,
    max_gap_seconds: float = 75.0
) -> Dict[str, Any]:
    """
    Detect repeated takes across spoken segments.
    Narrows candidates to the best version, defaulting to the last take
    when there is not a clear candidate.
    """
    if len(segments) < 2:
        return {
            "groups": [],
            "discarded_intervals": [],
            "kept_segments": segments,
            "total_retakes_removed": 0
        }
        
    n = len(segments)
    take_groups: List[Dict[str, Any]] = []
    visited = set()
    group_counter = 1
    
    i = 0
    while i < n:
        if i in visited:
            i += 1
            continue
            
        candidates = [i]
        curr_seg = segments[i]
        
        # Look ahead for repeated takes within temporal restart window
        for j in range(i + 1, n):
            next_seg = segments[j]
            if next_seg["start"] - curr_seg["end"] > max_gap_seconds:
                break
                
            sim = text_similarity(curr_seg["text"], next_seg["text"])
            if sim >= similarity_threshold:
                candidates.append(j)
                
        if len(candidates) > 1:
            scored_candidates = []
            for idx in candidates:
                q_score = score_take_quality(segments[idx])
                scored_candidates.append({
                    "segment_index": idx,
                    "segment": segments[idx],
                    "quality_score": q_score
                })
                visited.add(idx)
                
            # Default to the LAST take unless an earlier take is clearly superior
            last_candidate = scored_candidates[-1]
            best_candidate = last_candidate
            
            # Check for a "clear candidate":
            # If the last take is an aborted fragment (e.g. cut off, score < 0.6)
            # and an earlier candidate is complete (score >= 1.2, diff > 0.5)
            for c in scored_candidates[:-1]:
                if c["quality_score"] - last_candidate["quality_score"] > 0.5:
                    best_candidate = c
                    break
                    
            winning_idx = best_candidate["segment_index"]
            
            group_info = {
                "group_id": f"retake_group_{group_counter}",
                "candidates": [
                    {
                        "index": c["segment_index"],
                        "start": c["segment"]["start"],
                        "end": c["segment"]["end"],
                        "text": c["segment"]["text"],
                        "is_winner": (c["segment_index"] == winning_idx),
                        "quality_score": round(c["quality_score"], 2)
                    }
                    for c in scored_candidates
                ],
                "winner_index": winning_idx,
                "winner_reason": "superior_quality" if best_candidate != last_candidate else "last_take_default"
            }
            take_groups.append(group_info)
            group_counter += 1
        else:
            visited.add(i)
            
        i += 1
        
    discarded_intervals: List[Dict[str, Any]] = []
    discarded_indices = set()
    for g in take_groups:
        for c in g["candidates"]:
            if not c["is_winner"]:
                discarded_intervals.append({
                    "start": c["start"],
                    "end": c["end"],
                    "duration": round(c["end"] - c["start"], 3),
                    "reason": f"Discarded repeated take (won by {g['winner_reason']})",
                    "text": c["text"],
                    "group_id": g["group_id"]
                })
                discarded_indices.add(c["index"])
                
    kept_segments = [s for idx, s in enumerate(segments) if idx not in discarded_indices]
    
    return {
        "groups": take_groups,
        "discarded_intervals": discarded_intervals,
        "kept_segments": kept_segments,
        "total_retakes_removed": len(discarded_intervals)
    }

def filter_speech_segments_by_retakes(
    speech_segments: List[Dict[str, Any]],
    discarded_intervals: List[Dict[str, Any]]
) -> List[Dict[str, Any]]:
    """
    Remove speech segments that fall within discarded retake intervals.
    Tags segments with retake metadata for frontend timeline visualization.
    """
    if not discarded_intervals:
        for s in speech_segments:
            s["is_retake"] = False
        return speech_segments
        
    filtered = []
    for s in speech_segments:
        s_mid = (s["start"] + s["end"]) / 2.0
        s_dur = s["end"] - s["start"]
        is_retake = False
        group_id = None
        
        for disc in discarded_intervals:
            # Check overlap
            overlap_start = max(s["start"], disc["start"])
            overlap_end = min(s["end"], disc["end"])
            overlap = max(0.0, overlap_end - overlap_start)
            
            if overlap > 0.5 * s_dur or (s_mid >= disc["start"] and s_mid <= disc["end"]):
                is_retake = True
                group_id = disc.get("group_id")
                break
                
        s["is_retake"] = is_retake
        if group_id:
            s["retake_group_id"] = group_id
            
        if not is_retake:
            filtered.append(s)
            
    return filtered
