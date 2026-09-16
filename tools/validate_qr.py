#!/usr/bin/env python3
"""Decode offline QR fixtures with ZXing, independently of the bundled encoder.

Development check only: requires the pinned packages in requirements-qr-test.txt.
Never creates a real authorization session or calls HolyCrab.
"""

from __future__ import annotations

import importlib.util
import io
from pathlib import Path

from PIL import Image
import zxingcpp


def main() -> None:
    spec = importlib.util.spec_from_file_location("qr_validation_cli", Path(__file__).resolve().parents[1] / "holycrab/scripts/holycrab_cli.py")
    assert spec and spec.loader
    cli = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(cli)
    base = "https://www.byteplus.com/en/liveness-face-manage/authorization?"
    fixtures = (
        base + "pl=offline-fixture&token=example",
        base + "pl=" + "x" * 1200,
        base + "pl=example%2B%2F%3D&locale=zh-CN&name=%E5%B0%8F%E6%9E%97",
    )
    for link in fixtures:
        with Image.open(io.BytesIO(cli.qr_png(link))) as image:
            decoded = zxingcpp.read_barcode(image)
        if decoded is None or decoded.text != link:
            raise SystemExit("QR round-trip failed: decoded content did not match the input")
    print(f"Decoded {len(fixtures)} offline QR fixtures with ZXing; all links match exactly.")


if __name__ == "__main__":
    main()
