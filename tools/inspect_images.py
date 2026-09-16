from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageStat


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()

    for path in sorted(args.directory.glob("*.png")):
        with Image.open(path) as image:
            rgb = image.convert("RGB")
            print(
                f"{path.name}: {rgb.width}x{rgb.height}, bytes={path.stat().st_size}, "
                f"extrema={rgb.getextrema()}, "
                f"stddev={[round(value, 1) for value in ImageStat.Stat(rgb).stddev]}"
            )


if __name__ == "__main__":
    main()
