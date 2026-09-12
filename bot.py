import os
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)


# =========================
# НАСТРОЙКИ
# =========================

# ВСТАВЬ СЮДА НОВЫЙ ТОКЕН БОТА
TOKEN = os.environ.get("BOT_TOKEN")

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"

PORT = int(os.environ.get("PORT", 10000))

# Простое временное хранилище пользователей
users = {}


# =========================
# HTTP-СЕРВЕР ДЛЯ RENDER
# =========================

class HealthHandler(BaseHTTPRequestHandler):

    def do_GET(self):
        self.send_response(200)
        self.send_header(
            "Content-Type",
            "text/plain; charset=utf-8"
        )
        self.end_headers()
        self.wfile.write(b"BIZDE.KZ is running!")

    def log_message(self, format, *args):
        return


def run_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler
    )

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
        ],
    ])


# =========================
# РЕГИСТРАЦИЯ
# =========================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if user.id not in users:

        users[user.id] = {
            "id": user.id,
            "name": user.first_name,
            "username": user.username,
            "registered": True,
        }

        await update.message.reply_text(
            f"👋 Привет, {user.first_name}!\n\n"
            "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
            "Ты успешно зарегистрирован.\n"
            "Теперь тебе доступны категории, "
            "партнёры и специальные предложения.",
            reply_markup=main_keyboard()
        )

    else:

        await update.message.reply_text(
            f"👋 С возвращением, {user.first_name}!\n\n"
            "Добро пожаловать обратно в BIZDE.KZ 🇰🇿",
            reply_markup=main_keyboard()
        )


# =========================
# КАТЕГОРИИ
# =========================

def categories_keyboard():

    return InlineKeyboardMarkup([
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
                "💇 Красота",
                callback_data="beauty"
            )
        ],
        [
            InlineKeyboardButton(
                "🎮 Развлечения",
                callback_data="entertainment"
            )
        ],
        [
            InlineKeyboardButton(
                "⬅️ Назад",
                callback_data="back"
            )
        ],
    ])


# =========================
# ОБРАБОТКА КНОПОК
# =========================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    # -------------------------
    # КАТЕГОРИИ
    # -------------------------

    if query.data == "categories":

        await query.edit_message_text(
            "📂 Категории BIZDE.KZ\n\n"
            "Выбери интересующее направление:",
            reply_markup=categories_keyboard()
        )

    # -------------------------
    # ПАРТНЁРЫ
    # -------------------------

    elif query.data == "partners":

        keyboard = [
            [
                InlineKeyboardButton(
                    "📂 Смотреть категории",
                    callback_data="categories"
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
            "🏪 Партнёры BIZDE.KZ\n\n"
            "Здесь будут появляться наши партнёры "
            "и их специальные предложения.\n\n"
            "🔥 Скоро добавим первые заведения!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # -------------------------
    # ПОДПИСКА
    # -------------------------

    elif query.data == "subscription":

        keyboard = [
            [
                InlineKeyboardButton(
                    "💳 Оформить подписку",
                    callback_data="buy_subscription"
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
            "🎟 Моя подписка\n\n"
            "Статус: ❌ Не активна\n\n"
            "После подключения подписки "
            "ты сможешь пользоваться "
            "специальными предложениями партнёров.",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    # -------------------------
    # ОФОРМЛЕНИЕ
    # -------------------------

    elif query.data == "buy_subscription":

        await query.edit_message_text(
            "💳 Подписка BIZDE.KZ\n\n"
            "Функция оплаты пока находится "
            "в разработке.\n\n"
            "Скоро здесь появится возможность "
            "оформить подписку.",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "⬅️ Назад",
                        callback_data="subscription"
                    )
                ]
            ])
        )

    # -------------------------
    # КАТЕГОРИИ
    # -------------------------

    elif query.data in [
        "cafes",
        "shops",
        "sport",
        "beauty",
        "entertainment"
    ]:

        names = {
            "cafes": "☕ Кафе и рестораны",
            "shops": "🛍 Магазины",
            "sport": "🏋️ Спорт",
            "beauty": "💇 Красота",
            "entertainment": "🎮 Развлечения",
        }

        category_name = names[query.data]

        await query.edit_message_text(
            f"{category_name}\n\n"
            "🏪 Пока здесь нет партнёров.\n\n"
            "Мы уже работаем над добавлением "
            "новых предложений 🔥",
            reply_markup=InlineKeyboardMarkup([
                [
                    InlineKeyboardButton(
                        "📂 Другие категории",
                        callback_data="categories"
                    )
                ],
                [
                    InlineKeyboardButton(
                        "⬅️ Главное меню",
                        callback_data="back"
                    )
                ]
            ])
        )

    # -------------------------
    # НАЗАД
    # -------------------------

    elif query.data == "back":

        await query.edit_message_text(
            "🏠 Главное меню BIZDE.KZ\n\n"
            "Выбирай нужный раздел:",
            reply_markup=main_keyboard()
        )


# =========================
# ЗАПУСК
# =========================

if not TOKEN or TOKEN == "ВСТАВЬ_НОВЫЙ_ТОКЕН_СЮДА":

    raise RuntimeError(
        "Укажи токен Telegram-бота в переменной TOKEN"
    )


# Запускаем сервер Render
threading.Thread(
    target=run_server,
    daemon=True
).start()


# Создаём приложение Telegram
app = Application.builder().token(TOKEN).build()

app.add_handler(
    CommandHandler("start", start)
)

app.add_handler(
    CallbackQueryHandler(button_handler)
)


print("BIZDE.KZ запущен!")

app.run_polling()
