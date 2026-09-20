import os
import json
import urllib.parse
import urllib.request
import threading
import asyncio
import sqlite3
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_TOKEN = os.getenv("TMDB_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

# =========================================================
# CHANNEL SETTINGS
# =========================================================

CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"

DATABASE_CHANNEL_ID = -1004463648734

DB_FILE = "movie_files.db"


# =========================================================
# SQLITE DATABASE
# =========================================================

def init_database():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movie_files (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            filename TEXT NOT NULL,
            filename_lower TEXT NOT NULL,
            message_id INTEGER NOT NULL UNIQUE
        )
    """)

    conn.commit()
    conn.close()


def save_movie_file(filename, message_id):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO movie_files
        (filename, filename_lower, message_id)
        VALUES (?, ?, ?)
    """, (
        filename,
        filename.lower(),
        message_id
    ))

    conn.commit()
    conn.close()


def find_movie_files(search_text):

    search_text = search_text.strip().lower()

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT filename, message_id
        FROM movie_files
        WHERE filename_lower LIKE ?
        ORDER BY filename_lower ASC
    """, (
        search_text + "%",
    ))

    results = cursor.fetchall()

    conn.close()

    return results


# =========================================================
# HEALTH SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):

        self.send_response(200)
        self.end_headers()

        self.wfile.write(
            b"Bot is running!"
        )

    def log_message(self, format, *args):
        pass


def web_server():

    HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    ).serve_forever()


# =========================================================
# TMDB
# =========================================================

def tmdb_request(url):

    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + TMDB_TOKEN,
            "accept": "application/json"
        }
    )

    with urllib.request.urlopen(
        req,
        timeout=15
    ) as response:

        return json.loads(
            response.read().decode()
        )


def search_movies(name):

    query = urllib.parse.quote(name)

    url = (
        "https://api.themoviedb.org/3/search/movie"
        "?query=" + query +
        "&include_adult=false"
        "&language=en-US"
        "&page=1"
    )

    return tmdb_request(url)


def movie_details(movie_id):

    url = (
        "https://api.themoviedb.org/3/movie/"
        + str(movie_id)
        + "?language=en-US"
    )

    return tmdb_request(url)


# =========================================================
# ACCESS BUTTONS
# =========================================================

def join_channel_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Join Channel",
                callback_data="join_channel"
            )
        ]
    ])


def click_channel_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Click Channel",
                callback_data="click_channel"
            )
        ]
    ])


def open_channel_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔗 Open Private Channel",
                url=CHANNEL_LINK
            )
        ]
    ])


def continue_keyboard():

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Continue to Bot",
                callback_data="continue_to_bot"
            )
        ]
    ])


# =========================================================
# START
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    unlocked_users = context.application.bot_data.setdefault(
        "unlocked_users",
        set()
    )

    if user_id in unlocked_users:

        await update.message.reply_text(
            "🎬 <b>Welcome back!</b>\n\n"
            "Send me a movie name.\n\n"
            "Example: Avatar",
            parse_mode="HTML"
        )

        return

    await update.message.reply_text(
        "🎬 <b>Welcome to Movie Search Bot!</b>\n\n"
        "🔒 Join our channel to continue.",
        reply_markup=join_channel_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# STEP 1 - JOIN CHANNEL
# =========================================================

async def join_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🔔",
        reply_markup=click_channel_keyboard()
    )


# =========================================================
# STEP 2 - CLICK CHANNEL
# =========================================================

async def click_channel(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🔔",
        reply_markup=open_channel_keyboard()
    )

    await asyncio.sleep(15)

    try:

        await query.edit_message_text(
            "✅",
            reply_markup=continue_keyboard()
        )

    except Exception:
        pass


# =========================================================
# STEP 3 - CONTINUE
# =========================================================

async def continue_to_bot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    unlocked_users = context.application.bot_data.setdefault(
        "unlocked_users",
        set()
    )

    unlocked_users.add(user_id)

    await query.edit_message_text(
        "🎉 <b>Access Granted!</b>\n\n"
        "✅ You can now use the Movie Search Bot.\n\n"
        "🎬 Send me a movie name.\n\n"
        "Example: Avatar",
        parse_mode="HTML"
    )


# =========================================================
# INDEX MOVIE FILES
# =========================================================

async def index_database_file(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.channel_post

    if not message:
        return

    if message.chat_id != DATABASE_CHANNEL_ID:
        return

    filename = None

    if message.video:

        filename = message.video.file_name

    elif message.document:

        filename = message.document.file_name

    if not filename:
        return

    try:

        save_movie_file(
            filename,
            message.message_id
        )

        print(
            f"Movie indexed: {filename} "
            f"(message {message.message_id})"
        )

    except Exception as error:

        print(
            "Database indexing error:",
            error
        )


# =========================================================
# MOVIE SEARCH
# =========================================================

async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = update.effective_user.id

    unlocked_users = context.application.bot_data.setdefault(
        "unlocked_users",
        set()
    )

    if user_id not in unlocked_users:

        await update.message.reply_text(
            "🔒 Please join the channel first.",
            reply_markup=join_channel_keyboard()
        )

        return

    name = update.message.text.strip()

    matching_files = find_movie_files(name)

    if matching_files:

        await update.message.reply_text(
            f"🎬 <b>{len(matching_files)} file(s) found</b>\n\n"
            f"🔎 Search: <b>{name}</b>\n\n"
            "📤 Sending movie files...",
            parse_mode="HTML"
        )

        for filename, message_id in matching_files:

            try:

                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=DATABASE_CHANNEL_ID,
                    message_id=message_id
                )

                await asyncio.sleep(0.3)

            except Exception as error:

                print(
                    f"Could not send {filename}:",
                    error
                )

        return

    await update.message.reply_text(
        "❌ <b>Movie Not Available</b>\n\n"
        f"🔎 <b>{name}</b> is not available "
        "in our movie database.",
        parse_mode="HTML"
    )


# =========================================================
# MAIN
# =========================================================

def main():

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN is missing"
        )

    if not TMDB_TOKEN:

        raise ValueError(
            "TMDB_TOKEN is missing"
        )

    init_database()

    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .concurrent_updates(16)
        .build()
    )

    # START
    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # ACCESS FLOW
    app.add_handler(
        CallbackQueryHandler(
            join_channel,
            pattern="^join_channel$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            click_channel,
            pattern="^click_channel$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            continue_to_bot,
            pattern="^continue_to_bot$"
        )
    )

    # DATABASE CHANNEL
    app.add_handler(
        MessageHandler(
            filters.UpdateType.CHANNEL_POST
            & filters.Chat(
                chat_id=DATABASE_CHANNEL_ID
            )
            & (
                filters.VIDEO
                | filters.Document.ALL
            ),
            index_database_file
        )
    )

    # USER SEARCH
    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            search
        )
    )

    print(
        "🤖 Bot is running!"
    )

    # IMPORTANT:
    # Clear the old backlog of Telegram updates once.
    app.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
