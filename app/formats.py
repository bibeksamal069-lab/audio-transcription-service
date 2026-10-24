"""Turn a transcript result into formats other programs / people can use downstream."""


def _ts(seconds: float, sep: str) -> str:
    ms = int(round(seconds * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d}{sep}{ms:03d}"


def to_srt(result: dict) -> str:
    """SubRip subtitles: works in YouTube, VLC, video editors."""
    return "\n".join(f"{s['id']}\n{_ts(s['start'], ',')} --> {_ts(s['end'], ',')}\n{s['text']}\n"
                     for s in result["segments"])


def to_vtt(result: dict) -> str:
    """WebVTT subtitles: works in web browsers."""
    body = "\n".join(f"{_ts(s['start'], '.')} --> {_ts(s['end'], '.')}\n{s['text']}\n"
                     for s in result["segments"])
    return "WEBVTT\n\n" + body


def to_txt(result: dict) -> str:
    """Readable text, one line per segment: [00:00:05] text"""
    return "\n".join(f"[{_ts(s['start'], '.')[:8]}] {s['text']}" for s in result["segments"])
