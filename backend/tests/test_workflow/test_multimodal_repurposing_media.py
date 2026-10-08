"""Exercise real FFmpeg/ffprobe with a generated media fixture, no cloud keys."""

from __future__ import annotations

import json
import shutil
from copy import deepcopy

import pytest

from app.engine.agents.workflow import multimodal_repurposing_service as service_module
from app.engine.agents.workflow.multimodal_repurposing import run_command


pytestmark = pytest.mark.skipif(
    not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
    reason="FFmpeg and FFprobe required for media integration",
)


class InMemoryCheckpoint:
    def __init__(self, state):
        self.state = deepcopy(state)

    async def load(self, job_id):
        return deepcopy(self.state) if job_id == self.state["job_id"] else None

    async def save(self, state):
        self.state = deepcopy(state)


class RecordedASR:
    def __init__(self):
        self.calls = 0

    async def transcribe(self, path):
        self.calls += 1
        return [
            {
                "start": 0.0,
                "end": 16.0,
                "text": (
                    "This product demonstration explains the design and "
                    "shows how the features work and why the solution is useful."
                ),
            }
        ]


class RecordedPlanner:
    async def analyze(self, window, objective):
        return [
            {
                "start": 0.0,
                "end": 16.0,
                "title": "Practical product demo",
                "reason": "A complete explanation with usable footage.",
                "score": 0.88,
            }
        ]


@pytest.mark.asyncio
async def test_real_media_probe_asr_checkpoint_clip_render(tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    # 17 seconds: sufficient to satisfy the default 15s clip threshold.
    # The fixture is generated locally, avoiding third-party copyrighted video.
    await run_command(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "color=c=navy:size=160x90:rate=10:duration=17",
        "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=16000:duration=17",
        "-shortest", "-c:v", "mpeg4", "-q:v", "18", "-c:a", "aac",
        str(source), timeout=40,
    )
    assert source.stat().st_size > 0
    state = {
        "job_id": "fixture-job",
        "owner_id": "fixture-user",
        "source_path": str(source),
        "objective": "Find complete demonstrations",
        "status": "queued",
        "stage": "queued",
        "progress": 0,
        "transcribed_chunks": {},
        "candidate_windows": {},
        "transcript": [],
        "clips": [],
        "exports": {},
        "metrics": {"source_bytes": source.stat().st_size},
    }
    store = InMemoryCheckpoint(state)
    transcriber = RecordedASR()
    pipeline = service_module.VideoRepurposingService(
        store=store, transcriber=transcriber, planner=RecordedPlanner()
    )
    await pipeline._run("fixture-job")

    assert store.state["status"] == "ready", store.state.get("error")
    assert store.state["stage"] == "ready"
    assert len(store.state["clips"]) == 1
    assert store.state["transcript"][0]["start"] == 0.0
    assert store.state["metrics"]["audio_chunks"] == 1
    assert transcriber.calls == 1

    clip_id = store.state["clips"][0]["clip_id"]
    output = await pipeline.export("fixture-job", clip_id)
    assert output.is_file()
    probe = json.loads(
        await run_command(
            "ffprobe", "-v", "error", "-show_entries", "format=duration",
            "-of", "json", str(output),
        )
    )
    duration = float(probe["format"]["duration"])
    assert abs(duration - 16.0) < 0.35
    assert store.state["exports"][clip_id]["status"] == "completed"

    # Checkpoint makes repeated exports idempotent within the same job.
    reused = await pipeline.export("fixture-job", clip_id)
    assert reused == output
    assert transcriber.calls == 1


@pytest.mark.asyncio
async def test_manifest_offsets_match_real_ffmpeg_segments(tmp_path):
    from app.engine.agents.workflow.multimodal_repurposing import (
        parse_ffmpeg_segment_manifest,
    )

    source = tmp_path / "audio.wav"
    await run_command(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-f", "lavfi", "-i", "sine=frequency=350:duration=4:sample_rate=16000",
        str(source), timeout=20,
    )
    chunk_dir = tmp_path / "chunks"
    chunk_dir.mkdir()
    manifest = chunk_dir / "segments.csv"
    await run_command(
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-nostdin", "-y",
        "-i", str(source),
        "-ac", "1", "-ar", "16000", "-b:a", "48k",
        "-f", "segment", "-segment_time", "1",
        "-segment_list", str(manifest),
        "-segment_list_type", "csv", "-reset_timestamps", "1",
        str(chunk_dir / "chunk_%04d.mp3"), timeout=20,
    )
    chunks = sorted(chunk_dir.glob("chunk_*.mp3"))
    offsets = parse_ffmpeg_segment_manifest(manifest, chunks)
    assert len(offsets) >= 3
    assert offsets[0][1] == 0.0
    assert offsets[1][1] > 0.9
    assert offsets[-1][1] <= 4.1
    assert all(a[1] < b[1] for a, b in zip(offsets, offsets[1:]))
