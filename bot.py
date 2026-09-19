import os
import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Welcome to Movie Bot!\n\n"
        "🔎 Send me a movie name to search.\n\n"
        "Example: Avatar"
    )

async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_name = update.message.text.strip()

    await update.message.reply_text(
        f"🔎 Searching for:\n\n"
        f"🎬 {movie_name}\n\n"
        f"⏳ Please wait..."
    )

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(
        MessageHandler(filters.TEXT & ~filters.COMMAND, search_movie)
    )

    print("🤖 Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()
