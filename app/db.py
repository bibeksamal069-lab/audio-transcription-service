"""Job table in SQLite. The table doubles as a durable queue.

Why: if the server crashes, queued jobs are still in the database, so nothing is lost.
In production, swap SQLite for PostgreSQL and use `SELECT ... FOR UPDATE SKIP LOCKED`
(same idea as the atomic claim below).
"""
import sqlite3
import time
import uuid
from contextlib import contextmanager

QUEUED, PROCESSING, COMPLETED, FAILED = "QUEUED", "PROCESSING", "COMPLETED", "FAILED"

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    filename TEXT NOT NULL,
    sha256 TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    audio_key TEXT NOT NULL,
    transcript_key TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    max_attempts INTEGER NOT NULL,
    next_attempt_at REAL NOT NULL,
    locked_at REAL,
    error TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status, next_attempt_at);
CREATE INDEX IF NOT EXISTS idx_jobs_sha ON jobs(sha256);
"""


class Database:
    def __init__(self, path: str):
        self.path = path
        with self._conn() as c:
            c.execute("PRAGMA journal_mode=WAL")  # readers don't block writers
            c.executescript(SCHEMA)

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path, timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
        finally:
            conn.close()

    def create_job(self, job_id, filename, sha256, size, audio_key, max_attempts) -> dict:
        now = time.time()
        with self._conn() as c:
            c.execute(
                "INSERT INTO jobs (id, filename, sha256, size_bytes, audio_key, status, "
                "max_attempts, next_attempt_at, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (job_id, filename, sha256, size, audio_key, QUEUED, max_attempts, now, now, now),
            )
        return self.get(job_id)

    def get(self, job_id: str):
        with self._conn() as c:
            row = c.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def find_by_hash(self, sha256: str):
        """Used for de-duplication: same audio uploaded twice -> reuse the job."""
        with self._conn() as c:
            row = c.execute(
                "SELECT * FROM jobs WHERE sha256=? AND status!=? ORDER BY created_at LIMIT 1",
                (sha256, FAILED),
            ).fetchone()
        return dict(row) if row else None

    def list_jobs(self, status=None, limit=50):
        q, args = "SELECT * FROM jobs", []
        if status:
            q += " WHERE status=?"
            args.append(status)
        q += " ORDER BY created_at DESC LIMIT ?"
        args.append(limit)
        with self._conn() as c:
            return [dict(r) for r in c.execute(q, args).fetchall()]

    def claim_next(self):
        """Atomically grab one ready job. Two workers can never get the same job
        because BEGIN IMMEDIATE takes a write lock for the select+update."""
        now = time.time()
        with self._conn() as c:
            c.execute("BEGIN IMMEDIATE")
            row = c.execute(
                "SELECT * FROM jobs WHERE status=? AND next_attempt_at<=? "
                "ORDER BY next_attempt_at LIMIT 1", (QUEUED, now)).fetchone()
            if not row:
                c.execute("COMMIT")
                return None
            c.execute(
                "UPDATE jobs SET status=?, attempts=attempts+1, locked_at=?, updated_at=? WHERE id=?",
                (PROCESSING, now, now, row["id"]))
            c.execute("COMMIT")
        return self.get(row["id"])

    def mark_completed(self, job_id, transcript_key):
        now = time.time()
        with self._conn() as c:
            c.execute("UPDATE jobs SET status=?, transcript_key=?, error=NULL, locked_at=NULL, "
                      "updated_at=? WHERE id=?", (COMPLETED, transcript_key, now, job_id))

    def mark_retry(self, job_id, error, delay):
        now = time.time()
        with self._conn() as c:
            c.execute("UPDATE jobs SET status=?, error=?, next_attempt_at=?, locked_at=NULL, "
                      "updated_at=? WHERE id=?", (QUEUED, error, now + delay, now, job_id))

    def mark_failed(self, job_id, error):
        now = time.time()
        with self._conn() as c:
            c.execute("UPDATE jobs SET status=?, error=?, locked_at=NULL, updated_at=? WHERE id=?",
                      (FAILED, error, now, job_id))

    def requeue_stuck(self, stuck_after_seconds: float) -> int:
        """Crash recovery: a job 'PROCESSING' for too long means its worker died."""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status=?, locked_at=NULL, next_attempt_at=?, updated_at=?, "
                "error='worker died or timed out; re-queued' "
                "WHERE status=? AND locked_at < ?",
                (QUEUED, now, now, PROCESSING, now - stuck_after_seconds))
            return cur.rowcount

    def manual_retry(self, job_id) -> bool:
        """User/operator retries a FAILED job: gives it a fresh set of attempts."""
        now = time.time()
        with self._conn() as c:
            cur = c.execute(
                "UPDATE jobs SET status=?, attempts=0, next_attempt_at=?, updated_at=? "
                "WHERE id=? AND status=?", (QUEUED, now, now, job_id, FAILED))
            return cur.rowcount == 1

    def counts(self) -> dict:
        with self._conn() as c:
            rows = c.execute("SELECT status, COUNT(*) n FROM jobs GROUP BY status").fetchall()
        return {r["status"]: r["n"] for r in rows}
