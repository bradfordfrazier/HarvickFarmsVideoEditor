import os
import pytest
from pathlib import Path
from fastapi.testclient import TestClient

from backend.app import app
from backend.config import ASSETS_DIR, OUTPUT_DIR, UPLOADS_DIR
from backend.core.video_reframer import build_reframe_filter
from backend.core.branding_engine import build_branding_filter, ensure_default_harvick_assets
from backend.core.audio_master import build_audio_filter
from backend.core.silence_detector import analyze_video
from backend.core.exporter import run_export_pipeline, JOBS

client = TestClient(app)

def test_health_endpoint():
    res = client.get("/api/health")
    assert res.status_code == 200
    data = res.json()
    assert data["status"] == "online"
    assert "has_nvenc" in data
    assert "default_settings" in data

def test_presets_endpoint():
    res = client.get("/api/presets")
    assert res.status_code == 200
    presets = res.json()
    assert isinstance(presets, list)
    assert len(presets) >= 2
    preset_ids = [p["id"] for p in presets]
    assert "youtube_shorts_reframe" in preset_ids
    assert "longform_clean_cut" in preset_ids

def test_frontend_serving():
    res = client.get("/")
    assert res.status_code == 200
    assert "Harvick Farms Video" in res.text
    assert "previewVideo" in res.text

def test_reframe_filter_builder():
    filter_916 = build_reframe_filter("0:v", "v_out", target_aspect="9:16", framing_mode="blur_pillarbox")
    assert "boxblur" in filter_916
    assert "1080:1920" in filter_916

    filter_crop = build_reframe_filter("0:v", "v_out", target_aspect="1:1", framing_mode="crop")
    assert "crop=1080:1080" in filter_crop

    filter_pass = build_reframe_filter("0:v", "v_out", target_aspect="16:9", framing_mode="passthrough")
    assert "null" in filter_pass

def test_branding_filter_builder():
    ensure_default_harvick_assets()
    brand_filter, extra_asset = build_branding_filter(
        "v_in", "v_branded",
        position="top_right",
        opacity=0.85,
        lower_third_text="Test Banner"
    )
    assert "overlay=" in brand_filter
    assert "drawbox=" in brand_filter
    assert "drawtext=" in brand_filter
    assert extra_asset is not None
    assert Path(extra_asset).exists()

def test_audio_mastering_builder():
    full_filter = build_audio_filter(
        "0:a", "a_mastered",
        normalize=True,
        target_lufs=-14.0,
        cleanup_artifacts=True,
        resync_drift=True,
        audio_delay_ms=100.0
    )
    assert "loudnorm=I=-14.0" in full_filter
    assert "highpass=f=80" in full_filter
    assert "afftdn=nf=-25" in full_filter
    assert "aresample=async=1000" in full_filter
    assert "adelay=delays=100:all=1" in full_filter

    passthrough_filter = build_audio_filter(
        "0:a", "a_mastered",
        normalize=False,
        cleanup_artifacts=False,
        resync_drift=False,
        audio_delay_ms=0.0
    )
    assert "anull" in passthrough_filter

def test_silence_detection_on_sample():
    sample_file = UPLOADS_DIR / "test_sample.mp4"
    if not sample_file.exists():
        pytest.skip("Test sample not generated")

    analysis = analyze_video(str(sample_file), noise_db=-30.0, min_silence_duration=0.5, padding=0.1)
    assert analysis["metadata"]["duration"] > 0
    assert len(analysis["speech_segments"]) > 0
    assert len(analysis["silence_intervals"]) > 0
    assert analysis["time_saved_seconds"] > 0
    assert len(analysis["waveform"]) > 0

def test_export_pipeline_execution(tmp_path):
    sample_file = UPLOADS_DIR / "test_sample.mp4"
    if not sample_file.exists():
        pytest.skip("Test sample not generated")

    job_id = "test_export_job"
    output_filename = "test_pipeline_run.mp4"
    
    run_export_pipeline(
        job_id=job_id,
        source_file=str(sample_file),
        segments=[{"start": 0.0, "end": 1.6, "duration": 1.6}],
        target_aspect="9:16",
        framing_mode="blur_pillarbox",
        logo_overlay=True,
        logo_position="top_right",
        logo_opacity=0.8,
        lower_third_text="Harvick Farms Pipeline Test",
        burn_subtitles=False,
        normalize_audio=True,
        output_filename=output_filename
    )

    job = JOBS.get(job_id)
    assert job is not None
    assert job["status"] == "completed"
    assert job["progress"] == 100.0
    
    output_file = OUTPUT_DIR / output_filename
    assert output_file.exists()
    assert output_file.stat().st_size > 0

def test_detect_retakes_endpoint():
    sample_file = UPLOADS_DIR / "test_sample.mp4"
    if not sample_file.exists():
        pytest.skip("Test sample not generated")

    res = client.post("/api/detect-retakes", json={
        "file_path": str(sample_file),
        "similarity_threshold": 0.55,
        "max_gap_seconds": 60.0,
        "speech_segments": [{"start": 0.0, "end": 1.5, "duration": 1.5}]
    })
    assert res.status_code == 200
    data = res.json()
    assert "groups" in data
    assert "discarded_intervals" in data
    assert "total_retakes_removed" in data
    assert "filtered_speech_segments" in data
