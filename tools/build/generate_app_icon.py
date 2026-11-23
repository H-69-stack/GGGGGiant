"""Generate the application icon assets used by the Windows installer build.

The build script (`deploy\\make_native_windows_setup.ps1`) calls this before
staging `assets\\app_icon.ico` and `assets\\app_icon.png`. The committed assets
under `deploy\\windows\\assets\\` are the source of truth; this script merely
re-emits them (and builds a multi-resolution .ico) so a clean checkout can be
packaged without a designer round-trip.

Usage:
    python tools\\build\\generate_app_icon.py [--repo <repo root>]
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

ICO_SIZES = [(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]


def _fallback_image():
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (256, 256), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle([8, 8, 248, 248], radius=40, fill=(32, 96, 168, 255))
    draw.rounded_rectangle([56, 108, 200, 148], radius=20, fill=(255, 255, 255, 255))
    draw.ellipse([168, 96, 216, 160], fill=(255, 255, 255, 255))
    return img


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Regenerate app icon assets")
    parser.add_argument("--repo", default=str(Path(__file__).resolve().parents[2]))
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()
    assets = repo / "deploy" / "windows" / "assets"
    assets.mkdir(parents=True, exist_ok=True)
    png_path = assets / "app_icon.png"
    ico_path = assets / "app_icon.ico"

    try:
        from PIL import Image
    except ImportError:
        if png_path.exists() and ico_path.exists():
            print(f"Pillow not available; keeping committed icons in {assets}")
            return 0
        print("Pillow is required to generate icons when they are missing", file=sys.stderr)
        return 1

    if png_path.exists():
        img = Image.open(png_path)
        img.load()
        if img.mode != "RGBA":
            img = img.convert("RGBA")
        if img.size != (256, 256):
            img = img.resize((256, 256), Image.LANCZOS)
    else:
        img = _fallback_image()

    img.save(png_path, format="PNG")
    img.save(ico_path, format="ICO", sizes=ICO_SIZES)
    print(f"icons written: {png_path.name}, {ico_path.name} ({', '.join(f'{w}x{h}' for w, h in ICO_SIZES)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
