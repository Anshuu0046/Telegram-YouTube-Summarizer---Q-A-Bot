"""
Summarizer & Q&A — uses Ollama for LLM-powered summarization, deep-dive analysis,
action points extraction, question answering, and inline language detection.
"""

import re
import logging
import ollama
from src.config import OLLAMA_MODEL, OLLAMA_BASE_URL, SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)


def _get_client() -> ollama.Client:
    """Create an Ollama client pointing at the configured base URL."""
    return ollama.Client(host=OLLAMA_BASE_URL)


def _truncate_transcript(transcript: str, max_chars: int = 12000) -> str:
    """Truncate transcript to fit within model context limits."""
    if len(transcript) <= max_chars:
        return transcript
    return transcript[:max_chars] + "\n\n[... transcript truncated for length ...]"


def _build_language_instruction(language: str) -> str:
    """Build language instruction for prompts."""
    if language.lower() == "english":
        return "Respond entirely in English."
    return (
        f"Respond entirely in {language}. "
        f"Use the {language} script/alphabet. "
        f"Only use English for proper nouns or technical terms that have no translation."
    )


def _call_ollama(messages: list[dict], max_tokens: int = 2048) -> str:
    """Central Ollama call with unified error handling."""
    try:
        client = _get_client()
        response = client.chat(
            model=OLLAMA_MODEL,
            messages=messages,
            options={"temperature": 0.3, "num_predict": max_tokens},
        )
        return response.message.content
    except ollama.ResponseError as e:
        logger.error(f"Ollama response error: {e}")
        return f"❌ Ollama model error: {e}"
    except Exception as e:
        logger.error(f"Ollama call error: {e}")
        if "refused" in str(e).lower() or "connect" in str(e).lower():
            return (
                "❌ Cannot connect to Ollama.\n\n"
                "Make sure Ollama is running:\n"
                "`ollama serve`"
            )
        return f"❌ Error: {e}"


def detect_language_request(text: str) -> str | None:
    """
    Detect if the user is requesting a response in a specific language.
    Handles patterns like "summarize in hindi", "explain in kannada", "hindi me batao".
    """
    text_lower = text.lower().strip()
    for lang_key in SUPPORTED_LANGUAGES:
        patterns = [
            rf"\b(?:in|to)\s+{lang_key}\b",
            rf"\b{lang_key}\s+(?:me|mein|main|m[eè])\b",
            rf"\b(?:summarize|summary|explain|translate|answer)\b.*\b{lang_key}\b",
            rf"\b{lang_key}\b.*\b(?:summarize|summary|explain|translate|answer)\b",
        ]
        for pattern in patterns:
            if re.search(pattern, text_lower):
                return lang_key
    return None


# ══════════════════════════════════════════════════════════════════════════════
#  SUMMARY MODE — /summary (default when sending a link)
# ══════════════════════════════════════════════════════════════════════════════

def generate_summary(
    transcript_text: str,
    video_title: str,
    language: str = "English",
) -> str:
    """Generate a structured summary with key points, timestamps, and takeaway."""
    truncated = _truncate_transcript(transcript_text)
    lang_instruction = _build_language_instruction(language)

    prompt = f"""You are an expert video content analyst. Analyze the following YouTube video transcript and provide a structured summary.

Video Title: {video_title}

{lang_instruction}

Provide your response in EXACTLY this format (keep the emoji prefixes):

📌 **5 Key Points:**
1. [First key point]
2. [Second key point]
3. [Third key point]
4. [Fourth key point]
5. [Fifth key point]

⏱ **Important Timestamps:**
- [Timestamp] — [What happens at this point]
- [Timestamp] — [What happens at this point]
- [Timestamp] — [What happens at this point]
(Include 3-5 important timestamps from the transcript)

🧠 **Core Takeaway:**
[A 2-3 sentence summary of the most important insight from the video]

---

TRANSCRIPT:
{truncated}
"""

    return _call_ollama([
        {
            "role": "system",
            "content": (
                "You are a helpful video summarization assistant. "
                "Provide clear, structured, accurate summaries. "
                "Follow the exact format requested. "
                "Base your summary ONLY on the transcript — do not add external information."
            ),
        },
        {"role": "user", "content": prompt},
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  DEEP DIVE MODE — /deepdive
# ══════════════════════════════════════════════════════════════════════════════

def generate_deepdive(
    transcript_text: str,
    video_title: str,
    language: str = "English",
) -> str:
    """Generate an in-depth analysis of the video content."""
    truncated = _truncate_transcript(transcript_text)
    lang_instruction = _build_language_instruction(language)

    prompt = f"""You are an expert content analyst. Provide a deep-dive analysis of this YouTube video transcript.

Video Title: {video_title}

{lang_instruction}

Provide your response in EXACTLY this format:

🔬 **Deep Dive Analysis**

📖 **Detailed Summary:**
[A comprehensive 5-8 sentence summary covering all major topics discussed]

🎯 **Main Arguments/Claims:**
1. [First argument with supporting evidence from the video]
2. [Second argument with supporting evidence]
3. [Third argument with supporting evidence]

💡 **Key Insights & Nuances:**
- [Insight 1 — something non-obvious from the content]
- [Insight 2 — a subtle point worth noting]
- [Insight 3 — a connection or implication]

👥 **Target Audience:**
[Who would benefit most from this video and why]

📊 **Content Quality Assessment:**
[Brief assessment of the depth, accuracy, and value of the content]

---

TRANSCRIPT:
{truncated}
"""

    return _call_ollama([
        {
            "role": "system",
            "content": (
                "You are an expert content analyst providing deep-dive analysis. "
                "Be thorough, insightful, and analytical. "
                "Base everything ONLY on the transcript provided."
            ),
        },
        {"role": "user", "content": prompt},
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  ACTION POINTS MODE — /actionpoints
# ══════════════════════════════════════════════════════════════════════════════

def generate_action_points(
    transcript_text: str,
    video_title: str,
    language: str = "English",
) -> str:
    """Extract actionable takeaways from the video."""
    truncated = _truncate_transcript(transcript_text)
    lang_instruction = _build_language_instruction(language)

    prompt = f"""You are a productivity expert. Extract clear, actionable takeaways from this YouTube video transcript.

Video Title: {video_title}

{lang_instruction}

Provide your response in EXACTLY this format:

✅ **Action Points from This Video**

🔥 **Immediate Actions (Do Today):**
1. [Specific, concrete action the viewer can take right away]
2. [Another immediate action]

📋 **Short-term Actions (This Week):**
1. [Action to implement this week]
2. [Another short-term action]

🎯 **Long-term Goals (Ongoing):**
1. [Strategic goal inspired by the video]
2. [Another long-term goal]

⚠️ **Mistakes to Avoid:**
- [Common pitfall mentioned in the video]
- [Another mistake to watch out for]

📚 **Resources Mentioned:**
- [Any tools, books, websites, or resources mentioned in the video]
- [If none were mentioned, write "None specifically mentioned"]

---

TRANSCRIPT:
{truncated}
"""

    return _call_ollama([
        {
            "role": "system",
            "content": (
                "You are a productivity expert extracting actionable insights from videos. "
                "Make actions SPECIFIC and PRACTICAL, not vague. "
                "Base everything ONLY on the transcript provided."
            ),
        },
        {"role": "user", "content": prompt},
    ])


# ══════════════════════════════════════════════════════════════════════════════
#  Q&A MODE
# ══════════════════════════════════════════════════════════════════════════════

def ask_question(
    transcript_text: str,
    question: str,
    language: str = "English",
    chat_history: list[dict] | None = None,
) -> str:
    """Answer a user's question based on the video transcript context."""
    truncated = _truncate_transcript(transcript_text)
    lang_instruction = _build_language_instruction(language)

    system_prompt = (
        "You are a helpful Q&A assistant for YouTube videos. "
        "You ONLY answer based on the provided transcript. "
        "Be accurate, concise, and never hallucinate or make up information. "
        "If the answer is not in the transcript, say so clearly."
    )

    context_prompt = f"""Answer the user's question about a YouTube video based ONLY on its transcript.

{lang_instruction}

Rules:
- Answer ONLY based on the information in the transcript below.
- If the answer is NOT found in the transcript, respond with: "⚠️ This topic is not covered in the video."
- Be concise and clear.
- If relevant, mention the approximate timestamp where the topic is discussed.
- Do NOT make up or hallucinate any information.

TRANSCRIPT:
{truncated}

QUESTION: {question}"""

    messages = [{"role": "system", "content": system_prompt}]

    # Add chat history for follow-up context (last 4 exchanges max)
    if chat_history:
        for entry in chat_history[-4:]:
            messages.append({"role": entry["role"], "content": entry["content"]})

    messages.append({"role": "user", "content": context_prompt})
    return _call_ollama(messages, max_tokens=1024)


# ══════════════════════════════════════════════════════════════════════════════
#  HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════

def check_ollama_connection() -> dict:
    """Check if Ollama is reachable and the configured model is available."""
    try:
        client = _get_client()
        models = client.list()
        model_names = [m.model for m in models.models]
        return {"connected": True, "models": model_names, "error": None}
    except Exception as e:
        return {"connected": False, "models": [], "error": str(e)}
