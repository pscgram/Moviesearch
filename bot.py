import os
import json
import urllib.parse
import urllib.request
import threading
import asyncio
import re

from http.server import HTTPServer, BaseHTTPRequestHandler

from pymongo import MongoClient, ASCENDING
from pymongo.errors import PyMongoError

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup
)

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
MONGODB_URI = os.getenv("MONGODB_URI")

PORT = int(os.getenv("PORT", "10000"))


# =========================================================
# EXISTING ACCESS CHANNEL
# =========================================================

CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"


# =========================================================
# MOVIE DATABASE CHANNEL
# =========================================================

DATABASE_CHANNEL_ID = -1004463648734


# =========================================================
# MONGODB SETTINGS
# =========================================================

MONGODB_DATABASE = "pscgram"
MONGODB_COLLECTION = "movie_files"

mongo_client = None
movie_collection = None


# =========================================================
# USER REQUEST LIMIT
# =========================================================

# One movie request every 2 minutes per user
REQUEST_COOLDOWN = 120


# =========================================================
# INDEXING SETTINGS
# =========================================================

INDEX_WORKERS = 4
INDEX_QUEUE_SIZE = 5000

movie_index_queue = None


# =========================================================
# MONGODB INITIALIZATION
# =========================================================

def init_database():

    global mongo_client
    global movie_collection

    if not MONGODB_URI:

        raise ValueError(
            "MONGODB_URI is missing"
        )

    print("🔌 Connecting to MongoDB...")

    mongo_client = MongoClient(
        MONGODB_URI,

        serverSelectionTimeoutMS=10000,
        connectTimeoutMS=10000,
        socketTimeoutMS=30000,

        maxPoolSize=20,
        minPoolSize=1,

        retryWrites=True
    )

    # Test connection
    mongo_client.admin.command("ping")

    print(
        "✅ MongoDB connection successful"
    )

    database = mongo_client[
        MONGODB_DATABASE
    ]

    movie_collection = database[
        MONGODB_COLLECTION
    ]

    # Unique Telegram message ID
    movie_collection.create_index(
        [
            ("message_id", ASCENDING)
        ],
        unique=True
    )

    # Filename search index
    movie_collection.create_index(
        [
            ("filename_lower", ASCENDING)
        ]
    )

    print(
        "✅ MongoDB indexes ready"
    )


# =========================================================
# SAVE MOVIE FILE
# =========================================================

def save_movie_file(
    filename,
    message_id
):

    if movie_collection is None:

        raise RuntimeError(
            "MongoDB is not initialized"
        )

    filename_lower = (
        filename
        .strip()
        .lower()
    )

    movie_collection.update_one(

        {
            "message_id": message_id
        },

        {
            "$set": {
                "filename": filename,
                "filename_lower": filename_lower,
                "message_id": message_id
            }
        },

        upsert=True
    )


# =========================================================
# SEARCH MOVIE FILES
# =========================================================

def find_movie_files(
    search_text
):

    if movie_collection is None:

        raise RuntimeError(
            "MongoDB is not initialized"
        )

    search_text = (
        search_text
        .strip()
        .lower()
    )

    if not search_text:

        return []

    # Search only filenames
    # that START with the user's search
    escaped_text = re.escape(
        search_text
    )

    regex_pattern = (
        "^" + escaped_text
    )

    cursor = movie_collection.find(

        {
            "filename_lower": {
                "$regex": regex_pattern
            }
        },

        {
            "_id": 0,
            "filename": 1,
            "message_id": 1
        }

    ).sort(
        "filename_lower",
        ASCENDING
    )

    results = []

    for document in cursor:

        results.append(
            (
                document["filename"],
                document["message_id"]
            )
        )

    return results


# =========================================================
# BACKGROUND INDEX WORKER
# =========================================================

async def movie_index_worker(
    worker_id
):

    print(
        f"🗃️ MongoDB index worker "
        f"{worker_id} started"
    )

    while True:

        filename, message_id = (
            await movie_index_queue.get()
        )

        try:

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
                f"❌ Worker {worker_id} "
                f"indexing error: {error}"
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
            movie_index_worker(
                worker_id
            )
        )

    print(
        f"🚀 {INDEX_WORKERS} MongoDB "
        f"index workers started"
    )


# =========================================================
# RENDER HEALTH SERVER
# =========================================================

class HealthHandler(
    BaseHTTPRequestHandler
):

    def _send_health_response(
        self,
        include_body=True
    ):

        body = b"Bot is running!"

        self.send_response(200)

        self.send_header(
            "Content-Type",
            "text/plain"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.end_headers()

        if include_body:

            self.wfile.write(
                body
            )

    # UptimeRobot / normal browser
    def do_GET(self):

        self._send_health_response(
            include_body=True
        )

    # Some monitoring services use HEAD
    def do_HEAD(self):

        self._send_health_response(
            include_body=False
        )

    def log_message(
        self,
        format,
        *args
    ):

        pass


def web_server():

    server = HTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        HealthHandler
    )

    print(
        f"🌐 Health server running "
        f"on port {PORT}"
    )

    server.serve_forever()


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
            response
            .read()
            .decode()
        )


def search_movies(name):

    query = urllib.parse.quote(
        name
    )

    url = (
        "https://api.themoviedb.org/3/"
        "search/movie"
        "?query=" + query +
        "&include_adult=false"
        "&language=en-US"
        "&page=1"
    )

    return tmdb_request(
        url
    )


def movie_details(movie_id):

    url = (
        "https://api.themoviedb.org/3/"
        "movie/"
        + str(movie_id)
        + "?language=en-US"
    )

    return tmdb_request(
        url
    )


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

    user_id = (
        update.effective_user.id
    )

    unlocked_users = (
        context.application.bot_data
        .setdefault(
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

        reply_markup=
        join_channel_keyboard(),

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

        reply_markup=
        click_channel_keyboard()
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

        reply_markup=
        open_channel_keyboard()
    )

    # Wait 15 seconds
    await asyncio.sleep(15)

    try:

        await query.edit_message_text(

            "✅",

            reply_markup=
            continue_keyboard()
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

    user_id = (
        update.effective_user.id
    )

    unlocked_users = (
        context.application.bot_data
        .setdefault(
            "unlocked_users",
            set()
        )
    )

    unlocked_users.add(
        user_id
    )

    await query.edit_message_text(

        "🎉 <b>Access Granted!</b>\n\n"

        "✅ You can now use the "
        "Movie Search Bot.\n\n"

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

    # VIDEO
    if message.video:

        filename = (
            message.video.file_name
        )

    # DOCUMENT
    elif message.document:

        filename = (
            message.document.file_name
        )

    # NO FILE
    if not filename:

        return

    try:

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
            f"❌ Queue error: {error}"
        )


# =========================================================
# MOVIE SEARCH
# =========================================================

async def search(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user_id = (
        update.effective_user.id
    )

    # =====================================================
    # ACCESS CHECK
    # =====================================================

    unlocked_users = (
        context.application.bot_data
        .setdefault(
            "unlocked_users",
            set()
        )
    )

    if user_id not in unlocked_users:

        await update.message.reply_text(

            "🔒 Please join the channel first.",

            reply_markup=
            join_channel_keyboard()
        )

        return

    # =====================================================
    # GET MOVIE NAME
    # =====================================================

    name = (
        update.message.text
        .strip()
    )

    if not name:

        await update.message.reply_text(
            "❌ Please enter a movie name."
        )

        return

    # =====================================================
    # REQUEST LIMIT
    # =====================================================

    request_times = (
        context.application.bot_data
        .setdefault(
            "request_times",
            {}
        )
    )

    request_lock = (
        context.application.bot_data
        .setdefault(
            "request_lock",
            asyncio.Lock()
        )
    )

    async with request_lock:

        now = (
            asyncio.get_running_loop()
            .time()
        )

        last_request = (
            request_times.get(
                user_id
            )
        )

        if last_request is not None:

            elapsed = (
                now - last_request
            )

            if elapsed < REQUEST_COOLDOWN:

                remaining = int(
                    REQUEST_COOLDOWN
                    - elapsed
                )

                minutes = (
                    remaining // 60
                )

                seconds = (
                    remaining % 60
                )

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

                    "You can make another "
                    "movie request in "

                    f"<b>{wait_text}</b>.",

                    parse_mode="HTML"
                )

                return

        # Start cooldown
        request_times[user_id] = now

    # =====================================================
    # SEARCH MONGODB
    # =====================================================

    try:

        matching_files = (

            await asyncio.to_thread(

                find_movie_files,

                name
            )
        )

    except PyMongoError as error:

        print(
            f"❌ MongoDB search error: "
            f"{error}"
        )

        await update.message.reply_text(

            "⚠️ Database temporarily "
            "unavailable.\n\n"

            "Please try again later."
        )

        return

    except Exception as error:

        print(
            f"❌ Search error: "
            f"{error}"
        )

        await update.message.reply_text(

            "⚠️ Something went wrong.\n\n"

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

                    from_chat_id=
                    DATABASE_CHANNEL_ID,

                    message_id=message_id
                )

                await asyncio.sleep(
                    0.3
                )

            except Exception as error:

                print(

                    f"❌ Could not send "
                    f"{filename}: {error}"

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
# SHUTDOWN
# =========================================================

async def shutdown_database(
    application: Application
):

    global mongo_client

    if mongo_client:

        print(
            "🔌 Closing MongoDB connection..."
        )

        mongo_client.close()

        mongo_client = None


# =========================================================
# MAIN
# =========================================================

def main():

    # =====================================================
    # CHECK ENVIRONMENT
    # =====================================================

    if not BOT_TOKEN:

        raise ValueError(
            "BOT_TOKEN is missing"
        )

    if not TMDB_TOKEN:

        raise ValueError(
            "TMDB_TOKEN is missing"
        )

    if not MONGODB_URI:

        raise ValueError(
            "MONGODB_URI is missing"
        )

    # =====================================================
    # CONNECT MONGODB
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

        .token(
            BOT_TOKEN
        )

        .concurrent_updates(
            16
        )

        .post_init(
            start_index_workers
        )

        .post_shutdown(
            shutdown_database
        )

        .build()
    )

    # =====================================================
    # /START
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

            pattern=
            "^join_channel$"

        )

    )

    app.add_handler(

        CallbackQueryHandler(

            click_channel,

            pattern=
            "^click_channel$"

        )

    )

    app.add_handler(

        CallbackQueryHandler(

            continue_to_bot,

            pattern=
            "^continue_to_bot$"

        )

    )

    # =====================================================
    # DATABASE CHANNEL
    # =====================================================

    app.add_handler(

        MessageHandler(

            filters.UpdateType.CHANNEL_POST

            & filters.Chat(
                chat_id=
                DATABASE_CHANNEL_ID
            )

            & (

                filters.VIDEO

                | filters.Document.ALL

            ),

            index_database_file

        )

    )

    # =====================================================
    # USER MOVIE SEARCH
    # =====================================================

    app.add_handler(

        MessageHandler(

            filters.TEXT
            & ~filters.COMMAND,

            search

        )

    )

    # =====================================================
    # START BOT
    # =====================================================

    print(
        "🤖 Bot is running!"
    )

    print(
        "🗄️ Database: MongoDB Atlas"
    )

    print(
        "🎬 Movie indexing enabled"
    )

    print(
        "⏱️ Request cooldown: "
        f"{REQUEST_COOLDOWN} seconds"
    )

    app.run_polling(

        drop_pending_updates=False

    )


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    main()
