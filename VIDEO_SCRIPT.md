# Video Script: word-for-word (about 6 to 7 minutes, English)

Tips: speak slowly and pause between sections. You can read from this page; change words so it sounds like you.
Record your whole screen so the code is visible (Windows: Win+G or OBS; Mac: Shift+Cmd+5; or Loom).
Before recording: open the project in VS Code; open a terminal in the project folder; have `uvicorn app.main:app --reload`
running in a second terminal and `http://127.0.0.1:8000/docs` open in the browser. Do a dry run first.

---

## A. Introduction (0:00 – 0:30)  [Screen: README.md]
"Hello, my name is <YOUR NAME>. In this video I explain my solution to the transcription pipeline assignment.
I will cover how I approached the problem, the key decisions I made, how the code works, and a short live demo.
My solution has two layers: a pipeline that turns audio into timestamped text, and a service around it that handles many uploads and failures."

## B. How I approached the problem (0:30 – 1:20)  [Screen: diagram in README]
"I started by asking what could go wrong, because the task says the focus is engineering decisions.
I found four risks. One: audio comes in many formats. Two: recordings can be very long. Three: transcription is slow, so a web request cannot wait for it.
Four: things fail, like timeouts or corrupt files.
So I designed the solution in layers. First, convert any input into one standard format. Second, split long audio into pieces. Third, transcribe each piece
and merge the results with correct timestamps. Fourth, wrap it in a service with a background queue, retries and crash recovery."

## C. Part 1 code walkthrough (1:20 – 3:40)

**[Open app/audio.py]**
"This is audio.py. To handle different formats, I do not write code for each one. I use ffmpeg to convert whatever comes in, whether MP3, M4A, FLAC or even a video file,
into one standard format: 16 kilohertz, mono, WAV. That is what speech models expect, so everything after this step sees identical input.
If ffmpeg cannot read a file, I raise a permanent error, meaning retrying will never help."

"For long files, the function split_wav cuts the audio into pieces of about ten minutes. But cutting at an exact second could slice through a word,
so it looks around the target point for the quietest moment and cuts there. It copies the audio in blocks, so memory use stays small even for very long recordings."

**[Open app/pipeline.py]**
"This is pipeline.py, the core. It converts the file, splits it, transcribes each chunk, and then adds each chunk's start time to its timestamps.
That is how the final transcript has correct times for the whole recording, not just for each chunk. At the end, all segments are merged in order and numbered."

**[Open app/transcriber.py]**
"This is transcriber.py. There is one simple interface, and three engines behind it: faster-whisper, which is free, open-source and runs on the computer;
the OpenAI Whisper API; and a mock engine for testing. Each returns segments with a start time, an end time and text.
Because of this interface, a company could switch providers without changing the rest of the code."

**[Open app/formats.py and app/cli.py]**
"formats.py converts the result into JSON, SRT or VTT subtitles, or readable text, so it is ready for downstream use. And cli.py is the simple script version."

## D. Demo of the script (3:40 – 4:20)  [Screen: terminal]
Run: `python -m app.cli samples/sample.mp3`
"Here I give it an MP3. It prints segments with timestamps."
Run: `python -m app.cli samples/sample.m4a --format srt`
"A different format, M4A, and this time as subtitles."
Run: `python -m app.cli samples/sample.wav --format json --chunk 5`
"I force a tiny chunk size to show long-audio handling. The log says three chunks, and the timestamps still run continuously."
Run: `python -m app.cli samples/corrupt.mp3`
"A corrupt file gives a clear error straight away."
(Optional, if you installed it: `python -m app.cli your_recording.mp3 --engine whisper` "and here is real speech with the real engine.")

## E. Part 2: the service (4:20 – 5:40)

**[Open app/main.py]**
"The API has endpoints to upload, check status, download the transcript, list, retry, and check health. Upload returns 202, meaning accepted and still processing."

**[Open app/storage.py]**
"Uploads are streamed to disk in one-megabyte chunks and renamed when complete, so no half-written files. I also calculate a SHA-256 fingerprint to detect duplicates."

**[Open app/db.py, scroll to claim_next]**
"The database table is also the job queue, with statuses QUEUED, PROCESSING, COMPLETED and FAILED. claim_next marks a job as processing in one atomic transaction,
so two workers can never take the same job."

**[Open app/worker.py]**
"Four worker threads loop: claim a job, run the pipeline, and handle the result. A temporary error is retried with exponential backoff and random jitter.
A permanent error fails immediately. After the maximum attempts the job is marked FAILED and can be retried with an endpoint.
If the server crashes, jobs are still in the database, and on restart stuck jobs are put back in the queue."

**[Browser: /docs]**
"Quick demo: I upload sample.mp3 and get 202 and a job ID. The status becomes COMPLETED with the segments. Now flaky.mp3: in the terminal you can see two failures
with longer waits, then success. And uploading the same file again returns the existing job, so nothing is transcribed twice."

## F. Answers to the design questions (5:40 – 6:30)  [Screen: README answers section]
"Concurrent uploads: each upload streams to disk in its own thread, the slow work happens in background workers, and jobs are claimed atomically.
Storage: audio in blob storage, S3 in production, transcripts as JSON, and metadata in the database with pointers to the files.
Retries: transient errors retry with backoff, permanent errors fail fast, and crashed jobs are recovered from the database.
API: REST with submit-then-poll, proper status codes and versioning."

## G. Trade-offs and closing (6:30 – 7:00)
"My trade-offs: I used SQLite and local disk to keep it easy to run; in production I would use PostgreSQL, S3 and a message queue, and add authentication.
Chunks are processed one by one, and a failed job restarts from the beginning; I would add per-chunk checkpoints and parallel chunks.
I wrote 17 automated tests, including format conversion, splitting at silence, and 20 parallel uploads. One honest note: I tested the real Whisper engine
only through the shared interface and mock; <SAY WHAT YOU PERSONALLY TESTED>. The README has all the details. Thank you for watching."

---

## Follow-up questions to be ready for
- **Why convert to WAV first?** One standard input for every engine; the rest of the code never worries about formats.
- **Why split long audio?** Memory and time limits, API file-size limits, and failures only cost one piece (with checkpoints). Cut at silence so words are not sliced.
- **How do timestamps stay correct after splitting?** Each chunk's start offset is added to its segment times.
- **Why a database as the queue?** Survives crashes, no extra setup. At scale: Postgres SKIP LOCKED or SQS/RabbitMQ.
- **Why backoff and jitter?** Gives the provider time to recover, and stops retries from all landing together.
- **Main weaknesses?** Single machine, no authentication, chunks sequential, no speaker identification. All listed in the README.
