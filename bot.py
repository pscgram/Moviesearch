import os
import json
import urllib.parse
import urllib.request
import threading
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

CHANNEL_LINK = "https://t.me/+SqgUfajesfw1ZDhh"


# =========================
# HEALTH SERVER
# =========================

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass


def web_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()


# =========================
# TMDB
# =========================

def tmdb_request(url):
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + TMDB_TOKEN,
            "accept": "application/json"
        }
    )

    with urllib.request.urlopen(req, timeout=15) as response:
        return json.loads(response.read().decode())


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


# =========================
# ACCESS BUTTONS
# =========================

def join_channel_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Join Channel",
                callback_data="stage_join"
            )
        ]
    ])


def click_channel_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Click Channel",
                callback_data="stage_click"
            )
        ]
    ])


def continue_button():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Continue to Bot",
                callback_data="stage_continue"
            )
        ]
    ])


# =========================
# START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
        reply_markup=join_channel_button(),
        parse_mode="HTML"
    )


# =========================
# STEP 1
# JOIN CHANNEL
# =========================

async def stage_join(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "🔔 <b>Join Channel</b>\n\n"
        "Tap the button below to continue.",
        reply_markup=click_channel_button(),
        parse_mode="HTML"
    )


# =========================
# STEP 2
# CLICK CHANNEL
# =========================

async def stage_click(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    # Open the private channel link automatically
    # by answering with a URL is not possible.
    # Instead, edit the same message and show
    # the actual channel link as the ONLY button.

    await query.edit_message_text(
        "🔔 <b>Click Channel</b>\n\n"
        "Tap below to open the private channel.",
        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🔔 Open Private Channel",
                    url=CHANNEL_LINK
                )
            ]
        ]),
        parse_mode="HTML"
    )

    # Continue button is intentionally NOT shown here.


# =========================
# STEP 3
# CONTINUE
# =========================

async def stage_continue(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
        "✅ You can now use the bot.\n\n"
        "🎬 Send me a movie name.",
        parse_mode="HTML"
    )


# =========================
# MOVIE SEARCH
# =========================

async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user_id = update.effective_user.id

    unlocked_users = context.application.bot_data.setdefault(
        "unlocked_users",
        set()
    )

    if user_id not in unlocked_users:

        await update.message.reply_text(
            "🔒 Please join the channel first.",
            reply_markup=join_channel_button()
        )

        return

    name = update.message.text.strip()

    msg = await update.message.reply_text(
        "🔎 Searching for:\n\n🎬 " + name
    )

    try:

        data = search_movies(name)
        movies = data.get("results", [])[:8]

        if not movies:
            await msg.edit_text(
                "❌ No movies found."
            )
            return

        buttons = []

        for movie in movies:

            movie_id = movie.get("id")
            title = movie.get("title", "Unknown")

            date = movie.get("release_date", "")
            year = date[:4] if date else "N/A"

            buttons.append([
                InlineKeyboardButton(
                    f"🎬 {title} ({year})",
                    callback_data=f"movie:{movie_id}"
                )
            ])

        await msg.edit_text(
            "🎬 <b>Search Results</b>\n\n"
            "Choose a movie:",
            reply_markup=InlineKeyboardMarkup(buttons),
            parse_mode="HTML"
        )

    except Exception:

        await msg.edit_text(
            "⚠️ Search failed.\n"
            "Please try again."
        )


# =========================
# MOVIE DETAILS
# =========================

async def select_movie(update: Update, context: ContextTypes.DEFAULT_TYPE):

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
            reply_markup=join_channel_button()
        )

        return

    movie_id = query.data.split(":")[1]

    try:

        movie = movie_details(movie_id)

        title = movie.get("title", "Unknown")

        date = movie.get("release_date", "")
        year = date[:4] if date else "N/A"

        rating = movie.get("vote_average", 0)

        overview = movie.get(
            "overview",
            "No description available."
        )

        poster = movie.get("poster_path")

        text = (
            f"🎬 <b>{title}</b>\n\n"
            f"📅 Year: <b>{year}</b>\n"
            f"⭐ Rating: <b>{rating:.1f}/10</b>\n\n"
            f"📝 {overview}\n\n"
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
                parse_mode="HTML"
            )

        else:

            await query.message.reply_text(
                text,
                parse_mode="HTML"
            )

    except Exception:

        await query.message.reply_text(
            "⚠️ Couldn't load movie details."
        )


# =========================
# MAIN
# =========================

def main():

    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN is missing")

    if not TMDB_TOKEN:
        raise ValueError("TMDB_TOKEN is missing")

    threading.Thread(
        target=web_server,
        daemon=True
    ).start()

    app = Application.builder().token(BOT_TOKEN).build()

    # START
    app.add_handler(
        CommandHandler("start", start)
    )

    # ACCESS FLOW
    app.add_handler(
        CallbackQueryHandler(
            stage_join,
            pattern="^stage_join$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            stage_click,
            pattern="^stage_click$"
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            stage_continue,
            pattern="^stage_continue$"
        )
    )

    # MOVIE BUTTON
    app.add_handler(
        CallbackQueryHandler(
            select_movie,
            pattern="^movie:"
        )
    )

    # TEXT SEARCH
    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search
        )
    )

    print("🤖 Bot is running!")

    app.run_polling()


if __name__ == "__main__":
    main()
