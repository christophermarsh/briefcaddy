"""Document-processing helper."""

from __future__ import annotations

import re
from typing import Any


def decode_pdf417(image: Any) -> str | None:
    """Document-processing helper."""
    try:
        import zxingcpp
    except ImportError:
        return None

    try:
        results = zxingcpp.read_barcodes(image, formats=zxingcpp.BarcodeFormat.PDF417)
    except Exception:  # noqa: BLE001 -- decoding is best-effort, never fatal
        return None

    return results[0].text if results else None


_FIELD_LINE = re.compile(r"^(D[A-Z]{2})(.*)$")


def parse_aamva(raw: str) -> dict[str, str]:
    """Document-processing helper."""
    fields: dict[str, str] = {}
    for line in raw.replace("\r", "\n").split("\n"):
        match = _FIELD_LINE.match(line.strip())
        if match:
            code, value = match.groups()
            value = value.strip()
            if value:
                fields[code] = value
    return fields
