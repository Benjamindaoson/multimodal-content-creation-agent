"""Regression tests for resumable repurposing and authenticated access."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.api.v1 import api_video_repurposing as api
from app.engine.agents.workflow import multimodal_repurposing_service as runtime
from app.models.user import UserRole


class MemoryStore:
    def __init__(self, state):
        self.state = deepcopy(state)
        self.writes = []

    async def load(self, job_id):
        return deepcopy(self.state) if job_id == self.state["job_id"] else None

    async def save(self, state):
        self.state = deepcopy(state)
        self.writes.append(deepcopy(state))


class FakeASR:
    def __init__(self):
        self.calls = []

    async def transcribe(self, path):
        self.calls.append(path.name)
        return [
            {
                "start": 0,
                "end": 19,
                "text": "A real product demo explaining exactly how the product works",
            }
        ]


class FakePlanner:
    def __init__(self, fails=False):
        self.fails = fails
        self.calls = 0

    async def analyze(self, window, objective):
        self.calls += 1
        if self.fails:
            raise RuntimeError("injected transient LLM failure")
        return [
            {
                "start": 0,
                "end": 35,
                "title": "Product demonstration",
                "reason": "Complete explanation",
                "score": 0.9,
            }
        ]


def fake_state(source):
    return {
        "job_id": "repurpose_test",
        "owner_id": "alice",
        "source_path": str(source),
        "objective": "Find tutorials",
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "transcribed_chunks": {},
        "candidate_windows": {},
        "transcript": [],
        "clips": [],
        "exports": {},
        "metrics": {"source_bytes": 123},
    }


@pytest.mark.asyncio
async def test_resume_reuses_completed_asr_chunks_after_llm_failure(
    tmp_path, monkeypatch
):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    store = MemoryStore(fake_state(source))
    asr = FakeASR()
    planner = FakePlanner(fails=True)
    service = runtime.VideoRepurposingService(
        store=store, transcriber=asr, planner=planner
    )
    monkeypatch.setattr(
        service,
        "_probe",
        AsyncMock(return_value={"duration": 50.0, "has_video": True}),
    )
    monkeypatch.setattr(
        service,
        "_chunks",
        AsyncMock(
            return_value=[
                (Path("chunk_0.mp3"), 0.0),
                (Path("chunk_1.mp3"), 20.0),
            ]
        ),
    )

    await service._run("repurpose_test")
    assert store.state["status"] == "failed"
    assert len(store.state["transcribed_chunks"]) == 2
    assert len(asr.calls) == 2

    service.planner = FakePlanner()
    await service._run("repurpose_test")

    assert store.state["status"] == "ready"
    assert store.state["metrics"]["accepted_clip_count"] == 1
    assert len(asr.calls) == 2
    assert store.state["clips"][0]["start"] == 0
    assert len(store.writes) >= 6


@pytest.mark.asyncio
async def test_api_denies_other_users_and_hides_storage_paths(monkeypatch):
    source = Path("/private/source.mp4")
    store = MemoryStore(fake_state(source))
    monkeypatch.setattr(
        api,
        "get_video_repurposing_service",
        lambda: SimpleNamespace(store=store),
    )
    other = SimpleNamespace(id="bob", role=UserRole.USER)
    with pytest.raises(HTTPException) as exc:
        await api.get_job("repurpose_test", current_user=other)
    assert exc.value.status_code == 403

    owner = SimpleNamespace(id="alice", role=UserRole.USER)
    result = await api.get_job("repurpose_test", current_user=owner)
    assert "source_path" not in result
    assert "transcribed_chunks" not in result
    assert result["job_id"] == "repurpose_test"


@pytest.mark.asyncio
async def test_export_rejects_unknown_clip_without_media_execution(tmp_path):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    state = fake_state(source)
    state["status"] = "ready"
    state["metrics"]["has_video"] = True
    state["clips"] = [{"clip_id": "correct", "start": 0.0, "end": 30.0}]
    store = MemoryStore(state)
    service = runtime.VideoRepurposingService(store=store)
    with pytest.raises(KeyError):
        await service.export("repurpose_test", "unknown")


@pytest.mark.asyncio
async def test_export_creates_once_and_checkpoints(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    state = fake_state(source)
    state["status"] = "ready"
    state["metrics"]["has_video"] = True
    state["clips"] = [{"clip_id": "clip-1", "start": 1.0, "end": 30.0}]
    store = MemoryStore(state)
    service = runtime.VideoRepurposingService(store=store)
    calls = []

    async def fake_export(**kwargs):
        calls.append(kwargs)
        kwargs["destination"].parent.mkdir(exist_ok=True)
        kwargs["destination"].write_bytes(b"rendered")
        return kwargs["destination"]

    monkeypatch.setattr(runtime, "export_clip", fake_export)
    first = await service.export("repurpose_test", "clip-1")
    second = await service.export("repurpose_test", "clip-1")
    assert first == second
    assert len(calls) == 1
    assert store.state["exports"]["clip-1"]["bytes"] == 8
