import os
import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

import psycopg2

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
)


# ==========================================
# НАСТРОЙКИ
# ==========================================

TOKEN = os.environ.get("BOT_TOKEN")

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"

PORT = int(os.environ.get("PORT", "10000"))

DATABASE_URL = os.environ.get("DATABASE_URL")


# ==========================================
# LOGGING
# ==========================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

logger = logging.getLogger(__name__)


# ==========================================
# ПРОВЕРКА НАСТРОЕК
# ==========================================

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден. Добавь BOT_TOKEN в Render Environment."
    )

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не найден. Добавь DATABASE_URL в Render Environment."
    )


# ==========================================
# DATABASE
# ==========================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_database():
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name TEXT,
                username TEXT,
                phone TEXT,
                language TEXT DEFAULT 'ru',
                subscription_active BOOLEAN DEFAULT FALSE,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()
        cursor.close()
        conn.close()

        logger.info("PostgreSQL database initialized successfully.")

    except Exception as e:
        logger.error(f"Database initialization error: {e}")
        raise


def save_user(
    telegram_id,
    name=None,
    username=None,
    phone=None,
    language="ru"
):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            INSERT INTO users (
                telegram_id,
                name,
                username,
                phone,
                language
            )
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (telegram_id)
            DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username
        """, (
            telegram_id,
            name,
            username,
            phone,
            language
        ))

        conn.commit()

        cursor.close()
        conn.close()

        return True

    except Exception as e:
        logger.error(f"Save user error: {e}")
        return False


def get_user(telegram_id):
    try:
        conn = get_db()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                telegram_id,
                name,
                username,
                phone,
                language,
                subscription_active,
                registered_at
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        row = cursor.fetchone()

        cursor.close()
        conn.close()

        if not row:
            return None

        return {
            "telegram_id": row[0],
            "name": row[1],
            "username": row[2],
            "phone": row[3],
            "language": row[4],
            "subscription_active": row[5],
            "registered_at": str(row[6])
        }

    except Exception as e:
        logger.error(f"Get user error: {e}")
        return None


# ==========================================
# HEALTH + API SERVER
# ==========================================

class HealthHandler(BaseHTTPRequestHandler):

    def send_cors(self):
        self.send_header(
            "Access-Control-Allow-Origin",
            "*"
        )
        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )
        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

    def send_json(self, data, status=200):

        response = json.dumps(
            data,
            ensure_ascii=False
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(response))
        )

        self.send_cors()

        self.end_headers()

        self.wfile.write(response)

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_cors()

        self.end_headers()

    def do_GET(self):

        parsed = urlparse(self.path)

        # --------------------------
        # HEALTH CHECK
        # --------------------------

        if parsed.path == "/" or parsed.path == "/health":

            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.send_cors()

            self.end_headers()

            self.wfile.write(
                b"BIZDE.KZ is running!"
            )

            return

        # --------------------------
        # GET USER
        # --------------------------

        if parsed.path == "/api/user":

            params = parse_qs(parsed.query)

            telegram_id = params.get(
                "telegram_id",
                [None]
            )[0]

            if not telegram_id:

                self.send_json({
                    "success": False,
                    "error": "telegram_id is required"
                }, 400)

                return

            try:
                telegram_id = int(telegram_id)

            except ValueError:

                self.send_json({
                    "success": False,
                    "error": "Invalid telegram_id"
                }, 400)

                return

            user = get_user(telegram_id)

            if not user:

                self.send_json({
                    "success": True,
                    "registered": False,
                    "user": None
                })

                return

            self.send_json({
                "success": True,
                "registered": True,
                "user": user
            })

            return

        # --------------------------
        # 404
        # --------------------------

        self.send_json({
            "success": False,
            "error": "Not found"
        }, 404)

    def do_POST(self):

        parsed = urlparse(self.path)

        # --------------------------
        # REGISTER USER
        # --------------------------

        if parsed.path == "/api/register":

            try:

                content_length = int(
                    self.headers.get(
                        "Content-Length",
                        "0"
                    )
                )

                body = self.rfile.read(
                    content_length
                )

                data = json.loads(
                    body.decode("utf-8")
                )

                telegram_id = data.get(
                    "telegram_id"
                )

                name = data.get(
                    "name"
                )

                username = data.get(
                    "username"
                )

                phone = data.get(
                    "phone"
                )

                language = data.get(
                    "language",
                    "ru"
                )

                if not telegram_id:

                    self.send_json({
                        "success": False,
                        "error": "telegram_id is required"
                    }, 400)

                    return

                try:
                    telegram_id = int(
                        telegram_id
                    )

                except ValueError:

                    self.send_json({
                        "success": False,
                        "error": "Invalid telegram_id"
                    }, 400)

                    return

                success = save_user(
                    telegram_id=telegram_id,
                    name=name,
                    username=username,
                    phone=phone,
                    language=language
                )

                if not success:

                    self.send_json({
                        "success": False,
                        "error": "Database error"
                    }, 500)

                    return

                user = get_user(
                    telegram_id
                )

                self.send_json({
                    "success": True,
                    "registered": True,
                    "user": user
                })

                return

            except Exception as e:

                logger.error(
                    f"Registration API error: {e}"
                )

                self.send_json({
                    "success": False,
                    "error": "Invalid request"
                }, 400)

                return

        # --------------------------
        # 404
        # --------------------------

        self.send_json({
            "success": False,
            "error": "Not found"
        }, 404)

    def log_message(self, format, *args):
        return


def run_server():

    try:

        server = HTTPServer(
            ("0.0.0.0", PORT),
            HealthHandler
        )

        logger.info(
            f"HTTP server started on port {PORT}"
        )

        server.serve_forever()

    except Exception as e:

        logger.error(
            f"HTTP server error: {e}"
        )


# ==========================================
# TELEGRAM KEYBOARD
# ==========================================

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
        ]

    ])


# ==========================================
# START
# ==========================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        user = update.effective_user

        if not user:
            return

        save_user(
            telegram_id=user.id,
            name=user.first_name,
            username=user.username
        )

        await update.message.reply_text(

            f"👋 Привет, {user.first_name}!\n\n"

            "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"

            "Твой доступ к привилегиям уже создан.\n"
            "Открывай BIZDE и пользуйся предложениями "
            "наших партнёров.",

            reply_markup=main_keyboard()
        )

    except Exception as e:

        logger.error(
            f"Start error: {e}"
        )


# ==========================================
# CATEGORIES
# ==========================================

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
        ]

    ])


# ==========================================
# BUTTON HANDLER
# ==========================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:

        await query.answer()

        # --------------------------
        # CATEGORIES
        # --------------------------

        if query.data == "categories":

            await query.edit_message_text(

                "📂 Категории BIZDE.KZ\n\n"
                "Выбери интересующее направление:",

                reply_markup=categories_keyboard()
            )

        # --------------------------
        # PARTNERS
        # --------------------------

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
                ]

            ]

            await query.edit_message_text(

                "🏪 Партнёры BIZDE.KZ\n\n"

                "Здесь будут появляться наши партнёры "
                "и их специальные предложения.\n\n"

                "🔥 Скоро добавим первые заведения!",

                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        # --------------------------
        # SUBSCRIPTION
        # --------------------------

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
                ]

            ]

            await query.edit_message_text(

                "🎟 Моя подписка\n\n"

                "Статус: ❌ Не активна\n\n"

                "После подключения подписки ты сможешь "
                "пользоваться специальными предложениями "
                "партнёров.",

                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        # --------------------------
        # BUY SUBSCRIPTION
        # --------------------------

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

        # --------------------------
        # CATEGORY
        # --------------------------

        elif query.data in [
            "cafes",
            "shops",
            "sport",
            "beauty",
            "entertainment"
        ]:

            names = {

                "cafes":
                    "☕ Кафе и рестораны",

                "shops":
                    "🛍 Магазины",

                "sport":
                    "🏋️ Спорт",

                "beauty":
                    "💇 Красота",

                "entertainment":
                    "🎮 Развлечения"

            }

            category_name = names[
                query.data
            ]

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

        # --------------------------
        # BACK
        # --------------------------

        elif query.data == "back":

            await query.edit_message_text(

                "🏠 Главное меню BIZDE.KZ\n\n"
                "Выбирай нужный раздел:",

                reply_markup=main_keyboard()
            )

    except Exception as e:

        logger.error(
            f"Button handler error: {e}"
        )


# ==========================================
# ERROR HANDLER
# ==========================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Telegram error: %s",
        context.error
    )


# ==========================================
# START SERVER + DATABASE
# ==========================================

init_database()

threading.Thread(
    target=run_server,
    daemon=True
).start()


# ==========================================
# TELEGRAM APPLICATION
# ==========================================

app = (
    Application
    .builder()
    .token(TOKEN)
    .build()
)

app.add_handler(
    CommandHandler(
        "start",
        start
    )
)

app.add_handler(
    CallbackQueryHandler(
        button_handler
    )
)

app.add_error_handler(
    error_handler
)


# ==========================================
# RUN
# ==========================================

logger.info(
    "BIZDE.KZ Telegram bot is starting..."
)

app.run_polling(
    drop_pending_updates=True
)
