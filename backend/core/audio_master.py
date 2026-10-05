from typing import Optional, List

def build_audio_filter(
    input_label: str = "0:a",
    output_label: str = "a_out",
    normalize: bool = True,
    target_lufs: float = -14.0,
    cleanup_artifacts: bool = True,
    resync_drift: bool = True,
    audio_delay_ms: float = 0.0,
    music_file: Optional[str] = None,
    music_volume: float = 0.25
) -> str:
    """
    Build FFmpeg audio filter graph with:
    - Spurious audio artifact cleanup (sub-bass rumble removal, high-frequency hiss removal,
      FFT adaptive noise reduction, speech de-essing, speech dynamic leveling).
    - Broadcast loudness normalization (EBU R128).
    - Audio sync drift locking (dynamic resample PTS alignment).
    - Manual audio offset delay (adelay / atrim).
    """
    chain: List[str] = []

    # 1. Spurious Audio Artifacts Cleanup
    if cleanup_artifacts:
        # Highpass filter at 80Hz: removes sub-bass rumble, wind, and mic handling thumps
        chain.append("highpass=f=80")
        # Lowpass filter at 11kHz: eliminates high-frequency electrical hiss and ultrasonic noise
        chain.append("lowpass=f=11000")
        # Adaptive FFT denoiser: suppresses room/HVAC hum and background noise floor
        chain.append("afftdn=nf=-25")
        # De-esser: tames harsh sibilance ('s' and 'sh' clipping)
        chain.append("deesser=i=0.5:m=0.5:f=0.5:s=o")
        # Speech normalizer: balances voice dynamic range between quiet and loud takes
        chain.append("speechnorm=e=4:r=0.0001:l=1")

    # 2. Broadcast Loudness Normalization (EBU R128)
    if normalize:
        chain.append(f"loudnorm=I={target_lufs}:LRA=7.0:TP=-1.5")

    # 3. Audio Sync Delay Offset (for hardware/mic latency)
    if audio_delay_ms > 0.0:
        chain.append(f"adelay=delays={int(audio_delay_ms)}:all=1")
    elif audio_delay_ms < 0.0:
        trim_sec = round(abs(audio_delay_ms) / 1000.0, 3)
        chain.append(f"atrim=start={trim_sec},asetpts=PTS-STARTPTS")

    # 4. A/V Drift Lock Resampling
    if resync_drift:
        chain.append("aresample=async=1000:min_hard_comp=0.1:first_pts=0")

    if not chain:
        chain.append("anull")

    return f"[{input_label}]" + ",".join(chain) + f"[{output_label}]"
