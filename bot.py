import os
import logging
import threading
import json
import urllib.parse
import urllib.request
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

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
TMDB_TOKEN = os.getenv("TMDB_TOKEN")
PORT = int(os.getenv("PORT", "10000"))


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        return


def start_web_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    server.serve_forever()


def search_tmdb(movie_name):
    encoded_name = urllib.parse.quote(movie_name)

    url = (
        "https://api.themoviedb.org/3/search/movie"
        f"?query={encoded_name}&include_adult=false"
        "&language=en-US&page=1"
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TMDB_TOKEN}",
            "accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


def get_movie(movie_id):
    url = (
        f"https://api.themoviedb.org/3/movie/{movie_id}"
        "?language=en-US"
    )

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {TMDB_TOKEN}",
            "accept": "application/json",
        },
    )

    with urllib.request.urlopen(request, timeout=15) as response:
        return json.loads(response.read().decode("utf-8"))


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Welcome to Movie Search Bot!\n\n"
        "🔎 Send me a movie name.\n\n"
        "Example:\n"
        "Avatar"
    )


async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_name = update.message.text.strip()

    if not movie_name:
        return

    searching = await update.message.reply_text(
        f"🔎 Searching for:\n\n"
        f"🎬 {movie_name}\n\n"
        f"⏳ Please wait..."
    )

    try:
        data = search_tmdb(movie_name)
        results = data.get("results", [])

        if not results:
            await searching.edit_text(
                f"❌ No movies found for:\n\n🎬 {movie_name}"
            )
            return

        results = results[:8]

        buttons = []

        for movie in results:
            movie_id = movie.get("id")
            title = movie.get("title", "Unknown")
            release_date = movie.get("release_date", "")
            year = release_date[:4] if release_date else "N/A"

            button_text = f"🎬 {title} ({year})"

            buttons.append([
                InlineKeyboardButton(
                    button_text,
                    callback_data=f"movie_{movie_id}"
                )
            ])

        keyboard = InlineKeyboardMarkup(buttons)

        await searching.edit_text(
            f"🔎 Results for:\n\n"
            f"🎬 <b>{movie_name}</b>\n\n"
            f"👇 Select a movie:",
            reply_markup=keyboard,
            parse_mode="HTML"
        )

    except Exception:
        logging.exception("Search error")

        await searching.edit_text(
            "⚠️ Search failed.\n\n"
            "Please try again."
        )


async def movie_selected(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query
    await query.answer()

    movie_id = query.data.replace("movie_", "")

    try:
        movie = get_movie(movie_id)

        title = movie.get("title", "Unknown")
        release
