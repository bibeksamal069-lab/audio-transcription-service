"""Business logic, independent of the web framework (easy to test)."""
import json
import os
import uuid
from typing import BinaryIO, Optional, Tuple

from .db import COMPLETED, Database
from .storage import FileTooLarge, LocalStorage


class UnsupportedFileType(Exception):
    pass


class JobService:
    def __init__(self, db: Database, storage: LocalStorage, settings):
        self.db, self.storage, self.s = db, storage, settings

    def submit(self, filename: str, stream: BinaryIO) -> Tuple[dict, bool]:
        """Save the upload, create a job. Returns (job, is_duplicate)."""
        ext = os.path.splitext(filename or "")[1].lower()
        if ext not in self.s.allowed_extensions:
            raise UnsupportedFileType(ext or "(none)")

        job_id = uuid.uuid4().hex
        key, sha, size = self.storage.save_audio_stream(
            job_id, ext, stream, self.s.max_upload_mb * 1024 * 1024)
        if size == 0:
            self.storage.delete(key)
            raise UnsupportedFileType("empty file")

        existing = self.db.find_by_hash(sha)
        if existing:                       # same bytes already uploaded -> reuse, save money
            self.storage.delete(key)
            return existing, True

        job = self.db.create_job(job_id, os.path.basename(filename), sha, size, key,
                                 self.s.max_attempts)
        return job, False

    def get(self, job_id: str) -> Optional[dict]:
        return self.db.get(job_id)

    def transcript(self, job: dict) -> Optional[dict]:
        if job["status"] == COMPLETED and job["transcript_key"]:
            return json.loads(self.storage.read_text(job["transcript_key"]))
        return None

    def retry(self, job_id: str) -> bool:
        return self.db.manual_retry(job_id)
