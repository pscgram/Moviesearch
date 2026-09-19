import os
import logging
import threading
import json
import urllib.parse
import urllib.request
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, ContextTypes, filters

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


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "🎬 Welcome to Movie Search Bot!\n\n"
        "🔎 Send me a movie name.\n\n"
        "Example:\n"
        "Avatar"
    )


def search_tmdb(movie_name):
    encoded_name = urllib.parse.quote(movie_name)

    url = (
        "https://api.themoviedb.org/3/search/movie"
        f"?query={encoded_name}&include_adult=false&language=en-US&page=1"
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


async def search_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):
    movie_name = update.message.text.strip()

    if not movie_name:
        return

    if not TMDB_TOKEN:
        await update.message.reply_text(
            "⚠️ TMDB is not configured yet."
        )
        return

    searching_message = await update.message.reply_text(
        f"🔎 Searching TMDB for:\n\n"
        f"🎬 {movie_name}\n\n"
        f"⏳ Please wait..."
    )

    try:
        data = search_tmdb(movie_name)
        results = data.get("results", [])

        if not results:
            await searching_message.edit_text(
                f"❌ No movie found for:\n\n🎬 {movie_name}"
            )
            return

        movie = results[0]

        title = movie.get("title", "Unknown")
        release_date = movie.get("release_date", "")
        year = release_date[:4] if release_date else "N/A"
        rating = movie.get("vote_average", 0)
        overview = movie.get(
            "overview",
            "No description available."
        )

        poster_path = movie.get("poster_path")

        caption = (
            f"🎬 <b>{title}</b>\n\n"
            f"📅 Year: <b>{year}</b>\n"
            f"⭐ Rating: <b>{rating:.1f}/10</b>\n\n"
            f"📝 {overview}\n\n"
            f"<i>This product uses the TMDB API but is not "
            f"endorsed or certified by TMDB.</i>"
        )

        await searching_message.delete()

        if poster_path:
            poster_url = (
                f"https://image.tmdb.org/t/p/w500{poster_path}"
            )

            await update.message.reply_photo(
                photo=poster_url,
                caption=caption,
                parse_mode="HTML"
            )
        else:
            await update.message.reply_text(
                caption,
                parse_mode="HTML"
            )

    except Exception as e:
        logging.exception("TMDB search error")

        await searching_message.edit_text(
            "⚠️ Sorry, I couldn't search TMDB right now.\n\n"
            "Please try again."
        )


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is not configured")

    if not TMDB_TOKEN:
        raise ValueError("TMDB_TOKEN is not configured")

    threading.Thread(
        target=start_web_server,
        daemon=True
    ).start()

    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search_movie
        )
    )

    print("🤖 Movie Search Bot is running...")

    app.run_polling()


if __name__ == "__main__":
    main()
