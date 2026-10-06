"""Independent synthetic standard-phone photo fallback regression."""
from io import BytesIO

from PIL import Image


def test_standard_twelve_megapixel_jpeg_preserves_original_file_fallback():
    from portal.capture_derivatives import original_attachment
    from portal.store import MAX_UPLOAD

    image = Image.new("RGB", (4032, 3024), "white")
    payload = BytesIO()
    image.save(payload, format="JPEG", quality=85)
    raw = payload.getvalue()
    assert len(raw) < MAX_UPLOAD
    record = original_attachment(raw)
    assert record["source_dimensions"] == [4032, 3024]
    assert record["selection"] == "original"
    assert record["client_reported_quality"] == {"assessment": "not_run"}
    assert record["quality_review_required"] is True
