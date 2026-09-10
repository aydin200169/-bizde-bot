import os
TOKEN = 8789277125: AAHk5Le4h1CAPy87AlVNm94MzXHmt6RlaSw
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes
)


# =========================
# НАСТРОЙКИ
# =========================



WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"

PORT = int(os.getenv("PORT", "10000"))

# =========================
# WEB-СЕРВЕР RENDER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()

        self.wfile.write(
            b"BIZDE.KZ is running!"
        )

    def log_message(self, format, *args):
        pass


def start_web_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

    print(f"Web server started on port {PORT}")

    server.serve_forever()


# =========================
# ГЛАВНОЕ МЕНЮ
# =========================

def main_menu():

    keyboard = [
        [
            InlineKeyboardButton(
                "📱 Открыть BIZDE",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                )
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
    ]

    return InlineKeyboardMarkup(keyboard)


# =========================
# /START
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    await update.message.reply_text(
        "👋 Добро пожаловать в BIZDE.KZ!\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",
        reply_markup=main_menu()
    )


# =========================
# КНОПКИ
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()


    # КАТЕГОРИИ
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
                    callback_data="back"
                )
            ]
        ]

        await query.edit_message_text(
            "📂 Выбери категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    # ПАРТНЁРЫ
    elif query.data == "partners":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back"
                )
            ]
        ]

        await query.edit_message_text(
            "🏪 Партнёры BIZDE.KZ\n\n"
            "Пока здесь пусто.\n"
            "Скоро добавим первых партнёров! 🔥",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    # ПОДПИСКА
    elif query.data == "subscription":

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ Назад",
                    callback_data="back"
                )
            ]
        ]

        await query.edit_message_text(
            "🎟 Моя подписка\n\n"
            "Статус: ❌ Не активна",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    # КАТЕГОРИИ
    elif query.data in [
        "cafes",
        "shops",
        "sport"
    ]:

        keyboard = [
            [
                InlineKeyboardButton(
                    "⬅️ К категориям",
                    callback_data="categories"
                )
            ],
            [
                InlineKeyboardButton(
                    "🏠 Главное меню",
                    callback_data="back"
                )
            ]
        ]

        await query.edit_message_text(
            "🏪 Партнёры этой категории пока добавляются.\n\n"
            "Скоро здесь появятся предложения 🔥",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


    # НАЗАД
    elif query.data == "back":

        await query.edit_message_text(
            "🏠 Главное меню BIZDE.KZ",
            reply_markup=main_menu()
        )


# =========================
# ЗАПУСК
# =========================

def main():

    # Запускаем сервер Render
    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()


    # Создаём Telegram-бота
    app = (
        Application.builder()
        .token(TOKEN)
        .build()
    )


    # Команды
    app.add_handler(
        CommandHandler("start", start)
    )

    app.add_handler(
        CallbackQueryHandler(button_handler)
    )


    print("BIZDE.KZ запущен!")


    # Запускаем бота
    app.run_polling(
        drop_pending_updates=True
    )


# =========================
# START
# =========================

if __name__ == "__main__":
    main()
