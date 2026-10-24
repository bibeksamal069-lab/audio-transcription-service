import array
import io
import math
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import wave

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from app.audio import normalize_to_wav, split_wav, wav_duration
from app.config import Settings
from app.db import COMPLETED, FAILED, PROCESSING, QUEUED, Database
from app.formats import to_srt, to_txt, to_vtt
from app.pipeline import run_pipeline
from app.service import JobService, UnsupportedFileType
from app.storage import FileTooLarge, LocalStorage
from app.transcriber import MockTranscriber, PermanentError, Transcriber
from app.worker import WorkerPool

HAVE_FFMPEG = shutil.which("ffmpeg") is not None


def make_wav(path, seconds, freq=440, silence=None, rate=16000):
    """Write a test WAV: a sine tone, optionally with a silent gap (start, end) in seconds."""
    samples = array.array("h")
    for i in range(int(seconds * rate)):
        t = i / rate
        quiet = silence and silence[0] <= t < silence[1]
        samples.append(0 if quiet else int(12000 * math.sin(2 * math.pi * freq * t)))
    with wave.open(path, "wb") as w:
        w.setnchannels(1); w.setsampwidth(2); w.setframerate(rate)
        w.writeframes(samples.tobytes())


def wav_bytes(seconds=1, freq=440):
    d = tempfile.mkdtemp()
    try:
        p = os.path.join(d, "x.wav")
        make_wav(p, seconds, freq)
        with open(p, "rb") as f:
            return f.read()
    finally:
        shutil.rmtree(d, ignore_errors=True)


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.s = Settings(data_dir=self.tmp, max_upload_mb=1, max_attempts=3)
        self.storage = LocalStorage(self.tmp)
        self.db = Database(os.path.join(self.tmp, "jobs.db"))
        self.svc = JobService(self.db, self.storage, self.s)
        self._n = 0

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def upload(self, name, data=None):
        if data is None:
            self._n += 1
            data = wav_bytes(1, 200 + self._n * 7)      # unique audio each time
        return self.svc.submit(name, io.BytesIO(data))

    def pool(self, transcriber=None, **kw):
        return WorkerPool(self.db, self.storage, transcriber or MockTranscriber(),
                          backoff_base=0.01, poll_interval=0.01, **kw)

    def wait_for(self, job_id, status, timeout=15):
        end = time.time() + timeout
        while time.time() < end:
            if self.db.get(job_id)["status"] == status:
                return
            time.sleep(0.02)
        self.fail(f"job never reached {status}: {self.db.get(job_id)}")


# ---------------------------------------------------------------- Part 1
class TestAudioFormats(unittest.TestCase):
    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_many_formats_become_16k_mono_wav(self):
        d = tempfile.mkdtemp()
        try:
            for ext, codec in [("mp3", "libmp3lame"), ("m4a", "aac"), ("ogg", "libvorbis"),
                               ("flac", "flac"), ("wav", "pcm_s16le")]:
                src = os.path.join(d, f"in.{ext}")
                subprocess.run(["ffmpeg", "-nostdin", "-y", "-v", "error", "-f", "lavfi", "-i",
                                "sine=frequency=440:duration=2", "-ar", "44100", "-ac", "2",
                                "-c:a", codec, src], check=True)
                dst = os.path.join(d, f"out_{ext}.wav")
                normalize_to_wav(src, dst)
                with wave.open(dst) as w:
                    self.assertEqual((w.getframerate(), w.getnchannels(), w.getsampwidth()), (16000, 1, 2), ext)
                self.assertAlmostEqual(wav_duration(dst), 2.0, delta=0.15, msg=ext)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_garbage_file_is_a_permanent_error(self):
        d = tempfile.mkdtemp()
        try:
            bad = os.path.join(d, "bad.mp3")
            open(bad, "wb").write(os.urandom(2000))
            with self.assertRaises(PermanentError):
                normalize_to_wav(bad, os.path.join(d, "o.wav"))
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestLongAudio(unittest.TestCase):
    def test_split_cuts_in_silence_and_loses_nothing(self):
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "long.wav")
            make_wav(src, 35, silence=(9.6, 10.6))       # a pause right where the first cut lands
            pieces = split_wav(src, d, chunk_seconds=10, search_seconds=3)
            self.assertGreaterEqual(len(pieces), 3)
            self.assertAlmostEqual(pieces[1][1], 10.1, delta=0.5)   # cut happened INSIDE the pause
            self.assertTrue(9.6 <= pieces[1][1] <= 10.6)
            total = sum(wav_duration(p) for p, _ in pieces)
            self.assertAlmostEqual(total, 35, delta=0.01)           # no audio lost or duplicated
            for (p, off), nxt in zip(pieces, pieces[1:]):
                self.assertAlmostEqual(off + wav_duration(p), nxt[1], delta=0.01)
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def test_short_file_is_not_split(self):
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "s.wav")
            make_wav(src, 5)
            self.assertEqual(split_wav(src, d, 600), [(src, 0.0)])
        finally:
            shutil.rmtree(d, ignore_errors=True)

    @unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
    def test_pipeline_merges_chunks_with_correct_global_timestamps(self):
        d = tempfile.mkdtemp()
        try:
            src = os.path.join(d, "long.wav")
            make_wav(src, 32, silence=(9.8, 10.4))
            res = run_pipeline(src, MockTranscriber(), chunk_seconds=10, filename="long.wav")
            self.assertGreaterEqual(res["chunks"], 3)
            segs = res["segments"]
            self.assertAlmostEqual(res["duration_seconds"], 32, delta=0.1)
            starts = [s["start"] for s in segs]
            self.assertEqual(starts, sorted(starts))                 # global order
            self.assertEqual([s["id"] for s in segs], list(range(1, len(segs) + 1)))
            self.assertAlmostEqual(segs[0]["start"], 0, delta=0.01)
            self.assertAlmostEqual(segs[-1]["end"], 32, delta=0.1)   # covers the whole recording
            for a, b in zip(segs, segs[1:]):
                self.assertLessEqual(a["end"], b["start"] + 0.01)    # no overlaps
                self.assertLess(a["start"], a["end"])
        finally:
            shutil.rmtree(d, ignore_errors=True)


class TestFormats(unittest.TestCase):
    R = {"segments": [{"id": 1, "start": 0.0, "end": 4.5, "text": "Hello there."},
                      {"id": 2, "start": 3723.25, "end": 3725.0, "text": "Much later."}]}

    def test_srt_vtt_txt(self):
        self.assertIn("1\n00:00:00,000 --> 00:00:04,500\nHello there.", to_srt(self.R))
        self.assertIn("2\n01:02:03,250 --> 01:02:05,000\nMuch later.", to_srt(self.R))
        self.assertTrue(to_vtt(self.R).startswith("WEBVTT"))
        self.assertIn("01:02:03.250 --> 01:02:05.000", to_vtt(self.R))
        self.assertIn("[01:02:03] Much later.", to_txt(self.R))


# ---------------------------------------------------------------- Part 2
class TestUpload(Base):
    def test_rejects_wrong_extension_and_empty(self):
        with self.assertRaises(UnsupportedFileType):
            self.upload("notes.txt", b"hello")
        with self.assertRaises(UnsupportedFileType):
            self.upload("a.mp3", b"")

    def test_rejects_too_large_and_leaves_no_partial_file(self):
        with self.assertRaises(FileTooLarge):
            self.upload("big.mp3", b"x" * (1024 * 1024 + 1))
        self.assertEqual(os.listdir(os.path.join(self.tmp, "audio")), [])

    def test_duplicate_upload_reuses_job(self):
        j1, d1 = self.upload("a.mp3", b"same-bytes")
        j2, d2 = self.upload("copy.mp3", b"same-bytes")
        self.assertFalse(d1)
        self.assertTrue(d2)
        self.assertEqual(j1["id"], j2["id"])
        self.assertEqual(len(os.listdir(os.path.join(self.tmp, "audio"))), 1)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class TestConcurrency(Base):
    def test_20_parallel_uploads_all_transcribed_exactly_once(self):
        calls, lock = [], threading.Lock()

        class Counting(Transcriber):
            def transcribe(self, path, attempt, filename=""):
                with lock:
                    calls.append(filename)
                time.sleep(0.02)
                return {"language": "en", "segments": [{"start": 0, "end": 1, "text": "ok"}]}

        ids, errors = [], []

        def up(i):
            try:
                job, _ = self.upload(f"f{i}.wav", wav_bytes(1, 300 + i * 11))
                ids.append(job["id"])
            except Exception as e:
                errors.append(e)

        threads = [threading.Thread(target=up, args=(i,)) for i in range(20)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        self.assertEqual(errors, [])
        self.assertEqual(len(set(ids)), 20)

        pool = self.pool(Counting(), worker_count=4)
        pool.start()
        try:
            for jid in ids:
                self.wait_for(jid, COMPLETED)
        finally:
            pool.stop()
        self.assertEqual(len(calls), 20)
        self.assertEqual(len(set(calls)), 20)

    def test_claim_is_atomic_under_race(self):
        for i in range(30):
            self.upload(f"r{i}.mp3", f"d{i}".encode())
        claimed, lock = [], threading.Lock()

        def grab():
            while True:
                job = self.db.claim_next()
                if not job:
                    return
                with lock:
                    claimed.append(job["id"])

        ts = [threading.Thread(target=grab) for _ in range(8)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        self.assertEqual(len(claimed), 30)
        self.assertEqual(len(set(claimed)), 30)


@unittest.skipUnless(HAVE_FFMPEG, "ffmpeg not installed")
class TestRetryAndRecovery(Base):
    def run_pool_until(self, job_id, status):
        pool = self.pool(worker_count=1)
        pool.start()
        try:
            self.wait_for(job_id, status)
        finally:
            pool.stop()

    def test_end_to_end_completed_has_timestamped_segments(self):
        job, _ = self.upload("talk.wav", wav_bytes(12, 440))
        self.run_pool_until(job["id"], COMPLETED)
        done = self.svc.transcript(self.db.get(job["id"]))
        self.assertEqual(len(done["segments"]), 3)             # 12s -> 5s,5s,2s mock segments
        self.assertEqual(done["segments"][1]["start"], 5.0)

    def test_flaky_job_retries_then_succeeds(self):
        job, _ = self.upload("flaky.mp3")
        self.run_pool_until(job["id"], COMPLETED)
        self.assertEqual(self.db.get(job["id"])["attempts"], 3)

    def test_corrupt_audio_fails_immediately_without_retry(self):
        job, _ = self.upload("corrupt.mp3", os.urandom(3000))
        self.run_pool_until(job["id"], FAILED)
        failed = self.db.get(job["id"])
        self.assertEqual(failed["attempts"], 1)
        self.assertIn("permanent", failed["error"])

    def test_gives_up_after_max_attempts_then_manual_retry_requeues(self):
        job, _ = self.upload("down.mp3")
        self.run_pool_until(job["id"], FAILED)
        failed = self.db.get(job["id"])
        self.assertEqual(failed["attempts"], 3)
        self.assertIn("gave up", failed["error"])
        self.assertTrue(self.db.manual_retry(job["id"]))
        again = self.db.get(job["id"])
        self.assertEqual((again["status"], again["attempts"]), (QUEUED, 0))
        self.assertFalse(self.db.manual_retry(job["id"]))

    def test_crash_recovery_requeues_stuck_job(self):
        job, _ = self.upload("ok.wav")
        self.db.claim_next()
        self.assertEqual(self.db.get(job["id"])["status"], PROCESSING)
        self.assertEqual(self.db.requeue_stuck(3600), 0)
        time.sleep(0.05)
        self.assertEqual(self.db.requeue_stuck(0.01), 1)
        self.assertEqual(self.db.get(job["id"])["status"], QUEUED)

    def test_restart_recovers_in_flight_jobs(self):
        job, _ = self.upload("ok.wav")
        self.db.claim_next()
        self.run_pool_until(job["id"], COMPLETED)


if __name__ == "__main__":
    unittest.main(verbosity=2)
