"""HTTP API (FastAPI). Run:  uvicorn app.main:app --reload"""
import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import JSONResponse, PlainTextResponse

from .config import Settings
from .formats import to_srt, to_txt, to_vtt
from .db import Database
from .service import JobService, UnsupportedFileType
from .storage import FileTooLarge, LocalStorage
from .transcriber import build_transcriber
from .worker import WorkerPool

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
settings = Settings()
storage = LocalStorage(settings.data_dir)
db = Database(f"{settings.data_dir}/jobs.db")
service = JobService(db, storage, settings)
pool = WorkerPool(db, storage, build_transcriber(settings.transcriber, settings.whisper_model),
                  settings.worker_count, settings.backoff_base_seconds,
                  settings.stuck_after_seconds, settings.poll_interval_seconds,
                  settings.chunk_seconds)


@asynccontextmanager
async def lifespan(app: FastAPI):
    pool.start()          # start background workers with the server
    yield
    pool.stop()           # graceful shutdown


app = FastAPI(title="Audio Transcription Service", version="1.0", lifespan=lifespan)


def _public(job: dict) -> dict:
    """Only expose fields a client needs (hide internal paths / lock info)."""
    return {k: job[k] for k in ("id", "filename", "status", "attempts", "max_attempts",
                                "size_bytes", "error", "created_at", "updated_at")}


@app.post("/v1/transcriptions", status_code=202)
def upload(file: UploadFile = File(...)):
    # Plain `def` (not async): FastAPI runs it in a thread pool, so many uploads proceed
    # in parallel and slow disk writes never block the event loop.
    try:
        job, duplicate = service.submit(file.filename, file.file)
    except UnsupportedFileType as e:
        raise HTTPException(415, f"Unsupported file type: {e}")
    except FileTooLarge:
        raise HTTPException(413, f"File exceeds {settings.max_upload_mb} MB limit")
    body = {**_public(job), "duplicate": duplicate,
            "status_url": f"/v1/transcriptions/{job['id']}"}
    return JSONResponse(body, status_code=200 if duplicate else 202,
                        headers={"Location": body["status_url"]})


@app.get("/v1/transcriptions/{job_id}")
def get_status(job_id: str):
    job = service.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    body = _public(job)
    text = service.transcript(job)
    if text is not None:
        body["transcript"] = text
    return body


@app.get("/v1/transcriptions/{job_id}/transcript")
def get_transcript(job_id: str, format: str = "json"):
    """Download the finished transcript as json (with timestamps), srt, vtt or txt."""
    job = service.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    result = service.transcript(job)
    if result is None:
        raise HTTPException(409, f"Transcript not ready (status: {job['status']})")
    if format == "json":
        return result
    renderers = {"srt": to_srt, "vtt": to_vtt, "txt": to_txt}
    if format not in renderers:
        raise HTTPException(400, "format must be one of: json, srt, vtt, txt")
    return PlainTextResponse(renderers[format](result))


@app.get("/v1/transcriptions")
def list_jobs(status: str | None = None, limit: int = 50):
    return [_public(j) for j in db.list_jobs(status, min(limit, 200))]


@app.post("/v1/transcriptions/{job_id}/retry", status_code=202)
def retry(job_id: str):
    if not service.get(job_id):
        raise HTTPException(404, "Job not found")
    if not service.retry(job_id):
        raise HTTPException(409, "Only FAILED jobs can be retried")
    return _public(service.get(job_id))


@app.get("/health")
def health():
    return {"ok": True, "queue": db.counts()}
