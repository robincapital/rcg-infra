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


def find_bold_font(size):
    """Best-effort font discovery on NixOS."""
    candidates = [
        "/run/current-system/sw/share/fonts/dejavu/DejaVuSans-Bold.ttf",
        "/nix/store/*/share/fonts/truetype/DejaVuSans-Bold.ttf",
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    import glob
    for c in candidates:
        for path in glob.glob(c):
            try:
                return ImageFont.truetype(path, size)
            except Exception:
                pass
    # fallback to default — coarse, but works
    return ImageFont.load_default()


def draw_icon(size: int) -> Image.Image:
    """Square gold-on-black RCG icon with inset border ring."""
    img = Image.new("RGB", (size, size), BG)
    d = ImageDraw.Draw(img)

    # Outer rounded rectangle frame (thin gold border)
    margin = max(2, size // 32)
    border_w = max(1, size // 80)
    d.rounded_rectangle(
        [margin, margin, size - margin - 1, size - margin - 1],
        radius=size // 8,
        outline=GOLD,
        width=border_w,
    )
    # Subtle inner shadow ring
    inner = margin + border_w + max(1, size // 60)
    d.rounded_rectangle(
        [inner, inner, size - inner - 1, size - inner - 1],
        radius=size // 10,
        outline=BORDER,
        width=1,
    )

    # Centered "RCG" text in gold, bold
    text = "RCG"
    # Choose a font size that fills ~52% of icon width
    target_height = int(size * 0.42)
    font = find_bold_font(target_height)
    bbox = d.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    tx = (size - tw) // 2 - bbox[0]
    ty = (size - th) // 2 - bbox[1]
    # Drop a tiny shadow underneath for depth
    d.text((tx + max(1, size // 200), ty + max(1, size // 200)), text,
           font=font, fill=(0, 0, 0))
    d.text((tx, ty), text, font=font, fill=GOLD2)

    # Tiny tick marker — small inset square in lower-right (subtly indicates "data feed live")
    tick_w = max(4, size // 16)
    tick_x0 = size - margin - tick_w * 2
    tick_y0 = size - margin - tick_w * 2
    d.ellipse(
        [tick_x0, tick_y0, tick_x0 + tick_w, tick_y0 + tick_w],
        fill=(46, 255, 156),  # green-bright
    )
    return img


def main():
    for sz in (192, 512):
        img = draw_icon(sz)
        out = OUT_DIR / f"icon-{sz}.png"
        img.save(out, "PNG", optimize=True)
        print(f"  wrote {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
