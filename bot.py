import os
import json
import time
import hmac
import hashlib
import logging
import threading
from urllib.parse import parse_qsl
from http.server import BaseHTTPRequestHandler, HTTPServer

import psycopg2
from psycopg2.extras import RealDictCursor

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


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"
PORT = int(os.environ.get("PORT", "10000"))

if not TOKEN:
    raise RuntimeError(
        "BOT_TOKEN не найден. Добавь BOT_TOKEN в Render Environment."
    )

if not DATABASE_URL:
    raise RuntimeError(
        "DATABASE_URL не найден. Добавь DATABASE_URL в Render Environment."
    )


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger("BIZDE")


# =========================================================
# DATABASE
# =========================================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_database():
    connection = None

    try:
        connection = get_db()

        cursor = connection.cursor()

        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name TEXT,
                username TEXT,
                phone TEXT,
                language TEXT DEFAULT 'ru',
                subscription_active BOOLEAN DEFAULT FALSE,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            """
        )

        connection.commit()
        cursor.close()

        logger.info("PostgreSQL database initialized successfully.")

    except Exception as e:
        logger.error(f"Database initialization error: {e}")

        if connection:
            connection.rollback()

        raise

    finally:
        if connection:
            connection.close()


def save_user(
    telegram_id,
    name=None,
    username=None,
    phone=None,
    language="ru",
):
    connection = None

    try:
        connection = get_db()

        cursor = connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
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
                name = COALESCE(EXCLUDED.name, users.name),
                username = COALESCE(EXCLUDED.username, users.username),
                phone = COALESCE(EXCLUDED.phone, users.phone),
                language = COALESCE(EXCLUDED.language, users.language),
                updated_at = CURRENT_TIMESTAMP

            RETURNING
                id,
                telegram_id,
                name,
                username,
                phone,
                language,
                subscription_active,
                registered_at;
            """,
            (
                telegram_id,
                name,
                username,
                phone,
                language,
            ),
        )

        user = cursor.fetchone()

        connection.commit()

        cursor.close()

        return dict(user) if user else None

    except Exception as e:
        logger.error(f"Save user error: {e}")

        if connection:
            connection.rollback()

        return None

    finally:
        if connection:
            connection.close()


def get_user(telegram_id):
    connection = None

    try:
        connection = get_db()

        cursor = connection.cursor(cursor_factory=RealDictCursor)

        cursor.execute(
            """
            SELECT
                id,
                telegram_id,
                name,
                username,
                phone,
                language,
                subscription_active,
                registered_at
            FROM users
            WHERE telegram_id = %s
            LIMIT 1;
            """,
            (telegram_id,),
        )

        user = cursor.fetchone()

        cursor.close()

        return dict(user) if user else None

    except Exception as e:
        logger.error(f"Get user error: {e}")
        return None

    finally:
        if connection:
            connection.close()


# =========================================================
# TELEGRAM MINI APP AUTH
# =========================================================

def validate_init_data(init_data):
    """
    Проверяет Telegram Web App initData.

    Возвращает данные пользователя только если подпись
    действительно соответствует BOT_TOKEN.
    """

    if not init_data:
        return None

    try:
        parsed = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            logger.warning("initData does not contain hash.")
            return None

        # Формируем data-check-string
        data_check_string = "\n".join(
            f"{key}={parsed[key]}"
            for key in sorted(parsed.keys())
        )

        # Секретный ключ Telegram Web App
        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256,
        ).digest()

        # Ожидаемый hash
        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256,
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash,
        ):
            logger.warning("Invalid Telegram initData hash.")
            return None

        # Проверяем свежесть авторизации
        auth_date = parsed.get("auth_date")

        if auth_date:
            try:
                auth_timestamp = int(auth_date)

                # 24 часа
                if time.time() - auth_timestamp > 86400:
                    logger.warning("Telegram initData is expired.")
                    return None

            except ValueError:
                logger.warning("Invalid auth_date.")
                return None

        # Получаем Telegram user
        user_json = parsed.get("user")

        if not user_json:
            logger.warning("Telegram user is missing.")
            return None

        telegram_user = json.loads(user_json)

        if not telegram_user.get("id"):
            logger.warning("Telegram user ID is missing.")
            return None

        return telegram_user

    except Exception as e:
        logger.error(f"initData validation error: {e}")
        return None


# =========================================================
# HTTP SERVER
# =========================================================

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

        body = json.dumps(
            data,
            ensure_ascii=False,
            default=str
        ).encode("utf-8")

        self.send_response(status)

        self.send_header(
            "Content-Type",
            "application/json; charset=utf-8"
        )

        self.send_header(
            "Content-Length",
            str(len(body))
        )

        self.send_cors()

        self.end_headers()

        self.wfile.write(body)

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_cors()

        self.end_headers()

    def do_GET(self):

        try:

            if self.path == "/" or self.path == "/health":

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

            # -------------------------------------------------
            # GET USER
            # -------------------------------------------------

            if self.path.startswith("/api/user"):

                from urllib.parse import urlparse

                parsed_url = urlparse(self.path)

                query_params = dict(
                    parse_qsl(
                        parsed_url.query,
                        keep_blank_values=True
                    )
                )

                telegram_id = query_params.get(
                    "telegram_id"
                )

                if not telegram_id:
                    self.send_json(
                        {
                            "success": False,
                            "error": "telegram_id is required"
                        },
                        400
                    )

                    return

                try:
                    telegram_id = int(telegram_id)

                except ValueError:

                    self.send_json(
                        {
                            "success": False,
                            "error": "Invalid telegram_id"
                        },
                        400
                    )

                    return

                user = get_user(telegram_id)

                if not user:

                    self.send_json(
                        {
                            "success": False,
                            "error": "User not found"
                        },
                        404
                    )

                    return

                self.send_json(
                    {
                        "success": True,
                        "user": user
                    }
                )

                return

            self.send_json(
                {
                    "success": False,
                    "error": "Not found"
                },
                404
            )

        except Exception as e:

            logger.error(
                f"GET request error: {e}"
            )

            self.send_json(
                {
                    "success": False,
                    "error": "Server error"
                },
                500
            )

    def do_POST(self):

        try:

            content_length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            body = self.rfile.read(
                content_length
            )

            data = json.loads(
                body.decode("utf-8")
            )

            # =================================================
            # SECURE TELEGRAM AUTH
            # =================================================

            if self.path == "/api/auth":

                init_data = data.get(
                    "init_data"
                )

                language = data.get(
                    "language",
                    "ru"
                )

                phone = data.get(
                    "phone"
                )

                if language not in ["ru", "kk"]:
                    language = "ru"

                telegram_user = validate_init_data(
                    init_data
                )

                if not telegram_user:

                    self.send_json(
                        {
                            "success": False,
                            "error": "Telegram authorization failed"
                        },
                        401
                    )

                    return

                telegram_id = int(
                    telegram_user["id"]
                )

                first_name = telegram_user.get(
                    "first_name",
                    ""
                )

                last_name = telegram_user.get(
                    "last_name",
                    ""
                )

                username = telegram_user.get(
                    "username"
                )

                full_name = (
                    f"{first_name} {last_name}"
                ).strip()

                user = save_user(
                    telegram_id=telegram_id,
                    name=full_name,
                    username=username,
                    phone=phone,
                    language=language,
                )

                if not user:

                    self.send_json(
                        {
                            "success": False,
                            "error": "Could not save user"
                        },
                        500
                    )

                    return

                self.send_json(
                    {
                        "success": True,
                        "user": user
                    }
                )

                return

            # =================================================
            # OLD REGISTER ENDPOINT
            # =================================================

            if self.path == "/api/register":

                init_data = data.get(
                    "init_data"
                )

                language = data.get(
                    "language",
                    "ru"
                )

                phone = data.get(
                    "phone"
                )

                telegram_user = validate_init_data(
                    init_data
                )

                if not telegram_user:

                    self.send_json(
                        {
                            "success": False,
                            "error": "Telegram authorization failed"
                        },
                        401
                    )

                    return

                telegram_id = int(
                    telegram_user["id"]
                )

                first_name = telegram_user.get(
                    "first_name",
                    ""
                )

                last_name = telegram_user.get(
                    "last_name",
                    ""
                )

                username = telegram_user.get(
                    "username"
                )

                name = (
                    f"{first_name} {last_name}"
                ).strip()

                user = save_user(
                    telegram_id=telegram_id,
                    name=name,
                    username=username,
                    phone=phone,
                    language=language,
                )

                self.send_json(
                    {
                        "success": True,
                        "user": user
                    }
                )

                return

            self.send_json(
                {
                    "success": False,
                    "error": "Not found"
                },
                404
            )

        except Exception as e:

            logger.error(
                f"POST request error: {e}"
            )

            self.send_json(
                {
                    "success": False,
                    "error": "Server error"
                },
                500
            )

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


# =========================================================
# TELEGRAM BOT
# =========================================================

def main_keyboard():

    return InlineKeyboardMarkup(
        [
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
        ]
    )


async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    try:

        user = update.effective_user

        if not user:
            return

        saved_user = save_user(
            telegram_id=user.id,
            name=user.full_name,
            username=user.username,
            language="ru",
        )

        await update.message.reply_text(

            f"👋 Привет, {user.first_name}!\n\n"

            "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"

            "Твой клуб привилегий.\n"
            "Получай специальные условия "
            "у наших партнёров.\n\n"

            "Открывай BIZDE и пользуйся "
            "своими привилегиями.",

            reply_markup=main_keyboard()
        )

    except Exception as e:

        logger.error(
            f"Start error: {e}"
        )


# =========================================================
# CATEGORIES
# =========================================================

def categories_keyboard():

    return InlineKeyboardMarkup(
        [
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
        ]
    )


# =========================================================
# BUTTON HANDLER
# =========================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    try:

        await query.answer()

        # -------------------------------------------------
        # CATEGORIES
        # -------------------------------------------------

        if query.data == "categories":

            await query.edit_message_text(

                "📂 Категории BIZDE.KZ\n\n"
                "Выбери интересующее направление:",

                reply_markup=categories_keyboard()
            )

        # -------------------------------------------------
        # PARTNERS
        # -------------------------------------------------

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

                "Здесь будут появляться "
                "наши партнёры и их "
                "специальные предложения.\n\n"

                "🔥 Скоро добавим первые заведения!",

                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        # -------------------------------------------------
        # SUBSCRIPTION
        # -------------------------------------------------

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

                "После подключения подписки "
                "ты сможешь пользоваться "
                "специальными предложениями "
                "партнёров.",

                reply_markup=InlineKeyboardMarkup(
                    keyboard
                )
            )

        # -------------------------------------------------
        # BUY SUBSCRIPTION
        # -------------------------------------------------

        elif query.data == "buy_subscription":

            await query.edit_message_text(

                "💳 Подписка BIZDE.KZ\n\n"

                "Функция оплаты пока "
                "находится в разработке.\n\n"

                "Скоро здесь появится "
                "возможность оформить подписку.",

                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "⬅️ Назад",
                                callback_data="subscription"
                            )
                        ]
                    ]
                )
            )

        # -------------------------------------------------
        # CATEGORY
        # -------------------------------------------------

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
                    "🎮 Развлечения",
            }

            category_name = names[
                query.data
            ]

            await query.edit_message_text(

                f"{category_name}\n\n"

                "🏪 Пока здесь нет партнёров.\n\n"

                "Мы уже работаем над "
                "добавлением новых предложений 🔥",

                reply_markup=InlineKeyboardMarkup(
                    [
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
                        ],
                    ]
                )
            )

        # -------------------------------------------------
        # BACK
        # -------------------------------------------------

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


# =========================================================
# ERROR HANDLER
# =========================================================

async def error_handler(
    update: object,
    context: ContextTypes.DEFAULT_TYPE
):

    logger.error(
        "Telegram error: %s",
        context.error
    )


# =========================================================
# START SERVER + DATABASE
# =========================================================

init_database()

threading.Thread(
    target=run_server,
    daemon=True
).start()


# =========================================================
# START BOT
# =========================================================

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


logger.info(
    "BIZDE.KZ Telegram bot is starting..."
)

app.run_polling(
    drop_pending_updates=True
)
