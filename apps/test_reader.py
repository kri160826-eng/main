"""Non-UI smoke test: run extract() over the sample images and print results.

Usage:
    python test_reader.py            # runs on everything in samples/
    python test_reader.py path.png   # runs on a specific image
"""

from __future__ import annotations

import sys
from pathlib import Path

from meter_reader import extract

MIME_BY_EXT = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}


def run(path: Path) -> None:
    mime = MIME_BY_EXT.get(path.suffix.lower(), "image/png")
    result = extract(path.read_bytes(), mime)
    print(f"\n=== {path.name} ===")
    print(f"  reading_m3        : {result.reading_m3}")
    print(f"  integer (black)   : {result.integer_part!r}")
    print(f"  decimal (red)     : {result.decimal_part!r}")
    print(f"  handwritten label : {result.handwritten_label!r}")
    print(f"  confidence        : {result.confidence:.0%}")
    print(f"  review required   : {result.review_required}")
    if result.notes:
        print(f"  notes             : {result.notes}")


def main() -> None:
    if len(sys.argv) > 1:
        targets = [Path(a) for a in sys.argv[1:]]
    else:
        samples = Path(__file__).parent / "samples"
        targets = sorted(
            p for p in samples.iterdir() if p.suffix.lower() in MIME_BY_EXT
        )
        if not targets:
            print("No images found in samples/. Move the sample PNGs there first.")
            return

    for path in targets:
        try:
            run(path)
        except Exception as exc:  # noqa: BLE001
            print(f"\n=== {path.name} ===\n  ERROR: {exc}")


if __name__ == "__main__":
    main()
