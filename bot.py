import os
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


TOKEN = "8789277125: AAHk5Le4h1CAPy87AlVNm94MzXHmt6RlaSw"
PORT = int(os.environ.get("PORT", 10000))

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"


class Server(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.end_headers()
        self.wfile.write(b"BIZDE.KZ is running")

    def log_message(self, format, *args):
        return


def start_server():
    server = HTTPServer(("0.0.0.0", PORT), Server)
    print("Web server started")
    server.serve_forever()


def main_keyboard():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "📱 Открыть BIZDE",
                web_app=WebAppInfo(url=WEB_APP_URL)
            )
        ],
        [
            InlineKeyboardButton(
                "📂 Категории",
                callback_data="categories"
            )
        ],
        [
            InlineKeyboardButton(
                "🏪 Партнёры",
                callback_data="partners"
            )
        ],
        [
            InlineKeyboardButton(
                "🎟 Моя подписка",
                callback_data="subscription"
            )
        ]
    ])


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 Добро пожаловать в BIZDE.KZ!\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",
        reply_markup=main_keyboard()
    )


async def buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):

    query = update.callback_query
    await query.answer()

    if query.data == "categories":

        keyboard = [
            [
                InlineKeyboardButton(
                    "☕ Кафе и рестораны",
                    callback_data="cafes"
                )
            ],
            [
                InlineKeyboardButton(
                    "🛍 Магазины",
                    callback_data="shops"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏋️ Спорт",
                    callback_data="sport"
                )
            ],
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="home"
                )
            ]
        ]

        await query.edit_message_text(
            "📂 Выбери категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "partners":

        await query.edit_message_text(
            "🏪 Партнёры BIZDE.KZ\n\n"
            "Пока партнёров нет.\n"
            "Скоро здесь появятся первые предложения 🔥",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif query.data == "subscription":

        await query.edit_message_text(
            "🎟 Моя подписка\n\n"
            "Статус: ❌ Не активна",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif query.data in ["cafes", "shops", "sport"]:

        await query.edit_message_text(
            "🏪 В этой категории пока нет партнёров.\n\n"
            "Скоро добавим первые предложения 🔥",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Категории",
                        callback_data="categories"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "🏠 Главное меню",
                        callback_data="home"
                    )
                ]
            ])
        )

    elif query.data == "home":

        await query.edit_message_text(
            "🏠 Главное меню BIZDE.KZ",
            reply_markup=main_keyboard()
        )


def run_bot():

    if not TOKEN:
        print("ERROR: BOT_TOKEN не найден!")
        return

    app = Application.builder().token(TOKEN).build()

    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(buttons)
    )

    print("BIZDE.KZ bot started!")

    app.run_polling()


if __name__ == "__main__":

    server_thread = threading.Thread(
        target=start_server,
        daemon=True
    )

    server_thread.start()

    run_bot()
