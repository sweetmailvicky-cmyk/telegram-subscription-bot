import aiosqlite
import logging
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ChatMemberHandler,
)

# =========================
# LOGGING
# =========================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# =========================
# CONFIG — Edit these
# =========================

BOT_TOKEN   = "8453765782:AAENJEsrojZ2Dy-VwrCeU2vTFjBUof4G4oQ"
CHANNEL_ID  = -1002565325480   # Your channel ID (negative number)
ADMIN_ID    = 206193281        # Your Telegram user ID

DB_PATH     = "members.db"

# =========================
# DATABASE HELPERS
# =========================

async def init_db():
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id    INTEGER PRIMARY KEY,
                username   TEXT,
                joined_at  TEXT,
                expiry     TEXT,
                removed    INTEGER DEFAULT 0
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS links (
                invite_link TEXT PRIMARY KEY,
                created_at  TEXT,
                expire_date TEXT
            )
        """)
        # Track total joins ever (never decremented)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS stats (
                key   TEXT PRIMARY KEY,
                value INTEGER DEFAULT 0
            )
        """)
        await db.execute(
            "INSERT OR IGNORE INTO stats (key, value) VALUES ('total_joins', 0)"
        )
        await db.commit()


async def db_add_user(user_id: int, username: str, expiry: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            """INSERT OR REPLACE INTO users (user_id, username, joined_at, expiry, removed)
               VALUES (?, ?, ?, ?, 0)""",
            (
                user_id,
                username or "",
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                expiry.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        await db.execute(
            "UPDATE stats SET value = value + 1 WHERE key = 'total_joins'"
        )
        await db.commit()


async def db_remove_link(invite_link: str):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute("DELETE FROM links WHERE invite_link = ?", (invite_link,))
        await db.commit()


async def db_link_exists(invite_link: str) -> bool:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT 1 FROM links WHERE invite_link = ?", (invite_link,)
        ) as cur:
            return await cur.fetchone() is not None


async def db_add_link(invite_link: str, expire_date: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO links (invite_link, created_at, expire_date) VALUES (?, ?, ?)",
            (
                invite_link,
                datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                expire_date.strftime("%Y-%m-%d %H:%M:%S"),
            ),
        )
        await db.commit()


async def db_get_expired_users() -> list:
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT user_id, username FROM users WHERE expiry <= ? AND removed = 0",
            (now_str,),
        ) as cur:
            return await cur.fetchall()


async def db_mark_removed(user_id: int):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "UPDATE users SET removed = 1 WHERE user_id = ?", (user_id,)
        )
        await db.commit()


async def db_get_stats() -> dict:
    async with aiosqlite.connect(DB_PATH) as db:
        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE removed = 0"
        ) as cur:
            active = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT value FROM stats WHERE key = 'total_joins'"
        ) as cur:
            total = (await cur.fetchone())[0]

        async with db.execute(
            "SELECT COUNT(*) FROM users WHERE removed = 1"
        ) as cur:
            removed = (await cur.fetchone())[0]

    return {"active": active, "total": total, "removed": removed}

# =========================
# /start — SHOW BUTTONS
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("✅ Bot is active.")
        return

    keyboard = [
        [InlineKeyboardButton("🔗 Generate 30‑Min Invite Link", callback_data="generate")],
        [InlineKeyboardButton("📊 View Stats",                  callback_data="stats")],
    ]
    await update.message.reply_text(
        "👋 *Admin Panel* — choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )

# =========================
# BUTTON HANDLER
# =========================

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Not authorized.")
        return

    # ── Generate link ──────────────────────────────────────────────
    if query.data == "generate":
        expire_date = datetime.now() + timedelta(minutes=30)

        try:
            link_obj = await context.bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                member_limit=1,
                expire_date=expire_date,   # ← link expires on Telegram side too
            )
        except Exception as e:
            logger.error("create_chat_invite_link failed: %s", e)
            await query.edit_message_text(f"❌ Failed to create link:\n`{e}`", parse_mode="Markdown")
            return

        await db_add_link(link_obj.invite_link, expire_date)

        await query.edit_message_text(
            f"✅ *30‑Minute Invite Link*\n\n`{link_obj.invite_link}`\n\n"
            f"⏰ Expires at: `{expire_date.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"👤 Single‑use only",
            parse_mode="Markdown",
        )

    # ── Stats ───────────────────────────────────────────────────────
    elif query.data == "stats":
        s = await db_get_stats()
        await query.edit_message_text(
            f"📊 *Channel Statistics*\n\n"
            f"👥 Total joins ever: `{s['total']}`\n"
            f"✅ Currently active: `{s['active']}`\n"
            f"🚫 Removed (expired): `{s['removed']}`",
            parse_mode="Markdown",
        )

# =========================
# TRACK MEMBER JOIN
# =========================

async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Only care about our channel
    if update.chat_member.chat.id != CHANNEL_ID:
        return

    new = update.chat_member.new_chat_member
    old = update.chat_member.old_chat_member

    # User joined (wasn't a member before, now is)
    if old.status not in ("member", "administrator", "creator") and new.status == "member":
        invite = update.chat_member.invite_link
        if not invite:
            return

        link_str = invite.invite_link
        if not await db_link_exists(link_str):
            return   # Not one of our tracked links

        user    = new.user
        expiry  = datetime.now() + timedelta(minutes=30)

        await db_add_user(user.id, user.username, expiry)
        await db_remove_link(link_str)

        logger.info(
            "User %s (@%s) joined via tracked link. Expires at %s",
            user.id, user.username, expiry
        )

        # Notify admin
        try:
            await context.bot.send_message(
                ADMIN_ID,
                f"✅ *New member joined!*\n"
                f"👤 {user.full_name} (`{user.id}`)\n"
                f"⏰ Will be removed at: `{expiry.strftime('%Y-%m-%d %H:%M:%S')}`",
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning("Could not notify admin: %s", e)

# =========================
# REMOVE EXPIRED USERS
# =========================

async def remove_expired(context: ContextTypes.DEFAULT_TYPE):
    expired = await db_get_expired_users()

    for user_id, username in expired:
        try:
            # Ban kicks them from the channel
            await context.bot.ban_chat_member(
                chat_id=CHANNEL_ID,
                user_id=user_id,
            )
            # Immediately unban so they're not permanently blocked
            await context.bot.unban_chat_member(
                chat_id=CHANNEL_ID,
                user_id=user_id,
            )
            logger.info("Removed & unbanned user %s (@%s)", user_id, username)

            # Notify admin
            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"🚫 *Member removed* (30 min expired)\n"
                    f"👤 @{username} (`{user_id}`)",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        except Exception as e:
            logger.error("Failed to remove user %s: %s", user_id, e)

        # Mark removed regardless — don't retry forever
        await db_mark_removed(user_id)

# =========================
# MAIN
# =========================

async def post_init(application):
    await init_db()
    logger.info("Database initialised.")


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

    # Check for expired members every 60 seconds, starting 10 s after boot
    application.job_queue.run_repeating(remove_expired, interval=60, first=10)

    logger.info("Bot is running…")
    application.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
    
