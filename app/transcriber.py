"""Speech-to-text engines. The rest of the app only knows the `Transcriber` interface,
so providers can be swapped (local Whisper, OpenAI API, AWS Transcribe...) without touching
the queue / retry / storage code.

Every engine returns the same shape for ONE audio chunk:
    {"language": "en", "segments": [{"start": 0.0, "end": 4.2, "text": "Hello everyone"}, ...]}
(times are in seconds, relative to the start of that chunk; the pipeline shifts them.)

Two error types drive the retry logic:
  TransientError -> temporary problem (timeout, rate limit). RETRY later.
  PermanentError -> will never work (corrupt file, unsupported format). Do NOT retry.
"""
import os
import threading
import wave
from abc import ABC, abstractmethod


class TransientError(Exception):
    pass


class PermanentError(Exception):
    pass


class Transcriber(ABC):
    @abstractmethod
    def transcribe(self, audio_path: str, attempt: int, filename: str = "") -> dict: ...


class MockTranscriber(Transcriber):
    """Fake engine so the whole system can be demoed with no model and no API key.
    It invents one segment per 5 seconds of audio. Failure hooks (by ORIGINAL file name):
        'flaky' -> fails the first 2 attempts, then succeeds   (shows retries)
        'down'  -> always fails transiently (ends FAILED)      (shows giving up)
    """

    def transcribe(self, audio_path: str, attempt: int, filename: str = "") -> dict:
        name = (filename or os.path.basename(audio_path)).lower()
        if "down" in name:
            raise TransientError("Provider unavailable (simulated)")
        if "flaky" in name and attempt < 3:
            raise TransientError(f"Provider timeout (simulated, attempt {attempt})")
        with wave.open(audio_path, "rb") as wf:
            duration = wf.getnframes() / wf.getframerate()
        segments, t, i = [], 0.0, 1
        while t < duration:
            end = min(t + 5.0, duration)
            segments.append({"start": round(t, 3), "end": round(end, 3),
                             "text": f"[MOCK] Pretend speech number {i}."})
            t, i = end, i + 1
        return {"language": "en", "segments": segments}


class WhisperTranscriber(Transcriber):
    """Real, free, open-source transcription using faster-whisper (runs on your own computer).
    `pip install faster-whisper`. Models: tiny / base / small / medium / large-v3 (bigger = slower + more accurate).
    First run downloads the model automatically."""

    def __init__(self, model_size: str = "base"):
        from faster_whisper import WhisperModel   # lazy import: mock mode needs no extra package
        self._model = WhisperModel(model_size, device="cpu", compute_type="int8")
        self._lock = threading.Lock()              # one model, one transcription at a time

    def transcribe(self, audio_path: str, attempt: int, filename: str = "") -> dict:
        try:
            with self._lock:
                segments, info = self._model.transcribe(audio_path, vad_filter=True)
                out = [{"start": round(s.start, 3), "end": round(s.end, 3), "text": s.text.strip()}
                       for s in segments]              # `segments` is lazy: must be consumed here
            return {"language": info.language, "segments": out}
        except Exception as e:
            raise TransientError(f"whisper failed: {e}")


class OpenAITranscriber(Transcriber):
    """Cloud transcription via the OpenAI Whisper API (`pip install openai`, set OPENAI_API_KEY)."""

    def __init__(self):
        import openai
        self._openai = openai
        self._client = openai.OpenAI(timeout=120)

    def transcribe(self, audio_path: str, attempt: int, filename: str = "") -> dict:
        o = self._openai
        try:
            with open(audio_path, "rb") as f:
                r = self._client.audio.transcriptions.create(
                    model="whisper-1", file=f, response_format="verbose_json",
                    timestamp_granularities=["segment"])
            get = lambda s, k: s[k] if isinstance(s, dict) else getattr(s, k)
            segs = [{"start": round(float(get(s, "start")), 3), "end": round(float(get(s, "end")), 3),
                     "text": get(s, "text").strip()} for s in (r.segments or [])]
            return {"language": getattr(r, "language", None) or "unknown", "segments": segs}
        except (o.APITimeoutError, o.APIConnectionError, o.RateLimitError, o.InternalServerError) as e:
            raise TransientError(str(e))
        except o.OpenAIError as e:
            raise PermanentError(str(e))


def build_transcriber(kind: str, model: str = "base") -> Transcriber:
    if kind == "whisper":
        return WhisperTranscriber(model)
    if kind == "openai":
        return OpenAITranscriber()
    return MockTranscriber()
