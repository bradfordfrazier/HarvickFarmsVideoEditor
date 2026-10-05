from typing import Dict, Tuple

ASPECT_RESOLUTIONS: Dict[str, Tuple[int, int]] = {
    "9:16": (1080, 1920),
    "16:9": (1920, 1080),
    "1:1": (1080, 1080),
    "4:5": (1080, 1350)
}

def build_reframe_filter(
    input_label: str,
    output_label: str,
    target_aspect: str = "9:16",
    framing_mode: str = "blur_pillarbox",
    src_width: int = 1920,
    src_height: int = 1080
) -> str:
    """
    Generate FFmpeg video filter complex string for reframing.
    Modes:
      - 'blur_pillarbox': Blurred expanded background with crisp centered foreground
      - 'crop': Centered zoom crop to fill aspect ratio
      - 'fit': Scale to fit with black letterbox padding
      - 'passthrough': Keep original aspect ratio
    """
    if target_aspect not in ASPECT_RESOLUTIONS or framing_mode == "passthrough":
        return f"[{input_label}]null[{output_label}]"

    target_w, target_h = ASPECT_RESOLUTIONS[target_aspect]

    if framing_mode == "blur_pillarbox":
        # Background: fill & crop & blur
        # Foreground: scale down to fit
        # Composite: overlay sharp over blurred
        bg_scale = f"scale={target_w}:{target_h}:force_original_aspect_ratio=increase"
        bg_crop = f"crop={target_w}:{target_h}"
        bg_blur = "boxblur=25:5,eq=brightness=-0.15"
        fg_scale = f"scale={target_w}:{target_h}:force_original_aspect_ratio=decrease"

        filter_str = (
            f"[{input_label}]split=2[{input_label}_fg][{input_label}_bg];"
            f"[{input_label}_bg]{bg_scale},{bg_crop},{bg_blur}[{input_label}_blurred];"
            f"[{input_label}_fg]{fg_scale}[{input_label}_sharp];"
            f"[{input_label}_blurred][{input_label}_sharp]overlay=(W-w)/2:(H-h)/2[{output_label}]"
        )
        return filter_str

    elif framing_mode == "crop":
        # Center zoom crop
        filter_str = (
            f"[{input_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
            f"crop={target_w}:{target_h}[{output_label}]"
        )
        return filter_str

    elif framing_mode == "fit":
        # Pad with black letterbox
        filter_str = (
            f"[{input_label}]scale={target_w}:{target_h}:force_original_aspect_ratio=decrease,"
            f"pad={target_w}:{target_h}:(ow-iw)/2:(oh-ih)/2:black[{output_label}]"
        )
        return filter_str

    # Default fallback
    return f"[{input_label}]scale={target_w}:{target_h}[{output_label}]"
