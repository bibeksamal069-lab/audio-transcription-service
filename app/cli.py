"""Command-line script (Part 1, no server needed):

    python -m app.cli recording.mp3 --engine whisper --format srt
"""
import argparse
import json
import logging
import sys

from .formats import to_srt, to_txt, to_vtt
from .pipeline import run_pipeline
from .transcriber import PermanentError, build_transcriber


def main(argv=None):
    p = argparse.ArgumentParser(description="Transcribe an audio file with per-segment timestamps")
    p.add_argument("audio", help="path to an audio file (mp3, wav, m4a, flac, ogg, ...)")
    p.add_argument("--engine", default="mock", choices=["mock", "whisper", "openai"])
    p.add_argument("--model", default="base", help="whisper model size: tiny/base/small/medium/large-v3")
    p.add_argument("--chunk", type=int, default=600, help="split audio longer than this many seconds")
    p.add_argument("--format", default="txt", choices=["txt", "json", "srt", "vtt"])
    p.add_argument("--out", help="write to this file instead of the screen")
    a = p.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s", stream=sys.stderr)
    try:
        result = run_pipeline(a.audio, build_transcriber(a.engine, a.model), a.chunk, a.audio)
    except PermanentError as e:
        sys.exit(f"Error: {e}")
    text = {"txt": to_txt, "srt": to_srt, "vtt": to_vtt,
            "json": lambda r: json.dumps(r, indent=2, ensure_ascii=False)}[a.format](result)
    if a.out:
        open(a.out, "w", encoding="utf-8").write(text)
    else:
        print(text)


if __name__ == "__main__":
    main()
