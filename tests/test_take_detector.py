import pytest
from backend.core.take_detector import (
    clean_text,
    text_similarity,
    score_take_quality,
    detect_repeated_takes,
    filter_speech_segments_by_retakes
)

def test_clean_text():
    assert clean_text("Hello, World! 123") == "hello world 123"
    assert clean_text("  Multiple   spaces...  ") == "multiple spaces"

def test_text_similarity():
    # Identical prefix
    s1 = "Welcome to Harvick Farms today we are planting"
    s2 = "Welcome to Harvick Farms today we have seedlings"
    assert text_similarity(s1, s2) >= 0.90

    # Slight rephrasing
    s3 = "Today that same property four years later is a farm stop"
    s4 = "Today that same property is now a farm stop"
    assert text_similarity(s3, s4) >= 0.60

    # Completely different topics (well below 0.55 threshold)
    s5 = "The weather is really nice outside today"
    assert text_similarity(s1, s5) < 0.35

def test_score_take_quality():
    # Clean complete sentence
    clean = {"text": "All we wanted was just enough to grow food for our family."}
    assert score_take_quality(clean) >= 1.5

    # Short aborted fragment
    frag = {"text": "Instead in 2022"}
    assert score_take_quality(frag) < 0.5

    # Sentence with stutter
    stutter = {"text": "Instead in 2022 instead in 2022 we ended up taking a nursery."}
    assert score_take_quality(stutter) < 1.0

def test_detect_repeated_takes_defaults_to_last_take():
    # Two good takes -> user requirement specifies default to LAST take
    segs = [
        {"start": 10.0, "end": 15.0, "text": "Today that same property four years later is a farm stop."},
        {"start": 17.0, "end": 22.0, "text": "Today that same property is now a farm stop."}
    ]
    res = detect_repeated_takes(segs)
    assert res["total_retakes_removed"] == 1
    assert len(res["groups"]) == 1
    group = res["groups"][0]
    # Winner must be segment at index 1 (the last take)
    assert group["winner_index"] == 1
    assert group["winner_reason"] == "last_take_default"
    assert res["discarded_intervals"][0]["start"] == 10.0
    assert len(res["kept_segments"]) == 1
    assert res["kept_segments"][0]["start"] == 17.0

def test_detect_repeated_takes_picks_clear_candidate_when_last_is_aborted():
    # First take is complete, but last take was aborted/interrupted mid-sentence
    segs = [
        {"start": 10.0, "end": 18.0, "text": "We worked with 20 different local farmers across the region."},
        {"start": 20.0, "end": 22.0, "text": "We worked with"}
    ]
    res = detect_repeated_takes(segs)
    assert res["total_retakes_removed"] == 1
    group = res["groups"][0]
    # Winner must be index 0 (clear candidate, since index 1 is an aborted 3-word fragment)
    assert group["winner_index"] == 0
    assert group["winner_reason"] == "superior_quality"
    assert res["kept_segments"][0]["start"] == 10.0

def test_filter_speech_segments_by_retakes():
    speech_segments = [
        {"start": 0.0, "end": 9.5, "duration": 9.5},
        {"start": 10.0, "end": 15.0, "duration": 5.0},
        {"start": 17.0, "end": 22.0, "duration": 5.0},
        {"start": 25.0, "end": 35.0, "duration": 10.0}
    ]
    discarded = [
        {"start": 10.0, "end": 15.0, "group_id": "retake_group_1"}
    ]
    kept = filter_speech_segments_by_retakes(speech_segments, discarded)
    assert len(kept) == 3
    starts = [s["start"] for s in kept]
    assert 10.0 not in starts
    assert 17.0 in starts
