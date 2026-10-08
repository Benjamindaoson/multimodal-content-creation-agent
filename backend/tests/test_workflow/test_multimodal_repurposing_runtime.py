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


@pytest.mark.asyncio
async def test_incomplete_chunk_extraction_is_rebuilt(tmp_path, monkeypatch):
    chunk_dir = tmp_path / "audio_chunks"
    chunk_dir.mkdir()
    stale = chunk_dir / "chunk_0000.mp3"
    stale.write_bytes(b"partial")
    calls = []

    async def fake_run(*args, **kwargs):
        if args[0] == "ffmpeg":
            calls.append("encode")
            stale.write_bytes(b"complete-audio")
            (chunk_dir / "segments.csv").write_text(
                "chunk_0000.mp3,0.000000,15.000000\n", encoding="utf-8"
            )
            return ""
        return '{"format": {"duration": "15.0"}}'

    monkeypatch.setattr(runtime, "run_command", fake_run)
    monkeypatch.setattr(
        runtime,
        "get_settings",
        lambda: SimpleNamespace(FFMPEG_BIN="ffmpeg", FFPROBE_BIN="ffprobe"),
    )
    service = runtime.VideoRepurposingService(store=MemoryStore(fake_state(tmp_path)))
    first = await service._chunks(tmp_path / "source.mp4", tmp_path)
    second = await service._chunks(tmp_path / "source.mp4", tmp_path)

    assert calls == ["encode"]
    assert first == second
    assert first[0][1] == 0.0
    assert (chunk_dir / ".complete").is_file()


@pytest.mark.asyncio
async def test_batch_export_archives_selected_clips_and_persists_progress(
    tmp_path, monkeypatch
):
    import asyncio
    import zipfile

    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    state = fake_state(source)
    state["status"] = "ready"
    state["metrics"]["has_video"] = True
    state["clips"] = [
        {"clip_id": "first", "start": 1.0, "end": 20.0},
        {"clip_id": "second", "start": 20.0, "end": 40.0},
    ]
    store = MemoryStore(state)
    service = runtime.VideoRepurposingService(store=store)
    calls = []

    async def fake_export(**kwargs):
        calls.append(kwargs["destination"].name)
        kwargs["destination"].parent.mkdir(exist_ok=True)
        kwargs["destination"].write_bytes(b"mp4:" + kwargs["destination"].name.encode())
        return kwargs["destination"]

    monkeypatch.setattr(runtime, "export_clip", fake_export)
    batch_id = await service.submit_batch_export(
        "repurpose_test", ["first", "second"]
    )
    task = service.batch_tasks[batch_id]
    await asyncio.wait_for(task, timeout=5)
    updated = store.state["batch_exports"][batch_id]
    assert updated["status"] == "completed"
    assert updated["completed"] == 2
    assert updated["bytes"] > 0
    assert calls == ["first.mp4", "second.mp4"]
    archive = tmp_path / "exports" / (batch_id + ".zip")
    with zipfile.ZipFile(archive) as zipped:
        assert zipped.namelist() == ["first.mp4", "second.mp4"]
        assert zipped.read("first.mp4") == b"mp4:first.mp4"

    with pytest.raises(ValueError, match="unique"):
        await service.submit_batch_export("repurpose_test", ["first", "first"])
    with pytest.raises(ValueError, match="not in this job"):
        await service.submit_batch_export("repurpose_test", ["not-owned"])


@pytest.mark.asyncio
async def test_batch_download_rejects_non_owner(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"test")
    state = fake_state(source)
    state["batch_exports"] = {
        "batch_safe": {"status": "completed", "total": 1, "completed": 1}
    }
    monkeypatch.setattr(
        api,
        "get_video_repurposing_service",
        lambda: SimpleNamespace(store=MemoryStore(state)),
    )
    with pytest.raises(HTTPException) as exc:
        await api.download_batch(
            "repurpose_test",
            "batch_safe",
            current_user=SimpleNamespace(id="bob", role=UserRole.USER),
        )
    assert exc.value.status_code == 403
