"""Bounded, client-confirmed capture attachments; never replace pipeline evidence.

Quality measurements and corner selection are explicitly client-reported
heuristics, not server OCR/readability/identity or a trusted pixel transform.
"""
from __future__ import annotations

import hashlib
import io
import json
import math
import os
import re

from PIL import Image

POLICY = "local-paper-v1"
FLAGS = {"blur", "glare", "cutoff", "resolution", "detection"}
MAX_PIXELS = 12_000_000
MAX_FALLBACK_PIXELS = 24_000_000  # includes common 4032x3024 phone originals


def _dimensions(data, limit):
    try:
        with Image.open(io.BytesIO(data)) as image:
            if image.width * image.height > limit:
                raise ValueError("capture_image_too_large")
            if image.format not in {"JPEG", "PNG"}:
                raise ValueError()
            size = list(image.size)
            image.verify()
        # verify checks the container; load also rejects truncated pixel streams.
        with Image.open(io.BytesIO(data)) as image:
            image.load()
        return size
    except ValueError as exc:
        if str(exc) == "capture_image_too_large":
            raise
        raise ValueError("invalid_capture_image") from None
    except (OSError, SyntaxError, Image.DecompressionBombError):
        raise ValueError("invalid_capture_image") from None


def prepare(original, metadata, derivative=None):
    """Strict schema and actual decoded dimensions; server computes both hashes."""
    if not isinstance(metadata, str) or len(metadata) > 4096:
        raise ValueError("invalid_capture_metadata")
    try:
        value = json.loads(metadata)
    except (ValueError, TypeError):
        raise ValueError("invalid_capture_metadata") from None
    required = {"policy", "source_dimensions", "corners", "selection", "flags", "override", "sharpness", "saturated"}
    if not isinstance(value, dict) or set(value) != required or value["policy"] != POLICY:
        raise ValueError("invalid_capture_metadata")
    source_size = _dimensions(original, MAX_PIXELS)
    dims = value["source_dimensions"]
    if not isinstance(dims, list) or len(dims) != 2 or any(type(n) is not int for n in dims) or dims != source_size:
        raise ValueError("invalid_capture_dimensions")
    if not isinstance(value["selection"], str) or value["selection"] not in {"original", "corrected"} or type(value["override"]) is not bool:
        raise ValueError("invalid_capture_metadata")
    flags = value["flags"]
    if not isinstance(flags, list) or len(flags) > 5 or any(not isinstance(f, str) or f not in FLAGS for f in flags) or len(set(flags)) != len(flags):
        raise ValueError("invalid_capture_metadata")
    if flags and not value["override"]:
        raise ValueError("capture_warning_confirmation_required")
    for field, maximum in (("sharpness", 1_100_000), ("saturated", 1)):
        n = value[field]
        if type(n) not in (float, int) or not math.isfinite(n) or not 0 <= n <= maximum:
            raise ValueError("invalid_capture_metadata")
    p = value["corners"]
    if p is not None:
        if not isinstance(p, list) or len(p) != 4 or any(not isinstance(q, list) or len(q) != 2 or any(type(n) not in (int, float) or not math.isfinite(n) or not 0 <= n <= 1 for n in q) for q in p):
            raise ValueError("invalid_capture_corners")
        cross = lambda a, b, c: (b[0]-a[0])*(c[1]-a[1])-(b[1]-a[1])*(c[0]-a[0])
        if any(cross(p[i], p[(i+1)%4], p[(i+2)%4]) <= .002 for i in range(4)) or sum(q[0]*p[(i+1)%4][1]-q[1]*p[(i+1)%4][0] for i, q in enumerate(p)) <= .04:
            raise ValueError("invalid_capture_corners")
    corrected = value["selection"] == "corrected"
    if corrected != (derivative is not None) or corrected and p is None:
        raise ValueError("invalid_capture_derivative")
    derivative_size = _dimensions(derivative, 2_000_000) if corrected else None
    if derivative_size:
        length = lambda a, b: math.hypot((a[0]-b[0])*dims[0], (a[1]-b[1])*dims[1])
        expected = [max(length(p[0], p[1]), length(p[3], p[2])), max(length(p[0], p[3]), length(p[1], p[2]))]
        rounded = [math.floor(n + .5) for n in expected]
        if any(n < 1 for n in rounded):
            raise ValueError("invalid_capture_dimensions")
        scale = min(1, 1600 / max(rounded), math.sqrt(2_000_000 / (rounded[0] * rounded[1])))
        target = [max(1, math.floor(n * scale)) for n in rounded]
        if max(derivative_size) > 1600 or any(abs(derivative_size[i] - target[i]) > 2 for i in range(2)):
            raise ValueError("invalid_capture_dimensions")
    return {"version": 1, "policy": POLICY, "source_dimensions": source_size, "corners": p,
            "selection": value["selection"], "client_reported_quality": {k: value[k] for k in ("flags", "sharpness", "saturated", "override")},
            "quality_review_required": bool(flags), "original_sha256": hashlib.sha256(original).hexdigest(),
            "derivative_sha256": hashlib.sha256(derivative).hexdigest() if corrected else None,
            "derivative_dimensions": derivative_size, "transform": "client_reported_projective_crop" if corrected else "none"}


def bind(capture, identity):
    if not re.fullmatch(r"[a-z0-9_-]{1,160}", identity):
        raise ValueError("invalid_capture_identity")
    return dict(capture, original_stored=f"capture-evidence/{identity}-original.image",
                derivative_stored=f"capture-evidence/{identity}-corrected.image" if capture["derivative_sha256"] else None)


def original_attachment(original):
    """Ordinary JPG/PNG fallback retains raw bytes without an assessment claim."""
    try:
        dimensions = _dimensions(original, MAX_FALLBACK_PIXELS)
    except ValueError as exc:
        if str(exc) == "invalid_capture_image":
            raise ValueError("That image can't be opened. Please add a clear photo or a PDF.") from None
        raise
    return {"version": 1, "policy": "original-file-v1", "source_dimensions": dimensions,
            "corners": None, "selection": "original", "client_reported_quality": {"assessment": "not_run"},
            "quality_review_required": True, "original_sha256": hashlib.sha256(original).hexdigest(),
            "derivative_sha256": None, "derivative_dimensions": None, "transform": "none"}


def digest(capture):
    return hashlib.sha256(json.dumps(capture, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest() if capture else None


def _target(store, client, relative):
    from .queue_bridge import _safe
    if not re.fullmatch(r"capture-evidence/[a-z0-9_-]{1,160}-(?:original|corrected)\.image", relative or ""):
        raise ValueError("upload_receipt_damaged")
    target = store.client_dir(client) / relative
    _safe(target)
    return target


def persist(store, client, capture, original, derivative):
    for field, payload in (("original", original), ("derivative", derivative)):
        relative = capture[field + "_stored"]
        if relative is None:
            continue
        target = _target(store, client, relative)
        expected = capture[field + "_sha256"]
        if payload is None or hashlib.sha256(payload).hexdigest() != expected:
            raise ValueError("upload_receipt_damaged")
        if target.exists():
            if hashlib.sha256(target.read_bytes()).hexdigest() != expected:
                raise ValueError("upload_receipt_damaged")
            continue  # immutable: retries verify, never replace retained bytes
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        from .queue_bridge import _safe
        _safe(partial)
        partial.write_bytes(payload)
        os.replace(partial, target)


def verify(store, client, capture):
    if not capture:
        return True
    for field in ("original", "derivative"):
        relative = capture[field + "_stored"]
        if relative is None:
            continue
        target = _target(store, client, relative)
        if not target.is_file():
            return False
        if hashlib.sha256(target.read_bytes()).hexdigest() != capture[field + "_sha256"]:
            raise ValueError("upload_receipt_damaged")
    return True
