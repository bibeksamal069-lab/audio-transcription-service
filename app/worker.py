"""Background workers: a small pool of threads that pull jobs from the database queue,
transcribe them, and handle success / retry / failure."""
import json
import logging
import random
import threading
import time

from .db import Database
from .pipeline import run_pipeline
from .storage import LocalStorage
from .transcriber import PermanentError, Transcriber, TransientError

log = logging.getLogger("worker")


class WorkerPool:
    def __init__(self, db: Database, storage: LocalStorage, transcriber: Transcriber,
                 worker_count=4, backoff_base=2.0, stuck_after=300, poll_interval=0.5,
                 chunk_seconds=600):
        self.db, self.storage, self.transcriber = db, storage, transcriber
        self.worker_count, self.backoff_base = worker_count, backoff_base
        self.stuck_after, self.poll_interval = stuck_after, poll_interval
        self.chunk_seconds = chunk_seconds
        self._stop = threading.Event()
        self._threads = []

    # ---- lifecycle -------------------------------------------------------
    def start(self):
        n = self.db.requeue_stuck(0)  # on boot, nothing can legitimately be in-flight
        if n:
            log.warning("Recovered %d job(s) left PROCESSING by a previous crash", n)
        for i in range(self.worker_count):
            t = threading.Thread(target=self._loop, name=f"worker-{i}", daemon=True)
            t.start()
            self._threads.append(t)
        t = threading.Thread(target=self._janitor, name="janitor", daemon=True)
        t.start()
        self._threads.append(t)

    def stop(self):
        self._stop.set()
        for t in self._threads:
            t.join(timeout=5)

    # ---- loops -----------------------------------------------------------
    def _loop(self):
        while not self._stop.is_set():
            if not self.process_one():
                self._stop.wait(self.poll_interval)

    def _janitor(self):
        """Periodically rescues jobs whose worker froze or died mid-job."""
        while not self._stop.wait(max(1.0, self.stuck_after / 4)):
            n = self.db.requeue_stuck(self.stuck_after)
            if n:
                log.warning("Janitor re-queued %d stuck job(s)", n)

    # ---- one unit of work (also called directly by tests) -------------------
    def process_one(self) -> bool:
        job = self.db.claim_next()
        if not job:
            return False
        jid, attempt = job["id"], job["attempts"]
        try:
            result = run_pipeline(self.storage.path(job["audio_key"]), self.transcriber,
                                  self.chunk_seconds, job["filename"], attempt)
            key = f"transcripts/{jid}.json"
            self.storage.save_text(key, json.dumps(result, ensure_ascii=False))
            self.db.mark_completed(jid, key)
            log.info("job %s completed (attempt %d)", jid, attempt)
        except PermanentError as e:
            self.db.mark_failed(jid, f"permanent: {e}")
            log.error("job %s failed permanently: %s", jid, e)
        except Exception as e:  # TransientError and anything unexpected are retried
            msg = f"{type(e).__name__}: {e}"
            if attempt >= job["max_attempts"]:
                self.db.mark_failed(jid, f"gave up after {attempt} attempts: {msg}")
                log.error("job %s gave up: %s", jid, msg)
            else:
                # exponential backoff + jitter: 2s, 4s, 8s ... (+0-1s random)
                delay = self.backoff_base * (2 ** (attempt - 1)) + random.random()
                self.db.mark_retry(jid, msg, delay)
                log.warning("job %s will retry in %.1fs (%s)", jid, delay, msg)
        return True
