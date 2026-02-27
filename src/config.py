"""
Configuration module — loads environment variables and defines constants.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Telegram ──────────────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")

# ── Ollama ────────────────────────────────────────────────────────────────────
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")

# ── Supported Languages ──────────────────────────────────────────────────────
SUPPORTED_LANGUAGES = {
    "english": "English",
    "hindi": "हिन्दी (Hindi)",
    "kannada": "ಕನ್ನಡ (Kannada)",
    "tamil": "தமிழ் (Tamil)",
}

DEFAULT_LANGUAGE = "english"
