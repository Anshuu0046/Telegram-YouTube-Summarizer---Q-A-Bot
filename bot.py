"""
Telegram YouTube Summarizer & Q&A Bot
Main entry point — handles all Telegram interactions with multi-user support,
caching, and bonus commands (/summary, /deepdive, /actionpoints).
"""

import logging
import hashlib
from telegram import Update, BotCommand
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from src.config import TELEGRAM_BOT_TOKEN, SUPPORTED_LANGUAGES, DEFAULT_LANGUAGE
from src.transcript import (
    extract_video_id,
    is_youtube_url,
    fetch_transcript,
    format_transcript,
    get_plain_transcript,
    get_transcript_stats,
)
from src.video_info import get_video_metadata
from src.summarizer import (
    generate_summary,
    generate_deepdive,
    generate_action_points,
    ask_question,
    detect_language_request,
    check_ollama_connection,
)

# ── Logging ───────────────────────────────────────────────────────────────────
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

# ── In-memory session store ──────────────────────────────────────────────────
# Session per chat_id: { transcript, title, video_id, language, chat_history }
user_sessions: dict[int, dict] = {}

# ── Smart transcript cache: { video_id: { transcript, title, metadata } } ────
# Avoids re-fetching the same video across users or re-sends
transcript_cache: dict[str, dict] = {}
MAX_CACHE_SIZE = 50  # Max cached videos


# ══════════════════════════════════════════════════════════════════════════════
#  COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start — welcome message."""
    welcome = (
        "👋 *Welcome to YouTube Summarizer Bot!*\n\n"
        "I'm your personal AI research assistant for YouTube videos.\n\n"
        "🔹 *How to use:*\n"
        "1️⃣ Send me a YouTube link → I'll summarize it\n"
        "2️⃣ Ask follow-up questions about the video\n"
        "3️⃣ Use /language to switch response language\n"
        "4️⃣ Or just say _\"Summarize in Hindi\"_ inline!\n\n"
        "📋 *Special Commands:*\n"
        "/summary — Quick structured summary\n"
        "/deepdive — In-depth analysis\n"
        "/actionpoints — Actionable takeaways\n\n"
        "🌐 *Languages:* English • Hindi • Kannada • Tamil\n\n"
        "📎 Just paste a YouTube URL to get started!"
    )
    await update.message.reply_text(welcome, parse_mode="Markdown")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help — show available commands."""
    help_text = (
        "📖 *Available Commands:*\n\n"
        "🔗 *Video Analysis:*\n"
        "/summary — Structured summary (key points, timestamps)\n"
        "/deepdive — In-depth analysis with insights\n"
        "/actionpoints — Actionable takeaways & to-dos\n\n"
        "⚙️ *Settings:*\n"
        "/language <lang> — Set response language\n"
        "  ↳ Options: `english`, `hindi`, `kannada`, `tamil`\n"
        "/clear — Clear current video session\n"
        "/status — Check bot & Ollama status\n\n"
        "💡 *Tips:*\n"
        "• Send any YouTube link to get a summary\n"
        "• After a summary, just type your question!\n"
        "• Say _\"Explain in Hindi\"_ for inline language switch\n"
        "• Ask multiple follow-up questions — I remember context!\n"
        "• Use /deepdive for detailed analysis after loading a video"
    )
    await update.message.reply_text(help_text, parse_mode="Markdown")


async def language_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /language <lang> — set preferred language."""
    chat_id = update.effective_chat.id

    if not context.args:
        current = _get_session(chat_id).get("language", DEFAULT_LANGUAGE)
        lang_list = "\n".join(
            f"  • `{key}` — {val}" for key, val in SUPPORTED_LANGUAGES.items()
        )
        await update.message.reply_text(
            f"🌐 *Current language:* `{current}`\n\n"
            f"*Available languages:*\n{lang_list}\n\n"
            f"Usage: `/language hindi`",
            parse_mode="Markdown",
        )
        return

    lang = context.args[0].lower().strip()
    if lang not in SUPPORTED_LANGUAGES:
        await update.message.reply_text(
            f"❌ Unsupported language: `{lang}`\n"
            f"Supported: {', '.join(SUPPORTED_LANGUAGES.keys())}",
            parse_mode="Markdown",
        )
        return

    session = _get_session(chat_id)
    session["language"] = lang
    user_sessions[chat_id] = session
    await update.message.reply_text(
        f"✅ Language set to *{SUPPORTED_LANGUAGES[lang]}*\n"
        f"All future responses will be in {SUPPORTED_LANGUAGES[lang]}.",
        parse_mode="Markdown",
    )


async def clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clear — reset current video session."""
    chat_id = update.effective_chat.id
    lang = DEFAULT_LANGUAGE
    if chat_id in user_sessions:
        lang = user_sessions[chat_id].get("language", DEFAULT_LANGUAGE)
    user_sessions[chat_id] = {"language": lang, "chat_history": []}
    await update.message.reply_text(
        "🗑 Session cleared!\n"
        "Send a new YouTube link to analyze another video."
    )


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /status — check bot and Ollama connectivity."""
    from src.config import OLLAMA_MODEL, OLLAMA_BASE_URL

    status_lines = ["📊 *Bot Status:*\n", "✅ Bot is running"]

    # Ollama check
    ollama_status = check_ollama_connection()
    if ollama_status["connected"]:
        status_lines.append(f"✅ Ollama connected at `{OLLAMA_BASE_URL}`")
        if ollama_status["models"]:
            models_str = ", ".join(ollama_status["models"][:5])
            status_lines.append(f"📦 Models: {models_str}")
        status_lines.append(f"🎯 Active model: `{OLLAMA_MODEL}`")
    else:
        status_lines.append(f"❌ Ollama not reachable: {ollama_status['error']}")
        status_lines.append("ℹ️ Run: `ollama serve`")

    # Cache info
    status_lines.append(f"\n📦 Cached videos: {len(transcript_cache)}/{MAX_CACHE_SIZE}")

    # Session info
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    lang = SUPPORTED_LANGUAGES.get(session.get("language", DEFAULT_LANGUAGE), "English")
    status_lines.append(f"🌐 Language: {lang}")
    if session.get("title"):
        status_lines.append(f"🎥 Active video: _{session['title']}_")
        history_len = len(session.get("chat_history", []))
        if history_len:
            status_lines.append(f"💬 Q&A exchanges: {history_len // 2}")

    await update.message.reply_text("\n".join(status_lines), parse_mode="Markdown")


# ══════════════════════════════════════════════════════════════════════════════
#  BONUS COMMAND HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def summary_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /summary — regenerate summary for current video."""
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)

    if not session.get("transcript"):
        await update.message.reply_text(
            "🔗 *No video loaded!*\n\n"
            "Send a YouTube link first, then use /summary.",
            parse_mode="Markdown",
        )
        return

    language = session.get("language", DEFAULT_LANGUAGE)
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    title = session.get("title", "Unknown")

    processing_msg = await update.message.reply_text(
        f"📝 Regenerating summary in {lang_name}…"
    )

    summary = generate_summary(session["transcript"], title, lang_name)

    try:
        await processing_msg.edit_text(
            f"📝 *Summary — {_escape_md(title)}*\n\n{summary}",
            parse_mode="Markdown",
        )
    except Exception:
        await processing_msg.edit_text(f"📝 Summary — {title}\n\n{summary}")


async def deepdive_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deepdive — in-depth analysis of current video."""
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)

    if not session.get("transcript"):
        await update.message.reply_text(
            "🔗 *No video loaded!*\n\n"
            "Send a YouTube link first, then use /deepdive.",
            parse_mode="Markdown",
        )
        return

    language = session.get("language", DEFAULT_LANGUAGE)
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    title = session.get("title", "Unknown")

    processing_msg = await update.message.reply_text(
        f"🔬 Generating deep-dive analysis in {lang_name}… This may take a moment."
    )

    analysis = generate_deepdive(session["transcript"], title, lang_name)

    await processing_msg.delete()
    await _send_long_message(update, f"🔬 *Deep Dive — {_escape_md(title)}*\n\n{analysis}")


async def actionpoints_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /actionpoints — extract actionable takeaways."""
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)

    if not session.get("transcript"):
        await update.message.reply_text(
            "🔗 *No video loaded!*\n\n"
            "Send a YouTube link first, then use /actionpoints.",
            parse_mode="Markdown",
        )
        return

    language = session.get("language", DEFAULT_LANGUAGE)
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")
    title = session.get("title", "Unknown")

    processing_msg = await update.message.reply_text(
        f"✅ Extracting action points in {lang_name}…"
    )

    actions = generate_action_points(session["transcript"], title, lang_name)

    await processing_msg.delete()
    await _send_long_message(update, f"✅ *Action Points — {_escape_md(title)}*\n\n{actions}")


# ══════════════════════════════════════════════════════════════════════════════
#  MESSAGE HANDLERS
# ══════════════════════════════════════════════════════════════════════════════

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Route incoming messages: YouTube URL → summarize, otherwise → Q&A."""
    if not update.message or not update.message.text:
        return

    text = update.message.text.strip()
    chat_id = update.effective_chat.id

    # Check for inline language request
    detected_lang = detect_language_request(text)
    if detected_lang:
        session = _get_session(chat_id)
        session["language"] = detected_lang
        user_sessions[chat_id] = session

    # Route: YouTube URL or question
    video_id = extract_video_id(text)
    if video_id:
        await handle_youtube_link(update, context, text, video_id)
    elif is_youtube_url(text):
        await update.message.reply_text(
            "⚠️ That looks like a YouTube link, but I couldn't extract the video ID.\n\n"
            "Please send a valid YouTube URL, for example:\n"
            "`https://www.youtube.com/watch?v=VIDEO_ID`",
            parse_mode="Markdown",
        )
    else:
        await handle_question(update, context, text)


async def handle_youtube_link(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    url: str,
    video_id: str,
) -> None:
    """Process a YouTube link: fetch transcript (with caching) → generate summary."""
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)
    language = session.get("language", DEFAULT_LANGUAGE)
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")

    # Reset chat history for new video
    session["chat_history"] = []

    # Step 1: Acknowledge
    processing_msg = await update.message.reply_text(
        "⏳ Processing your video… This may take a moment.\n\n"
        "📥 Fetching transcript & metadata…"
    )

    # Step 2: Check cache first
    cached = transcript_cache.get(video_id)
    if cached:
        logger.info(f"Cache HIT for video {video_id}")
        segments = cached["segments"]
        title = cached["title"]
        channel = cached.get("channel", "")
        duration_str = cached.get("duration_str", "")
        plain_transcript = cached["plain_transcript"]
        stats = cached["stats"]
    else:
        logger.info(f"Cache MISS for video {video_id} — fetching fresh")

        # Fetch metadata
        metadata = get_video_metadata(url)
        title = metadata["title"] if metadata else "Unknown Video"
        channel = metadata.get("channel", "") if metadata else ""
        duration_str = metadata.get("duration_str", "") if metadata else ""

        # Fetch transcript
        transcript_result = fetch_transcript(video_id)
        if not transcript_result["success"]:
            await processing_msg.edit_text(
                f"❌ *Could not fetch transcript*\n\n"
                f"{transcript_result['error']}\n\n"
                f"Please try another video.",
                parse_mode="Markdown",
            )
            return

        segments = transcript_result["segments"]
        stats = get_transcript_stats(segments)
        plain_transcript = get_plain_transcript(segments)

        # Cache the result
        _cache_transcript(video_id, segments, plain_transcript, title, channel, duration_str, stats)

    # Step 3: Update status
    is_long = stats["char_count"] > 15000
    long_note = "\n📏 Long transcript — summarizing key parts." if is_long else ""

    await processing_msg.edit_text(
        f"📥 Transcript ready!\n"
        f"   📊 {stats['word_count']} words • {stats['segment_count']} segments\n"
        f"{long_note}\n"
        f"🤖 Generating summary in {lang_name}… Please wait."
    )

    # Step 4: Generate summary
    summary = generate_summary(plain_transcript, title, lang_name)

    # Step 5: Build response
    header_parts = [f"🎥 *{_escape_md(title)}*"]
    if channel:
        header_parts.append(f"📺 {_escape_md(channel)}")
    if duration_str:
        header_parts.append(f"⏱ Duration: {duration_str}")
    header_parts.append(f"🌐 Language: {lang_name}")
    header = "\n".join(header_parts)

    full_response = f"{header}\n\n{'─' * 30}\n\n{summary}"

    # Step 6: Save session
    session["transcript"] = plain_transcript
    session["title"] = title
    session["video_id"] = video_id
    user_sessions[chat_id] = session

    # Step 7: Send response
    await processing_msg.delete()
    await _send_long_message(update, full_response)

    # Step 8: Prompt for next steps
    await update.message.reply_text(
        "💬 _What would you like to do next?_\n\n"
        "• Ask a question about the video\n"
        "• /deepdive — In-depth analysis\n"
        "• /actionpoints — Actionable takeaways\n"
        "• /summary — Regenerate summary\n"
        "• Say _\"Explain in Hindi\"_ to switch language",
        parse_mode="Markdown",
    )


async def handle_question(
    update: Update, context: ContextTypes.DEFAULT_TYPE, question: str
) -> None:
    """Handle a follow-up question about the current video."""
    chat_id = update.effective_chat.id
    session = _get_session(chat_id)

    if not session.get("transcript"):
        await update.message.reply_text(
            "🔗 *No video loaded!*\n\n"
            "Please send a YouTube link first.\n"
            "I need a video transcript to answer questions about.\n\n"
            "Example: `https://www.youtube.com/watch?v=...`",
            parse_mode="Markdown",
        )
        return

    language = session.get("language", DEFAULT_LANGUAGE)
    lang_name = SUPPORTED_LANGUAGES.get(language, "English")

    # Acknowledge
    thinking_msg = await update.message.reply_text(
        f"🤔 Thinking… (answering in {lang_name})"
    )

    # Get chat history for context continuity
    chat_history = session.get("chat_history", [])

    # Generate answer
    answer = ask_question(session["transcript"], question, lang_name, chat_history)

    # Store in chat history
    chat_history.append({"role": "user", "content": question})
    chat_history.append({"role": "assistant", "content": answer})
    session["chat_history"] = chat_history
    user_sessions[chat_id] = session

    try:
        await thinking_msg.edit_text(
            f"💡 *Answer:*\n\n{answer}",
            parse_mode="Markdown",
        )
    except Exception:
        await thinking_msg.edit_text(f"💡 Answer:\n\n{answer}")


# ══════════════════════════════════════════════════════════════════════════════
#  HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def _get_session(chat_id: int) -> dict:
    """Get or create a per-user session."""
    if chat_id not in user_sessions:
        user_sessions[chat_id] = {
            "language": DEFAULT_LANGUAGE,
            "chat_history": [],
        }
    return user_sessions[chat_id]


def _cache_transcript(
    video_id: str,
    segments: list[dict],
    plain_transcript: str,
    title: str,
    channel: str,
    duration_str: str,
    stats: dict,
) -> None:
    """Cache a transcript for smart reuse. Evicts oldest if cache is full."""
    global transcript_cache
    if len(transcript_cache) >= MAX_CACHE_SIZE:
        # Evict oldest entry (FIFO)
        oldest_key = next(iter(transcript_cache))
        del transcript_cache[oldest_key]
        logger.info(f"Cache evicted: {oldest_key}")

    transcript_cache[video_id] = {
        "segments": segments,
        "plain_transcript": plain_transcript,
        "title": title,
        "channel": channel,
        "duration_str": duration_str,
        "stats": stats,
    }
    logger.info(f"Cached transcript for {video_id} ({len(transcript_cache)}/{MAX_CACHE_SIZE})")


def _escape_md(text: str) -> str:
    """Escape special Markdown characters for Telegram."""
    for ch in ("_", "*", "[", "]", "(", ")", "~", "`", ">", "#", "+", "-", "=", "|", "{", "}", ".", "!"):
        text = text.replace(ch, f"\\{ch}")
    return text


async def _send_long_message(update: Update, text: str, max_len: int = 4000) -> None:
    """Send a message, splitting into chunks if it exceeds Telegram's 4096 char limit."""
    if len(text) <= max_len:
        try:
            await update.message.reply_text(text, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(text)
        return

    chunks = []
    current = ""
    for line in text.split("\n"):
        if len(current) + len(line) + 1 > max_len:
            chunks.append(current)
            current = line
        else:
            current = f"{current}\n{line}" if current else line
    if current:
        chunks.append(current)

    for chunk in chunks:
        try:
            await update.message.reply_text(chunk, parse_mode="Markdown")
        except Exception:
            await update.message.reply_text(chunk)


async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Global error handler for unhandled exceptions."""
    logger.error(f"Exception while handling update: {context.error}", exc_info=context.error)
    if isinstance(update, Update) and update.message:
        await update.message.reply_text(
            "⚠️ An unexpected error occurred. Please try again.\n"
            "If the problem persists, use /clear and send the link again."
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ══════════════════════════════════════════════════════════════════════════════

async def post_init(application) -> None:
    """Set bot commands in the Telegram menu."""
    commands = [
        BotCommand("start", "Welcome & instructions"),
        BotCommand("help", "Show available commands"),
        BotCommand("summary", "Structured summary of current video"),
        BotCommand("deepdive", "In-depth video analysis"),
        BotCommand("actionpoints", "Actionable takeaways"),
        BotCommand("language", "Set response language"),
        BotCommand("clear", "Clear current video session"),
        BotCommand("status", "Check bot & Ollama status"),
    ]
    await application.bot.set_my_commands(commands)


def main() -> None:
    """Start the Telegram bot."""
    if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_token_here":
        print("=" * 60)
        print("ERROR: TELEGRAM_BOT_TOKEN not set!")
        print("1. Copy .env.example to .env")
        print("2. Add your bot token from @BotFather")
        print("=" * 60)
        return

    from src.config import OLLAMA_MODEL, OLLAMA_BASE_URL

    print("🚀 Starting YouTube Summarizer Bot…")
    print(f"   Model  : {OLLAMA_MODEL}")
    print(f"   Ollama : {OLLAMA_BASE_URL}")

    # Pre-check Ollama
    ollama_status = check_ollama_connection()
    if ollama_status["connected"]:
        print(f"   Status : ✅ Ollama connected ({len(ollama_status['models'])} models)")
    else:
        print(f"   Status : ⚠️  Ollama not reachable — start it with: ollama serve")

    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).post_init(post_init).build()

    # Register command handlers
    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("summary", summary_command))
    app.add_handler(CommandHandler("deepdive", deepdive_command))
    app.add_handler(CommandHandler("actionpoints", actionpoints_command))
    app.add_handler(CommandHandler("language", language_command))
    app.add_handler(CommandHandler("clear", clear_command))
    app.add_handler(CommandHandler("status", status_command))

    # Message handler for URLs and questions
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    # Global error handler
    app.add_error_handler(error_handler)

    print("✅ Bot is running! Press Ctrl+C to stop.\n")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
