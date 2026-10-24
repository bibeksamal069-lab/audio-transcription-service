# Audio Transcription Pipeline

Turns an audio file (WAV, MP3, M4A, FLAC, OGG...) into text **with a timestamp for every segment**, as a command-line
script and as an HTTP service with a background job queue, retries and crash recovery.

```
audio file ─▶ ffmpeg converts to 16 kHz mono WAV ─▶ split long audio at quiet moments
           ─▶ transcribe each piece (Whisper / OpenAI / mock) ─▶ shift timestamps ─▶ merge
           ─▶ JSON / SRT / VTT / TXT
```

For the service, the upload only saves the file and creates a job; workers do the slow part in the background:

```
Client ─POST /v1/transcriptions─▶ API ─▶ save audio + create job (QUEUED) ─▶ reply 202 + job_id
                     Worker threads (x4) ◀─ claim a job atomically
                        │ success ─▶ transcript saved, COMPLETED
                        │ temporary error ─▶ back to QUEUED after a delay (retry)
                        └ permanent error / out of attempts ─▶ FAILED
Client ─GET /v1/transcriptions/{id}─▶ status (+ transcript when COMPLETED)
```

## Quick start

1. Install **Python 3.10+** and **ffmpeg** (Windows: `winget install ffmpeg`; Mac: `brew install ffmpeg`; Ubuntu: `sudo apt install ffmpeg`).
2. In the project folder:
```bash
python -m venv .venv && source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```
3. **Script** (works immediately with the built-in mock engine; the `samples/` folder has ready-made files):
```bash
python -m app.cli samples/sample.mp3                      # text with timestamps
python -m app.cli samples/sample.m4a --format srt         # subtitles
python -m app.cli samples/sample.wav --format json --chunk 5   # force splitting into small chunks
```
4. **Real transcription** (free, runs on your computer; first run downloads the model):
```bash
pip install faster-whisper
python -m app.cli my_recording.mp3 --engine whisper --model base --format srt
```
5. **Service:**
```bash
uvicorn app.main:app --reload        # then open http://127.0.0.1:8000/docs
# set TRANSCRIBER=whisper (or openai) to use a real engine; default is mock
curl -F "file=@samples/sample.mp3" http://127.0.0.1:8000/v1/transcriptions
curl http://127.0.0.1:8000/v1/transcriptions/<job_id>
curl "http://127.0.0.1:8000/v1/transcriptions/<job_id>/transcript?format=srt"
```

Demo files in `samples/`: `sample.wav|mp3|m4a` (three formats), `flaky.mp3` (mock engine fails twice, then succeeds: shows retries),
`down.mp3` (always fails: ends FAILED after all attempts), `corrupt.mp3` (random bytes: rejected immediately as not audio).
The sample files are plain tones, so with the mock engine they produce placeholder text; use `--engine whisper` with a real recording for real words.

## Tests

```bash
python -m unittest tests.test_core -v      # or: pytest
```
17 tests: format conversion (MP3/M4A/OGG/FLAC/WAV), garbage-file rejection, splitting at silence without losing audio, correct global
timestamps after merging chunks, SRT/VTT/TXT output, 20 parallel uploads processed exactly once, atomic job claiming under a race,
retry then success, no retry on permanent errors, giving up after N attempts, manual retry, crash recovery, duplicate uploads, size/type validation.

## Key design decisions

| Decision | Why |
|---|---|
| Convert everything to 16 kHz mono WAV first | One format downstream; any input format works; engines get identical input |
| Split long audio at the quietest point near ~10 min | Bounded memory/time per piece; avoids cutting words; fits API size limits |
| Timestamps shifted by chunk offset | Output is continuous for the whole recording |
| `Transcriber` interface (Whisper / OpenAI / mock) | Swap engines freely; test everything without a model |
| Return `202` and process in background | Transcription is slow; never hold a request open |
| Database table as the queue + atomic claim | Survives crashes, no extra infrastructure; two workers never take one job |
| Stream uploads in 1 MB chunks, rename when complete | Flat memory use; no half-written files |
| SHA-256 duplicate detection | Same audio is never transcribed (or paid for) twice |
| Transient vs permanent errors, backoff + jitter | Retry what can succeed, fail fast on what cannot |

## Known limitations (honest list)

- **Tested here:** everything except the two real engines. The mock engine, ffmpeg conversion, splitting, merging, queue, retries and recovery are covered by tests.
  The `faster-whisper` and OpenAI engines are written to the same interface but were **not run against a real model/API** in my environment, and I did not run the FastAPI web layer
  there either (no internet to install it); run the Quick start once to confirm on your machine.
- Chunks are processed one after another, and a failed job restarts from the first chunk (improvement: checkpoint and parallelise chunks).
- Fixed ~10-minute chunks cut at the quietest point; a smarter voice-activity detector would be better for noisy audio.
- SQLite + local disk = one machine. No authentication or rate limiting yet. Speaker identification (who spoke) is not included.

## Project layout

```
app/cli.py        command-line script          app/pipeline.py   convert -> split -> transcribe -> merge
app/audio.py      ffmpeg conversion + splitting app/transcriber.py engines: Whisper / OpenAI / mock
app/formats.py    JSON/SRT/VTT/TXT output       app/main.py       HTTP API
app/db.py         job table + queue ops         app/worker.py     background workers, retries, recovery
app/storage.py    audio + transcript storage    app/service.py    business logic
tests/test_core.py  automated tests             samples/          demo audio files
```

---

