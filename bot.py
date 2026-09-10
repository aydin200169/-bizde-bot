import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes


# =========================
# НАСТРОЙКИ
# =========================

TOKEN = "8789277125:AAHk5Le4h1CAPy87AlVNm94MzXHmt6RlaSw"

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"

PORT = int(os.environ.get("PORT", 10000))


# =========================
# HTTP-СЕРВЕР ДЛЯ RENDER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.end_headers()
        self.wfile.write(b"BIZDE.KZ is running!")

    def log_message(self, format, *args):
        return


def run_server():
    server = HTTPServer(("0.0.0.0", PORT), HealthHandler)
    print(f"HTTP server started on port {PORT}")
    server.serve_forever()


# =========================
# ГЛАВНОЕ МЕНЮ
# =========================

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
        ],
    ])


# =========================
# /START
# =========================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    await update.message.reply_text(
        "👋 Добро пожаловать в BIZDE.KZ!\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",
        reply_markup=main_keyboard()
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
            ],
        ]

        await query.edit_message_text(
            "📂 Выбери категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "partners":

        await query.edit_message_text(
            "🏪 Партнёры BIZDE.KZ\n\n"
            "Пока здесь пусто.\n"
            "Скоро добавим первых партнёров! 🔥"
        )

    elif query.data == "subscription":

        await query.edit_message_text(
            "🎟 Моя подписка\n\n"
            "Статус: ❌ Не активна"
        )

    elif query.data in ["cafes", "shops", "sport"]:

        await query.edit_message_text(
            "🏪 Партнёры этой категории пока добавляются.\n\n"
            "Скоро здесь появятся предложения 🔥"
        )

    elif query.data == "back":

        await query.edit_message_text(
            "🏠 Главное меню BIZDE.KZ",
            reply_markup=main_keyboard()
        )


# =========================
# ЗАПУСК
# =========================

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден!")

# Запускаем HTTP-сервер для Render
threading.Thread(
    target=run_server,
    daemon=True
).start()

# Создаём Telegram-приложение
app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))

print("BIZDE.KZ запущен!")

app.run_polling()
