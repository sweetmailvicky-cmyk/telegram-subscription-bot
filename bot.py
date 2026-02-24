import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
)

BOT_TOKEN = "8453765782:AAENJEsrojZ2Dy-VwrCeU2vTFjBUof4G4oQ"
CHANNEL_ID = -1002565325480
ADMIN_ID = 206193281

app = ApplicationBuilder().token(BOT_TOKEN).build()

conn = sqlite3.connect("members.db")
c = conn.cursor()

c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    expiry TEXT
)
""")

c.execute("""
CREATE TABLE IF NOT EXISTS links (
    invite_link TEXT PRIMARY KEY,
    created_at TEXT
)
""")

conn.commit()

# =========================
# GENERATE LINK
# =========================

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text("❌ Not authorized.")
        return

    if update.effective_chat.type != "private":
        await update.message.reply_text("❌ Use in private chat only.")
        return

    link = await context.bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        member_limit=1
    )

    c.execute(
        "INSERT INTO links VALUES (?, ?)",
        (link.invite_link, datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    )
    conn.commit()

    await update.message.reply_text(f"✅ 1 Day Link:\n{link.invite_link}")

# =========================
# TRACK MEMBER USING INVITE LINK
# =========================

async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.chat.id != CHANNEL_ID:
        return

    member = update.chat_member.new_chat_member

    if member.status == "member":

        invite_link = update.chat_member.invite_link

        if invite_link:
            link_used = invite_link.invite_link

            c.execute("SELECT invite_link FROM links WHERE invite_link=?", (link_used,))
            result = c.fetchone()

            if result:
                user_id = member.user.id
                expiry_time = datetime.now() + timedelta(days=1)

                c.execute(
                    "INSERT OR REPLACE INTO users VALUES (?, ?)",
                    (user_id, expiry_time.strftime("%Y-%m-%d %H:%M:%S"))
                )

                c.execute("DELETE FROM links WHERE invite_link=?", (link_used,))
                conn.commit()

# =========================
# REMOVE EXPIRED USERS
# =========================

async def remove_expired(context: ContextTypes.DEFAULT_TYPE):

    now = datetime.now()

    c.execute("SELECT user_id, expiry FROM users")
    rows = c.fetchall()

    for user_id, expiry in rows:

        expiry_time = datetime.strptime(expiry, "%Y-%m-%d %H:%M:%S")

        if now > expiry_time:
            try:
                await context.bot.ban_chat_member(CHANNEL_ID, user_id)
                await context.bot.unban_chat_member(CHANNEL_ID, user_id)
            except:
                pass

            c.execute("DELETE FROM users WHERE user_id=?", (user_id,))
            conn.commit()

# =========================
# STATS
# =========================

async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.effective_user.id != ADMIN_ID:
        return

    c.execute("SELECT COUNT(*) FROM users")
    total = c.fetchone()[0]

    await update.message.reply_text(f"📊 Active Subscribers: {total}")

# =========================
# HANDLERS
# =========================

app.add_handler(CommandHandler("generate", generate))
app.add_handler(CommandHandler("stats", stats))
app.add_handler(ChatMemberHandler(track_member, ChatMemberHandler.CHAT_MEMBER))

app.job_queue.run_daily(
    remove_expired,
    time=datetime.strptime("01:00", "%H:%M").time()
)

app.run_polling()
