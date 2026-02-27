"""
YouTube Transcript Fetcher — extracts video ID, fetches and processes transcripts.
Uses yt-dlp for reliable transcript extraction (fallback from youtube-transcript-api).
Handles: invalid links, missing transcripts, long transcripts, and errors gracefully.
"""

import re
import os
import json
import tempfile
import logging
import yt_dlp

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────
MAX_TRANSCRIPT_CHARS = 15000
CHUNK_SIZE = 4000


def extract_video_id(url: str) -> str | None:
    """
    Extract the YouTube video ID from various URL formats.
    Supports: watch, youtu.be, embed, shorts, live URLs.
    Returns None if no valid ID is found.
    """
    if not url or not isinstance(url, str):
        return None

    url = url.strip()

    patterns = [
        r"(?:youtube\.com\/watch\?.*v=)([\w-]{11})",
        r"(?:youtu\.be\/)([\w-]{11})",
        r"(?:youtube\.com\/embed\/)([\w-]{11})",
        r"(?:youtube\.com\/shorts\/)([\w-]{11})",
        r"(?:youtube\.com\/live\/)([\w-]{11})",
    ]
    for pattern in patterns:
        match = re.search(pattern, url)
        if match:
            return match.group(1)
    return None


def is_youtube_url(text: str) -> bool:
    """Check if text contains a YouTube URL."""
    youtube_patterns = [
        r"youtube\.com\/watch",
        r"youtu\.be\/",
        r"youtube\.com\/embed\/",
        r"youtube\.com\/shorts\/",
        r"youtube\.com\/live\/",
    ]
    return any(re.search(p, text) for p in youtube_patterns)


def fetch_transcript(video_id: str) -> dict:
    """
    Fetch the transcript for a YouTube video using yt-dlp.
    Returns a dict with:
      - "success": bool
      - "segments": list of {text, start, duration}
      - "error": str (if failure)
      - "is_long": bool
    """
    result = {"success": False, "segments": [], "error": "", "is_long": False}

    if not video_id:
        result["error"] = "Invalid video ID."
        return result

    url = f"https://www.youtube.com/watch?v={video_id}"

    try:
        # Use a temp directory for subtitle files
        with tempfile.TemporaryDirectory() as tmpdir:
            output_template = os.path.join(tmpdir, "%(id)s")

            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "skip_download": True,
                "writesubtitles": True,
                "writeautomaticsub": True,
                "subtitlesformat": "json3",
                "subtitleslangs": ["en", "en-US", "en-GB", "hi", "kn", "ta"],
                "outtmpl": output_template,
            }

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)

                if info is None:
                    result["error"] = "Could not fetch video information."
                    return result

                # Try to get subtitles: prefer manual, fallback to auto
                subs = info.get("subtitles", {})
                auto_subs = info.get("automatic_captions", {})

                # Priority: manual English > auto English > any manual > any auto
                subtitle_data = None
                lang_used = None

                for lang in ["en", "en-US", "en-GB", "hi", "kn", "ta"]:
                    # Check manual subs
                    if lang in subs:
                        subtitle_data = _fetch_subtitle_data(subs[lang])
                        if subtitle_data:
                            lang_used = lang
                            break
                    # Check auto captions
                    if lang in auto_subs:
                        subtitle_data = _fetch_subtitle_data(auto_subs[lang])
                        if subtitle_data:
                            lang_used = lang
                            break

                # If no English/Indian lang found, try any available
                if not subtitle_data:
                    for lang, formats in subs.items():
                        subtitle_data = _fetch_subtitle_data(formats)
                        if subtitle_data:
                            lang_used = lang
                            break

                if not subtitle_data:
                    for lang, formats in auto_subs.items():
                        subtitle_data = _fetch_subtitle_data(formats)
                        if subtitle_data:
                            lang_used = lang
                            break

                if not subtitle_data:
                    result["error"] = (
                        "No transcript available for this video.\n"
                        "The video may not have captions or subtitles enabled."
                    )
                    return result

                # Parse segments
                segments = _parse_subtitle_data(subtitle_data)

                if not segments:
                    result["error"] = "Transcript is empty — the video may have no spoken content."
                    return result

                total_chars = sum(len(s["text"]) for s in segments)
                result["success"] = True
                result["segments"] = segments
                result["is_long"] = total_chars > MAX_TRANSCRIPT_CHARS
                logger.info(
                    f"Fetched transcript for {video_id} (lang={lang_used}): "
                    f"{len(segments)} segments, {total_chars} chars"
                )
                return result

    except Exception as e:
        error_str = str(e).lower()
        if "private" in error_str or "unavailable" in error_str:
            result["error"] = "This video is unavailable, private, or age-restricted."
        elif "too many requests" in error_str or "429" in error_str:
            result["error"] = "Too many requests — please try again in a moment."
        else:
            result["error"] = f"Could not fetch transcript: {e}"
            logger.error(f"Transcript fetch error for {video_id}: {e}")
        return result


def _fetch_subtitle_data(formats: list[dict]) -> dict | None:
    """Fetch subtitle data from available formats, preferring json3."""
    import requests

    # Prefer json3 format
    for fmt in formats:
        if fmt.get("ext") == "json3" and fmt.get("url"):
            try:
                resp = requests.get(fmt["url"], timeout=15)
                resp.raise_for_status()
                return resp.json()
            except Exception as e:
                logger.warning(f"Failed to fetch json3 subtitle: {e}")
                continue

    # Fallback: try srv3 or vtt
    for fmt in formats:
        if fmt.get("ext") in ("srv3", "vtt") and fmt.get("url"):
            try:
                resp = requests.get(fmt["url"], timeout=15)
                resp.raise_for_status()
                return {"raw_text": resp.text, "format": fmt["ext"]}
            except Exception as e:
                logger.warning(f"Failed to fetch {fmt['ext']} subtitle: {e}")
                continue

    return None


def _parse_subtitle_data(data: dict) -> list[dict]:
    """Parse subtitle data into segments [{text, start, duration}]."""

    # JSON3 format (from YouTube's timedtext API)
    if "events" in data:
        segments = []
        for event in data["events"]:
            if "segs" not in event:
                continue
            text = "".join(s.get("utf8", "") for s in event["segs"]).strip()
            text = text.replace("\n", " ").strip()
            if not text:
                continue
            start = event.get("tStartMs", 0) / 1000.0
            duration = event.get("dDurationMs", 0) / 1000.0
            segments.append({"text": text, "start": start, "duration": duration})
        return segments

    # Raw text fallback (VTT/SRV3) — basic line-by-line parsing
    if "raw_text" in data:
        raw = data["raw_text"]
        fmt = data.get("format", "vtt")

        if fmt == "vtt":
            return _parse_vtt(raw)
        else:
            # SRV3 is XML-based, try simple regex
            return _parse_srv3(raw)

    return []


def _parse_vtt(vtt_text: str) -> list[dict]:
    """Parse WebVTT subtitle text into segments."""
    segments = []
    lines = vtt_text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i].strip()
        # Look for timestamp lines: 00:00:00.000 --> 00:00:05.000
        ts_match = re.match(
            r"(\d{2}:\d{2}:\d{2}\.\d{3})\s*-->\s*(\d{2}:\d{2}:\d{2}\.\d{3})", line
        )
        if ts_match:
            start = _vtt_time_to_seconds(ts_match.group(1))
            end = _vtt_time_to_seconds(ts_match.group(2))
            duration = end - start

            # Collect text lines until blank line
            text_lines = []
            i += 1
            while i < len(lines) and lines[i].strip():
                text_lines.append(lines[i].strip())
                i += 1
            text = " ".join(text_lines)
            # Remove VTT tags like <c> </c>
            text = re.sub(r"<[^>]+>", "", text).strip()
            if text:
                segments.append({"text": text, "start": start, "duration": duration})
        i += 1
    return segments


def _parse_srv3(xml_text: str) -> list[dict]:
    """Parse SRV3 (XML) subtitle text into segments."""
    import xml.etree.ElementTree as ET

    segments = []
    try:
        root = ET.fromstring(xml_text)
        for elem in root.iter("p"):
            start_ms = int(elem.get("t", 0))
            dur_ms = int(elem.get("d", 0))
            text = (elem.text or "").strip()
            if text:
                segments.append({
                    "text": text,
                    "start": start_ms / 1000.0,
                    "duration": dur_ms / 1000.0,
                })
    except Exception as e:
        logger.warning(f"SRV3 parse error: {e}")
    return segments


def _vtt_time_to_seconds(time_str: str) -> float:
    """Convert VTT timestamp (HH:MM:SS.mmm) to seconds."""
    parts = time_str.split(":")
    hours = int(parts[0])
    minutes = int(parts[1])
    seconds = float(parts[2])
    return hours * 3600 + minutes * 60 + seconds


# ── Formatting utilities ─────────────────────────────────────────────────────

def format_timestamp(seconds: float) -> str:
    """Convert seconds to HH:MM:SS or MM:SS format."""
    total_seconds = int(seconds)
    hours = total_seconds // 3600
    minutes = (total_seconds % 3600) // 60
    secs = total_seconds % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_transcript(segments: list[dict]) -> str:
    """Join transcript segments into timestamped plain text: [MM:SS] text"""
    lines = []
    for seg in segments:
        ts = format_timestamp(seg["start"])
        lines.append(f"[{ts}] {seg['text']}")
    return "\n".join(lines)


def get_plain_transcript(segments: list[dict]) -> str:
    """Join transcript segments into plain text (no timestamps)."""
    return " ".join(seg["text"] for seg in segments)


def chunk_transcript(text: str, chunk_size: int = CHUNK_SIZE) -> list[str]:
    """Split a long transcript into chunks at sentence boundaries."""
    if len(text) <= chunk_size:
        return [text]

    chunks = []
    current = ""
    sentences = re.split(r'(?<=[.!?])\s+', text)

    for sentence in sentences:
        if len(current) + len(sentence) + 1 > chunk_size:
            if current:
                chunks.append(current.strip())
            current = sentence
        else:
            current = f"{current} {sentence}" if current else sentence

    if current.strip():
        chunks.append(current.strip())

    return chunks


def get_transcript_stats(segments: list[dict]) -> dict:
    """Get statistics about the transcript for display."""
    total_chars = sum(len(s["text"]) for s in segments)
    total_duration = max((s["start"] + s["duration"]) for s in segments) if segments else 0
    return {
        "segment_count": len(segments),
        "char_count": total_chars,
        "word_count": len(get_plain_transcript(segments).split()),
        "duration_str": format_timestamp(total_duration),
    }
