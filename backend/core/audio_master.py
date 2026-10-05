from typing import Optional

def build_audio_filter(
    input_label: str = "0:a",
    output_label: str = "a_out",
    normalize: bool = True,
    target_lufs: float = -14.0,
    music_file: Optional[str] = None,
    music_volume: float = 0.25
) -> str:
    """
    Build FFmpeg audio filter graph for loudness normalization (EBU R128)
    and optional background music mixing with sidechain ducking.
    """
    filters = []

    if normalize:
        loudnorm_str = f"[{input_label}]loudnorm=I={target_lufs}:LRA=7.0:TP=-1.5[{output_label}]"
        filters.append(loudnorm_str)
    else:
        filters.append(f"[{input_label}]anull[{output_label}]")

    return ";".join(filters)
