"""Real subprocess deadlines and pixel limits for optional source previews."""
import subprocess
import time
import pytest
from review.evidence import render_source_page_bounded, render_source_image


def test_render_worker_timeout_stops_actual_subprocess(monkeypatch):
    real_run = subprocess.run
    def slow_worker(args, **kwargs):
        return real_run([args[0], "-c", "import time;time.sleep(30)"], **kwargs)
    monkeypatch.setattr(subprocess, "run", slow_worker)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        render_source_page_bounded(b"fictional source", 0, .15)
    assert time.monotonic() - started < 2


def test_original_image_pixel_budget_before_full_decode(monkeypatch):
    from PIL import Image
    class HugeImage:
        width = 30_000; height = 30_000
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def copy(self): raise AssertionError("Oversized image should not decode")
    monkeypatch.setattr(Image, "open", lambda *args: HugeImage())
    with pytest.raises(ValueError, match="pixel budget"):
        render_source_image(b"fictional header")
