# Beginner's guide: what the company expects and how to submit

## 1. What are they really testing?
The problem statement says it directly: *"The focus is on engineering decisions, not training a model from scratch."*
They are not checking whether you can build speech recognition. They are checking whether you can **think like an engineer**:

| They look for | What it means | Where it is in this project |
|---|---|---|
| A working end-to-end result | Audio goes in, timestamped text comes out | `python -m app.cli samples/sample.mp3` |
| Handling real-world mess | Different formats, very long files, bad files | `app/audio.py`, `app/pipeline.py` |
| Sensible use of existing tools | Do not reinvent speech recognition: use Whisper and ffmpeg | `app/transcriber.py` |
| Thinking about failure | Retries, corrupt files, crashes | `app/worker.py`, `app/db.py` |
| Scale thinking (Part 2) | Many users at once, storage, API design | README answers |
| Clear communication | README, video, explaining *why*, not only *what* | README + your video |
| Honesty about trade-offs | Knowing what is simplified and what you would improve | README "Known limitations" |

## 2. How to answer (the pattern interviewers like)
For every decision, say four things: **the problem, what I chose, why, and what the trade-off is**.
Example: "Long audio can exhaust memory (problem), so I split it into ten-minute pieces (choice) at quiet moments so words are not cut (why).
The trade-off is that pieces run one after another; I would add parallel processing later."
Do not say "it just works". Do say "I chose X over Y because...".

## 3. Be honest and be able to explain it
This project was built with an AI assistant's help. Check the assignment's rules about AI tools; if it is not mentioned and you are asked, say so plainly.
Either way, **you must understand what you submit**: the video asks you to explain the code, and interviewers often ask follow-ups.
Spend a couple of hours: run everything, read each file in the order of VIDEO_SCRIPT.md, and use the glossary below. If something in the script is
not true for you (for example you did not test real Whisper), say what you actually did. Replace the `<...>` placeholders in the script.

## 4. Set up and run (about 20 minutes)
1. Install Python (python.org, version 3.10 or newer; on Windows tick "Add Python to PATH").
2. Install ffmpeg (Windows: open PowerShell, `winget install ffmpeg`; Mac: `brew install ffmpeg`).
3. Unzip the project, open a terminal in the folder, then follow "Quick start" in README.md.
4. Run `python -m unittest tests.test_core -v`: all tests should say OK.
5. Try real transcription: `pip install faster-whisper`, record a 20-second voice memo on your phone, send it to your computer, run
   `python -m app.cli memo.m4a --engine whisper --format srt`. Showing real words in the video is far more convincing than the mock output.
If any step errors, copy the error message to me and I will help fix it.

## 5. Put the code on GitHub
1. Create an account at github.com; click **New repository**; name it `audio-transcription-service`; leave "Add a README" **unticked**.
2. In the project folder terminal:
   ```bash
   git init
   git add .
   git commit -m "Audio transcription pipeline and service"
   git branch -M main
   git remote add origin https://github.com/<your-username>/audio-transcription-service.git
   git push -u origin main
   ```
3. Open the link in a private window to check it is visible; paste it in the form.

## 6. Record the video
Follow VIDEO_SCRIPT.md; rehearse once. Save as `.mp4` (under 100 MB). 6 to 7 minutes is fine for "short" given two parts; trim to ~5 if you can.

## 7. Fill the Google Form
| Form field | What to put |
|---|---|
| Part 1: five questions | Paste the matching answers from `ANSWERS_FOR_FORM.md` |
| Part 2: four questions | Paste the matching answers from `ANSWERS_FOR_FORM.md` |
| Source code in a Git repo | Your GitHub link |
| Video (1 file) | The `.mp4` |
| README (1 file) | `README.md` (it already contains all answers). The README upload says "up to 5 files" in one place: you may also add `ANSWERS_FOR_FORM.md` |

If a Part 1 box expects a link or a file instead of text, put the GitHub link and name the file (for example "app/pipeline.py") and say it is explained in the README.

## Glossary
- **Pipeline:** a chain of steps where each step's output feeds the next.
- **Segment:** one chunk of speech with a start time, end time and text.
- **ffmpeg:** a free tool that converts almost any audio/video format.
- **Whisper:** a free, open-source speech-to-text AI model from OpenAI. **faster-whisper** is a quick way to run it locally.
- **Chunk:** a piece of a long file.
- **API / endpoint:** a way for programs to talk to your service; one specific address of it.
- **Queue / worker:** a waiting line of jobs / a background helper that takes jobs from it.
- **Retry / backoff / jitter:** try again / wait longer each time / add randomness to the wait.
- **Transient vs permanent error:** temporary problem worth retrying / a problem retrying cannot fix.
- **Atomic:** all-or-nothing, so two workers cannot both take the same job.
- **Idempotent:** doing it twice has the same result as once.
- **Blob storage (S3):** a place to keep big files.
