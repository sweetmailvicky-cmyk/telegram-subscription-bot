#!/usr/bin/env python3
"""
Telegram Channel Access Bot
- Admin generates a single-use 30-min invite link
- User joins via link, gets auto-removed after 30 minutes
- Admin gets notified on join and removal
- Stats tracked: total joins, active, removed
"""

import logging
import asyncio
import aiosqlite
from datetime import datetime, timedelta

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, Bot
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    CallbackQueryHandler,
    ChatMemberHandler,
)

# ==================================================
# LOGGING — leave DEBUG on until everything works
# ==================================================
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.DEBUG
)
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

# ==================================================
# ✏️  EDIT THESE 3 VALUES ONLY
# ==================================================
BOT_TOKEN  = "8453765782:AAENJEsrojZ2Dy-VwrCeU2vTFjBUof4G4oQ"   # from @BotFather
CHANNEL_ID = -1002565325480                 # your channel/group id (negative)
ADMIN_ID   = 206193281                      # your personal telegram user id
# ==================================================

DB_PATH = "members.db"


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
        await db.execute(
            "INSERT OR IGNORE INTO stats(key,value) VALUES('total_joins',0)"
        )
        await db.commit()
    logger.info("✅ Database ready.")


async def db_add_link(invite_link: str, expire_date: datetime):
    async with aiosqlite.connect(DB_PATH) as db:
        await db.execute(
            "INSERT OR REPLACE INTO links(invite_link,created_at,expire_date) VALUES(?,?,?)",
            (invite_link,
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             expire_date.strftime("%Y-%m-%d %H:%M:%S"))
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
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             expiry.strftime("%Y-%m-%d %H:%M:%S"))
        )
        await db.execute(
            "UPDATE stats SET value=value+1 WHERE key='total_joins'"
        )
        await db.commit()


async def db_get_expired_users():
    now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
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


# ──────────────────────────────────────────────────
# HANDLERS
# ──────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.info("✅ /start received from user_id=%s", update.effective_user.id)

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("✅ Bot is active.")
        return

    keyboard = [
        [InlineKeyboardButton("🔗 Generate 30-Min Invite Link", callback_data="generate")],
        [InlineKeyboardButton("📊 View Stats",                  callback_data="stats")],
    ]
    await update.message.reply_text(
        "👋 *Admin Panel* — Choose an option:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode="Markdown",
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    logger.info("🔘 Button pressed: %s by user_id=%s", query.data, update.effective_user.id)

    if update.effective_user.id != ADMIN_ID:
        await query.edit_message_text("❌ Not authorized.")
        return

    if query.data == "generate":
        expire_date = datetime.now() + timedelta(minutes=30)

        try:
            link_obj = await context.bot.create_chat_invite_link(
                chat_id=CHANNEL_ID,
                member_limit=1,
                expire_date=expire_date,
            )
        except Exception as e:
            logger.error("❌ create_chat_invite_link error: %s", e)
            await query.edit_message_text(
                f"❌ Failed to create link.\n\nError: `{e}`\n\n"
                f"Make sure the bot is an *Admin* in the channel with invite permissions.",
                parse_mode="Markdown"
            )
            return

        await db_add_link(link_obj.invite_link, expire_date)
        logger.info("🔗 Link created: %s | expires: %s", link_obj.invite_link, expire_date)

        await query.edit_message_text(
            f"✅ *30-Minute Invite Link Generated*\n\n"
            f"`{link_obj.invite_link}`\n\n"
            f"⏰ Expires: `{expire_date.strftime('%Y-%m-%d %H:%M:%S')}`\n"
            f"👤 Single-use only",
            parse_mode="Markdown",
        )

    elif query.data == "stats":
        s = await db_get_stats()
        await query.edit_message_text(
            f"📊 *Channel Statistics*\n\n"
            f"🔢 Total joined ever : `{s['total']}`\n"
            f"✅ Currently active  : `{s['active']}`\n"
            f"🚫 Removed (expired) : `{s['removed']}`",
            parse_mode="Markdown",
        )


async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.debug("📡 chat_member update: chat_id=%s", update.chat_member.chat.id)

    if update.chat_member.chat.id != CHANNEL_ID:
        return

    old = update.chat_member.old_chat_member
    new = update.chat_member.new_chat_member

    logger.info("👤 Status change: user=%s | %s → %s", new.user.id, old.status, new.status)

    if old.status in ("member", "administrator", "creator"):
        return
    if new.status != "member":
        return

    invite = update.chat_member.invite_link
    if not invite:
        logger.info("ℹ️ User %s joined without a tracked invite link.", new.user.id)
        return

    link_str = invite.invite_link
    logger.info("🔗 Invite link used: %s", link_str)

    if not await db_link_exists(link_str):
        logger.info("ℹ️ Link not in our DB, ignoring.")
        return

    user   = new.user
    expiry = datetime.now() + timedelta(minutes=30)

    await db_add_user(user.id, user.username, expiry)
    await db_remove_link(link_str)

    logger.info("✅ Tracked user %s (@%s) — expires %s", user.id, user.username, expiry)

    try:
        await context.bot.send_message(
            ADMIN_ID,
            f"✅ *New Member Joined!*\n\n"
            f"👤 Name    : {user.full_name}\n"
            f"🆔 ID      : `{user.id}`\n"
            f"📛 Username: @{user.username or 'N/A'}\n"
            f"⏰ Removes at: `{expiry.strftime('%Y-%m-%d %H:%M:%S')}`",
            parse_mode="Markdown",
        )
    except Exception as e:
        logger.warning("Could not notify admin: %s", e)


async def remove_expired(context: ContextTypes.DEFAULT_TYPE):
    expired = await db_get_expired_users()

    if expired:
        logger.info("⏰ Removing %d expired user(s).", len(expired))

    for user_id, username in expired:
        try:
            await context.bot.ban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            await asyncio.sleep(1)
            await context.bot.unban_chat_member(chat_id=CHANNEL_ID, user_id=user_id)
            logger.info("🚫 Removed & unbanned user %s (@%s)", user_id, username)

            try:
                await context.bot.send_message(
                    ADMIN_ID,
                    f"🚫 *Member Removed* (30 min expired)\n\n"
                    f"👤 @{username}\n"
                    f"🆔 `{user_id}`",
                    parse_mode="Markdown",
                )
            except Exception:
                pass

        except Exception as e:
            logger.error("❌ Failed to remove user %s: %s", user_id, e)

        await db_mark_removed(user_id)


# ──────────────────────────────────────────────────
# STARTUP TOKEN CHECK
# ──────────────────────────────────────────────────

async def verify_token() -> bool:
    try:
        bot = Bot(token=BOT_TOKEN)
        async with bot:
            me = await bot.get_me()
        logger.info("✅ Token OK — Bot: %s (@%s)", me.full_name, me.username)
        return True
    except Exception as e:
        logger.error("❌ Token INVALID: %s", e)
        return False


async def post_init(application):
    await init_db()


# ──────────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────────

def main():
    print("\n" + "="*50)
    print("  Telegram Access Bot Starting...")
    print("="*50 + "\n")

    # Verify token first
    if not asyncio.run(verify_token()):
        print("\n❌ INVALID TOKEN — Open @BotFather → /mybots → API Token\n")
        return

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

    application.job_queue.run_repeating(remove_expired, interval=60, first=10)

    print("\n✅ Bot is running! Open Telegram and send /start to your bot.\n")

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
