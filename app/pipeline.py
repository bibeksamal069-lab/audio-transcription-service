"""The transcription pipeline (Part 1), independent of the web server and the queue:

    any audio file -> convert to standard WAV -> split if long -> transcribe each chunk
                   -> shift timestamps by the chunk's start -> one merged result
"""
import logging
import os
import tempfile

from .audio import normalize_to_wav, split_wav, wav_duration
from .transcriber import Transcriber

log = logging.getLogger("pipeline")


def run_pipeline(audio_path: str, transcriber: Transcriber, chunk_seconds: float = 600,
                 filename: str = "", attempt: int = 1, on_progress=None) -> dict:
    with tempfile.TemporaryDirectory(prefix="transcribe_") as tmp:
        wav = os.path.join(tmp, "normalized.wav")
        normalize_to_wav(audio_path, wav)
        duration = wav_duration(wav)
        chunks = split_wav(wav, tmp, chunk_seconds)
        log.info("%s: %.1fs of audio, %d chunk(s)", filename or audio_path, duration, len(chunks))

        segments, language = [], None
        for i, (chunk_path, offset) in enumerate(chunks, start=1):
            res = transcriber.transcribe(chunk_path, attempt, filename)
            language = language or res.get("language")
            for s in res["segments"]:
                if not s["text"].strip():
                    continue
                segments.append({"start": round(s["start"] + offset, 3),
                                 "end": round(s["end"] + offset, 3), "text": s["text"].strip()})
            if on_progress:
                on_progress(i, len(chunks))

    segments.sort(key=lambda s: s["start"])
    for i, s in enumerate(segments, start=1):
        s["id"] = i
    return {"language": language or "unknown", "duration_seconds": round(duration, 3),
            "chunks": len(chunks), "text": " ".join(s["text"] for s in segments),
            "segments": segments}
