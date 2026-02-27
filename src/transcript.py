"""
YouTube Transcript Fetcher — extracts video ID, fetches and processes transcripts.
Handles: invalid links, missing transcripts, long transcripts, and errors gracefully.
"""

import re
import logging
from youtube_transcript_api import YouTubeTranscriptApi

logger = logging.getLogger(__name__)

# ── Maximum transcript length before chunking (characters) ────────────────────
MAX_TRANSCRIPT_CHARS = 15000
CHUNK_SIZE = 4000  # Characters per chunk for processing


def extract_video_id(url: str) -> str | None:
    """
    Extract the YouTube video ID from various URL formats.
    Supports:
      - https://www.youtube.com/watch?v=VIDEO_ID
      - https://youtu.be/VIDEO_ID
      - https://www.youtube.com/embed/VIDEO_ID
      - https://www.youtube.com/shorts/VIDEO_ID
      - https://www.youtube.com/live/VIDEO_ID
    Returns None if no valid ID is found.
    """
    if not url or not isinstance(url, str):
        return None

    # Clean URL
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
    """Check if text contains a YouTube URL (even if mixed with other text)."""
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
    Fetch the transcript for a YouTube video.
    Returns a dict with:
      - "success": bool
      - "segments": list of {text, start, duration} (if success)
      - "error": str (if failure)
      - "is_long": bool (if transcript exceeds MAX_TRANSCRIPT_CHARS)
    """
    result = {"success": False, "segments": [], "error": "", "is_long": False}

    if not video_id:
        result["error"] = "Invalid video ID."
        return result

    try:
        ytt_api = YouTubeTranscriptApi()
        transcript = ytt_api.fetch(video_id)
        segments = []
        total_chars = 0
        for entry in transcript:
            seg = {
                "text": entry.text,
                "start": entry.start,
                "duration": entry.duration,
            }
            segments.append(seg)
            total_chars += len(entry.text)

        if not segments:
            result["error"] = "Transcript is empty — the video may have no spoken content."
            return result

        result["success"] = True
        result["segments"] = segments
        result["is_long"] = total_chars > MAX_TRANSCRIPT_CHARS
        logger.info(
            f"Fetched transcript for {video_id}: {len(segments)} segments, "
            f"{total_chars} chars, is_long={result['is_long']}"
        )
        return result

    except Exception as e:
        error_str = str(e).lower()
        if "no transcript" in error_str or "could not retrieve" in error_str:
            result["error"] = (
                "No transcript available for this video.\n"
                "The video may not have captions or subtitles enabled."
            )
        elif "video unavailable" in error_str or "not available" in error_str:
            result["error"] = "This video is unavailable, private, or age-restricted."
        elif "too many requests" in error_str:
            result["error"] = "Too many requests — please try again in a moment."
        else:
            result["error"] = f"Could not fetch transcript: {e}"
            logger.error(f"Transcript fetch error for {video_id}: {e}")
        return result


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
    """
    Split a long transcript into chunks for processing.
    Splits at sentence boundaries when possible.
    """
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
