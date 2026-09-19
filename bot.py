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

# Private Telegram channel
CHANNEL_ID = -1004290623496

# Private channel invite link
CHANNEL_INVITE_LINK = "https://t.me/+SqgUfajesfw1ZDhh"


class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b"Bot is running!")

    def log_message(self, format, *args):
        pass


def web_server():
    HTTPServer(("0.0.0.0", PORT), HealthHandler).serve_forever()


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


async def is_subscribed(user_id, context):
    try:
        member = await context.bot.get_chat_member(
            chat_id=CHANNEL_ID,
            user_id=user_id
        )

        return member.status in (
            "member",
            "administrator",
            "creator"
        )

    except Exception as e:
        print("Membership check error:", e)
        return False


def subscription_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "🔔 Join Channel",
                url=CHANNEL_INVITE_LINK
            )
        ],
        [
            InlineKeyboardButton(
                "✅ Verify Subscription",
                callback_data="verify_subscription"
            )
        ]
    ])


async def show_subscription_message(update, context):
    text = (
        "🔒 <b>Channel Subscription Required</b>\n\n"
        "Please join our private channel first.\n\n"
        "After joining, press <b>Verify Subscription</b> below."
    )

    if update.callback_query:
        await update.callback_query.message.reply_text(
            text,
            reply_markup=subscription_keyboard(),
            parse_mode="HTML"
        )
    elif update.message:
        await update.message.reply_text(
            text,
            reply_markup=subscription_keyboard(),
            parse_mode="HTML"
        )


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    subscribed = await is_subscribed(user_id, context)

    if not subscribed:
        await update.message.reply_text(
            "🔒 <b>Join our channel to use this bot.</b>\n\n"
            "1️⃣ Tap <b>Join Channel</b>\n"
            "2️⃣ Join the channel\n"
            "3️⃣ Come back and tap <b>Verify Subscription</b>",
            reply_markup=subscription_keyboard(),
            parse_mode="HTML"
        )
        return

    await update.message.reply_text(
        "🎬 Welcome to Movie Search Bot!\n\n"
        "Send me a movie name.\n\n"
        "Example: Avatar"
    )


async def verify_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    subscribed = await is_subscribed(user_id, context)

    if subscribed:
        await query.message.edit_text(
            "✅ <b>Subscription Verified!</b>\n\n"
            "🎬 You can now search for movies.\n\n"
            "Send me a movie name.\n"
            "Example: Avatar",
            parse_mode="HTML"
        )
    else:
        await query.answer(
            "❌ You haven't joined the channel yet.",
            show_alert=True
        )


async def search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    subscribed = await is_subscribed(user_id, context)

    if not subscribed:
        await show_subscription_message(update, context)
        return

    name = update.message.text.strip()

    msg = await update.message.reply_text(
        "🔎 Searching for:\n\n🎬 " + name
    )

    try:
        data = search_movies(name)
        movies = data.get("results", [])[:8]

        if not movies:
            await msg.edit_text("❌ No movies found.")
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
            "⚠️ Search failed.\nPlease try again."
        )


async def select_movie(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    user_id = query.from_user.id

    # Check membership again before showing movie details
    subscribed = await is_subscribed(user_id, context)

    if not subscribed:
        await query.message.reply_text(
            "🔒 <b>Please join our channel first.</b>",
            reply_markup=subscription_keyboard(),
            parse_mode="HTML"
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
                "https://image.tmdb.org/t/p/w500" + poster
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

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(
            verify_subscription,
            pattern="^verify_subscription$"
        )
    )

    app.add_handler(
        MessageHandler(
            filters.TEXT & ~filters.COMMAND,
            search
        )
    )

    app.add_handler(
        CallbackQueryHandler(
            select_movie,
            pattern="^movie:"
        )
    )

    print("🤖 Bot is running!")

    app.run_polling()


if __name__ == "__main__":
    main()
