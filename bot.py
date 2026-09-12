import os
import json
import hmac
import hashlib
import logging
import threading
from datetime import datetime, timezone

import psycopg2
from psycopg2.extras import RealDictCursor

from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    WebAppInfo,
)
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
)


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"
PORT = int(os.environ.get("PORT", "10000"))

ALLOWED_ORIGIN = "https://aydin200169.github.io"

if not TOKEN:
    raise RuntimeError("BOT_TOKEN не найден в Environment")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL не найден в Environment")


# =========================================================
# LOGGING
# =========================================================

logging.basicConfig(
    format="%(asctime)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)

logger = logging.getLogger(__name__)


# =========================================================
# DATABASE
# =========================================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_database():
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name TEXT,
                username TEXT,
                phone TEXT,
                language VARCHAR(5) DEFAULT 'ru',
                subscription_active BOOLEAN DEFAULT FALSE,
                total_savings NUMERIC(12,2) DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.commit()

        logger.info("PostgreSQL database initialized successfully.")

    finally:
        conn.close()


def upsert_user(
    telegram_id,
    name=None,
    username=None,
    language="ru",
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            INSERT INTO users
            (
                telegram_id,
                name,
                username,
                language
            )
            VALUES (%s, %s, %s, %s)

            ON CONFLICT (telegram_id)
            DO UPDATE SET
                name = EXCLUDED.name,
                username = EXCLUDED.username,
                language = EXCLUDED.language,
                updated_at = CURRENT_TIMESTAMP

            RETURNING
                telegram_id,
                name,
                username,
                phone,
                language,
                subscription_active,
                total_savings
            """,
            (
                telegram_id,
                name,
                username,
                language,
            ),
        )

        user = cur.fetchone()

        conn.commit()

        return dict(user) if user else None

    finally:
        conn.close()


def get_user(telegram_id):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute(
            """
            SELECT
                telegram_id,
                name,
                username,
                phone,
                language,
                subscription_active,
                total_savings
            FROM users
            WHERE telegram_id = %s
            """,
            (telegram_id,),
        )

        user = cur.fetchone()

        return dict(user) if user else None

    finally:
        conn.close()


def update_language(telegram_id, language):
    if language not in ("ru", "kk"):
        language = "ru"

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute(
            """
            UPDATE users
            SET
                language = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = %s
            """,
            (language, telegram_id),
        )

        conn.commit()

    finally:
        conn.close()


# =========================================================
# TELEGRAM MINI APP AUTH
# =========================================================

def validate_telegram_init_data(init_data):
    """
    Проверяет Telegram Web App initData.

    Возвращает данные пользователя,
    если подпись Telegram правильная.
    """

    if not init_data:
        raise ValueError("init_data отсутствует")

    try:
        parsed = dict(
            item.split("=", 1)
            for item in init_data.split("&")
            if "=" in item
        )
    except Exception:
        raise ValueError("Некорректный init_data")

    received_hash = parsed.pop("hash", None)

    if not received_hash:
        raise ValueError("hash отсутствует")

    # Создаём data-check-string
    data_check_string = "\n".join(
        f"{key}={parsed[key]}"
        for key in sorted(parsed.keys())
    )

    # Секретный ключ Telegram
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
        raise ValueError("Неверная подпись Telegram")

    # Проверяем время авторизации
    auth_date = parsed.get("auth_date")

    if not auth_date:
        raise ValueError("auth_date отсутствует")

    try:
        auth_timestamp = int(auth_date)
    except ValueError:
        raise ValueError("Некорректный auth_date")

    now = int(datetime.now(timezone.utc).timestamp())

    # 24 часа
    if now - auth_timestamp > 86400:
        raise ValueError("init_data устарел")

    # Достаём Telegram user
    telegram_user = parsed.get("user")

    if not telegram_user:
        raise ValueError("Telegram user отсутствует")

    try:
        user = json.loads(telegram_user)
    except Exception:
        raise ValueError("Не удалось прочитать Telegram user")

    telegram_id = user.get("id")

    if not telegram_id:
        raise ValueError("Telegram ID отсутствует")

    first_name = user.get("first_name", "")
    last_name = user.get("last_name", "")

    name = f"{first_name} {last_name}".strip()

    username = user.get("username")

    return {
        "telegram_id": int(telegram_id),
        "name": name or "Участник BIZDE",
        "username": username,
        "photo_url": user.get("photo_url"),
    }


# =========================================================
# JSON
# =========================================================

def json_response(handler, data, status=200):
    body = json.dumps(
        data,
        ensure_ascii=False,
        default=str,
    ).encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8",
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        ALLOWED_ORIGIN,
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS",
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type",
    )

    handler.send_header(
        "Access-Control-Allow-Credentials",
        "true",
    )

    handler.send_header(
        "Content-Length",
        str(len(body)),
    )

    handler.end_headers()

    handler.wfile.write(body)


def read_json(handler):
    length = int(
        handler.headers.get(
            "Content-Length",
            "0",
        )
    )

    if length <= 0:
        return {}

    raw = handler.rfile.read(length)

    try:
        return json.loads(
            raw.decode("utf-8")
        )
    except Exception:
        return {}


# =========================================================
# HTTP SERVER
# =========================================================

class HealthHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        return

    def do_OPTIONS(self):
        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            ALLOWED_ORIGIN,
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS",
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type",
        )

        self.end_headers()

    def do_GET(self):

        parsed = urlparse(self.path)

        # -----------------------------------------
        # HEALTH
        # -----------------------------------------

        if parsed.path == "/":
            json_response(
                self,
                {
                    "ok": True,
                    "service": "BIZDE.KZ",
                    "message": "BIZDE.KZ is running!"
                }
            )
            return

        # -----------------------------------------
        # USER
        # -----------------------------------------

        if parsed.path == "/api/user":

            params = parse_qs(parsed.query)

            telegram_id = params.get(
                "telegram_id",
                [None]
            )[0]

            if not telegram_id:
                json_response(
                    self,
                    {
                        "ok": False,
                        "error": "telegram_id required"
                    },
                    400,
                )
                return

            try:
                telegram_id = int(telegram_id)
            except ValueError:
                json_response(
                    self,
                    {
                        "ok": False,
                        "error": "invalid telegram_id"
                    },
                    400,
                )
                return

            user = get_user(telegram_id)

            if not user:
                json_response(
                    self,
                    {
                        "ok": False,
                        "error": "user not found"
                    },
                    404,
                )
                return

            json_response(
                self,
                {
                    "ok": True,
                    "user": user,
                }
            )

            return

        json_response(
            self,
            {
                "ok": False,
                "error": "not found"
            },
            404,
        )

    def do_POST(self):

        parsed = urlparse(self.path)

        data = read_json(self)

        # =========================================
        # AUTH
        # =========================================

        if parsed.path == "/api/auth":

            init_data = data.get("init_data")
            language = data.get("language", "ru")

            try:
                tg_user = validate_telegram_init_data(
                    init_data
                )

                telegram_id = tg_user["telegram_id"]

                user = upsert_user(
                    telegram_id=telegram_id,
                    name=tg_user["name"],
                    username=tg_user["username"],
                    language=language,
                )

                if not user:
                    raise ValueError(
                        "Не удалось сохранить пользователя"
                    )

                user["photo_url"] = tg_user.get(
                    "photo_url"
                )

                json_response(
                    self,
                    {
                        "ok": True,
                        "user": user,
                    }
                )

            except Exception as e:

                logger.warning(
                    "Auth error: %s",
                    str(e),
                )

                json_response(
                    self,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                    401,
                )

            return

        # =========================================
        # LANGUAGE
        # =========================================

        if parsed.path == "/api/language":

            init_data = data.get("init_data")
            language = data.get("language", "ru")

            try:

                tg_user = validate_telegram_init_data(
                    init_data
                )

                telegram_id = tg_user["telegram_id"]

                update_language(
                    telegram_id,
                    language,
                )

                user = get_user(
                    telegram_id
                )

                json_response(
                    self,
                    {
                        "ok": True,
                        "user": user,
                    }
                )

            except Exception as e:

                json_response(
                    self,
                    {
                        "ok": False,
                        "error": str(e),
                    },
                    401,
                )

            return

        json_response(
            self,
            {
                "ok": False,
                "error": "not found"
            },
            404,
        )


def start_http_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        HealthHandler,
    )

    logger.info(
        "HTTP server started on port %s",
        PORT,
    )

    server.serve_forever()


# =========================================================
# TELEGRAM BOT
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    if user:

        name = (
            f"{user.first_name or ''} "
            f"{user.last_name or ''}"
        ).strip()

        if not name:
            name = "Участник BIZDE"

        try:
            upsert_user(
                telegram_id=user.id,
                name=name,
                username=user.username,
                language="ru",
            )
        except Exception as e:
            logger.error(
                "Ошибка сохранения пользователя: %s",
                e,
            )

    keyboard = [

        [
            InlineKeyboardButton(
                "📱 Открыть BIZDE",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                ),
            )
        ],

        [
            InlineKeyboardButton(
                "📂 Категории",
                callback_data="categories"
            ),

            InlineKeyboardButton(
                "🏪 Партнёры",
                callback_data="partners"
            ),
        ],

        [
            InlineKeyboardButton(
                "🎟 Моя подписка",
                callback_data="subscription"
            )
        ],
    ]

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Экономьте больше.\n"
        "Получайте больше привилегий.\n"
        "Будьте частью клуба.",
        reply_markup=reply_markup,
    )


# =========================================================
# MAIN
# =========================================================

def main():

    logger.info("Starting BIZDE.KZ...")

    init_database()

    http_thread = threading.Thread(
        target=start_http_server,
        daemon=True,
    )

    http_thread.start()

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start,
        )
    )

    logger.info(
        "Telegram bot started."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
