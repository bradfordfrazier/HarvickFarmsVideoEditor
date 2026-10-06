import os
import json
import shutil
import subprocess
from pathlib import Path
from typing import Dict, Any, List, Optional
from fastapi import APIRouter, UploadFile, File, Form, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from backend.config import (
    UPLOADS_DIR, OUTPUT_DIR, PRESETS_DIR, ASSETS_DIR,
    HAS_NVENC, FFMPEG_PATH, DEFAULT_SETTINGS
)
from backend.core.silence_detector import analyze_video, get_media_metadata
from backend.core.exporter import start_export_async, get_job_status

router = APIRouter(prefix="/api")

class ScanRequest(BaseModel):
    file_path: str
    noise_db: float = -35.0
    min_silence_duration: float = 0.45
    padding_duration: float = 0.12
    detect_retakes: bool = False
    similarity_threshold: float = 0.55

class RetakeRequest(BaseModel):
    file_path: str
    similarity_threshold: float = 0.55
    max_gap_seconds: float = 150.0  # longest single take before its retake starts
    speech_segments: Optional[List[Dict[str, Any]]] = None

class RenderSegment(BaseModel):
    start: float
    end: float
    duration: float

class RenderRequest(BaseModel):
    source_file: str
    segments: List[RenderSegment]
    target_aspect: str = "9:16"
    framing_mode: str = "blur_pillarbox"
    logo_overlay: bool = True
    logo_path: Optional[str] = None
    logo_position: str = "top_right"
    logo_opacity: float = 0.85
    logo_scale: float = 0.22
    lower_third_text: Optional[str] = None
    burn_subtitles: bool = False
    subtitle_style: str = "bold_yellow"
    normalize_audio: bool = True
    target_lufs: float = -14.0
    cleanup_audio: bool = True
    resync_drift: bool = True
    audio_delay_ms: float = 0.0
    fps: float = 60.0
    output_filename: Optional[str] = None
    rough_cut: bool = False  # copy the video instead of re-encoding (fast, keyframe-accurate)

@router.get("/health")
def health_check():
    return {
        "status": "online",
        "has_nvenc": HAS_NVENC,
        "ffmpeg": FFMPEG_PATH,
        "default_settings": DEFAULT_SETTINGS
    }

@router.post("/scan")
def scan_media(req: ScanRequest):
    p = Path(req.file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    analysis = analyze_video(
        file_path=str(p),
        noise_db=req.noise_db,
        min_silence_duration=req.min_silence_duration,
        padding=req.padding_duration,
        detect_retakes=req.detect_retakes,
        similarity_threshold=req.similarity_threshold
    )
    return analysis

@router.post("/detect-retakes")
def detect_retakes_endpoint(req: RetakeRequest):
    p = Path(req.file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail=f"File not found: {req.file_path}")

    from backend.core.take_detector import (
        transcribe_audio_whisper,
        detect_repeated_takes,
        filter_speech_segments_by_retakes
    )
    transcribed = transcribe_audio_whisper(str(p))
    retake_res = detect_repeated_takes(
        transcribed,
        similarity_threshold=req.similarity_threshold,
        max_gap_seconds=req.max_gap_seconds
    )
    filtered = []
    if req.speech_segments:
        filtered = filter_speech_segments_by_retakes(req.speech_segments, retake_res["discarded_intervals"])

    return {
        "groups": retake_res["groups"],
        "discarded_intervals": retake_res["discarded_intervals"],
        "total_retakes_removed": retake_res["total_retakes_removed"],
        "transcribed_segments": transcribed,
        "filtered_speech_segments": filtered
    }

@router.post("/upload")
async def upload_video(file: UploadFile = File(...)):
    filename = file.filename or "uploaded_video.mp4"
    dest_path = UPLOADS_DIR / filename
    
    # Save file to uploads directory
    with open(dest_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
        
    return {
        "filename": filename,
        "file_path": str(dest_path.resolve()),
        "stream_url": f"/uploads/{filename}",
        "size": dest_path.stat().st_size
    }

@router.get("/media-stream")
def stream_media(file_path: str):
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        raise HTTPException(status_code=404, detail="Media file not found")
    return FileResponse(str(p), media_type="video/mp4")

@router.get("/presets")
def list_presets():
    presets = []
    for f in PRESETS_DIR.glob("*.json"):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            presets.append({
                "id": f.stem,
                "name": data.get("name", f.stem),
                "description": data.get("description", ""),
                "config": data
            })
        except Exception:
            continue
    return presets

@router.post("/render")
def trigger_render(req: RenderRequest):
    source_p = Path(req.source_file)
    if not source_p.exists():
        raise HTTPException(status_code=404, detail="Source video file not found")

    job_id = start_export_async(
        source_file=str(source_p),
        segments=[s.model_dump() for s in req.segments],
        target_aspect=req.target_aspect,
        framing_mode=req.framing_mode,
        logo_overlay=req.logo_overlay,
        logo_path=req.logo_path,
        logo_position=req.logo_position,
        logo_opacity=req.logo_opacity,
        logo_scale=req.logo_scale,
        lower_third_text=req.lower_third_text,
        burn_subtitles=req.burn_subtitles,
        subtitle_style=req.subtitle_style,
        normalize_audio=req.normalize_audio,
        target_lufs=req.target_lufs,
        cleanup_audio=req.cleanup_audio,
        resync_drift=req.resync_drift,
        audio_delay_ms=req.audio_delay_ms,
        fps=req.fps,
        output_filename=req.output_filename,
        rough_cut=req.rough_cut
    )

    return {"job_id": job_id, "status": "processing"}

@router.get("/jobs/{job_id}")
def check_job(job_id: str):
    job = get_job_status(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job

@router.get("/jobs/{job_id}/download")
def download_rendered_video(job_id: str):
    job = get_job_status(job_id)
    if not job or job.get("status") != "completed":
        raise HTTPException(status_code=400, detail="Video is not ready or failed")
    
    file_path = job.get("output_file")
    if not file_path or not Path(file_path).exists():
        raise HTTPException(status_code=404, detail="Exported file missing")

    return FileResponse(file_path, media_type="video/mp4", filename=Path(file_path).name)

@router.get("/outputs")
def list_outputs():
    files = []
    for f in OUTPUT_DIR.glob("*.mp4"):
        stat = f.stat()
        files.append({
            "name": f.name,
            "path": str(f.resolve()),
            "size_mb": round(stat.st_size / (1024 * 1024), 2),
            "modified": stat.st_mtime,
            "url": f"/output/{f.name}"
        })
    files.sort(key=lambda x: x["modified"], reverse=True)
    return files

@router.post("/open-folder")
def open_folder(folder_type: str = "output"):
    target = OUTPUT_DIR if folder_type == "output" else UPLOADS_DIR
    try:
        os.startfile(str(target))
        return {"status": "opened", "path": str(target)}
    except Exception as e:
        return {"error": str(e)}
