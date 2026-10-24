"""All tunable settings in one place (override with environment variables)."""
import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    data_dir: str = os.getenv("DATA_DIR", "./data")
    max_upload_mb: int = int(os.getenv("MAX_UPLOAD_MB", "100"))
    worker_count: int = int(os.getenv("WORKER_COUNT", "4"))      # parallel transcriptions
    max_attempts: int = int(os.getenv("MAX_ATTEMPTS", "4"))      # 1 try + 3 retries
    backoff_base_seconds: float = float(os.getenv("BACKOFF_BASE", "2"))  # 2s, 4s, 8s...
    stuck_after_seconds: int = int(os.getenv("STUCK_AFTER", "300"))      # recover crashed jobs
    poll_interval_seconds: float = float(os.getenv("POLL_INTERVAL", "0.5"))
    transcriber: str = os.getenv("TRANSCRIBER", "mock")          # "mock", "whisper" or "openai"
    whisper_model: str = os.getenv("WHISPER_MODEL", "base")      # tiny/base/small/medium/large-v3
    chunk_seconds: int = int(os.getenv("CHUNK_SECONDS", "600"))  # long audio is split into ~10 min pieces
    allowed_extensions: tuple = (".mp3", ".wav", ".m4a", ".flac", ".ogg", ".webm", ".aac", ".mp4")
