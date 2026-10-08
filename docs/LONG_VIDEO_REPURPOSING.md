# Long-video repurposing (transcript-grounded clips)

The existing **brief → generative video** workflow is unchanged. This feature
adds a separate **uploaded long video → ASR → clip discovery → FFmpeg export**
workflow; it does not require Runway or ElevenLabs credentials.

## Setup

Apply the new Alembic revision before starting the API:

```bash
cd backend
alembic upgrade head
```

Set `GROQ_API_KEY`, `DEEPSEEK_API_KEY`, plus optional
`GROQ_ASR_MODEL=whisper-large-v3-turbo`. Set `FFMPEG_BIN` and
`FFPROBE_BIN` if executables aren't on PATH. The API also uses the existing
JWT auth and `MULTIMODAL_ARTIFACT_DIR` (make it a persistent volume).

```bash
curl -X POST http://localhost:8000/api/v1/video-repurposing/jobs \
  -H "Authorization: Bearer $TOKEN" \
  -F "file=@recording.mp4" \
  -F "objective=Find self-contained product demonstrations"
```

The response returns `job_id`. Authenticated clients use
`GET /jobs/{job_id}`, `GET /jobs/{job_id}/transcript`, or an authenticated
streaming fetch of `GET /jobs/{job_id}/events`. To export, call
`POST /jobs/{job_id}/clips/{clip_id}/export`; then follow the authenticated
download URL. Export re-encodes for accurate timestamps rather than promising
keyframe-aligned `-c copy` cuts.

## Processing and recovery

* Uploads are streamed in 1 MiB blocks and size-capped. Paths are server-generated.
* FFmpeg resamples to mono 16 kHz MP3 chunks of approximately 10 minutes.
* Bounded Groq ASR concurrency records completed chunks in PostgreSQL.
* Overlapped, bounded transcript windows are submitted to the existing DeepSeek
  structured-output gateway and checkpointed individually.
* Clip QA rejects invalid/out-of-range timestamps, unsupported ASR spans,
  too-short/long suggestions, and near duplicates.
* Failed tasks preserve partial ASR and LLM results. Authenticated
  `POST /jobs/{job_id}/resume` reuses saved chunks/windows.

**Operational boundary:** Like the pre-existing production service, background
execution is currently process-local. Run one API worker for this feature;
use a DB-claimed or queue-backed worker before multi-process scale-out. An
interrupted upload must be resubmitted. No automatic publishing is performed.
The original media and private transcripts remain on server storage, and the
operator must configure lifecycle cleanup/retention. Cloud-only deployments
need a shared durable volume or external object store before worker failover.

## Measurement

Each job stores source bytes, source duration, whether a video stream exists,
audio chunk count, transcript segment count, LLM window count, candidate and
accepted clip counts, and end-to-end elapsed seconds. Genuine vendor token
usage, cache hits, peak memory and business adoption rates are **not** measured
by this initial endpoint and must not be claimed in a résumé. Tests use mocks;
run real end-to-end jobs with your own credentials and licensed media to
validate quality and measured latency.
