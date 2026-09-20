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

# =========================================================
# SETTINGS
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_TOKEN = os.getenv("TMDB_TOKEN")
PORT = int(os.getenv("PORT", "10000"))

# Existing access channel
CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"

# Movie Database Channel
DATABASE_CHANNEL_ID = -1004463648734

# SQLite database
DB_FILE = "movie_files.db"

# =========================================================
# USER REQUEST LIMIT
# =========================================================

# One movie request every 2 minutes per user
REQUEST_COOLDOWN = 120

# =========================================================
# INDEXING SETTINGS
# =========================================================

# Number of background SQLite workers
INDEX_WORKERS = 4

# Maximum files waiting in the indexing queue
INDEX_QUEUE_SIZE = 5000

movie_index_queue = None


# =========================================================
# SQLITE DATABASE
# =========================================================

def init_database():

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    try:

        cursor = conn.cursor()

        # Allow readers while database is being written
        cursor.execute(
            "PRAGMA journal_mode=WAL"
        )

        cursor.execute(
            "PRAGMA busy_timeout=30000"
        )

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS movie_files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                filename TEXT NOT NULL,
                filename_lower TEXT NOT NULL,
                message_id INTEGER NOT NULL UNIQUE
            )
        """)

        # Fast filename search
        cursor.execute("""
            CREATE INDEX IF NOT EXISTS idx_filename_lower
            ON movie_files(filename_lower)
        """)

        conn.commit()

    finally:

        conn.close()


# =========================================================
# SAVE MOVIE FILE
# =========================================================

def save_movie_file(filename, message_id):

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    try:

        conn.execute(
            "PRAGMA busy_timeout=30000"
        )

        conn.execute("""
            INSERT OR REPLACE INTO movie_files
            (
                filename,
                filename_lower,
                message_id
            )
            VALUES (?, ?, ?)
        """, (
            filename,
            filename.lower(),
            message_id
        ))

        conn.commit()

    finally:

        conn.close()


# =========================================================
# SEARCH MOVIE FILES
# =========================================================

def find_movie_files(search_text):

    search_text = search_text.strip().lower()

    conn = sqlite3.connect(
        DB_FILE,
        timeout=30
    )

    try:

        conn.execute(
            "PRAGMA busy_timeout=30000"
        )

        cursor = conn.cursor()

        # Filename MUST start with search text
        cursor.execute("""
            SELECT filename, message_id
            FROM movie_files
            WHERE filename_lower LIKE ?
            ORDER BY filename_lower ASC
        """, (
            search_text + "%",
        ))

        return cursor.fetchall()

    finally:

        conn.close()


# =========================================================
# BACKGROUND INDEX WORKER
# =========================================================

async def movie_index_worker(worker_id):

    print(
        f"🗃️ Index worker {worker_id} started"
    )

    while True:

        filename, message_id = (
            await movie_index_queue.get()
        )

        try:

            # SQLite runs outside Telegram event loop
            await asyncio.to_thread(
                save_movie_file,
                filename,
                message_id
            )

            print(
                f"✅ Worker {worker_id}: "
                f"{filename} indexed "
                f"(message {message_id})"
            )

        except Exception as error:

            print(
                f"❌ Worker {worker_id} error:",
                error
            )

        finally:

            movie_index_queue.task_done()


# =========================================================
# START INDEX WORKERS
# =========================================================

async def start_index_workers(
    application: Application
):

    global movie_index_queue

    movie_index_queue = asyncio.Queue(
        maxsize=INDEX_QUEUE_SIZE
    )

    for worker_id in range(
        1,
        INDEX_WORKERS + 1
    ):

        application.create_task(
            movie_index_worker(worker_id)
        )

    print(
        f"🚀 {INDEX_WORKERS} movie index workers started"
    )


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def do_GET(self):

        self.send_response(200)

        self.end_headers()

        self.wfile.write(
            b"Bot is running!"
        )

    def log_message(
        self,
        format,
        *args
    ):

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
            "Authorization":
                "Bearer " + TMDB_TOKEN,
            "accept":
                "application/json"
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

    unlocked_users = (
        context.application.bot_data.setdefault(
            "unlocked_users",
            set()
        )
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
# STEP 1
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
# STEP 2
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

    # Wait 15 seconds
    await asyncio.sleep(15)

    try:

        await query.edit_message_text(
            "✅",
            reply_markup=continue_keyboard()
        )

    except Exception:

        pass


# =========================================================
# STEP 3
# =========================================================

async def continue_to_bot(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user_id = update.effective_user.id

    unlocked_users = (
        context.application.bot_data.setdefault(
            "unlocked_users",
            set()
        )
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
# DATABASE CHANNEL
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

    # Video file
    if message.video:

        filename = message.video.file_name

    # Document file
    elif message.document:

        filename = message.document.file_name

    if not filename:

        return

    try:

        # IMPORTANT:
        # Do NOT write to SQLite here.
        # Just queue the file.

        await movie_index_queue.put(
            (
                filename,
                message.message_id
            )
        )

        print(
            f"📥 Queued: {filename} "
            f"(message {message.message_id})"
        )

    except Exception as error:

        print(
            "❌ Queue error:",
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

    unlocked_users = (
        context.application.bot_data.setdefault(
            "unlocked_users",
            set()
        )
    )

    # =====================================================
    # ACCESS CHECK
    # =====================================================

    if user_id not in unlocked_users:

        await update.message.reply_text(
            "🔒 Please join the channel first.",
            reply_markup=join_channel_keyboard()
        )

        return

    # =====================================================
    # REQUEST LIMIT
    # =====================================================

    request_times = (
        context.application.bot_data.setdefault(
            "request_times",
            {}
        )
    )

    now = asyncio.get_running_loop().time()

    last_request = request_times.get(
        user_id
    )

    if last_request is not None:

        elapsed = now - last_request

        if elapsed < REQUEST_COOLDOWN:

            remaining = int(
                REQUEST_COOLDOWN - elapsed
            )

            minutes = remaining // 60

            seconds = remaining % 60

            if minutes > 0:

                wait_text = (
                    f"{minutes} minute(s) "
                    f"{seconds} second(s)"
                )

            else:

                wait_text = (
                    f"{seconds} second(s)"
                )

            await update.message.reply_text(
                "⏳ <b>Please wait.</b>\n\n"
                "You can make another movie "
                "request in "
                f"<b>{wait_text}</b>.",
                parse_mode="HTML"
            )

            return

    # =====================================================
    # START COOLDOWN
    # =====================================================

    request_times[user_id] = now

    # =====================================================
    # MOVIE NAME
    # =====================================================

    name = update.message.text.strip()

    if not name:

        await update.message.reply_text(
            "❌ Please enter a movie name."
        )

        return

    # =====================================================
    # DATABASE SEARCH
    # =====================================================

    try:

        matching_files = (
            await asyncio.to_thread(
                find_movie_files,
                name
            )
        )

    except Exception as error:

        print(
            "❌ Search database error:",
            error
        )

        await update.message.reply_text(
            "⚠️ Database temporarily busy.\n\n"
            "Please try again later."
        )

        return

    # =====================================================
    # FILES FOUND
    # =====================================================

    if matching_files:

        await update.message.reply_text(
            f"🎬 <b>{len(matching_files)} "
            f"file(s) found</b>\n\n"
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

                # Protect against Telegram flooding
                await asyncio.sleep(0.3)

            except Exception as error:

                print(
                    f"❌ Could not send "
                    f"{filename}:",
                    error
                )

        return

    # =====================================================
    # NOT AVAILABLE
    # =====================================================

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

    # =====================================================
    # ENVIRONMENT
    # =====================================================

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN is missing"
        )

    if not TMDB_TOKEN:

        raise ValueError(
            "TMDB_TOKEN is missing"
        )

    # =====================================================
    # DATABASE
    # =====================================================

    init_database()

    # =====================================================
    # RENDER HEALTH SERVER
    # =====================================================

    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    # =====================================================
    # TELEGRAM APPLICATION
    # =====================================================

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .concurrent_updates(16)
        .post_init(start_index_workers)
        .build()
    )

    # =====================================================
    # START COMMAND
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # =====================================================
    # ACCESS FLOW
    # =====================================================

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

    # =====================================================
    # MOVIE DATABASE CHANNEL
    # =====================================================

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

    # =====================================================
    # USER SEARCH
    # =====================================================

    app.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            search
        )
    )

    # =====================================================
    # START
    # =====================================================

    print(
        "🤖 Bot is running!"
    )

    app.run_polling(
        drop_pending_updates=False
    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
