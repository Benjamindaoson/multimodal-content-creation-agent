"""Checkpointed long-video -> ASR -> LLM -> short-clip pipeline.

Deployment scope: one API process. Durable stage/chunk/window checkpoints permit
manual resume after a crash; distributed job claiming is not yet implemented.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

import httpx

from app.core.config import get_settings
from app.core.database import AsyncSessionLocal
from app.engine.llm.providers.unified_adapter import UnifiedLLMProviderAdapter
from app.models.video_repurposing import VideoRepurposingJob

from .multimodal_repurposing import (
    TranscriptSegment,
    export_clip,
    format_window,
    normalize_segments,
    run_command,
    transcript_windows,
    validated_candidates,
)

logger = logging.getLogger(__name__)
TERMINAL = {"ready", "failed", "cancelled"}


class RepurposingCheckpointStore:
    """A small dedicated store, separate from generated-video production jobs."""

    def __init__(self, session_factory=AsyncSessionLocal):
        self.session_factory = session_factory

    async def load(self, job_id: str) -> Optional[Dict[str, Any]]:
        async with self.session_factory() as session:
            row = await session.get(VideoRepurposingJob, job_id)
            return dict(row.state_json) if row is not None else None

    async def save(self, state: Dict[str, Any]) -> None:
        async with self.session_factory() as session:
            row = await session.get(
                VideoRepurposingJob, state["job_id"], with_for_update=True
            )
            if row is None:
                row = VideoRepurposingJob(job_id=state["job_id"])
                session.add(row)
            row.owner_id = state["owner_id"]
            row.source_path = state["source_path"]
            row.status = state["status"]
            row.stage = state["stage"]
            row.state_json = dict(state)
            await session.commit()


class GroqSegmentTranscriber:
    """Returns ASR segments with chunk-local timestamps."""

    def __init__(self, api_key: str, model: str):
        self.api_key = api_key
        self.model = model

    async def transcribe(self, path: Path) -> List[Dict[str, Any]]:
        async with httpx.AsyncClient(timeout=180.0) as client:
            for attempt in range(3):
                try:
                    with path.open("rb") as audio:
                        response = await client.post(
                            "https://api.groq.com/openai/v1/audio/transcriptions",
                            headers={"Authorization": f"Bearer {self.api_key}"},
                            files={"file": (path.name, audio, "audio/mpeg")},
                            data={
                                "model": self.model,
                                "response_format": "verbose_json",
                                "timestamp_granularities[]": "segment",
                            },
                        )
                    response.raise_for_status()
                    payload = response.json()
                    raw = payload.get("segments")
                    if not isinstance(raw, list):
                        raise ValueError("ASR did not return segment timestamps")
                    return raw
                except (httpx.TimeoutException, httpx.TransportError):
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
                except httpx.HTTPStatusError as exc:
                    if exc.response.status_code not in {429, 500, 502, 503, 504}:
                        raise
                    if attempt == 2:
                        raise
                    await asyncio.sleep(2**attempt)
        raise RuntimeError("ASR retries exhausted")


class RepurposingClipPlanner:
    """Uses the existing model gateway, anchored to ASR time ranges."""

    SCHEMA = {
        "type": "object",
        "properties": {
            "clips": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "start": {"type": "number"},
                        "end": {"type": "number"},
                        "title": {"type": "string"},
                        "reason": {"type": "string"},
                        "score": {"type": "number"},
                    },
                    "required": ["start", "end", "title", "reason", "score"],
                },
            }
        },
        "required": ["clips"],
    }

    def __init__(self, provider: str, model: str):
        self.llm = UnifiedLLMProviderAdapter(
            provider=provider, model=model, temperature=0.2, max_tokens=2500
        )
        self.model = model

    async def analyze(
        self, window: List[TranscriptSegment], objective: str
    ) -> List[Dict[str, Any]]:
        result = await self.llm.structured_output(
            messages=[
                {
                    "role": "system",
                    "content": (
                        "You discover coherent, useful short clips in a long video. "
                        "Use ONLY speech present in the timestamped transcript. "
                        "All start/end values are ABSOLUTE seconds in the original "
                        "video, not window-relative seconds. Prefer a complete "
                        "thought rather than viral-sounding hallucinations. "
                        "Return up to five clips, each 15-180 seconds, with "
                        "a concise title, reason and score in [0,1]. "
                        "Return JSON matching the schema."
                    ),
                },
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "objective": objective,
                            "transcript": format_window(window),
                            "window_start": window[0].start,
                            "window_end": window[-1].end,
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            schema=self.SCHEMA,
            model=self.model,
            temperature=0.2,
        )
        clips = result.get("clips", [])
        if not isinstance(clips, list):
            raise ValueError("LLM clips must be an array")
        # Refuse hallucinated times outside the supplied window before global QA.
        return [
            clip
            for clip in clips
            if isinstance(clip, dict)
            and self._within_window(clip, window[0].start, window[-1].end)
        ]

    @staticmethod
    def _within_window(clip: Dict[str, Any], start: float, end: float) -> bool:
        try:
            a, b = float(clip["start"]), float(clip["end"])
            return math.isfinite(a) and math.isfinite(b) and a >= start - 0.2 and b <= end + 0.2
        except (KeyError, ValueError, TypeError):
            return False


class VideoRepurposingService:
    def __init__(
        self,
        *,
        store: Optional[RepurposingCheckpointStore] = None,
        transcriber: Any = None,
        planner: Any = None,
    ) -> None:
        self.store = store or RepurposingCheckpointStore()
        self.transcriber = transcriber
        self.planner = planner
        self.active: Dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()
        self.export_locks: Dict[str, asyncio.Lock] = {}

    async def submit(self, owner_id: str, source: Path, objective: str) -> str:
        job_id = source.parent.name
        state: Dict[str, Any] = {
            "job_id": job_id,
            "owner_id": owner_id,
            "source_path": str(source),
            "objective": objective[:600],
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
        await self.store.save(state)
        await self.start(job_id)
        return job_id

    async def start(self, job_id: str) -> None:
        async with self.lock:
            task = self.active.get(job_id)
            if task is not None and not task.done():
                raise ValueError("job is already running")
            state = await self.store.load(job_id)
            if state is None:
                raise KeyError(job_id)
            if state["status"] == "ready":
                raise ValueError("job is already ready")
            task = asyncio.create_task(self._run(job_id), name=f"repurpose:{job_id}")
            self.active[job_id] = task
            task.add_done_callback(lambda done, key=job_id: self._finished(key, done))

    def _finished(self, job_id: str, task: asyncio.Task) -> None:
        if self.active.get(job_id) is task:
            self.active.pop(job_id, None)
        if not task.cancelled() and task.exception() is not None:
            logger.error("repurposing task %s failed: %s", job_id, task.exception())

    async def _update(self, state: Dict[str, Any], **changes: Any) -> None:
        state.update(changes)
        await self.store.save(state)

    async def _probe(self, source: Path) -> Dict[str, Any]:
        settings = get_settings()
        payload = json.loads(
            await run_command(
                settings.FFPROBE_BIN,
                "-v", "error", "-show_entries", "format=duration",
                "-show_streams", "-of", "json", str(source), timeout=120.0,
            )
        )
        duration = float(payload.get("format", {}).get("duration") or 0)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError("media has no valid duration")
        streams = payload.get("streams", [])
        if not any(s.get("codec_type") == "audio" for s in streams):
            raise ValueError("media contains no audio track")
        return {
            "duration": duration,
            "has_video": any(s.get("codec_type") == "video" for s in streams),
        }

    async def _chunks(self, source: Path, job_dir: Path) -> List[tuple[Path, float]]:
        settings = get_settings()
        chunk_dir = job_dir / "audio_chunks"
        chunk_dir.mkdir(exist_ok=True)
        files = sorted(chunk_dir.glob("chunk_*.mp3"))
        if not files:
            await run_command(
                settings.FFMPEG_BIN,
                "-nostdin", "-y", "-i", str(source),
                "-map", "0:a:0", "-ac", "1", "-ar", "16000", "-b:a", "48k",
                "-f", "segment", "-segment_time", "600",
                "-reset_timestamps", "1",
                str(chunk_dir / "chunk_%04d.mp3"), timeout=1800.0,
            )
            files = sorted(chunk_dir.glob("chunk_*.mp3"))
        if not files:
            raise RuntimeError("FFmpeg produced no audio chunks")
        offsets: List[tuple[Path, float]] = []
        offset = 0.0
        for path in files:
            result = json.loads(
                await run_command(
                    settings.FFPROBE_BIN, "-v", "error",
                    "-show_entries", "format=duration", "-of", "json",
                    str(path), timeout=60.0,
                )
            )
            duration = float(result["format"]["duration"])
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("invalid audio chunk duration")
            offsets.append((path, offset))
            offset += duration
        return offsets

    async def _run(self, job_id: str) -> None:
        state = await self.store.load(job_id)
        if state is None:
            return
        started = time.monotonic()
        try:
            source = Path(state["source_path"])
            if not source.is_file():
                raise FileNotFoundError("source video is missing; cannot resume")
            await self._update(state, status="running", stage="probing", progress=2)
            media = await self._probe(source)
            state["metrics"].update(media)
            await self._update(state, stage="transcribing", progress=5)
            chunks = await self._chunks(source, source.parent)
            state["metrics"]["audio_chunks"] = len(chunks)
            settings = get_settings()
            transcriber = self.transcriber or GroqSegmentTranscriber(
                api_key=settings.GROQ_API_KEY or "", model=settings.GROQ_ASR_MODEL
            )
            semaphore = asyncio.Semaphore(max(1, min(settings.GROQ_ASR_CONCURRENCY, 6)))

            async def transcribe_one(index: int, path: Path, offset: float):
                async with semaphore:
                    result = await transcriber.transcribe(path)
                return str(index), [
                    item.to_dict() for item in normalize_segments(result, offset=offset)
                ]

            pending = [
                asyncio.create_task(transcribe_one(i, path, offset))
                for i, (path, offset) in enumerate(chunks)
                if str(i) not in state["transcribed_chunks"]
            ]
            try:
                for finished in asyncio.as_completed(pending):
                    index, segments = await finished
                    state["transcribed_chunks"][index] = segments
                    state["progress"] = 5 + int(
                        60 * len(state["transcribed_chunks"]) / len(chunks)
                    )
                    await self.store.save(state)
            finally:
                for task in pending:
                    if not task.done():
                        task.cancel()
                if pending:
                    await asyncio.gather(*pending, return_exceptions=True)

            segments = [
                TranscriptSegment(**entry)
                for i in range(len(chunks))
                for entry in state["transcribed_chunks"][str(i)]
            ]
            if not segments:
                raise ValueError("ASR returned no usable timed speech")
            state["transcript"] = [segment.to_dict() for segment in segments]
            await self._update(state, stage="analyzing", progress=68)

            windows = transcript_windows(segments, max_chars=7800, overlap_seconds=45)
            planner = self.planner or RepurposingClipPlanner(
                provider="deepseek", model=settings.DEEPSEEK_MODEL
            )
            llm_limit = asyncio.Semaphore(
                max(1, min(settings.VIDEO_REPURPOSING_LLM_CONCURRENCY, 4))
            )

            async def analyze_one(index: int, window: List[TranscriptSegment]):
                async with llm_limit:
                    return str(index), await planner.analyze(
                        window, state["objective"]
                    )

            analysis = [
                asyncio.create_task(analyze_one(i, window))
                for i, window in enumerate(windows)
                if str(i) not in state["candidate_windows"]
            ]
            try:
                for finished in asyncio.as_completed(analysis):
                    index, clips = await finished
                    state["candidate_windows"][index] = clips
                    state["progress"] = 68 + int(
                        25 * len(state["candidate_windows"]) / len(windows)
                    )
                    await self.store.save(state)
            finally:
                for task in analysis:
                    if not task.done():
                        task.cancel()
                if analysis:
                    await asyncio.gather(*analysis, return_exceptions=True)

            proposals = [
                clip
                for i in range(len(windows))
                for clip in state["candidate_windows"][str(i)]
            ]
            clips = validated_candidates(
                proposals, segments, duration=media["duration"]
            )
            state["metrics"].update(
                {
                    "transcript_segments": len(segments),
                    "llm_windows": len(windows),
                    "candidate_count": len(proposals),
                    "accepted_clip_count": len(clips),
                    "elapsed_seconds": round(time.monotonic() - started, 3),
                }
            )
            await self._update(
                state, clips=clips, status="ready", stage="ready", progress=100
            )
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            # Preserve every completed ASR chunk / LLM window for resume.
            logger.exception("long-video repurposing failed for job %s", job_id)
            state["metrics"]["elapsed_seconds"] = round(
                time.monotonic() - started, 3
            )
            await self._update(
                state, status="failed", stage="failed", error=str(exc)[:500]
            )

    async def export(self, job_id: str, clip_id: str) -> Path:
        key = f"{job_id}:{clip_id}"
        lock = self.export_locks.setdefault(key, asyncio.Lock())
        async with lock:
            state = await self.store.load(job_id)
            if state is None:
                raise KeyError(job_id)
            if state["status"] != "ready":
                raise ValueError("analysis is not ready")
            if not state["metrics"].get("has_video"):
                raise ValueError("source has no video stream")
            clip = next(
                (c for c in state["clips"] if c["clip_id"] == clip_id), None
            )
            if clip is None:
                raise KeyError(clip_id)
            destination = (
                Path(state["source_path"]).parent / "exports" / f"{clip_id}.mp4"
            )
            if not destination.is_file() or destination.stat().st_size == 0:
                await export_clip(
                    source=Path(state["source_path"]),
                    destination=destination,
                    start=clip["start"],
                    end=clip["end"],
                    ffmpeg_bin=get_settings().FFMPEG_BIN,
                )
            state["exports"][clip_id] = {
                "bytes": destination.stat().st_size, "status": "completed"
            }
            await self.store.save(state)
            return destination


_service: Optional[VideoRepurposingService] = None


def get_video_repurposing_service() -> VideoRepurposingService:
    global _service
    if _service is None:
        _service = VideoRepurposingService()
    return _service
