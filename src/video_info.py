"""
Video Info — fetches YouTube video metadata using yt-dlp (no download).
"""

import yt_dlp


def get_video_metadata(url: str) -> dict | None:
    """
    Fetch basic metadata for a YouTube video without downloading.
    Returns: {"title": ..., "channel": ..., "duration": ..., "duration_str": ...}
    Returns None on failure.
    """
    ydl_opts = {
        "quiet": True,
        "no_warnings": True,
        "skip_download": True,
        "extract_flat": False,
    }
    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            if info is None:
                return None
            duration = info.get("duration", 0)
            hours = duration // 3600
            minutes = (duration % 3600) // 60
            secs = duration % 60
            if hours > 0:
                duration_str = f"{hours}h {minutes}m {secs}s"
            elif minutes > 0:
                duration_str = f"{minutes}m {secs}s"
            else:
                duration_str = f"{secs}s"

            return {
                "title": info.get("title", "Unknown Title"),
                "channel": info.get("uploader", "Unknown Channel"),
                "duration": duration,
                "duration_str": duration_str,
            }
    except Exception as e:
        print(f"[Video Info Error] {e}")
        return None
