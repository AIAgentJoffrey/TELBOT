import os
import time
import logging
from typing import List, Dict

import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)


# === Environment variables ===
TG_BOT_TOKEN = os.getenv("TG_BOT_TOKEN")
SORA_API_BASE = os.getenv("SORA_API_BASE")
SORA_API_KEY = os.getenv("SORA_API_KEY")

# Check required variables
missing = [
    k
    for k, v in [
        ("TG_BOT_TOKEN", TG_BOT_TOKEN),
        ("SORA_API_BASE", SORA_API_BASE),
        ("SORA_API_KEY", SORA_API_KEY),
    ]
    if not v
]
if missing:
    raise SystemExit(f"Missing required env vars: {', '.join(missing)}")


# === Logging ===
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s"
)
log = logging.getLogger("video-bot")


# === Sora API client ===
def _headers() -> Dict[str, str]:
    """Build headers for Sora API requests."""
    return {"Authorization": f"Bearer {SORA_API_KEY}", "Content-Type": "application/json"}


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=6))
def create_video_job(
    script: str, assets: List[str], style: str, aspect: str, duration_sec: int
) -> str:
    """
    Submit a video generation job to the Sora API.

    Args:
        script: Narrative script to render.
        assets: List of asset URLs to include.
        style: Visual style (e.g. "luxury", "fun").
        aspect: Aspect ratio (e.g. "9:16").
        duration_sec: Length of the video in seconds.

    Returns:
        Job ID returned by the Sora API.

    Raises:
        RuntimeError: If the API returns an error.
    """
    payload = {
        "prompt": script,
        "style": style,
        "aspect_ratio": aspect,
        "duration": duration_sec,
        "assets": [{"url": u} for u in assets] if assets else [],
    }
    resp = requests.post(
        f"{SORA_API_BASE}/v2/videos",
        headers=_headers(),
        json=payload,
        timeout=30,
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Sora create job error {resp.status_code}: {resp.text[:300]}"
        )
    data = resp.json()
    job_id = data.get("job_id") or data.get("id")
    if not job_id:
        raise RuntimeError(f"Unexpected Sora response: {data}")
    return job_id


@retry(stop=stop_after_attempt(5), wait=wait_exponential(multiplier=1, min=1, max=10))
def get_job_status(job_id: str) -> dict:
    """Check status of a submitted Sora video job."""
    resp = requests.get(
        f"{SORA_API_BASE}/v2/jobs/{job_id}", headers=_headers(), timeout=20
    )
    if resp.status_code >= 400:
        raise RuntimeError(
            f"Sora get job error {resp.status_code}: {resp.text[:300]}"
        )
    return resp.json()


def wait_for_video(job_id: str, hard_timeout: int = 900) -> str:
    """
    Poll the Sora API until the job completes or times out.

    Args:
        job_id: Identifier returned by create_video_job().
        hard_timeout: Max time to wait in seconds.

    Returns:
        URL of the generated video.

    Raises:
        TimeoutError: If the job does not complete in time.
        RuntimeError: If the job fails.
    """
    start_time = time.time()
    backoff = 5
    while time.time() - start_time < hard_timeout:
        data = get_job_status(job_id)
        status = (data.get("status") or "").lower()
        if status == "completed":
            url = data.get("output_url") or data.get("video_url")
            if not url:
                raise RuntimeError(f"Completed job has no output URL: {data}")
            return url
        if status in ("failed", "error", "canceled"):
            raise RuntimeError(f"Job failed: {data}")
        time.sleep(backoff)
        backoff = min(backoff * 1.5, 20)
    raise TimeoutError("Video generation timed out")


# === Bot state ===
INTRO = (
    "Изпрати ред във формат:\n"
    "<продукт/услуга> | <аудитория> | <тон> | <секунди> | <аспект>\n"
    "Пример: кафе|млади професионалисти|забавен|20|9:16\n"
    "След това прати линкове към лого/снимки (по един на ред). Напиши /go когато си готов.\n"
    "Команди: /start /help /reset"
)


# In-memory session store: chat_id -> session data
user_sessions: Dict[int, dict] = {}


def make_script(product: str, audience: str, tone: str, seconds: int) -> str:
    """
    Construct a script string for the video generation job.

    Args:
        product: Product or service.
        audience: Target audience.
        tone: Tone/style of the ad.
        seconds: Duration of the video.

    Returns:
        A formatted script string.
    """
    return (
        f"Кратка видео реклама ({seconds}s) за '{product}'. "
        f"Целева аудитория: {audience}. Тон: {tone}. "
        "3–4 динамични сцени, фокус върху ключови ползи и силен CTA в края "
        "(\"Поръчай сега\" или \"Виж повече\"). Добави четливи субтитри."
    )


def clamp_seconds(value: int) -> int:
    """
    Clamp a requested duration to the 10–45s range.
    """
    return max(10, min(int(value), 45))


# === Handlers ===
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command."""
    user_sessions[update.effective_chat.id] = {"assets": []}
    await update.message.reply_text(INTRO)


async def help_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    await update.message.reply_text(INTRO)


async def reset_cmd(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /reset command: clear session."""
    user_sessions.pop(update.effective_chat.id, None)
    await update.message.reply_text("Сесията е изчистена. /start за нова.")


async def text(update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Main handler for non-command text messages."""
    chat_id = update.effective_chat.id
    text_content = (update.message.text or "").strip()

    # Handle /go
    if text_content.lower() == "/go":
        session = user_sessions.get(chat_id)
        if not session or not all(
            k in session for k in ("product", "audience", "tone", "seconds", "aspect")
        ):
            return await update.message.reply_text(
                "Липсват полета. Изпрати: продукт|аудитория|тон|секунди|аспект, после /go."
            )
        script = make_script(
            session["product"],
            session["audience"],
            session["tone"],
            session["seconds"],
        )
        assets = session.get("assets", [])
        try:
            progress = await update.message.reply_text("Стартирам видео задание…")
            job_id = create_video_job(
                script,
                assets,
                session["tone"],
                session["aspect"],
                session["seconds"],
            )
            url = wait_for_video(job_id)
            await progress.edit_text(f"Готово ✅\n{url}")
        except Exception as e:
            log.exception("Video job error")
            await update.message.reply_text(f"❌ Грешка: {e}")
        finally:
            user_sessions.pop(chat_id, None)
        return

    # If pipe-delimited params and no previous data stored
    if "|" in text_content and chat_id in user_sessions and "product" not in user_sessions[chat_id]:
        parts = [p.strip() for p in text_content.split("|")]
        if len(parts) < 3:
            return await update.message.reply_text(
                "Минимум: продукт|аудитория|тон. Пример: кафе|млади професионалисти|забавен"
            )
        try:
            product = (parts[0] or "")[:120]
            audience = (parts[1] or "")[:120]
            tone = (parts[2] or "")[:60]
            seconds = (
                clamp_seconds(parts[3])
                if len(parts) > 3 and parts[3].isdigit()
                else 20
            )
            aspect = (
                parts[4]
                if len(parts) > 4 and parts[4] in ("9:16", "1:1", "16:9")
                else "9:16"
            )
            if not product or not audience or not tone:
                return await update.message.reply_text(
                    "Невалидни стойности. Опитай отново."
                )
            user_sessions[chat_id] = {
                "product": product,
                "audience": audience,
                "tone": tone,
                "seconds": seconds,
                "aspect": aspect,
                "assets": [],
            }
            return await update.message.reply_text(
                "Ок. Прати линкове към лого/снимки (по един на ред) или /go."
            )
        except Exception:
            return await update.message.reply_text(
                "Формат: продукт|аудитория|тон|секунди|аспект (пример в /start)."
            )

    # If session exists, treat message as asset URL
    if chat_id in user_sessions:
        if text_content.lower().startswith("http"):
            user_sessions[chat_id]["assets"].append(text_content)
            await update.message.reply_text(
                "Добавих асет. Можеш още линкове или /go."
            )
        else:
            await update.message.reply_text(
                "Това не изглежда като URL. Прати валиден линк или /go."
            )
    else:
        # Unknown context
        await update.message.reply_text("Напиши /start за инструкции.")


def main() -> None:
    """Entry point for the Telegram bot."""
    app = Application.builder().token(TG_BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("reset", reset_cmd))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, text)
    )
    app.run_polling(drop_pending_updates=True, allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()