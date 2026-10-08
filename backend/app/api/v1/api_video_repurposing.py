"""Private upload, progress, clip candidates, and export APIs."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from app.api.deps import get_current_active_user
from app.core.config import get_settings
from app.engine.agents.workflow.multimodal_repurposing_service import (
    get_video_repurposing_service,
)
from app.models.user import User, UserRole

router = APIRouter(prefix="/api/v1/video-repurposing", tags=["video-repurposing"])
ALLOWED_EXTENSIONS = {".mp4", ".mov", ".mkv", ".webm", ".m4a", ".mp3", ".wav"}


def _owned(state: dict, user: User) -> None:
    if user.role != UserRole.ADMIN and str(state["owner_id"]) != str(user.id):
        raise HTTPException(status_code=403, detail="Not your job")


async def _get_owned(job_id: str, user: User) -> dict:
    state = await get_video_repurposing_service().store.load(job_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Job not found")
    _owned(state, user)
    return state


def _public_state(state: dict) -> dict:
    """Never expose local filesystem paths or unpublished provider raw outputs."""
    return {
        key: state.get(key)
        for key in (
            "job_id",
            "status",
            "stage",
            "progress",
            "error",
            "clips",
            "exports",
            "batch_exports",
            "metrics",
            "objective",
        )
    }


@router.post("/jobs", status_code=status.HTTP_202_ACCEPTED)
async def upload_job(
    file: UploadFile = File(...),
    objective: str = Form(default="Find complete, useful short clips"),
    current_user: User = Depends(get_current_active_user),
):
    settings = get_settings()
    if not settings.GROQ_API_KEY or not settings.DEEPSEEK_API_KEY:
        raise HTTPException(
            status_code=503, detail="GROQ_API_KEY and DEEPSEEK_API_KEY are required"
        )
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=415, detail="Unsupported media extension")
    if len(objective) > 600:
        raise HTTPException(status_code=422, detail="Objective exceeds 600 characters")

    job_id = f"repurpose_{uuid4().hex}"
    job_dir = Path(settings.MULTIMODAL_ARTIFACT_DIR) / "repurposing" / job_id
    job_dir.mkdir(parents=True, exist_ok=False)
    source = job_dir / f"source{suffix}"
    total = 0
    try:
        with source.open("wb") as target:
            while chunk := await file.read(1024 * 1024):
                total += len(chunk)
                if total > settings.VIDEO_REPURPOSING_MAX_UPLOAD_BYTES:
                    raise HTTPException(status_code=413, detail="Upload too large")
                target.write(chunk)
        if total == 0:
            raise HTTPException(status_code=422, detail="Empty upload")
        new_id = await get_video_repurposing_service().submit(
            str(current_user.id), source, objective
        )
        return {"job_id": new_id, "status": "accepted"}
    except Exception:
        source.unlink(missing_ok=True)
        raise
    finally:
        await file.close()


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, current_user: User = Depends(get_current_active_user)):
    return _public_state(await _get_owned(job_id, current_user))


@router.post("/jobs/{job_id}/resume", status_code=202)
async def resume_job(
    job_id: str, current_user: User = Depends(get_current_active_user)
):
    await _get_owned(job_id, current_user)
    try:
        await get_video_repurposing_service().start(job_id)
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "status": "resuming"}


@router.get("/jobs/{job_id}/transcript")
async def transcript(
    job_id: str, current_user: User = Depends(get_current_active_user)
):
    state = await _get_owned(job_id, current_user)
    return {"job_id": job_id, "segments": state.get("transcript", [])}


@router.get("/jobs/{job_id}/events")
async def stream_job(
    job_id: str, current_user: User = Depends(get_current_active_user)
):
    await _get_owned(job_id, current_user)

    async def updates():
        last = None
        for tick in range(3600):
            state = await get_video_repurposing_service().store.load(job_id)
            if state is None:
                break
            payload = _public_state(state)
            encoded = json.dumps(payload, ensure_ascii=False)
            if encoded != last:
                yield f"event: progress\ndata: {encoded}\n\n"
                last = encoded
            elif tick % 7 == 0:
                yield ": keepalive\n\n"
            if state["status"] in {"ready", "failed", "cancelled"}:
                break
            await asyncio.sleep(2)

    # Authenticated fetch streaming is supported; native EventSource cannot
    # send the existing Bearer auth header.
    return StreamingResponse(
        updates(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class BatchExportRequest(BaseModel):
    clip_ids: list[str] = Field(min_length=1, max_length=30)


@router.post("/jobs/{job_id}/exports/batch", status_code=202)
async def export_batch(
    job_id: str,
    request: BatchExportRequest,
    current_user: User = Depends(get_current_active_user),
):
    await _get_owned(job_id, current_user)
    try:
        batch_id = await get_video_repurposing_service().submit_batch_export(
            job_id, request.clip_ids
        )
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Job not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"job_id": job_id, "batch_id": batch_id, "status": "queued"}


@router.get("/jobs/{job_id}/exports/batch/{batch_id}/download")
async def download_batch(
    job_id: str,
    batch_id: str,
    current_user: User = Depends(get_current_active_user),
):
    state = await _get_owned(job_id, current_user)
    info = state.get("batch_exports", {}).get(batch_id)
    if not info or info.get("status") != "completed":
        raise HTTPException(status_code=404, detail="Batch archive is not ready")
    path = Path(state["source_path"]).parent / "exports" / f"{batch_id}.zip"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Batch archive is missing")
    return FileResponse(
        path, media_type="application/zip", filename=f"{batch_id}.zip"
    )


@router.post("/jobs/{job_id}/clips/{clip_id}/export")
async def export_one(
    job_id: str,
    clip_id: str,
    current_user: User = Depends(get_current_active_user),
):
    await _get_owned(job_id, current_user)
    try:
        result = await get_video_repurposing_service().export(job_id, clip_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="Clip not found") from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {
        "clip_id": clip_id,
        "bytes": result.stat().st_size,
        "download_url": f"/api/v1/video-repurposing/jobs/{job_id}/clips/{clip_id}/download",
    }


@router.get("/jobs/{job_id}/clips/{clip_id}/download")
async def download_one(
    job_id: str,
    clip_id: str,
    current_user: User = Depends(get_current_active_user),
):
    state = await _get_owned(job_id, current_user)
    if state.get("exports", {}).get(clip_id, {}).get("status") != "completed":
        raise HTTPException(status_code=404, detail="Clip not exported")
    path = Path(state["source_path"]).parent / "exports" / f"{clip_id}.mp4"
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Export file missing")
    return FileResponse(path, media_type="video/mp4", filename=f"{clip_id}.mp4")
