"""
Generate RCG PWA icons. Uses Pillow (already in jupyterEnv via pillow dep).
Writes to /home/nixos/Prod/V1/outputs/icon-192.png and icon-512.png.
"""
from PIL import Image, ImageDraw, ImageFont
from pathlib import Path

OUT_DIR = Path("/home/nixos/Prod/V1/outputs")
OUT_DIR.mkdir(parents=True, exist_ok=True)

BG     = (10, 10, 10)         # #0a0a0a — matches dashboard
GOLD   = (201, 168, 76)       # --gold
GOLD2  = (224, 188, 92)       # --gold2 highlight
BORDER = (38, 38, 38)         # --border


def find_bold_font_path():
    """
    Resolve a real bold TTF on this NixOS box. Prefer matplotlib's bundled
    DejaVuSans-Bold (most reliable), fall back to fontconfig-listed fonts,
    finally fall back to PIL's bitmap default (which is tiny — caller should warn).
    """
    # 1) matplotlib bundles fonts at well-known relative path
    try:
        import matplotlib
        candidate = Path(matplotlib.get_data_path()) / "fonts" / "ttf" / "DejaVuSans-Bold.ttf"
        if candidate.exists():
            return str(candidate)
    except Exception:
        pass

    # 2) fontconfig discovery
    import subprocess
    try:
        out = subprocess.run(["fc-match", "-f", "%{file}", "DejaVu Sans:bold"],
                             capture_output=True, text=True, timeout=3)
        if out.returncode == 0 and out.stdout.strip():
            return out.stdout.strip()
    except Exception:
        pass

    # 3) glob fallback
    import glob
    for pattern in ("/nix/store/*/share/fonts/truetype/DejaVuSans-Bold.ttf",
                    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"):
        for path in glob.glob(pattern):
            return path

    return None


def draw_icon(size: int, font_path: str | None) -> Image.Image:
    """Square gold-on-black RCG icon with bold readable RCG mark."""
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    # Outer rounded frame
    margin = max(2, size // 24)
    border_w = max(2, size // 56)
    d.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=size // 7,
        outline=GOLD,
        width=border_w,
    )

    # Centered "RCG" — fill majority of inner area
    text = "RCG"
    if font_path:
        # Iterate to find the largest font size that fits horizontally.
        max_text_w = size - margin * 4
        max_text_h = size - margin * 4
        # Start at roughly 70% of icon size, shrink until text fits both width + height
        font_size = int(size * 0.70)
        while font_size > 8:
            font = ImageFont.truetype(font_path, font_size)
            bbox = d.textbbox((0, 0), text, font=font)
            tw = bbox[2] - bbox[0]
            th = bbox[3] - bbox[1]
            if tw <= max_text_w and th <= max_text_h:
                break
            font_size -= 4
    else:
        # PIL default — will look terrible, but avoid hard-failing
        font = ImageFont.load_default()
        bbox = d.textbbox((0, 0), text, font=font)

    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]

    # Drop shadow + main text
    shadow = max(1, size // 90)
    d.text((tx + shadow, ty + shadow), text, font=font, fill=(0, 0, 0))
    d.text((tx, ty), text, font=font, fill=GOLD2)

    # Tiny live-feed dot lower right
    dot_r = max(3, size // 24)
    dot_cx = size - margin - dot_r - max(1, size // 60)
    dot_cy = size - margin - dot_r - max(1, size // 60)
    d.ellipse(
        [dot_cx - dot_r, dot_cy - dot_r, dot_cx + dot_r, dot_cy + dot_r],
        fill=(46, 255, 156),
    )
    return img


def main():
    font_path = find_bold_font_path()
    if font_path:
        print(f"  font: {font_path}")
    else:
        print("  WARNING: no bold TTF found; falling back to PIL default (text will be tiny)")
    for sz in (192, 512):
        img = draw_icon(sz, font_path)
        out = OUT_DIR / f"icon-{sz}.png"
        img.save(out, "PNG", optimize=True)
        print(f"  wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
