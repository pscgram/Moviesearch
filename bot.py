import os
import json
import re
import sqlite3
import urllib.parse
import urllib.request
import threading
import asyncio
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
# CHANNELS
# =========================================================

# Existing access/private channel
CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"

# New Movie Database Channel
DATABASE_CHANNEL_ID = -1004463648734


# =========================================================
# DATABASE
# =========================================================

DB_FILE = "movies.db"


def init_database():

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS movies (
            tmdb_id INTEGER PRIMARY KEY,
            title TEXT NOT NULL,
            year TEXT,
            channel_message_id INTEGER NOT NULL
        )
    """)

    conn.commit()
    conn.close()


def save_movie(
    tmdb_id,
    title,
    year,
    channel_message_id
):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        INSERT OR REPLACE INTO movies
        (tmdb_id, title, year, channel_message_id)
        VALUES (?, ?, ?, ?)
    """, (
        tmdb_id,
        title,
        year,
        channel_message_id
    ))

    conn.commit()
    conn.close()


def get_movie_message_id(tmdb_id):

    conn = sqlite3.connect(DB_FILE)

    cursor = conn.cursor()

    cursor.execute("""
        SELECT channel_message_id
        FROM movies
        WHERE tmdb_id = ?
    """, (tmdb_id,))

    result = cursor.fetchone()

    conn.close()

    if result:
        return result[0]

    return None


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
# CLEAN MOVIE FILE NAME
# =========================================================

def clean_movie_filename(filename):

    # Remove extension
    name = re.sub(
        r"\.(mp4|mkv|avi|mov|webm|m4v)$",
        "",
        filename,
        flags=re.IGNORECASE
    )

    # Replace separators with spaces
    name = re.sub(
        r"[._]+",
        " ",
        name
    )

    # Remove common release/quality information
    remove_words = [
        r"\b2160p\b",
        r"\b4k\b",
        r"\b1080p\b",
        r"\b720p\b",
        r"\b480p\b",
        r"\b360p\b",
        r"\bWEB[- ]?DL\b",
        r"\bWEBRip\b",
        r"\bBluRay\b",
        r"\bBRRip\b",
        r"\bHDRip\b",
        r"\bHDTV\b",
        r"\bDVDRip\b",
        r"\bHEVC\b",
        r"\bx264\b",
        r"\bx265\b",
        r"\bH264\b",
        r"\bH265\b",
        r"\bAAC\b",
        r"\bDDP\b",
        r"\bDD5\.1\b",
        r"\b5\.1\b",
        r"\b10bit\b",
        r"\b8bit\b",
        r"\bEnglish\b",
        r"\bMalayalam\b",
        r"\bTamil\b",
        r"\bTelugu\b",
        r"\bHindi\b",
        r"\bKorean\b",
        r"\bJapanese\b",
        r"\bDual Audio\b",
        r"\bMulti Audio\b",
    ]

    for word in remove_words:

        name = re.sub(
            word,
            "",
            name,
            flags=re.IGNORECASE
        )

    # Extract year if present
    year_match = re.search(
        r"\b(19|20)\d{2}\b",
        name
    )

    year = ""

    if year_match:
        year = year_match.group(0)

        # Remove year from search title
        name = re.sub(
            r"\b(19|20)\d{2}\b",
            "",
            name
        )

    # Remove brackets
    name = re.sub(
        r"[\[\]\(\)\{\}]",
        " ",
        name
    )

    # Clean multiple spaces
    name = re.sub(
        r"\s+",
        " ",
        name
    ).strip()

    return name, year


# =========================================================
# AUTOMATIC DATABASE CHANNEL PROCESSOR
# =========================================================

async def database_channel_movie(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    message = update.channel_post

    if not message:
        return

    # Make sure this is our Movie Database Channel
    if message.chat_id != DATABASE_CHANNEL_ID:
        return

    filename = None

    # Video file
    if message.video:

        filename = message.video.file_name

    # Document file
    elif message.document:

        filename = message.document.file_name

    else:

        return

    if not filename:

        await context.bot.send_message(
            chat_id=DATABASE_CHANNEL_ID,
            text=(
                "⚠️ Could not identify this movie.\n\n"
                "Please use a filename containing the movie title."
            )
        )

        return

    movie_name, file_year = clean_movie_filename(
        filename
    )

    if not movie_name:

        return

    try:

        data = search_movies(movie_name)

        movies = data.get(
            "results",
            []
        )

        if not movies:

            await context.bot.send_message(
                chat_id=DATABASE_CHANNEL_ID,
                text=(
                    "⚠️ Movie not found on TMDB.\n\n"
                    f"📁 File: {filename}\n"
                    f"🔎 Search: {movie_name}"
                )
            )

            return

        selected_movie = None

        # First try matching the year
        if file_year:

            for movie in movies:

                release_date = movie.get(
                    "release_date",
                    ""
                )

                if release_date.startswith(
                    file_year
                ):

                    selected_movie = movie
                    break

        # If no year match, use first result
        if not selected_movie:

            selected_movie = movies[0]

        tmdb_id = selected_movie.get("id")

        title = selected_movie.get(
            "title",
            movie_name
        )

        release_date = selected_movie.get(
            "release_date",
            ""
        )

        year = (
            release_date[:4]
            if release_date
            else file_year
        )

        save_movie(
            tmdb_id,
            title,
            year,
            message.message_id
        )

        await context.bot.send_message(
            chat_id=DATABASE_CHANNEL_ID,
            text=(
                "✅ <b>Movie Added</b>\n\n"
                f"🎬 <b>{title}</b>\n"
                f"📅 Year: <b>{year or 'N/A'}</b>\n"
                f"🆔 TMDB ID: <b>{tmdb_id}</b>\n"
                f"📌 Message ID: <b>{message.message_id}</b>"
            ),
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            "Database channel error:",
            error
        )

        await context.bot.send_message(
            chat_id=DATABASE_CHANNEL_ID,
            text=(
                "⚠️ Could not add this movie.\n\n"
                "Please check the filename and try again."
            )
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
# MOVIE FILE BUTTON
# =========================================================

def movie_file_keyboard(tmdb_id):

    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "▶️ Watch / Get Movie",
                callback_data=f"getmovie:{tmdb_id}"
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
# STEP 3 - CONTINUE TO BOT
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

    msg = await update.message.reply_text(
        "🔎 Searching for:\n\n🎬 " + name
    )

    try:

        data = search_movies(name)

        movies = data.get(
            "results",
            []
        )[:8]

        if not movies:

            await msg.edit_text(
                "❌ No movies found."
            )

            return

        buttons = []

        for movie in movies:

            movie_id = movie.get("id")

            title = movie.get(
                "title",
                "Unknown"
            )

            date = movie.get(
                "release_date",
                ""
            )

            year = (
                date[:4]
                if date
                else "N/A"
            )

            buttons.append([
                InlineKeyboardButton(
                    f"🎬 {title} ({year})",
                    callback_data=f"movie:{movie_id}"
                )
            ])

        await msg.edit_text(
            "🎬 <b>Search Results</b>\n\n"
            "Choose a movie:",
            reply_markup=InlineKeyboardMarkup(
                buttons
            ),
            parse_mode="HTML"
        )

    except Exception as error:

        print(
            "Search error:",
            error
        )

        await msg.edit_text(
            "⚠️ Search failed.\n"
            "Please try again."
        )


# =========================================================
# MOVIE DETAILS
# =========================================================

async def select_movie(
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

    if user_id not in unlocked_users:

        await query.message.reply_text(
            "🔒 Please join the channel first.",
            reply_markup=join_channel_keyboard()
        )

        return

    movie_id = int(
        query.data.split(":")[1]
    )

    try:

        movie = movie_details(
            movie_id
        )

        title = movie.get(
            "title",
            "Unknown"
        )

        date = movie.get(
            "release_date",
            ""
        )

        year = (
            date[:4]
            if date
            else "N/A"
        )

        rating = movie.get(
            "vote_average",
            0
        )

        overview = movie.get(
            "overview",
            "No description available."
        )

        poster = movie.get(
            "poster_path"
        )

        # Check our Movie Database
        channel_message_id = get_movie_message_id(
            movie_id
        )

        if channel_message_id:

            availability_text = (
                "\n\n"
                "✅ <b>Movie Available</b>"
            )

            keyboard = movie_file_keyboard(
                movie_id
            )

        else:

            availability_text = (
                "\n\n"
                "❌ <b>Movie Not Available</b>"
            )

            keyboard = None

        text = (
            f"🎬 <b>{title}</b>\n\n"
            f"📅 Year: <b>{year}</b>\n"
            f"⭐ Rating: <b>{rating:.1f}/10</b>\n\n"
            f"📝 {overview}"
            f"{availability_text}\n\n"
            "This product uses the TMDB API but is not "
            "endorsed or certified by TMDB."
        )

        if poster:

            poster_url = (
                "https://image.tmdb.org/t/p/w500"
                + poster
            )

            await query.message.reply_photo(
                photo=poster_url,
                caption=text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

        else:

            await query.message.reply_text(
                text,
                reply_markup=keyboard,
                parse_mode="HTML"
            )

    except Exception as error:

        print(
            "Movie details error:",
            error
        )

        await query.message.reply_text(
            "⚠️ Couldn't load movie details."
        )


# =========================================================
# SEND MOVIE FILE
# =========================================================

async def get_movie(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer(
        "Preparing movie..."
    )

    user_id = update.effective_user.id

    unlocked_users = context.application.bot_data.setdefault(
        "unlocked_users",
        set()
    )

    if user_id not in unlocked_users:

        await query.message.reply_text(
            "🔒 Please join the channel first.",
            reply_markup=join_channel_keyboard()
        )

        return

    movie_id = int(
        query.data.split(":")[1]
    )

    channel_message_id = get_movie_message_id(
        movie_id
    )

    if not channel_message_id:

        await query.message.reply_text(
            "❌ <b>Movie Not Available</b>",
            parse_mode="HTML"
        )

        return

    try:

        await context.bot.copy_message(
            chat_id=user_id,
            from_chat_id=DATABASE_CHANNEL_ID,
            message_id=channel_message_id
        )

    except Exception as error:

        print(
            "File delivery error:",
            error
        )

        await query.message.reply_text(
            "⚠️ Unable to send the movie file right now.\n"
            "Please try again later."
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

    # Initialize SQLite database
    init_database()

    # Start Render health server
    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    app = (
        Application
        .builder()
        .token(BOT_TOKEN)
        .build()
    )

    # =====================================================
    # START
    # =====================================================

    app.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # =====================================================
    # JOIN CHANNEL
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            join_channel,
            pattern="^join_channel$"
        )
    )

    # =====================================================
    # CLICK CHANNEL
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            click_channel,
            pattern="^click_channel$"
        )
    )

    # =====================================================
    # CONTINUE
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            continue_to_bot,
            pattern="^continue_to_bot$"
        )
    )

    # =====================================================
    # GET MOVIE FILE
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            get_movie,
            pattern="^getmovie:"
        )
    )

    # =====================================================
    # MOVIE DETAILS
    # =====================================================

    app.add_handler(
        CallbackQueryHandler(
            select_movie,
            pattern="^movie:"
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
            database_channel_movie
        )
    )

    # =====================================================
    # MOVIE SEARCH
    # =====================================================

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

    app.run_polling()


if __name__ == "__main__":
    main()
