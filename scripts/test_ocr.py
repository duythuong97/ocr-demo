"""Quick OCR diagnostic — run from project root:

    python scripts/test_ocr.py <image_path> [lang]

Prints the raw PaddleOCR result so you can see exactly what is detected
and at what confidence, without going through the app.
"""
from __future__ import annotations

import sys
from pathlib import Path

def main() -> None:
    if len(sys.argv) < 2:
        print("Usage: python scripts/test_ocr.py <image_path> [lang]")
        sys.exit(1)

    img_path = Path(sys.argv[1]).expanduser().resolve()
    lang = sys.argv[2] if len(sys.argv) >= 3 else "japan"

    if not img_path.exists():
        print(f"ERROR: file not found: {img_path}")
        sys.exit(1)

    print(f"Image : {img_path}  ({img_path.stat().st_size} bytes)")
    print(f"Lang  : {lang}")

    # ── Open image ────────────────────────────────────────────────────────────
    from PIL import Image, ImageEnhance
    img = Image.open(img_path)
    print(f"Mode  : {img.mode}  Size: {img.size}")

    # ── Run PaddleOCR ─────────────────────────────────────────────────────────
    from paddleocr import PaddleOCR
    engine = PaddleOCR(use_angle_cls=True, lang=lang, use_gpu=False, show_log=True)

    import numpy as np

    # Try 1: as-is (converted to RGB)
    arr = np.array(img.convert("RGB"))
    print(f"\n--- OCR attempt 1: RGB as-is ---")
    result = engine.ocr(arr, cls=True)
    _print_result(result)

    # Try 2: contrast enhanced
    enhanced = ImageEnhance.Contrast(img.convert("RGB")).enhance(1.5)
    arr2 = np.array(enhanced)
    print(f"\n--- OCR attempt 2: RGB + contrast ×1.5 ---")
    result2 = engine.ocr(arr2, cls=True)
    _print_result(result2)

    # Try 3: grayscale → RGB (sometimes helps for faint images)
    gray_rgb = np.array(img.convert("L").convert("RGB"))
    print(f"\n--- OCR attempt 3: grayscale → RGB ---")
    result3 = engine.ocr(gray_rgb, cls=True)
    _print_result(result3)


def _print_result(result) -> None:
    if not result:
        print("  [no result — PaddleOCR returned None or empty list]")
        return
    if not result[0]:
        print(f"  [result[0] is empty/None: {result!r}]")
        return
    for i, line in enumerate(result[0]):
        if line is None:
            print(f"  line[{i}]: None")
            continue
        bbox, (text, conf) = line
        marker = "✓" if conf >= 0.3 else "✗"
        print(f"  {marker} line[{i}] conf={conf:.3f}  text={text!r}")


if __name__ == "__main__":
    main()
