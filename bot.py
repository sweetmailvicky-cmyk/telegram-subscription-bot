import sqlite3
from datetime import datetime, timedelta
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    ChatMemberHandler,
)

# =========================
# CONFIGURATION
# =========================

BOT_TOKEN = "8453765782:AAENJEsrojZ2Dy-VwrCeU2vTFjBUof4G4oQ"
CHANNEL_ID = -1002565325480
ADMIN_ID = 206193281

# =========================
# INIT
# =========================

app = ApplicationBuilder().token(BOT_TOKEN).build()

conn = sqlite3.connect("members.db")
c = conn.cursor()
c.execute("""
CREATE TABLE IF NOT EXISTS users (
    user_id TEXT PRIMARY KEY,
    expiry TEXT
)
""")
conn.commit()

# =========================
# GENERATE LINK (STRICT LOCK)
# =========================

async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id
    chat_type = update.effective_chat.type

    # Allow only admin ID
    if user_id != ADMIN_ID:
        await update.message.reply_text("❌ You are not authorized to use this command.")
        return

    # Allow only in private chat
    if chat_type != "private":
        await update.message.reply_text("❌ Use this command in private chat only.")
        return

    link = await context.bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        member_limit=1
    )

    await update.message.reply_text(
        f"✅ 1 Day Channel Link:\n{link.invite_link}"
    )

# =========================
# TRACK NEW MEMBER (1 DAY EXPIRY)
# =========================

async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):

    if update.chat_member.new_chat_member.status == "member":

        user_id = update.chat_member.new_chat_member.user.id
        expiry_time = datetime.now() + timedelta(days=1)

        c.execute(
            "INSERT OR REPLACE INTO users VALUES (?, ?)",
            (user_id, expiry_time.strftime("%Y-%m-%d %H:%M:%S"))
        )
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
# HANDLERS
# =========================

app.add_handler(CommandHandler("generate", generate))
app.add_handler(ChatMemberHandler(track_member, ChatMemberHandler.CHAT_MEMBER))

app.job_queue.run_daily(
    remove_expired,
    time=datetime.strptime("01:00", "%H:%M").time()
)

# =========================
# START
# =========================

app.run_polling()

