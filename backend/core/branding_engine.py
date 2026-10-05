import os
from pathlib import Path
from typing import Dict, Any, Optional, Tuple
from backend.config import ASSETS_DIR

POSITION_COORDS = {
    "top_right": "W-w-40:40",
    "top_left": "40:40",
    "bottom_right": "W-w-40:H-h-60",
    "bottom_left": "40:H-h-60",
    "center": "(W-w)/2:(H-h)/2"
}

def ensure_default_harvick_assets():
    """Generate default Harvick Farms branding SVG if not present."""
    logo_svg = ASSETS_DIR / "harvick_farms_logo.svg"
    if not logo_svg.exists():
        svg_content = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 500 160" width="500" height="160">
  <defs>
    <linearGradient id="farmGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#2E7D32"/>
      <stop offset="100%" stop-color="#1B5E20"/>
    </linearGradient>
    <linearGradient id="goldGrad" x1="0%" y1="0%" x2="100%" y2="100%">
      <stop offset="0%" stop-color="#FFD54F"/>
      <stop offset="100%" stop-color="#FFA000"/>
    </linearGradient>
    <filter id="shadow" x="-20%" y="-20%" width="140%" height="140%">
      <feDropShadow dx="2" dy="3" stdDeviation="3" flood-opacity="0.5"/>
    </filter>
  </defs>
  
  <rect width="100%" height="100%" rx="24" fill="#0D1117" opacity="0.85" filter="url(#shadow)"/>
  <rect x="3" y="3" width="494" height="154" rx="21" fill="none" stroke="url(#goldGrad)" stroke-width="2.5" opacity="0.6"/>
  
  <!-- Tractor / Barn silhouette icon -->
  <g transform="translate(25, 25)" fill="url(#goldGrad)">
    <path d="M 35 15 L 75 15 L 85 45 L 25 45 Z" opacity="0.9"/>
    <rect x="20" y="45" width="70" height="35" rx="4"/>
    <circle cx="32" cy="80" r="18" fill="#1B5E20" stroke="#FFD54F" stroke-width="4"/>
    <circle cx="75" cy="82" r="14" fill="#1B5E20" stroke="#FFD54F" stroke-width="3"/>
    <path d="M 50 15 L 50 5 L 56 5 L 56 15 Z"/>
    <!-- Wheat sheaf accent -->
    <path d="M 85 30 Q 95 10 92 5 Q 85 20 85 30 Z" fill="#FFD54F"/>
  </g>
  
  <!-- Text Brand -->
  <text x="135" y="65" font-family="'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-weight="900" font-size="38" fill="#FFFFFF" letter-spacing="2">HARVICK</text>
  <text x="315" y="65" font-family="'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-weight="900" font-size="38" fill="url(#goldGrad)" letter-spacing="2">FARMS</text>
  <text x="137" y="105" font-family="'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-weight="600" font-size="16" fill="#A5D6A7" letter-spacing="6">EST. QUALITY LIVESTOCK &amp; CROPS</text>
  
  <line x1="137" y1="120" x2="460" y2="120" stroke="url(#goldGrad)" stroke-width="2" opacity="0.5"/>
</svg>"""
        logo_svg.write_text(svg_content, encoding="utf-8")

    logo_png = ASSETS_DIR / "harvick_farms_logo.png"
    if not logo_png.exists():
        try:
            from PIL import Image, ImageDraw, ImageFont
            w, h = 1000, 320
            img = Image.new('RGBA', (w, h), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)
            draw.rounded_rectangle([10, 10, w - 10, h - 10], radius=40, fill=(13, 17, 23, 220), outline=(251, 192, 45, 200), width=4)
            icon_left = 60
            draw.rounded_rectangle([icon_left, 90, icon_left + 140, 210], radius=16, fill=(46, 125, 50, 240), outline=(255, 213, 79, 255), width=3)
            draw.ellipse([icon_left + 15, 170, icon_left + 65, 220], fill=(27, 94, 32, 255), outline=(255, 213, 79, 255), width=4)
            draw.ellipse([icon_left + 90, 175, icon_left + 130, 215], fill=(27, 94, 32, 255), outline=(255, 213, 79, 255), width=3)
            draw.rounded_rectangle([icon_left + 35, 105, icon_left + 75, 145], radius=6, fill=(255, 213, 79, 230))
            try:
                font_large = ImageFont.truetype('arialbd.ttf', 72)
                font_farms = ImageFont.truetype('arialbd.ttf', 72)
                font_sub = ImageFont.truetype('arial.ttf', 24)
            except Exception:
                font_large = ImageFont.load_default()
                font_farms = font_large
                font_sub = font_large
            draw.text((240, 75), 'HARVICK ', fill=(255, 255, 255, 255), font=font_large)
            bbox = draw.textbbox((240, 75), 'HARVICK ', font=font_large)
            draw.text((bbox[2], 75), 'FARMS', fill=(251, 192, 45, 255), font=font_farms)
            draw.text((242, 170), 'QUALITY LIVESTOCK & CROPS', fill=(165, 214, 167, 255), font=font_sub)
            draw.line([(242, 210), (w - 60, 210)], fill=(251, 192, 45, 180), width=3)
            img.save(str(logo_png), 'PNG')
        except Exception:
            pass

    return logo_png if logo_png.exists() else logo_svg

def build_branding_filter(
    video_label: str,
    output_label: str,
    logo_path: Optional[str] = None,
    position: str = "top_right",
    opacity: float = 0.85,
    scale_ratio: float = 0.22,
    lower_third_text: Optional[str] = None
) -> Tuple[str, Optional[str]]:
    """
    Returns (filter_complex_string, extra_input_file_path_if_needed).
    """
    ensure_default_harvick_assets()
    
    active_logo = logo_path
    default_png = ASSETS_DIR / "harvick_farms_logo.png"
    if not active_logo or not Path(active_logo).exists():
        if default_png.exists():
            active_logo = str(default_png)
    elif active_logo.lower().endswith(".svg") and default_png.exists():
        active_logo = str(default_png)

    coords = POSITION_COORDS.get(position, "W-w-40:40")

    filter_chains = []
    current_v = video_label

    extra_input = None
    if active_logo and Path(active_logo).exists():
        extra_input = active_logo
        # The extra input is assigned an input index in exporter.py (e.g. [logo_in])
        # We scale logo relative to video width, apply opacity, then overlay
        logo_filter = (
            f"[logo_in]format=rgba,"
            f"colorchannelmixer=aa={opacity},"
            f"scale=iw*min(1\\,({scale_ratio}*1080)/iw):-1[scaled_logo];"
            f"[{current_v}][scaled_logo]overlay={coords}[v_branded]"
        )
        filter_chains.append(logo_filter)
        current_v = "v_branded"

    if lower_third_text:
        # Lower third banner
        # Text escaped for FFmpeg
        clean_text = lower_third_text.replace(":", "\\:").replace("'", "\\'")
        banner_filter = (
            f"[{current_v}]drawbox=y=ih-180:color=black@0.65:width=iw:height=100:t=fill,"
            f"drawtext=text='{clean_text}':fontcolor=white:fontsize=36:"
            f"x=(w-text_w)/2:y=h-145:box=0[{output_label}]"
        )
        filter_chains.append(banner_filter)
    else:
        if current_v != output_label:
            filter_chains.append(f"[{current_v}]null[{output_label}]")

    return ";".join(filter_chains), extra_input
