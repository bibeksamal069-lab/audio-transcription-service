# Answers for the Google Form (copy and paste each one)

## PART 1: Transcription Pipeline

### Implement a service or script that accepts an audio file (e.g., WAV or MP3)
I built both a command-line script and an HTTP service. The script (`app/cli.py`) takes a path:
`python -m app.cli recording.mp3`. The service (`app/main.py`) accepts uploads at `POST /v1/transcriptions`.
Accepted types: WAV, MP3, M4A, FLAC, OGG, WebM, AAC and MP4 (audio is extracted). The upload is validated (type, non-empty,
100 MB limit), streamed to disk in 1 MB chunks so memory stays flat, and stored under a random ID. Files that are not
really audio are detected later by ffmpeg and rejected with a clear error instead of being retried.

### Implement a service or script that transcribes spoken language into text
Transcription sits behind a small `Transcriber` interface (`app/transcriber.py`) with three engines: **faster-whisper**
(free, open-source Whisper that runs on the user's own computer; this is the real engine), the **OpenAI Whisper API**, and a **mock**
engine that needs no model, so the full pipeline can be demoed and tested anywhere. Because they share one interface, any other
provider can be added without touching the rest of the code. Whisper also detects the language automatically.

### Implement a service or script that returns the transcription with timestamps per segment
Every result has this shape: `{language, duration_seconds, text, segments: [{id, start, end, text}, ...]}`, with `start` and
`end` in seconds from the start of the recording. For long files, each chunk's timestamps are shifted by the chunk's start time,
so they are correct for the whole recording (`app/pipeline.py`). The result can be downloaded as JSON, SRT or VTT subtitles,
or readable text (`[00:01:05] ...`) via `GET /v1/transcriptions/{id}/transcript?format=srt`, so it is ready for downstream use
such as subtitles, search or summarisation.

### How do you handle different audio formats?
I do not write code per format. Every input is first converted with **ffmpeg** into one standard format: 16 kHz, mono, 16-bit
WAV (`app/audio.py`). That is what speech models expect, so every engine always sees identical input, and the rest of the
pipeline only deals with one format. ffmpeg understands almost every audio and video format, so supporting a new one is just adding
its extension to an allow-list. If ffmpeg cannot read a file, that is treated as a *permanent* error: the job fails right away with a
clear message and is not retried. I tested conversion from MP3, M4A, OGG, FLAC and WAV.

### How do you deal with long audio files?
Long files are split into pieces of about 10 minutes (configurable) and transcribed piece by piece, then merged.
Key details: (1) the cut is placed at the **quietest moment** within about 15 seconds of the target point, so words are not
sliced in half; (2) splitting streams through the file in blocks, so memory use does not grow with the length of the recording;
(3) timestamps are shifted by each chunk's offset so the final transcript is continuous and in order; (4) in the service, work happens
in a background job, so the request never waits and a crash does not lose it. A 10-minute 16 kHz mono chunk is about 19 MB, which
also fits cloud API file-size limits. Current limitation: chunks run one after another and a failed job restarts from the first
chunk. Improvements: save each finished chunk and resume, and run chunks in parallel.

## PART 2: System Design

### How would you handle concurrent uploads?
The upload endpoint only does the fast work: it streams the file to disk, creates a job record, and immediately returns `202 Accepted` with
a job ID. Transcription runs in background workers, so a slow transcription never blocks other users.
- **Many uploads at once:** each upload runs in its own thread (FastAPI runs sync endpoints in a thread pool). Files are streamed to disk in
  1 MB chunks, so many 100 MB uploads do not exhaust memory, and written to a `.part` file that is renamed atomically when complete.
- **Many jobs at once:** a pool of worker threads (default 4) pulls jobs from a database-backed queue. A job is claimed in an atomic
  transaction, so two workers can never take the same job. I tested this with 8 threads racing for 30 jobs: each was claimed exactly once.
- **Duplicates:** a SHA-256 fingerprint is computed for each upload; if the same audio arrives again, the existing job is returned
  instead of paying to transcribe it twice.
- **Scaling further:** run more API instances and worker processes against PostgreSQL (`FOR UPDATE SKIP LOCKED` gives the same atomic claiming)
  or move the queue to SQS/RabbitMQ/Celery, so the API and workers scale independently.

### How would you store audio and transcripts?
- **Audio** goes in blob storage (local disk in this demo, behind a small `LocalStorage` class so it can be swapped for Amazon S3), named by a random job ID,
  never the user's file name, which prevents collisions and path tricks. Large binary files do not belong in a database.
- **Transcripts** are stored as JSON (full text plus timestamped segments) in the same storage, written atomically.
- **Metadata** lives in a database table: job ID, original name, SHA-256, size, status, attempts, error, timestamps, and the storage keys.
  The database holds pointers to the files, not the files.
- **In production:** S3 with encryption, direct client uploads via presigned URLs (audio never passes through the API server), lifecycle rules to
  archive or delete old audio, PostgreSQL for metadata, and per-user access checks and retention/delete endpoints where privacy requires it.

### How do you retry or recover failed transcriptions?
Failures are split into two kinds:
- **Transient** (timeout, rate limit, provider outage): retried, with **exponential backoff plus random jitter** (about 2s, 4s, 8s...). Backoff gives the
  provider time to recover; jitter stops all failed jobs from retrying at the same instant.
- **Permanent** (corrupt or non-audio file): fails immediately with the reason recorded. Retrying cannot fix it and would waste money.
- **Out of attempts** (default 4): the job becomes `FAILED` with the last error and stays as a dead-letter record. A user or operator can call
  `POST /v1/transcriptions/{id}/retry` to give it a fresh set of attempts, for example after an outage is over.
- **Crash recovery:** jobs live in the database, so nothing is lost if the server dies. On startup, jobs left in `PROCESSING` are re-queued, and a
  background "janitor" re-queues any job stuck longer than a timeout (a frozen worker). This gives at-least-once processing; saving the transcript
  under the job ID makes a repeated run harmless.
- **Observability:** retries and failures are logged, `error` and `attempts` are visible in the API, and `/health` shows queue counts.

### How would you expose this as an API?
A REST API using the asynchronous "submit, then poll" pattern:

| Method | Path | Behaviour |
|---|---|---|
| POST | `/v1/transcriptions` | Upload audio; returns `202` with `job_id` and a `Location` header (`200` if the same file was already uploaded) |
| GET | `/v1/transcriptions/{id}` | Status (`QUEUED`, `PROCESSING`, `COMPLETED`, `FAILED`), attempts, error, and the transcript when done |
| GET | `/v1/transcriptions/{id}/transcript?format=json\|srt\|vtt\|txt` | Download the transcript in the chosen format |
| GET | `/v1/transcriptions?status=` | List jobs |
| POST | `/v1/transcriptions/{id}/retry` | Retry a failed job |
| GET | `/health` | Health and queue depth |

Proper status codes: `202` accepted, `404` unknown job, `409` not ready / not failed, `413` too large, `415` unsupported type. The API is versioned
(`/v1`) and FastAPI generates interactive documentation at `/docs`. Next steps: API-key/OAuth authentication, per-user rate limits, webhook callbacks so
clients do not have to poll, idempotency keys, and pagination.
