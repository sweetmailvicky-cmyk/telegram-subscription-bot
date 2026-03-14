#!/usr/bin/env python3

import logging
import asyncio
import aiosqlite
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ChatMemberHandler,
)

# ==================================================
# LOGGING
# ==================================================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================================================
# CONFIG — Edit these 3 values only
# ==================================================
BOT_TOKEN  = "8453765782:AAENJEsrojZ2Dy-VwrCeU2vTFjBUof4G4oQ"
CHANNEL_ID = -1002565325480
ADMIN_ID   = 206193281
ADMIN_IDS  = {206193281, 7190468561}
# ==================================================

# How many days BEFORE expiry to send a reminder to admin
REMINDER_DAYS_BEFORE = [3, 1]  # Sends reminder 3 days before AND 1 day before

DB_PATH = "members.db"

IST = ZoneInfo("Asia/Kolkata")
FMT = "%Y-%m-%d %H:%M:%S"


def now_ist() -> datetime:
    """Current time in IST (timezone-aware)."""
    return datetime.now(tz=IST)


def fmt_ist(dt: datetime) -> str:
    """Format a datetime as IST string, converting from UTC if needed."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    else:
        dt = dt.astimezone(IST)
    return dt.strftime(FMT) + " IST"


# ──────────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────────

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                joined_at TEXT,
                expiry    TEXT,
                removed   INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS links (
                invite_link TEXT PRIMARY KEY,
                created_at  TEXT,
                expire_date TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key   TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """)
        # New table: tracks which reminders have already been sent
        # so we don't spam the admin on every 60-second tick
        await db.execute("""
            CREATE TABLE IF NOT EXISTS reminders_sent (
                user_id     INTEGER,
                days_before INTEGER,
                PRIMARY KEY (user_id, days_before)
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO stats(key,value) VALUES('total_joins',0)"
        )
        await db.commit()
    logger.info("Database ready.")


async def db_add_link(invite_link: str, expire_date: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO links(invite_link,created_at,expire_date) VALUES(?,?,?)",
            (invite_link,
             now_ist().strftime(FMT),
             fmt_ist(expire_date))
        )
        await db.commit()


async def db_link_exists(invite_link: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM links WHERE invite_link=?", (invite_link,)
        ) as cur:
            return await cur.fetchone() is not None


async def db_remove_link(invite_link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM links WHERE invite_link=?", (invite_link,))
        await db.commit()


async def db_add_user(user_id: int, username: str, expiry: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO users(user_id,username,joined_at,expiry,removed)
               VALUES(?,?,?,?,0)""",
            (user_id,
             username or "unknown",
             now_ist().strftime(FMT),
             fmt_ist(expiry))
        )
        await db.execute(
            "UPDATE stats SET value=value+1 WHERE key='total_joins'"
        )
        # Clear any prior reminders for this user (fresh join = fresh state)
        await db.execute("DELETE FROM reminders_sent WHERE user_id=?", (user_id,))
        await db.commit()


async def db_get_expired_users():
    now_str = now_ist().strftime(FMT)
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username FROM users WHERE expiry<=? AND removed=0",
            (now_str,)
        ) as cur:
            return await cur.fetchall()


async def db_mark_removed(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET removed=1 WHERE user_id=?", (user_id,)
        )
        await db.commit()


async def db_get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE removed=0"
        ) as cur:
            active = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT value FROM stats WHERE key='total_joins'"
        ) as cur:
            total = (await cur.fetchone())[0]
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE removed=1"
        ) as cur:
            removed = (await cur.fetchone())[0]
    return {"active": active, "total": total, "removed": removed}


async def db_get_users_expiring_soon():
    """
    Returns active users whose expiry falls within the next REMINDER_DAYS_BEFORE window,
    along with how many days are left (approximate).
    """
    now = now_ist()
    results = []
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username, expiry FROM users WHERE removed=0"
        ) as cur:
            rows = await cur.fetchall()

        for user_id, username, expiry_str in rows:
            expiry_raw = expiry_str.replace(" IST", "")
            expiry = datetime.strptime(expiry_raw, FMT).replace(tzinfo=IST)
            days_left = (expiry - now).days  # integer floor

            for days_before in REMINDER_DAYS_BEFORE:
                if days_left == days_before:
                    # Check if we already sent this reminder
                    async with db.execute(
                        "SELECT 1 FROM reminders_sent WHERE user_id=? AND days_before=?",
                        (user_id, days_before)
                    ) as cur2:
                        already_sent = await cur2.fetchone()

                    if not already_sent:
                        results.append((user_id, username, expiry, days_before))

    return results


async def db_mark_reminder_sent(user_id: int, days_before: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR IGNORE INTO reminders_sent(user_id, days_before) VALUES(?,?)",
            (user_id, days_before)
        )
        await db.commit()


async def notify_admins(bot, text: str):
    """Send a message to all admins."""
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(admin_id, text, parse_mode="Markdown")
        except Exception as e:
            logger.warning("Could not notify admin %s: %s", admin_id, e)




def main_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🔗 Generate Invite Link", callback_data="gen_menu")],
        [InlineKeyboardButton("📊 View Stats",            callback_data="stats")],
    ])


def generate_menu_keyboard():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📅 30 Days", callback_data="gen_30")],
        [InlineKeyboardButton("📅 90 Days", callback_data="gen_90")],
        [InlineKeyboardButton("« Back",     callback_data="back_main")],
    ])


async def create_and_send_link(bot, chat_id: int, days: int, message_editor=None, text_sender=None):
    """
    Creates a Telegram invite link valid for `days` days (single-use).
    - message_editor: async callable(text, parse_mode) — used when editing an existing message
    - text_sender:    async callable(text, parse_mode) — used when sending a new message
    """
    expire_date = now_ist() + timedelta(days=days)
    try:
        link_obj = await bot.create_chat_invite_link(
            chat_id=CHANNEL_ID,
            member_limit=1,
            expire_date=expire_date,
        )
    except Exception as e:
        logger.error("Failed to create invite link: %s", e)
        msg = f"❌ Failed to create link.\n\n`{e}`\n\nMake sure the bot is admin in the channel."
        if message_editor:
            await message_editor(msg, "Markdown")
        else:
            await text_sender(msg, "Markdown")
        return

    await db_add_link(link_obj.invite_link, expire_date)

    reply = (
        f"✅ *Invite Link — {days} Day(s)*\n\n"
        f"`{link_obj.invite_link}`\n\n"
        f"⏰ Your Expires on : `{fmt_ist(expire_date)}`\n"
        f"👤 Single-use only"
    )
    if message_editor:
        await message_editor(reply, "Markdown")
    else:
        await text_sender(reply, "Markdown")


# ──────────────────────────────────────────────────
# HANDLERS
# ──────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("/start from user_id=%s", update.effective_user.id)

    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    await update.message.reply_text(
        "👋 *Admin Panel* — Choose an option:",
        reply_markup=main_keyboard(),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id not in ADMIN_IDS:
        await query.edit_message_text("Not authorized.")
        return

    data = query.data

    # ── Main menu ──────────────────────────────────
    if data == "back_main":
        await query.edit_message_text(
            "👋 *Admin Panel* — Choose an option:",
            reply_markup=main_keyboard(),
            parse_mode="Markdown",
        )

    # ── Generate sub-menu ──────────────────────────
    elif data == "gen_menu":
        await query.edit_message_text(
            "🔗 *Generate Invite Link*\n\nSelect membership duration:",
            reply_markup=generate_menu_keyboard(),
            parse_mode="Markdown",
        )

    # ── 30-day link ────────────────────────────────
    elif data == "gen_30":
        await create_and_send_link(
            context.bot, ADMIN_ID, days=30,
            message_editor=lambda t, p: query.edit_message_text(t, parse_mode=p)
        )

    # ── 90-day link ────────────────────────────────
    elif data == "gen_90":
        await create_and_send_link(
            context.bot, ADMIN_ID, days=90,
            message_editor=lambda t, p: query.edit_message_text(t, parse_mode=p)
        )

    # ── Stats ──────────────────────────────────────
    elif data == "stats":
        s = await db_get_stats()
        await query.edit_message_text(
            f"📊 *Statistics*\n\n"
            f"🔢 Total joined ever : `{s['total']}`\n"
            f"✅ Currently active  : `{s['active']}`\n"
            f"🚫 Removed (expired) : `{s['removed']}`",
            parse_mode="Markdown",
        )


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.chat.id != CHANNEL_ID:
        return

    old = update.chat_member.old_chat_member
    new = update.chat_member.new_chat_member

    if old.status in ("member", "administrator", "creator"):
        return
    if new.status != "member":
        return

    invite = update.chat_member.invite_link
    if not invite:
        return

    link_str = invite.invite_link
    if not await db_link_exists(link_str):
        return

    user   = new.user
    # Determine expiry based on the link's own expiry stored in DB
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT expire_date FROM links WHERE invite_link=?", (link_str,)
        ) as cur:
            row = await cur.fetchone()

    if row:
        # Use the link's expiry as the member's membership expiry
        expiry = datetime.strptime(row[0].replace(" IST", ""), FMT).replace(tzinfo=IST)
    else:
        # Fallback: shouldn't happen, but default to 30 days
        expiry = now_ist() + timedelta(days=30)

    await db_add_user(user.id, user.username, expiry)
    await db_remove_link(link_str)

    logger.info("Tracked user %s (@%s) expires %s", user.id, user.username, expiry)

    await notify_admins(
        context.bot,
        f"✅ *New Member Joined!*\n\n"
        f"👤 {user.full_name}\n"
        f"🆔 `{user.id}`\n"
        f"📛 @{user.username or 'N/A'}\n"
        f"⏰ Removes at: `{fmt_ist(expiry)}`",
    )


# ──────────────────────────────────────────────────
# SCHEDULED JOBS
# ──────────────────────────────────────────────────

async def remove_expired(context: ContextTypes.DEFAULT_TYPE):
    expired = await db_get_expired_users()
    if expired:
        logger.info("Removing %d expired user(s).", len(expired))

    for user_id, username in expired:
        try:
            await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            await asyncio.sleep(1)
            await context.bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            logger.info("Removed & unbanned %s (@%s)", user_id, username)

            await notify_admins(
                    context.bot,
                    f"🚫 *Member Removed* (expired)\n\n"
                    f"👤 @{username}\n"
                    f"🆔 `{user_id}`",
                )

        except Exception as e:
            logger.error("Failed to remove user %s: %s", user_id, e)

        await db_mark_removed(user_id)


async def send_expiry_reminders(context: ContextTypes.DEFAULT_TYPE):
    """
    Runs every hour. Sends admin a reminder for each member who will
    expire in exactly REMINDER_DAYS_BEFORE days (once per threshold).
    """
    expiring = await db_get_users_expiring_soon()

    for user_id, username, expiry, days_before in expiring:
        try:
            await notify_admins(
                context.bot,
                f"⚠️ *Expiry Reminder*\n\n"
                f"👤 @{username or 'N/A'}\n"
                f"🆔 `{user_id}`\n"
                f"📅 Expires: `{fmt_ist(expiry)}`\n"
                f"⏳ *{days_before} day(s) remaining*",
            )
            await db_mark_reminder_sent(user_id, days_before)
            logger.info("Sent %d-day reminder for user %s", days_before, user_id)
        except Exception as e:
            logger.warning("Could not send reminder for user %s: %s", user_id, e)


# ──────────────────────────────────────────────────
# POST INIT
# ──────────────────────────────────────────────────

async def post_init(application):
    await init_db()
    me = await application.bot.get_me()
    logger.info("Bot started: %s (@%s)", me.full_name, me.username)


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(
        ChatMemberHandler(track_member, ChatMemberHandler.CHAT_MEMBER)
    )

    # Existing job: remove expired every 60 seconds
    application.job_queue.run_repeating(remove_expired, interval=60, first=10)

    # New job: check for upcoming expiries every hour
    application.job_queue.run_repeating(send_expiry_reminders, interval=3600, first=30)

    application.run_polling(
        allowed_updates=[
            "message",
            "callback_query",
            "chat_member",
            "my_chat_member",
        ],
        drop_pending_updates=True,
    )


if __name__ == "__main__":
    main()
