"""Blob storage for audio files and transcripts.

Local disk here; the interface is tiny so an S3 version is a drop-in replacement
(save_audio_stream -> multipart upload, path -> presigned URL, etc.).
"""
import hashlib
import os
from typing import BinaryIO, Tuple

CHUNK = 1024 * 1024  # 1 MB


class FileTooLarge(Exception):
    pass


class LocalStorage:
    def __init__(self, root: str):
        self.root = os.path.abspath(root)
        os.makedirs(os.path.join(self.root, "audio"), exist_ok=True)
        os.makedirs(os.path.join(self.root, "transcripts"), exist_ok=True)

    def path(self, key: str) -> str:
        p = os.path.abspath(os.path.join(self.root, key))
        if not p.startswith(self.root + os.sep):     # block "../../" tricks
            raise ValueError("invalid storage key")
        return p

    def save_audio_stream(self, job_id: str, ext: str, stream: BinaryIO,
                          max_bytes: int) -> Tuple[str, str, int]:
        """Stream to disk in 1 MB chunks (never load the whole file into RAM),
        computing SHA-256 and size on the fly. Returns (key, sha256, size)."""
        key = f"audio/{job_id}{ext}"
        tmp = self.path(key + ".part")
        sha, size = hashlib.sha256(), 0
        try:
            with open(tmp, "wb") as out:
                while True:
                    chunk = stream.read(CHUNK)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > max_bytes:
                        raise FileTooLarge()
                    sha.update(chunk)
                    out.write(chunk)
            os.replace(tmp, self.path(key))          # atomic: no half-written files visible
        except BaseException:
            if os.path.exists(tmp):
                os.remove(tmp)
            raise
        return key, sha.hexdigest(), size

    def delete(self, key: str):
        try:
            os.remove(self.path(key))
        except FileNotFoundError:
            pass

    def save_text(self, key: str, text: str):
        tmp = self.path(key + ".part")
        with open(tmp, "w", encoding="utf-8") as f:
            f.write(text)
        os.replace(tmp, self.path(key))

    def read_text(self, key: str) -> str:
        with open(self.path(key), encoding="utf-8") as f:
            return f.read()
