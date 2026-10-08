"""Transcript-grounded clip discovery and accurate media export.

This module is deliberately independent of an ASR or LLM vendor so candidates
can be evaluated with recorded transcripts and deterministic tests.
"""

from __future__ import annotations

import asyncio
import csv
import hashlib
import math
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Sequence


@dataclass(frozen=True)
class TranscriptSegment:
    start: float
    end: float
    text: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def normalize_segments(
    raw: Sequence[Dict[str, Any]],
    *,
    offset: float = 0.0,
) -> List[TranscriptSegment]:
    """Convert provider-local segment timestamps into absolute seconds."""
    cleaned = []
    for item in raw:
        try:
            start = float(item["start"]) + offset
            end = float(item["end"]) + offset
        except (KeyError, TypeError, ValueError):
            continue
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if (
            not text
            or not math.isfinite(start)
            or not math.isfinite(end)
            or start < 0
            or end <= start
        ):
            continue
        cleaned.append(TranscriptSegment(round(start, 3), round(end, 3), text))
    return sorted(cleaned, key=lambda s: (s.start, s.end))


def parse_ffmpeg_segment_manifest(
    manifest: Path, chunks: Sequence[Path]
) -> List[tuple[Path, float]]:
    """Use FFmpeg's source-timeline segment start PTS instead of MP3 durations.

    The segment muxer emits filename,start,end CSV rows before resetting the
    timestamps of each individual MP3. Exact input-to-output frame alignment
    still depends on source media/ASR precision and must be measured.
    """
    if not chunks:
        raise ValueError("no audio chunks")
    rows: List[tuple[Path, float]] = []
    with manifest.open("r", encoding="utf-8", newline="") as stream:
        for raw in csv.reader(stream):
            if len(raw) != 3:
                raise ValueError("invalid FFmpeg segment manifest row")
            name = Path(raw[0]).name
            try:
                start, end = float(raw[1]), float(raw[2])
            except ValueError as exc:
                raise ValueError("invalid segment timestamp") from exc
            if (
                not math.isfinite(start)
                or not math.isfinite(end)
                or start < -0.05
                or end <= start
            ):
                raise ValueError("invalid FFmpeg segment time range")
            rows.append((Path(name), max(0.0, start)))
    if len(rows) != len(chunks):
        raise ValueError("FFmpeg segment manifest does not match chunk count")
    last = -1.0
    resolved: List[tuple[Path, float]] = []
    for (name, offset), chunk in zip(rows, chunks):
        if name.name != chunk.name or offset <= last:
            raise ValueError("segment manifest has misordered or duplicate chunks")
        resolved.append((chunk, offset))
        last = offset
    return resolved


def transcript_windows(
    segments: Sequence[TranscriptSegment],
    *,
    max_chars: int = 9000,
    overlap_seconds: float = 45.0,
) -> List[List[TranscriptSegment]]:
    """Pack short ASR segments with overlap so topics crossing boundaries survive."""
    if max_chars < 100:
        raise ValueError("max_chars must be at least 100")
    if overlap_seconds < 0:
        raise ValueError("overlap_seconds cannot be negative")
    if not segments:
        return []

    windows: List[List[TranscriptSegment]] = []
    start = 0
    while start < len(segments):
        end = start
        chars = 0
        while end < len(segments):
            size = len(segments[end].text) + 32
            if end > start and chars + size > max_chars:
                break
            chars += size
            end += 1
        windows.append(list(segments[start:end]))
        if end == len(segments):
            break
        overlap_start = end
        while (
            overlap_start > start + 1
            and segments[end - 1].end - segments[overlap_start - 1].start
            < overlap_seconds
        ):
            overlap_start -= 1
        start = max(start + 1, overlap_start)
    return windows


def format_window(window: Sequence[TranscriptSegment]) -> str:
    return "\n".join(f"[{s.start:.3f}-{s.end:.3f}] {s.text}" for s in window)


def _intersection(a_start: float, a_end: float, b_start: float, b_end: float) -> float:
    return max(0.0, min(a_end, b_end) - max(a_start, b_start))


def validated_candidates(
    proposals: Sequence[Dict[str, Any]],
    segments: Sequence[TranscriptSegment],
    *,
    duration: float,
    min_seconds: float = 15.0,
    max_seconds: float = 180.0,
) -> List[Dict[str, Any]]:
    """Reject invented timestamps and ungrounded / overlapping clip proposals."""
    if duration <= 0 or min_seconds <= 0 or max_seconds < min_seconds:
        raise ValueError("invalid duration constraints")
    accepted: List[Dict[str, Any]] = []
    for item in proposals:
        if not isinstance(item, dict):
            continue
        try:
            start, end = float(item["start"]), float(item["end"])
            score = float(item.get("score", 0.5))
        except (KeyError, ValueError, TypeError):
            continue
        if not all(map(math.isfinite, (start, end, score))):
            continue
        if start < 0 or end > duration + 0.25:
            continue
        end = min(end, duration)
        if end - start < min_seconds or end - start > max_seconds:
            continue
        evidence = [
            s.text for s in segments if _intersection(start, end, s.start, s.end) > 0
        ]
        excerpt = " ".join(evidence).strip()
        if len(excerpt) < 12:
            continue
        title = str(item.get("title") or "").strip()[:160]
        if not title:
            continue
        clip = {
            "clip_id": hashlib.sha256(
                f"{round(start, 2)}:{round(end, 2)}".encode()
            ).hexdigest()[:16],
            "start": round(start, 3),
            "end": round(end, 3),
            "title": title,
            "reason": str(item.get("reason") or "").strip()[:500],
            "score": round(max(0.0, min(score, 1.0)), 4),
            "evidence_excerpt": excerpt[:500],
        }
        replaced = False
        for index, other in enumerate(accepted):
            overlap = _intersection(start, end, other["start"], other["end"])
            union = max(end, other["end"]) - min(start, other["start"])
            if union and overlap / union >= 0.65:
                if clip["score"] > other["score"]:
                    accepted[index] = clip
                replaced = True
                break
        if not replaced:
            accepted.append(clip)
    return sorted(accepted, key=lambda c: (-c["score"], c["start"]))


async def run_command(*args: str, timeout: float = 900.0) -> str:
    """Execute media binaries without shell interpolation or an event-loop block."""
    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.communicate()
        raise RuntimeError(f"media command timed out: {args[0]}") from None
    if proc.returncode:
        raise RuntimeError(
            f"media command failed ({proc.returncode}): "
            + stderr.decode("utf-8", errors="replace")[-1800:]
        )
    return stdout.decode("utf-8", errors="replace")


async def export_clip(
    *,
    source: Path,
    destination: Path,
    start: float,
    end: float,
    ffmpeg_bin: str = "ffmpeg",
) -> Path:
    """Frame-accurate export by re-encoding; never depend on input keyframes."""
    if not source.is_file() or end <= start or start < 0:
        raise ValueError("invalid source or clip time range")
    destination.parent.mkdir(parents=True, exist_ok=True)
    await run_command(
        ffmpeg_bin,
        "-nostdin",
        "-y",
        "-ss",
        f"{start:.3f}",
        "-i",
        str(source),
        "-t",
        f"{end - start:.3f}",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "23",
        "-c:a",
        "aac",
        "-movflags",
        "+faststart",
        str(destination),
    )
    if not destination.is_file() or destination.stat().st_size == 0:
        raise RuntimeError("FFmpeg produced no clip")
    return destination
