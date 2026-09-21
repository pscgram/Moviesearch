import os
import json
import urllib.parse
import urllib.request
import asyncio
import re
import hashlib

from aiohttp import web

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
    ChatJoinRequestHandler,
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

RENDER_EXTERNAL_URL = os.getenv("RENDER_EXTERNAL_URL")


# =========================================================
# EXISTING ACCESS CHANNEL
# =========================================================

ACCESS_CHANNEL_ID = -1004290623496

CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"


# =========================================================
# MOVIE DATABASE CHANNEL
# =========================================================

DATABASE_CHANNEL_ID = -1004463648734


# =========================================================
# LOG CHANNEL
# =========================================================

LOG_CHANNEL_ID = -1004407816036


# =========================================================
# MONGODB
# =========================================================

MONGODB_DATABASE = "pscgram"
MONGODB_COLLECTION = "movie_files"

mongo_client = None
movie_collection = None


# =========================================================
# USER REQUEST LIMIT
# =========================================================

REQUEST_COOLDOWN = 120


# =========================================================
# INDEXING
# =========================================================

INDEX_WORKERS = 4
INDEX_QUEUE_SIZE = 5000

movie_index_queue = None


# =========================================================
# WEBHOOK
# =========================================================

WEBHOOK_PATH = "/telegram"

WEBHOOK_SECRET = hashlib.sha256(
    (BOT_TOKEN or "").encode()
).hexdigest()


# =========================================================
# MONGODB INITIALIZATION
# =========================================================

def init_database():

    global mongo_client
    global movie_collection

    if not MONGODB_URI:
        raise ValueError("MONGODB_URI is missing")

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

    mongo_client.admin.command("ping")

    print("✅ MongoDB connection successful")

    database = mongo_client[MONGODB_DATABASE]

    movie_collection = database[MONGODB_COLLECTION]

    movie_collection.create_index(
        [("message_id", ASCENDING)],
        unique=True
    )

    movie_collection.create_index(
        [("filename_lower", ASCENDING)]
    )

    print("✅ MongoDB indexes ready")


# =========================================================
# SAVE MOVIE
# =========================================================

def save_movie_file(filename, message_id):

    if movie_collection is None:
        raise RuntimeError(
            "MongoDB is not initialized"
        )

    filename_lower = filename.strip().lower()

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
# SEARCH MOVIES
# =========================================================

def find_movie_files(search_text):

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

    escaped_text = re.escape(search_text)

    regex_pattern = "^" + escaped_text

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
# INDEX WORKER
# =========================================================

async def movie_index_worker(worker_id):

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

async def start_index_workers(application):

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
        f"🚀 {INDEX_WORKERS} MongoDB "
        f"index workers started"
    )


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

    query = urllib.parse.quote(name)

    url = (
        "https://api.themoviedb.org/3/"
        "search/movie"
        "?query=" + query +
        "&include_adult=false"
        "&language=en-US"
        "&page=1"
    )

    return tmdb_request(url)


def movie_details(movie_id):

    url = (
        "https://api.themoviedb.org/3/"
        "movie/"
        + str(movie_id)
        + "?language=en-US"
    )

    return tmdb_request(url)


# =========================================================
# LOGGING HELPERS
# =========================================================

def get_user_name(user):

    if not user:
        return "Unknown"

    name = " ".join(
        part for part in [
            user.first_name,
            user.last_name
        ]
        if part
    )

    if not name:
        name = "Unknown"

    if user.username:
        return f"{name} (@{user.username})"

    return name


async def send_log(context, text):

    try:

        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=text,
            disable_web_page_preview=True
        )

    except Exception as error:

        print(
            f"⚠️ Log channel error: {error}"
        )


async def log_new_user(update, context):

    user = update.effective_user

    if not user:
        return

    logged_users = (
        context.application.bot_data
        .setdefault(
            "logged_users",
            set()
        )
    )

    # Only log a user the first time
    if user.id in logged_users:
        return

    logged_users.add(user.id)

    text = (
        "👤 <b>NEW BOT USER</b>\n\n"
        f"Name: {get_user_name(user)}\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Username: "
        f"{('@' + user.username) if user.username else 'None'}\n"
        f"Language: "
        f"{user.language_code or 'Unknown'}"
    )

    await send_log(
        context,
        text
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

async def start(update, context):

    user = update.effective_user

    if not user:
        return

    await log_new_user(
        update,
        context
    )

    user_id = user.id

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
        reply_markup=join_channel_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# STEP 1
# =========================================================

async def join_channel(update, context):

    query = update.callback_query

    await query.answer()

    await query.edit_message_text(
        "🔔",
        reply_markup=click_channel_keyboard()
    )


# =========================================================
# STEP 2
# =========================================================

async def click_channel(update, context):

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
# STEP 3
# =========================================================

async def continue_to_bot(update, context):

    query = update.callback_query

    await query.answer()

    user = update.effective_user

    user_id = user.id

    unlocked_users = (
        context.application.bot_data
        .setdefault(
            "unlocked_users",
            set()
        )
    )

    unlocked_users.add(user_id)

    await send_log(
        context,
        "🎉 <b>ACCESS GRANTED</b>\n\n"
        f"Name: {get_user_name(user)}\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Username: "
        f"{('@' + user.username) if user.username else 'None'}"
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
# JOIN REQUEST LOGGING
# =========================================================

async def join_request_handler(
    update,
    context
):

    request = update.chat_join_request

    if not request:
        return

    user = request.from_user

    invite_link = "Unknown"

    if request.invite_link:

        invite_link = (
            request.invite_link.invite_link
        )

    try:

        await send_log(
            context,
            "📩 <b>NEW JOIN REQUEST</b>\n\n"
            f"Name: {get_user_name(user)}\n"
            f"User ID: <code>{user.id}</code>\n"
            f"Username: "
            f"{('@' + user.username) if user.username else 'None'}\n"
            f"Channel ID: <code>{request.chat.id}</code>\n"
            f"Invite Link: {invite_link}\n"
            f"Request Time: {request.date}"
        )

    except Exception as error:

        print(
            f"❌ Join request log error: "
            f"{error}"
        )


# =========================================================
# DATABASE CHANNEL
# =========================================================

async def index_database_file(update, context):

    message = update.channel_post

    if not message:
        return

    if message.chat_id != DATABASE_CHANNEL_ID:
        return

    filename = None

    # VIDEO
    if message.video:

        filename = message.video.file_name

    # DOCUMENT
    elif message.document:

        filename = message.document.file_name

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

async def search(update, context):

    user = update.effective_user

    if not user:
        return

    user_id = user.id

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
            reply_markup=join_channel_keyboard()
        )

        return

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
    # RATE LIMIT
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
            request_times.get(user_id)
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

        request_times[user_id] = now

    # =====================================================
    # LOG SEARCH
    # =====================================================

    await send_log(
        context,
        "🔎 <b>MOVIE SEARCH</b>\n\n"
        f"User: {get_user_name(user)}\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Search: <b>{name}</b>"
    )

    # =====================================================
    # MONGODB SEARCH
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

        delivered = 0

        for filename, message_id in matching_files:

            try:

                await context.bot.copy_message(
                    chat_id=user_id,
                    from_chat_id=DATABASE_CHANNEL_ID,
                    message_id=message_id
                )

                delivered += 1

                await asyncio.sleep(0.3)

            except Exception as error:

                print(
                    f"❌ Could not send "
                    f"{filename}: {error}"
                )

        # =================================================
        # LOG DELIVERY
        # =================================================

        await send_log(
            context,
            "🎬 <b>MOVIE DELIVERY</b>\n\n"
            f"User: {get_user_name(user)}\n"
            f"User ID: <code>{user.id}</code>\n"
            f"Search: <b>{name}</b>\n"
            f"Files found: <b>{len(matching_files)}</b>\n"
            f"Files delivered: <b>{delivered}</b>"
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

    await send_log(
        context,
        "❌ <b>MOVIE NOT FOUND</b>\n\n"
        f"User: {get_user_name(user)}\n"
        f"User ID: <code>{user.id}</code>\n"
        f"Search: <b>{name}</b>"
    )


# =========================================================
# STATISTICS
# =========================================================

async def stats(update, context):

    # /stats works only inside the Log Channel
    if not update.effective_chat:
        return

    if update.effective_chat.id != LOG_CHANNEL_ID:
        return

    try:

        # Number of indexed movie files
        movie_count = await asyncio.to_thread(
            movie_collection.count_documents,
            {}
        )

        # Current access-channel member count
        member_count = (
            await context.bot.get_chat_member_count(
                ACCESS_CHANNEL_ID
            )
        )

        # Number of users currently known
        logged_users = (
            context.application.bot_data
            .get(
                "logged_users",
                set()
            )
        )

        bot_users = len(logged_users)

        text = (
            "📊 <b>BOT STATISTICS</b>\n\n"
            f"👤 Bot Users Logged: "
            f"<b>{bot_users}</b>\n"
            f"👥 Access Channel Members: "
            f"<b>{member_count}</b>\n"
            f"🎬 Indexed Movie Files: "
            f"<b>{movie_count}</b>\n\n"
            "ℹ️ Send /stats again anytime "
            "to refresh."
        )

        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=text,
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            f"❌ Stats error: {error}"
        )

        await context.bot.send_message(
            chat_id=LOG_CHANNEL_ID,
            text=(
                "⚠️ Could not retrieve "
                "statistics.\n\n"
                f"Error: {error}"
            )
        )


# =========================================================
# HEALTH CHECK
# =========================================================

async def health(request):

    return web.Response(
        text="Bot is running!"
    )


# =========================================================
# TELEGRAM WEBHOOK
# =========================================================

async def telegram_webhook(
    request
):

    secret = request.headers.get(
        "X-Telegram-Bot-Api-Secret-Token"
    )

    if secret != WEBHOOK_SECRET:

        print(
            "⚠️ Rejected unauthorized "
            "webhook request"
        )

        return web.Response(
            status=403,
            text="Forbidden"
        )

    try:

        data = await request.json()

        update = Update.de_json(
            data,
            application.bot
        )

        await application.update_queue.put(
            update
        )

        return web.Response(
            status=200,
            text="OK"
        )

    except Exception as error:

        print(
            f"❌ Webhook error: {error}"
        )

        return web.Response(
            status=500,
            text="Webhook error"
        )


# =========================================================
# WEB SERVER
# =========================================================

async def start_web_server():

    app_web = web.Application()

    app_web.router.add_get(
        "/",
        health
    )

    app_web.router.add_get(
        "/health",
        health
    )

    app_web.router.add_post(
        WEBHOOK_PATH,
        telegram_webhook
    )

    runner = web.AppRunner(
        app_web
    )

    await runner.setup()

    site = web.TCPSite(
        runner,
        "0.0.0.0",
        PORT
    )

    await site.start()

    print(
        f"🌐 Web server running "
        f"on port {PORT}"
    )

    return runner


# =========================================================
# SHUTDOWN DATABASE
# =========================================================

async def shutdown_database():

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

async def main():

    global application

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

    if not RENDER_EXTERNAL_URL:

        raise ValueError(
            "RENDER_EXTERNAL_URL is missing"
        )

    # =====================================================
    # MONGODB
    # =====================================================

    init_database()

    # =====================================================
    # TELEGRAM APPLICATION
    # =====================================================

    application = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .updater(None)
        .concurrent_updates(16)
        .build()
    )

    # =====================================================
    # HANDLERS
    # =====================================================

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CommandHandler(
            "stats",
            stats
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            join_channel,
            pattern="^join_channel$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            click_channel,
            pattern="^click_channel$"
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            continue_to_bot,
            pattern="^continue_to_bot$"
        )
    )

    # Join-request logging
    application.add_handler(
        ChatJoinRequestHandler(
            join_request_handler
        )
    )

    # Movie database channel
    application.add_handler(
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

    # User movie searches
    application.add_handler(
        MessageHandler(
            filters.TEXT
            & ~filters.COMMAND,
            search
        )
    )

    # =====================================================
    # START PTB
    # =====================================================

    await application.initialize()

    await application.start()

    # =====================================================
    # START INDEX WORKERS
    # =====================================================

    await start_index_workers(
        application
    )

    # =====================================================
    # WEBHOOK URL
    # =====================================================

    webhook_url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + WEBHOOK_PATH
    )

    print(
        "🔗 Setting Telegram webhook:"
    )

    print(
        webhook_url
    )

    await application.bot.set_webhook(
        url=webhook_url,
        secret_token=WEBHOOK_SECRET,
        allowed_updates=Update.ALL_TYPES,
        drop_pending_updates=False
    )

    print(
        "✅ Telegram webhook connected"
    )

    # =====================================================
    # WEB SERVER
    # =====================================================

    runner = await start_web_server()

    print(
        "🤖 Bot is running in WEBHOOK mode!"
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

    print(
        "📊 Log channel enabled"
    )

    print(
        "🚫 Telegram polling disabled"
    )

    try:

        await asyncio.Event().wait()

    finally:

        print(
            "🛑 Shutting down..."
        )

        await runner.cleanup()

        await application.stop()

        await application.shutdown()

        await shutdown_database()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    asyncio.run(main())
