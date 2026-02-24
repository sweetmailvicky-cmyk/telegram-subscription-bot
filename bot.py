from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, ChatMemberHandler
from datetime import datetime, timedelta
import sqlite3

BOT_TOKEN = "8453765782:AAEZU4wmKuwU6pUE9fA-lw0G-2khvkS2t2k"
CHANNEL_ID = -1002565325480

app = ApplicationBuilder().token(BOT_TOKEN).build()

conn = sqlite3.connect("members.db")
c = conn.cursor()
c.execute("CREATE TABLE IF NOT EXISTS users (user_id TEXT, expiry TEXT)")
conn.commit()

# Generate 1 month invite link
async def generate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    expire = datetime.now() + timedelta(days=1)

    link = await context.bot.create_chat_invite_link(
        chat_id=CHANNEL_ID,
        member_limit=1
    )

    await update.message.reply_text(f"1 Day Channel Link:\n{link.invite_link}")

# Track new subscribers
async def track_member(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.chat_member.new_chat_member.status == "member":
        user_id = update.chat_member.new_chat_member.user.id
        expiry = datetime.now() + timedelta(days=30)

        c.execute("INSERT INTO users VALUES (?, ?)",
                  (user_id, expiry.strftime("%Y-%m-%d")))
        conn.commit()

# Remove expired users
async def remove_expired(context: ContextTypes.DEFAULT_TYPE):
    today = datetime.now().strftime("%Y-%m-%d")

    c.execute("SELECT user_id FROM users WHERE expiry < ?", (today,))
    users = c.fetchall()

    for user in users:
        await context.bot.ban_chat_member(CHANNEL_ID, user[0])
        await context.bot.unban_chat_member(CHANNEL_ID, user[0])
        c.execute("DELETE FROM users WHERE user_id=?", (user[0],))
        conn.commit()

app.add_handler(CommandHandler("generate", generate))
app.add_handler(ChatMemberHandler(track_member, ChatMemberHandler.CHAT_MEMBER))

app.job_queue.run_daily(remove_expired, time=datetime.strptime("01:00","%H:%M").time())


app.run_polling()
