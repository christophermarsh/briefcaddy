"""Every call to the local model takes its turn on the GPU (jobs.gpu_lock), the way the job worker's and the overnight run's readings do: the model is one GPU's, and two uses
loading it together fill its memory. Here the model's address answers from a stand-in that checks the turn is held while it is asked."""

from __future__ import annotations

import os

import pytest

import jobs


@pytest.fixture
def held(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))
    seen = []

    def stand_in(endpoint, payload, timeout):
        fd = os.open(tmp_path / "jobs" / "gpu.lock", os.O_RDWR)
        try:
            taken = not jobs._try_lock(fd)  # somebody else (this call) holds it
            if not taken:
                jobs._unlock(fd)
        finally:
            os.close(fd)
        seen.append(taken)
        return {"response": "ok", "answers": {"kind": {"choice": "passport"}}}

    import vision.ollama as ollama

    monkeypatch.setattr(ollama, "_post_http", stand_in)
    return seen


def test_drafting_takes_the_turn(held):
    import drafting

    assert drafting.local_model("Say ok.", 8)[0] == "ok"
    assert held == [True]


def test_the_decision_model_takes_the_turn(held):
    from learning import decision

    assert decision.decide({"document_text": "x"}, {"kind": {"type": "choice", "instructions": "?", "criteria": {}}}, "m")[0]["kind"]["choice"] == "passport"
    assert held == [True]
