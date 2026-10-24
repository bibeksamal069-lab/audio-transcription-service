"""Audio handling: (1) convert ANY format to one standard format, (2) split long audio.

Standard format = 16 kHz, mono, 16-bit WAV. That is what speech models want, so every
engine sees identical input no matter whether the user uploaded MP3, M4A, FLAC, OGG...
"""
import array
import os
import subprocess
import wave
from typing import List, Tuple

from .transcriber import PermanentError, TransientError

TARGET_RATE = 16000


def normalize_to_wav(src: str, dst: str, timeout: int = 1800) -> None:
    """Convert any audio/video file to 16 kHz mono WAV using ffmpeg."""
    cmd = ["ffmpeg", "-nostdin", "-y", "-v", "error", "-i", src,
           "-vn", "-ac", "1", "-ar", str(TARGET_RATE), "-c:a", "pcm_s16le", dst]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except FileNotFoundError:
        raise RuntimeError("ffmpeg is not installed on this machine (see README)")
    except subprocess.TimeoutExpired:
        raise TransientError("audio conversion timed out")
    if r.returncode != 0 or not os.path.exists(dst) or os.path.getsize(dst) < 100:
        # ffmpeg could not understand the file: retrying will never help
        raise PermanentError("Not a valid audio file: " + (r.stderr.strip().splitlines() or ["unreadable"])[-1])


def wav_duration(path: str) -> float:
    with wave.open(path, "rb") as wf:
        return wf.getnframes() / wf.getframerate()


def _quietest_cut(wf: wave.Wave_read, target: int, search: int, window: int, lo_limit: int) -> int:
    """Find the quietest moment near `target` (frame index) so we cut between words, not through one."""
    n = wf.getnframes()
    lo, hi = max(lo_limit, target - search), min(n, target + search)
    if hi - lo < window * 2:
        return min(target, n)
    wf.setpos(lo)
    samples = array.array("h")
    samples.frombytes(wf.readframes(hi - lo))
    best_i, best_energy = 0, None
    for i in range(0, len(samples) - window, window // 2):
        energy = sum(abs(x) for x in samples[i:i + window])
        if best_energy is None or energy < best_energy:
            best_i, best_energy = i, energy
    return lo + best_i + window // 2


def split_wav(path: str, out_dir: str, chunk_seconds: float,
              search_seconds: float = 15.0) -> List[Tuple[str, float]]:
    """Split a normalized WAV into ~chunk_seconds pieces, cutting at quiet moments.
    Returns [(chunk_path, start_offset_seconds), ...]. Short files come back untouched."""
    with wave.open(path, "rb") as wf:
        rate, n = wf.getframerate(), wf.getnframes()
        chunk = int(chunk_seconds * rate)
        if n <= chunk * 1.1:
            return [(path, 0.0)]
        search, window = int(search_seconds * rate), int(0.2 * rate)
        bounds, pos = [0], 0
        while n - pos > chunk * 1.1:
            cut = _quietest_cut(wf, pos + chunk, search, window, pos + rate)
            bounds.append(cut)
            pos = cut
        bounds.append(n)
        pieces = []
        for idx in range(len(bounds) - 1):
            a, b = bounds[idx], bounds[idx + 1]
            out = os.path.join(out_dir, f"chunk_{idx:04d}.wav")
            wf.setpos(a)
            with wave.open(out, "wb") as o:
                o.setnchannels(wf.getnchannels()); o.setsampwidth(wf.getsampwidth()); o.setframerate(rate)
                left = b - a
                while left > 0:                      # copy in blocks: constant memory
                    block = wf.readframes(min(left, rate * 30))
                    o.writeframes(block)
                    left -= len(block) // (wf.getsampwidth() * wf.getnchannels())
            pieces.append((out, a / rate))
        return pieces
