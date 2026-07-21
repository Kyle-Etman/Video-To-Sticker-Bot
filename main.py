#!/usr/bin/env python3
"""
=====================================================================
 Telegram Video/Photo/GIF Sticker Bot — Render.com Edition
=====================================================================
 Hosting: Render.com (Docker) + UptimeRobot (keep-alive pings)

 Environment Variables Required (set in Render Dashboard):
    - BOT_TOKEN  → Your Telegram bot token from @BotFather
    - OWNER_ID   → Your numeric Telegram user ID (for admin commands)

 Features:
    - Video / Photo / GIF → Sticker conversion
    - Auto-trim videos longer than MAX_DURATION
    - Format info before conversion
    - Live animated progress bar
    - /mystickers - personal sticker counter
    - Admin panel: /stats /ban /unban /maintenance /broadcast /whoami
    - Universal broadcast (any message type) via reply
    - Tracks all users & groups (not just /start users)
    - Flask keep-alive server (for Render free tier + UptimeRobot)
=====================================================================
"""

import os
import json
import uuid
import asyncio
import logging
import threading
from pathlib import Path
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ChatAction
from telegram.error import Forbidden, TelegramError
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ChatMemberHandler,
    ContextTypes,
    filters,
)

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("StickerBot")

# ---------------------------------------------------------------------------
# Load environment variables (.env locally, Render dashboard in production)
# ---------------------------------------------------------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
OWNER_ID_RAW = os.getenv("OWNER_ID")
PORT = int(os.getenv("PORT", "10000"))  # Render provides this automatically

if not BOT_TOKEN:
    raise SystemExit("❌ Missing BOT_TOKEN environment variable.")
if not OWNER_ID_RAW:
    raise SystemExit("❌ Missing OWNER_ID environment variable.")

try:
    OWNER_ID = int(OWNER_ID_RAW.strip())
except ValueError:
    raise SystemExit("❌ OWNER_ID must be a numeric Telegram user ID.")

logger.info("✅ Bot configured. OWNER_ID=%s", OWNER_ID)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MAX_DURATION   = 30
STICKER_SIZE   = 512
TEMP_DIR       = Path("temp_stickers")
TEMP_DIR.mkdir(exist_ok=True)
DATA_FILE      = "bot_data.json"
UPDATES_CHANNEL_URL = "https://t.me/KIRA_BOTS"


# =============================================================================
#                         FLASK KEEP-ALIVE SERVER (for Render + UptimeRobot)
# =============================================================================

flask_app = Flask(__name__)


@flask_app.route("/")
def home():
    """UptimeRobot will ping this endpoint every few minutes to keep the
    Render free web service awake 24/7."""
    return "✅ Sticker Bot is alive and running!", 200


def run_flask():
    """Runs the Flask server in a background thread on Render's assigned port."""
    flask_app.run(host="0.0.0.0", port=PORT, use_reloader=False)


# =============================================================================
#                         JSON DATA LAYER (Users / Chats / Settings)
# =============================================================================

_data: dict = {}
_data_lock = asyncio.Lock()


def _default_data() -> dict:
    return {"users": {}, "chats": {}, "settings": {"maintenance": False}}


def load_data() -> None:
    global _data
    if Path(DATA_FILE).exists():
        try:
            with open(DATA_FILE, "r", encoding="utf-8") as f:
                _data = json.load(f)
        except Exception as e:
            logger.warning("Failed to load data file, using defaults: %s", e)
            _data = _default_data()
    else:
        _data = _default_data()

    _data.setdefault("users", {})
    _data.setdefault("chats", {})
    _data.setdefault("settings", {})
    _data["settings"].setdefault("maintenance", False)


def _write_data_sync() -> None:
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(_data, f, indent=2, ensure_ascii=False)


async def save_data() -> None:
    async with _data_lock:
        await asyncio.to_thread(_write_data_sync)


async def track_chat(update: Update) -> None:
    """Registers every user & chat that interacts with the bot at all."""
    chat = update.effective_chat
    user = update.effective_user
    changed = False

    if user:
        uid = str(user.id)
        display_name = user.username or user.first_name or "Unknown"
        if uid not in _data["users"]:
            _data["users"][uid] = {
                "username": display_name,
                "joined_at": datetime.now(timezone.utc).isoformat(),
                "sticker_count": 0,
                "banned": False,
            }
            changed = True
        elif _data["users"][uid].get("username") != display_name:
            _data["users"][uid]["username"] = display_name
            changed = True

    if chat:
        cid = str(chat.id)
        if cid not in _data["chats"]:
            _data["chats"][cid] = {
                "type": chat.type,
                "title": chat.title or chat.username or (user.first_name if user else "Unknown"),
                "joined_at": datetime.now(timezone.utc).isoformat(),
            }
            changed = True

    if changed:
        await save_data()


def is_banned(user_id: int) -> bool:
    return bool(_data["users"].get(str(user_id), {}).get("banned", False))


async def ban_user(user_id: int) -> None:
    uid = str(user_id)
    if uid not in _data["users"]:
        _data["users"][uid] = {
            "username": "Unknown",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "sticker_count": 0,
            "banned": True,
        }
    else:
        _data["users"][uid]["banned"] = True
    await save_data()


async def unban_user(user_id: int) -> None:
    uid = str(user_id)
    if uid not in _data["users"]:
        _data["users"][uid] = {
            "username": "Unknown",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "sticker_count": 0,
            "banned": False,
        }
    else:
        _data["users"][uid]["banned"] = False
    await save_data()


def is_maintenance() -> bool:
    return bool(_data["settings"].get("maintenance", False))


async def set_maintenance(state: bool) -> None:
    _data["settings"]["maintenance"] = state
    await save_data()


async def increment_sticker_count(user_id: int) -> None:
    uid = str(user_id)
    if uid not in _data["users"]:
        _data["users"][uid] = {
            "username": "Unknown",
            "joined_at": datetime.now(timezone.utc).isoformat(),
            "sticker_count": 1,
            "banned": False,
        }
    else:
        _data["users"][uid]["sticker_count"] = _data["users"][uid].get("sticker_count", 0) + 1
    await save_data()


def get_sticker_count(user_id: int) -> int:
    return _data["users"].get(str(user_id), {}).get("sticker_count", 0)


def get_stats() -> dict:
    total_users = len(_data["users"])
    total_stickers = sum(u.get("sticker_count", 0) for u in _data["users"].values())
    banned_count = sum(1 for u in _data["users"].values() if u.get("banned"))
    total_chats = len(_data["chats"])
    groups = sum(1 for c in _data["chats"].values() if c.get("type") in ("group", "supergroup"))
    return {
        "total_users": total_users,
        "total_stickers": total_stickers,
        "banned_users": banned_count,
        "total_chats": total_chats,
        "groups": groups,
    }


def get_all_chat_ids() -> list:
    ids = []
    for cid, info in _data["chats"].items():
        try:
            cid_int = int(cid)
        except ValueError:
            continue
        if info.get("type") == "private" and is_banned(cid_int):
            continue
        ids.append(cid_int)
    return ids


# =============================================================================
#                              FFMPEG / FFPROBE HELPERS
# =============================================================================

async def run_command(cmd: list) -> tuple[bool, str]:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        _, stderr = await proc.communicate()
        return proc.returncode == 0, stderr.decode(errors="ignore")
    except FileNotFoundError:
        return False, "ffmpeg/ffprobe not found."
    except Exception as e:
        return False, str(e)


async def run_ffprobe(cmd: list) -> str:
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode(errors="ignore")
    except Exception:
        return ""


async def get_duration(path: str) -> float:
    cmd = [
        "ffprobe", "-v", "error",
        "-show_entries", "format=duration",
        "-of", "default=noprint_wrappers=1:nokey=1",
        path,
    ]
    out = await run_ffprobe(cmd)
    try:
        return float(out.strip())
    except Exception:
        return 0.0


async def get_video_info(path: str) -> dict:
    cmd = [
        "ffprobe", "-v", "error",
        "-select_streams", "v:0",
        "-show_entries", "stream=width,height:format=duration",
        "-of", "default=noprint_wrappers=1",
        path,
    ]
    out = await run_ffprobe(cmd)
    info = {}
    for line in out.strip().split("\n"):
        if "=" in line:
            k, v = line.split("=", 1)
            info[k.strip()] = v.strip()
    return info


async def convert_to_webm(input_path: str, output_path: str) -> bool:
    scale = (
        f"scale={STICKER_SIZE}:{STICKER_SIZE}:"
        "force_original_aspect_ratio=decrease:force_divisible_by=2"
    )

    primary = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(MAX_DURATION),
        "-an",
        "-vf", scale,
        "-c:v", "libvpx-vp9",
        "-crf", "30",
        "-b:v", "0",
        "-deadline", "good",
        "-cpu-used", "4",
        "-row-mt", "1",
        "-pix_fmt", "yuva420p",
        "-f", "webm",
        output_path,
    ]

    ok, err = await run_command(primary)
    if ok and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
        return True

    logger.warning("Primary conversion failed, trying fallback...\n%s", err)

    fallback = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-t", str(MAX_DURATION),
        "-an",
        "-vf", scale,
        "-c:v", "libvpx-vp9",
        "-crf", "34",
        "-b:v", "0",
        "-deadline", "good",
        "-cpu-used", "5",
        "-pix_fmt", "yuv420p",
        "-f", "webm",
        output_path,
    ]

    ok, err = await run_command(fallback)
    if ok and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
        return True

    logger.error("Both conversions failed.\n%s", err)
    return False


async def convert_photo_to_webp(input_path: str, output_path: str) -> bool:
    scale = f"scale={STICKER_SIZE}:{STICKER_SIZE}:force_original_aspect_ratio=decrease"
    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vf", scale,
        "-c:v", "libwebp",
        "-lossless", "0",
        "-q:v", "80",
        "-compression_level", "6",
        "-vsync", "0",
        output_path,
    ]
    ok, err = await run_command(cmd)
    if ok and Path(output_path).exists() and Path(output_path).stat().st_size > 0:
        return True
    logger.error("Photo conversion failed.\n%s", err)
    return False


def cleanup(*paths) -> None:
    for p in paths:
        try:
            if p and Path(p).exists():
                Path(p).unlink()
        except Exception as e:
            logger.warning("Cleanup failed for %s: %s", p, e)


# =============================================================================
#                              LIVE ANIMATION
# =============================================================================

async def animate(msg, stop_event: asyncio.Event) -> None:
    steps = [10, 20, 30, 40, 50, 60, 70, 80, 90]
    spinner = ["⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏"]
    i = 0

    while not stop_event.is_set():
        if i < len(steps):
            pct = steps[i]
            filled = pct // 10
            bar = "▓" * filled + "░" * (10 - filled)
            text = f"⚙️ *Converting Your Sticker...*\n`[{bar}]` {pct}%"
        else:
            spin = spinner[(i - len(steps)) % len(spinner)]
            bar = "▓" * 9 + "░"
            text = f"⚙️ *Converting Your Sticker...*\n`[{bar}]` 90% {spin}"

        try:
            await msg.edit_text(text, parse_mode="Markdown")
        except Exception:
            pass

        i += 1
        for _ in range(5):
            if stop_event.is_set():
                break
            await asyncio.sleep(0.1)


# =============================================================================
#                              ACCESS CONTROL HELPERS
# =============================================================================

def is_owner(user_id: int) -> bool:
    return user_id == OWNER_ID


async def check_access(update: Update) -> bool:
    user = update.effective_user

    if is_banned(user.id):
        await update.message.reply_text("🚫 You have been banned from using this bot.")
        return False

    if not is_owner(user.id) and is_maintenance():
        await update.message.reply_text(
            "🛠 Bot is currently under maintenance.\nPlease try again later."
        )
        return False

    return True


# =============================================================================
#                     GLOBAL TRACKER (runs before everything else)
# =============================================================================

async def tracker_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        await track_chat(update)
    except Exception as e:
        logger.warning("Tracker failed: %s", e)


async def my_chat_member_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    try:
        chat = update.effective_chat
        if chat:
            cid = str(chat.id)
            if cid not in _data["chats"]:
                _data["chats"][cid] = {
                    "type": chat.type,
                    "title": chat.title or chat.username or "Unknown",
                    "joined_at": datetime.now(timezone.utc).isoformat(),
                }
                await save_data()
    except Exception as e:
        logger.warning("my_chat_member tracker failed: %s", e)


# =============================================================================
#                              USER COMMANDS
# =============================================================================

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "👋 *Welcome to @Videos\\_2\\_sticker\\_robot!*\n\n"
        "Send me any:\n"
        "🎬 Video (up to 30s, longer ones auto-trimmed)\n"
        "🖼️ Photo\n"
        "🎞️ GIF\n\n"
        "...and I'll instantly convert it into a Telegram sticker!\n\n"
        "Type /help to see all available commands.",
        parse_mode="Markdown",
    )


async def help_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    text = (
        "📖 *Bot Commands & Guide*\n\n"
        "🔹 /start — Start the bot\n"
        "🔹 /help — Show this help message\n"
        "🔹 /mystickers — See how many stickers you've created\n"
        "🔹 /whoami — Check your Telegram ID\n\n"
        "*How to use:*\n"
        "1️⃣ Send a video (up to 30s), photo, or GIF\n"
        "2️⃣ Wait for the conversion animation\n"
        "3️⃣ Receive your Telegram sticker instantly!\n\n"
    )

    if is_owner(update.effective_user.id):
        text += (
            "👑 *Owner Commands:*\n"
            "🔸 /stats — View bot statistics\n"
            "🔸 /ban <user_id> — Ban a user (or reply to their message with /ban)\n"
            "🔸 /unban <user_id> — Unban a user (or reply with /unban)\n"
            "🔸 /maintenance on|off — Toggle maintenance mode\n"
            "🔸 /broadcast — Reply to any message (text/photo/sticker/audio/etc.) "
            "to send it to all users & groups\n\n"
        )

    text += (
        "ℹ️ *Note:* These are FFmpeg-generated stickers, not official Telegram "
        "Sticker Pack stickers. They cannot be added to a sticker pack — you can "
        "only save them to your *Favorites* (long-press the sticker → Add to Favorites)."
    )

    keyboard = InlineKeyboardMarkup(
        [[InlineKeyboardButton("📢 Updates Channel", url=UPDATES_CHANNEL_URL)]]
    )

    await update.message.reply_text(text, parse_mode="Markdown", reply_markup=keyboard)


async def mystickers_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    count = get_sticker_count(update.effective_user.id)
    await update.message.reply_text(
        f"🎨 *Your Sticker Stats*\n\n"
        f"You've created a total of *{count}* stickers with this bot! 🚀",
        parse_mode="Markdown",
    )


async def whoami_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Debug helper: shows your Telegram ID and whether you're recognized as owner."""
    user = update.effective_user
    await update.message.reply_text(
        f"🆔 Your Telegram ID: `{user.id}`\n"
        f"👑 Configured OWNER_ID: `{OWNER_ID}`\n"
        f"Status: {'✅ You ARE the owner' if is_owner(user.id) else '❌ You are NOT the owner'}",
        parse_mode="Markdown",
    )


async def reject_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.message.reply_text(
        "❌ Unsupported file type.\nPlease send a *video*, *photo*, or *GIF*.",
        parse_mode="Markdown",
    )


# =============================================================================
#                              VIDEO HANDLER
# =============================================================================

async def video_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    if not await check_access(update):
        return

    user = update.effective_user
    video = message.video
    input_path = output_path = status_msg = stop_event = anim_task = None

    try:
        duration = video.duration or 0

        if duration > MAX_DURATION:
            await message.reply_text(
                f"✂️ Your video is longer than {MAX_DURATION}s.\n"
                f"Auto-trimming to the first {MAX_DURATION} seconds..."
            )

        await context.bot.send_chat_action(message.chat_id, ChatAction.TYPING)
        uid = uuid.uuid4().hex
        input_path = TEMP_DIR / f"{uid}_in.mp4"
        output_path = TEMP_DIR / f"{uid}_out.webm"

        tg_file = await context.bot.get_file(video.file_id)
        await tg_file.download_to_drive(custom_path=str(input_path))

        if duration <= 0:
            real_dur = await get_duration(str(input_path))
            if real_dur > MAX_DURATION:
                await message.reply_text(
                    f"✂️ Your video is longer than {MAX_DURATION}s.\n"
                    f"Auto-trimming to the first {MAX_DURATION} seconds..."
                )

        info = await get_video_info(str(input_path))
        width = info.get("width", "?")
        height = info.get("height", "?")
        dur_raw = info.get("duration", "0")
        try:
            dur_display = f"{float(dur_raw):.1f}s"
        except Exception:
            dur_display = "?"
        size_mb = input_path.stat().st_size / (1024 * 1024)

        await message.reply_text(
            "📊 *Video Info*\n"
            f"▫️ Resolution: `{width}x{height}`\n"
            f"▫️ Duration: `{dur_display}`\n"
            f"▫️ Size: `{size_mb:.2f} MB`",
            parse_mode="Markdown",
        )

        status_msg = await message.reply_text("⚙️ *Starting conversion...*", parse_mode="Markdown")
        stop_event = asyncio.Event()
        anim_task = asyncio.create_task(animate(status_msg, stop_event))

        await context.bot.send_chat_action(message.chat_id, ChatAction.UPLOAD_VIDEO)
        converted = await convert_to_webm(str(input_path), str(output_path))

        stop_event.set()
        await anim_task

        if not converted or not output_path.exists():
            await status_msg.edit_text("❌ Failed to convert the video.\nPlease try another video.")
            return

        await status_msg.delete()
        status_msg = None

        await context.bot.send_chat_action(message.chat_id, ChatAction.CHOOSE_STICKER)
        with open(output_path, "rb") as f:
            await context.bot.send_sticker(chat_id=message.chat_id, sticker=f)

        await increment_sticker_count(user.id)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("👑 Owner", url="https://t.me/zoastra")]]
        )
        await message.reply_text(
            "✨ *Sticker Created Successfully!*\n\n"
            "Thank you for using @Videos\\_2\\_sticker\\_robot! ❤️\n\n"
            "We hope you enjoy your new sticker.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.exception("Error in video_handler: %s", e)
        if stop_event and not stop_event.is_set():
            stop_event.set()
        if anim_task and not anim_task.done():
            try:
                await anim_task
            except Exception:
                pass
        try:
            err_text = "⚠️ Something went wrong while processing your video.\nPlease try again."
            if status_msg:
                await status_msg.edit_text(err_text)
            else:
                await message.reply_text(err_text)
        except Exception:
            pass
    finally:
        cleanup(input_path, output_path)


# =============================================================================
#                              PHOTO HANDLER
# =============================================================================

async def photo_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    if not await check_access(update):
        return

    user = update.effective_user
    input_path = output_path = status_msg = None

    try:
        photo = message.photo[-1]
        status_msg = await message.reply_text("🖼️ Processing your photo...")

        uid = uuid.uuid4().hex
        input_path = TEMP_DIR / f"{uid}_photo.jpg"
        output_path = TEMP_DIR / f"{uid}_photo.webp"

        tg_file = await context.bot.get_file(photo.file_id)
        await tg_file.download_to_drive(custom_path=str(input_path))

        converted = await convert_photo_to_webp(str(input_path), str(output_path))

        if not converted:
            await status_msg.edit_text("❌ Failed to convert the photo.\nPlease try another image.")
            return

        await status_msg.delete()
        status_msg = None

        await context.bot.send_chat_action(message.chat_id, ChatAction.CHOOSE_STICKER)
        with open(output_path, "rb") as f:
            await context.bot.send_sticker(chat_id=message.chat_id, sticker=f)

        await increment_sticker_count(user.id)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("👑 Owner", url="https://t.me/zoastra")]]
        )
        await message.reply_text(
            "✨ *Sticker Created Successfully!*\n\n"
            "Thank you for using @Videos\\_2\\_sticker\\_robot! ❤️\n\n"
            "We hope you enjoy your new sticker.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.exception("Error in photo_handler: %s", e)
        try:
            err_text = "⚠️ Something went wrong while processing your photo.\nPlease try again."
            if status_msg:
                await status_msg.edit_text(err_text)
            else:
                await message.reply_text(err_text)
        except Exception:
            pass
    finally:
        cleanup(input_path, output_path)


# =============================================================================
#                              GIF / ANIMATION HANDLER
# =============================================================================

async def gif_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message

    if not await check_access(update):
        return

    user = update.effective_user
    input_path = output_path = status_msg = stop_event = anim_task = None

    try:
        if message.animation:
            file_id = message.animation.file_id
        elif message.document and message.document.mime_type == "image/gif":
            file_id = message.document.file_id
        else:
            await message.reply_text("❌ Unsupported file.")
            return

        status_msg = await message.reply_text("⚙️ *Starting conversion...*", parse_mode="Markdown")
        stop_event = asyncio.Event()
        anim_task = asyncio.create_task(animate(status_msg, stop_event))

        uid = uuid.uuid4().hex
        input_path = TEMP_DIR / f"{uid}_gif_in"
        output_path = TEMP_DIR / f"{uid}_gif_out.webm"

        tg_file = await context.bot.get_file(file_id)
        await tg_file.download_to_drive(custom_path=str(input_path))

        converted = await convert_to_webm(str(input_path), str(output_path))

        stop_event.set()
        await anim_task

        if not converted or not output_path.exists():
            await status_msg.edit_text("❌ Failed to convert the GIF.\nPlease try another file.")
            return

        await status_msg.delete()
        status_msg = None

        await context.bot.send_chat_action(message.chat_id, ChatAction.CHOOSE_STICKER)
        with open(output_path, "rb") as f:
            await context.bot.send_sticker(chat_id=message.chat_id, sticker=f)

        await increment_sticker_count(user.id)

        keyboard = InlineKeyboardMarkup(
            [[InlineKeyboardButton("👑 Owner", url="https://t.me/zoastra")]]
        )
        await message.reply_text(
            "✨ *Sticker Created Successfully!*\n\n"
            "Thank you for using @Videos\\_2\\_sticker\\_robot! ❤️\n\n"
            "We hope you enjoy your new sticker.",
            parse_mode="Markdown",
            reply_markup=keyboard,
        )

    except Exception as e:
        logger.exception("Error in gif_handler: %s", e)
        if stop_event and not stop_event.is_set():
            stop_event.set()
        if anim_task and not anim_task.done():
            try:
                await anim_task
            except Exception:
                pass
        try:
            err_text = "⚠️ Something went wrong while processing your GIF.\nPlease try again."
            if status_msg:
                await status_msg.edit_text(err_text)
            else:
                await message.reply_text(err_text)
        except Exception:
            pass
    finally:
        cleanup(input_path, output_path)


# =============================================================================
#                              ADMIN PANEL (Owner Only) — FIXED
# =============================================================================

async def stats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    caller_id = update.effective_user.id
    logger.info("/stats invoked by %s", caller_id)

    if not is_owner(caller_id):
        await update.message.reply_text("⛔ This command is for the bot owner only.")
        return

    stats = get_stats()
    await update.message.reply_text(
        "📊 *Bot Statistics*\n\n"
        f"👥 Total Users: `{stats['total_users']}`\n"
        f"💬 Total Chats (Users+Groups): `{stats['total_chats']}`\n"
        f"👨‍👩‍👧‍👦 Groups: `{stats['groups']}`\n"
        f"🎨 Total Stickers Created: `{stats['total_stickers']}`\n"
        f"🚫 Banned Users: `{stats['banned_users']}`",
        parse_mode="Markdown",
    )


async def ban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /ban - Ban a user. Owner only.
    Supports TWO usage styles:
        1. Reply to the target user's message with /ban
        2. /ban <user_id>
    """
    caller_id = update.effective_user.id
    logger.info("/ban invoked by %s", caller_id)

    if not is_owner(caller_id):
        await update.message.reply_text("⛔ This command is for the bot owner only.")
        return

    target_id = None

    # Style 1: reply-based
    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id

    # Style 2: argument-based
    elif context.args:
        try:
            target_id = int(context.args[0].strip())
        except (ValueError, IndexError):
            target_id = None

    if target_id is None:
        await update.message.reply_text(
            "⚠️ *Usage:*\n"
            "• Reply to a user's message with `/ban`\n"
            "• Or type `/ban <user_id>`",
            parse_mode="Markdown",
        )
        return

    try:
        await ban_user(target_id)
        logger.info("User %s banned successfully by owner %s", target_id, caller_id)
        await update.message.reply_text(f"🚫 User `{target_id}` has been banned.", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Failed to ban user %s: %s", target_id, e)
        await update.message.reply_text(f"❌ Failed to ban user: {e}")


async def unban_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /unban - Unban a user. Owner only.
    Supports reply-based OR /unban <user_id>.
    """
    caller_id = update.effective_user.id
    logger.info("/unban invoked by %s", caller_id)

    if not is_owner(caller_id):
        await update.message.reply_text("⛔ This command is for the bot owner only.")
        return

    target_id = None

    if update.message.reply_to_message and update.message.reply_to_message.from_user:
        target_id = update.message.reply_to_message.from_user.id
    elif context.args:
        try:
            target_id = int(context.args[0].strip())
        except (ValueError, IndexError):
            target_id = None

    if target_id is None:
        await update.message.reply_text(
            "⚠️ *Usage:*\n"
            "• Reply to a user's message with `/unban`\n"
            "• Or type `/unban <user_id>`",
            parse_mode="Markdown",
        )
        return

    try:
        await unban_user(target_id)
        logger.info("User %s unbanned successfully by owner %s", target_id, caller_id)
        await update.message.reply_text(f"✅ User `{target_id}` has been unbanned.", parse_mode="Markdown")
    except Exception as e:
        logger.exception("Failed to unban user %s: %s", target_id, e)
        await update.message.reply_text(f"❌ Failed to unban user: {e}")


async def maintenance_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/maintenance on|off - Toggles maintenance mode. Owner only."""
    caller_id = update.effective_user.id
    logger.info("/maintenance invoked by %s with args=%s", caller_id, context.args)

    if not is_owner(caller_id):
        await update.message.reply_text("⛔ This command is for the bot owner only.")
        return

    if not context.args or context.args[0].lower() not in ("on", "off"):
        current = "✅ ON" if is_maintenance() else "❌ OFF"
        await update.message.reply_text(
            f"Current maintenance status: *{current}*\n\n"
            "Usage: `/maintenance on` or `/maintenance off`",
            parse_mode="Markdown",
        )
        return

    try:
        state = context.args[0].lower() == "on"
        await set_maintenance(state)
        logger.info("Maintenance mode set to %s by owner %s", state, caller_id)
        await update.message.reply_text(
            f"🛠 Maintenance mode {'enabled ✅' if state else 'disabled ❌'}."
        )
    except Exception as e:
        logger.exception("Failed to set maintenance mode: %s", e)
        await update.message.reply_text(f"❌ Failed to update maintenance mode: {e}")


async def broadcast_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """
    /broadcast - Reply to ANY message (text, photo, sticker, audio, video,
    document, GIF, voice, etc.) with this command to copy it to every
    saved user & group. Owner only.
    """
    caller_id = update.effective_user.id
    logger.info("/broadcast invoked by %s", caller_id)

    if not is_owner(caller_id):
        await update.message.reply_text("⛔ This command is for the bot owner only.")
        return

    replied = update.message.reply_to_message

    if not replied:
        await update.message.reply_text(
            "⚠️ Please *reply* to any message (text/photo/sticker/audio/video/etc.) "
            "with /broadcast to send it to everyone.",
            parse_mode="Markdown",
        )
        return

    target_ids = get_all_chat_ids()
    status = await update.message.reply_text(
        f"📢 Broadcasting to {len(target_ids)} chats..."
    )

    sent = failed = 0
    dead_chats = []

    for cid in target_ids:
        try:
            await context.bot.copy_message(
                chat_id=cid,
                from_chat_id=replied.chat_id,
                message_id=replied.message_id,
            )
            sent += 1
        except Forbidden:
            failed += 1
            dead_chats.append(str(cid))
        except TelegramError:
            failed += 1
        except Exception:
            failed += 1

        await asyncio.sleep(0.05)

    if dead_chats:
        for cid in dead_chats:
            _data["chats"].pop(cid, None)
        await save_data()

    await status.edit_text(
        f"✅ *Broadcast Complete!*\n\n"
        f"📤 Sent: `{sent}`\n"
        f"❌ Failed: `{failed}`",
        parse_mode="Markdown",
    )


# =============================================================================
#                              GLOBAL ERROR HANDLER
# =============================================================================

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Global error: %s", context.error, exc_info=context.error)


# =============================================================================
#                              ENTRY POINT
# =============================================================================

def main() -> None:
    load_data()

    # Start Flask keep-alive server in background thread (Render + UptimeRobot)
    threading.Thread(target=run_flask, daemon=True).start()
    logger.info("🌐 Flask keep-alive server started on port %s", PORT)

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Global tracker: runs FIRST for every update
    app.add_handler(MessageHandler(filters.ALL, tracker_handler), group=-1)
    app.add_handler(ChatMemberHandler(my_chat_member_handler, ChatMemberHandler.MY_CHAT_MEMBER), group=-1)

    # User commands
    app.add_handler(CommandHandler("start", start_handler))
    app.add_handler(CommandHandler("help", help_handler))
    app.add_handler(CommandHandler("mystickers", mystickers_handler))
    app.add_handler(CommandHandler("whoami", whoami_handler))

    # Admin commands
    app.add_handler(CommandHandler("stats", stats_handler))
    app.add_handler(CommandHandler("ban", ban_handler))
    app.add_handler(CommandHandler("unban", unban_handler))
    app.add_handler(CommandHandler("maintenance", maintenance_handler))
    app.add_handler(CommandHandler("broadcast", broadcast_handler))

    # Media handlers
    app.add_handler(MessageHandler(filters.VIDEO, video_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_handler))
    app.add_handler(
        MessageHandler(
            filters.ANIMATION | filters.Document.MimeType("image/gif"),
            gif_handler,
        )
    )

    # Reject everything else
    app.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.VIDEO
            & ~filters.PHOTO
            & ~filters.ANIMATION
            & ~filters.Document.MimeType("image/gif")
            & ~filters.COMMAND,
            reject_handler,
        )
    )

    app.add_error_handler(error_handler)

    logger.info("🚀 Bot is running (Render.com deployment)...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()