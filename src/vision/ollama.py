"""Local vision-language model client (Ollama) for reading handwritten
questionnaire answers. Local only, per docs/decisions.md's data policy:
the model runs on this machine and requests go to localhost -- nothing
leaves the machine.

The model is used for one bounded task: *transcribing* what is written
next to a printed label. It is never asked a legal question, and its
output is Tier 3 (client self-report, machine-transcribed), always flagged
for human review, and cross-checked against documents where one exists.

WSL development note (same situation as classify/ocr.py's Tesseract
handling): a Windows Ollama install listens on the *Windows* localhost,
which a WSL process can't reach. Rather than exposing Ollama on a network
interface (OLLAMA_HOST=0.0.0.0 would put client data on the wire), under
WSL the request is handed to Windows' own curl.exe, which can. On a native
install (Windows Python + Windows Ollama, or Linux + Linux) the plain HTTP
path is used and this never triggers.
"""

from __future__ import annotations

import base64
import io
import json
import os
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
# The *instruct* (non-reasoning) variant: the default qwen3-vl:8b tag
# Fictional fixture or generic implementation note.
# in its reasoning for 15k tokens without ever answering.
DEFAULT_MODEL = os.environ.get("VISION_MODEL", "qwen3-vl:8b-instruct")
MAX_OUTPUT_TOKENS = 400  # a field transcription is short; anything longer is a runaway


class VisionModelError(RuntimeError):
    """The model produced no usable answer (runaway, empty, server error).
    Callers treat this as "unread -> human", never as a blank answer."""


def _image_b64(image: Any) -> str:
    buf = io.BytesIO()
    image.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode("ascii")


def _post_http(url: str, payload: dict, timeout: float) -> dict:
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _post_via_windows_curl(url: str, payload: dict, timeout: float, work_dir: Path) -> dict:
    from classify.ocr import _to_windows_path

    work_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=work_dir) as tmp:
        body = Path(tmp) / "request.json"
        body.write_text(json.dumps(payload), encoding="utf-8")
        out = subprocess.run(
            ["curl.exe", "-s", "-m", str(int(timeout)), "-H", "Content-Type: application/json",
             "--data-binary", "@" + _to_windows_path(body.resolve()), url],
            check=True, capture_output=True,
        )
    return json.loads(out.stdout.decode("utf-8"))


def _is_wsl() -> bool:
    return os.name == "posix" and "microsoft" in Path("/proc/version").read_text(errors="ignore").lower() if Path("/proc/version").exists() else False


def generate(
    prompt: str,
    images: list[Any],
    model: str = DEFAULT_MODEL,
    url: str = DEFAULT_URL,
    json_schema: dict | None = None,
    timeout: float = 600,
    work_dir: str | Path = ".ocr_tmp",
) -> str:
    """One non-streaming generation. Temperature 0: transcription should be
    deterministic, not creative. json_schema, if given, constrains output
    to that schema (Ollama structured outputs)."""
    payload: dict[str, Any] = {
        "model": model,
        "prompt": prompt,
        "images": [_image_b64(im) for im in images],
        "stream": False,
        "options": {"temperature": 0, "num_ctx": 8192, "num_predict": MAX_OUTPUT_TOKENS},
    }
    if json_schema is not None:
        payload["format"] = json_schema
    endpoint = url.rstrip("/") + "/api/generate"
    import jobs

    with jobs.gpu_lock():  # one reading at a time on the GPU, whichever process asks (the overnight run's workers, the job worker)
        try:
            result = _post_http(endpoint, payload, timeout)
        except (urllib.error.URLError, ConnectionError, OSError):
            if not _is_wsl():
                raise
            result = _post_via_windows_curl(endpoint, payload, timeout, Path(work_dir))
    if "error" in result:
        raise VisionModelError(f"Ollama error: {result['error']}")
    if result.get("done_reason") == "length" or not result.get("response", "").strip():
        raise VisionModelError(f"no usable answer (done_reason={result.get('done_reason')!r})")
    return result["response"]
