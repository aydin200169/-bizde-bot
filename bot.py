import os
import json
import hmac
import hashlib
import secrets
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

ALLOWED_ORIGIN = "https://aydin200169.github.io"


# =========================================================
# ПРОВЕРКА НАСТРОЕК
# =========================================================

if not TOKEN:
    raise RuntimeError("BOT_TOKEN is not configured")

if not DATABASE_URL:
    raise RuntimeError("DATABASE_URL is not configured")


# =========================================================
# DATABASE
# =========================================================

def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_database():
    conn = get_db()

    try:
        cur = conn.cursor()

        # -------------------------------------------------
        # USERS
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                name TEXT,
                username TEXT,
                phone TEXT,
                language VARCHAR(5) DEFAULT 'ru',
                member_code TEXT UNIQUE,
                subscription_active BOOLEAN DEFAULT FALSE,
                total_savings NUMERIC(12,2) DEFAULT 0,
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # На случай если таблица уже существовала
        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS member_code TEXT UNIQUE
        """)

        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS total_savings NUMERIC(12,2) DEFAULT 0
        """)

        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS subscription_active BOOLEAN DEFAULT FALSE
        """)

        cur.execute("""
            ALTER TABLE users
            ADD COLUMN IF NOT EXISTS language VARCHAR(5) DEFAULT 'ru'
        """)

        # -------------------------------------------------
        # PARTNERS
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS partners (
                id SERIAL PRIMARY KEY,
                name TEXT NOT NULL,
                category TEXT NOT NULL,
                description TEXT,
                city TEXT DEFAULT 'Павлодар',
                address TEXT,
                offer_title TEXT,
                discount_percent NUMERIC(5,2) DEFAULT 0,
                conditions TEXT,
                image_url TEXT,
                active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -------------------------------------------------
        # TRANSACTIONS
        # -------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                partner_id INTEGER NOT NULL,
                receipt_amount NUMERIC(12,2) NOT NULL,
                discount_percent NUMERIC(5,2) NOT NULL,
                savings NUMERIC(12,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -------------------------------------------------
        # DEMO PARTNERS
        # -------------------------------------------------

        cur.execute("SELECT COUNT(*) FROM partners")
        count = cur.fetchone()[0]

        if count == 0:
            cur.execute("""
                INSERT INTO partners
                (
                    name,
                    category,
                    description,
                    city,
                    address,
                    offer_title,
                    discount_percent,
                    conditions,
                    image_url,
                    active
                )
                VALUES
                (
                    'Партнёр 1',
                    'Рестораны',
                    'Восточная кухня',
                    'Павлодар',
                    'Павлодар',
                    'Привилегия BIZDE',
                    20,
                    'Скидка действует для активных участников BIZDE.',
                    '',
                    TRUE
                )
            """)

            cur.execute("""
                INSERT INTO partners
                (
                    name,
                    category,
                    description,
                    city,
                    address,
                    offer_title,
                    discount_percent,
                    conditions,
                    image_url,
                    active
                )
                VALUES
                (
                    'VERO Café',
                    'Кафе',
                    'Кофе и десерты',
                    'Павлодар',
                    'Павлодар',
                    'Привилегия BIZDE',
                    20,
                    'Скидка для активных участников клуба.',
                    '',
                    TRUE
                )
            """)

            cur.execute("""
                INSERT INTO partners
                (
                    name,
                    category,
                    description,
                    city,
                    address,
                    offer_title,
                    discount_percent,
                    conditions,
                    image_url,
                    active
                )
                VALUES
                (
                    'FITROOM',
                    'Спорт',
                    'Фитнес и тренировки',
                    'Павлодар',
                    'Павлодар',
                    'Привилегия BIZDE',
                    15,
                    'Условия уточняются у партнёра.',
                    '',
                    TRUE
                )
            """)

            cur.execute("""
                INSERT INTO partners
                (
                    name,
                    category,
                    description,
                    city,
                    address,
                    offer_title,
                    discount_percent,
                    conditions,
                    image_url,
                    active
                )
                VALUES
                (
                    'Beauty Room',
                    'Красота',
                    'Услуги красоты',
                    'Павлодар',
                    'Павлодар',
                    'Привилегия BIZDE',
                    15,
                    'Действует при предъявлении членства BIZDE.',
                    '',
                    TRUE
                )
            """)

        conn.commit()

        print("PostgreSQL database initialized successfully.")

    finally:
        conn.close()


# =========================================================
# MEMBER CODE
# =========================================================

def generate_member_code():
    return "BIZDE-" + secrets.token_hex(4).upper()


def get_unique_member_code(conn):
    while True:
        code = generate_member_code()

        cur = conn.cursor()
        cur.execute(
            "SELECT id FROM users WHERE member_code = %s",
            (code,)
        )

        if not cur.fetchone():
            return code


# =========================================================
# USER FUNCTIONS
# =========================================================

def upsert_user(
    telegram_id,
    name=None,
    username=None,
    phone=None,
    language="ru"
):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT id, member_code
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        existing = cur.fetchone()

        if existing:
            member_code = existing[1]

            if not member_code:
                member_code = get_unique_member_code(conn)

                cur.execute("""
                    UPDATE users
                    SET
                        name = COALESCE(%s, name),
                        username = COALESCE(%s, username),
                        phone = COALESCE(%s, phone),
                        language = COALESCE(%s, language),
                        member_code = %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE telegram_id = %s
                """, (
                    name,
                    username,
                    phone,
                    language,
                    member_code,
                    telegram_id
                ))
            else:
                cur.execute("""
                    UPDATE users
                    SET
                        name = COALESCE(%s, name),
                        username = COALESCE(%s, username),
                        phone = COALESCE(%s, phone),
                        language = COALESCE(%s, language),
                        updated_at = CURRENT_TIMESTAMP
                    WHERE telegram_id = %s
                """, (
                    name,
                    username,
                    phone,
                    language,
                    telegram_id
                ))

        else:
            member_code = get_unique_member_code(conn)

            cur.execute("""
                INSERT INTO users
                (
                    telegram_id,
                    name,
                    username,
                    phone,
                    language,
                    member_code
                )
                VALUES (%s, %s, %s, %s, %s, %s)
            """, (
                telegram_id,
                name,
                username,
                phone,
                language,
                member_code
            ))

        conn.commit()

    finally:
        conn.close()


def get_user(telegram_id):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                id,
                telegram_id,
                name,
                username,
                phone,
                language,
                member_code,
                subscription_active,
                total_savings,
                registered_at
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        return cur.fetchone()

    finally:
        conn.close()


def update_language(telegram_id, language):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET
                language = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = %s
        """, (
            language,
            telegram_id
        ))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# TELEGRAM INIT DATA
# =========================================================

def validate_telegram_init_data(init_data):
    if not init_data:
        return None

    try:
        from urllib.parse import parse_qsl

        data = dict(parse_qsl(init_data, keep_blank_values=True))

        received_hash = data.get("hash")
        auth_date = data.get("auth_date")

        if not received_hash or not auth_date:
            return None

        # Проверяем время
        current_time = int(datetime.now(timezone.utc).timestamp())
        auth_time = int(auth_date)

        # 24 часа
        if current_time - auth_time > 86400:
            return None

        data_check = []

        for key in sorted(data.keys()):
            if key == "hash":
                continue

            data_check.append(
                f"{key}={data[key]}"
            )

        data_check_string = "\n".join(data_check)

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode(),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        telegram_user = json.loads(data["user"])

        return telegram_user

    except Exception as e:
        print("Telegram auth error:", e)
        return None


# =========================================================
# JSON RESPONSE
# =========================================================

def send_json(handler, data, status=200):
    body = json.dumps(
        data,
        ensure_ascii=False,
        default=str
    ).encode("utf-8")

    handler.send_response(status)

    handler.send_header(
        "Content-Type",
        "application/json; charset=utf-8"
    )

    handler.send_header(
        "Access-Control-Allow-Origin",
        ALLOWED_ORIGIN
    )

    handler.send_header(
        "Access-Control-Allow-Headers",
        "Content-Type"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
    )

    handler.send_header(
        "Content-Length",
        str(len(body))
    )

    handler.end_headers()

    handler.wfile.write(body)


# =========================================================
# READ JSON
# =========================================================

def read_json(handler):
    try:
        length = int(
            handler.headers.get("Content-Length", 0)
        )

        raw = handler.rfile.read(length)

        return json.loads(
            raw.decode("utf-8")
        )

    except Exception:
        return {}


# =========================================================
# API SERVER
# =========================================================

class APIHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        return

    # -----------------------------------------------------
    # OPTIONS
    # -----------------------------------------------------

    def do_OPTIONS(self):
        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            ALLOWED_ORIGIN
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.end_headers()

    # -----------------------------------------------------
    # GET
    # -----------------------------------------------------

    def do_GET(self):

        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # Health check
        if path == "/":
            self.send_response(200)

            self.send_header(
                "Content-Type",
                "text/plain; charset=utf-8"
            )

            self.end_headers()

            self.wfile.write(
                b"BIZDE.KZ is running!"
            )

            return

        # -------------------------------------------------
        # GET /api/user
        # -------------------------------------------------

        if path == "/api/user":

            telegram_id = params.get(
                "telegram_id",
                [None]
            )[0]

            if not telegram_id:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "telegram_id required"
                    },
                    400
                )
                return

            user = get_user(int(telegram_id))

            if not user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "user_not_found"
                    },
                    404
                )
                return

            send_json(
                self,
                {
                    "ok": True,
                    "user": dict(user)
                }
            )

            return

        # -------------------------------------------------
        # GET /api/partners
        # -------------------------------------------------

        if path == "/api/partners":

            category = params.get(
                "category",
                [None]
            )[0]

            conn = get_db()

            try:
                cur = conn.cursor(
                    cursor_factory=RealDictCursor
                )

                if category:
                    cur.execute("""
                        SELECT *
                        FROM partners
                        WHERE active = TRUE
                        AND category = %s
                        ORDER BY id DESC
                    """, (category,))
                else:
                    cur.execute("""
                        SELECT *
                        FROM partners
                        WHERE active = TRUE
                        ORDER BY id DESC
                    """)

                partners = cur.fetchall()

            finally:
                conn.close()

            send_json(
                self,
                {
                    "ok": True,
                    "partners": [
                        dict(partner)
                        for partner in partners
                    ]
                }
            )

            return

        # -------------------------------------------------
        # GET /api/history
        # -------------------------------------------------

        if path == "/api/history":

            init_data = params.get(
                "init_data",
                [None]
            )[0]

            telegram_user = validate_telegram_init_data(
                init_data
            )

            if not telegram_user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "invalid_init_data"
                    },
                    401
                )
                return

            telegram_id = telegram_user["id"]

            conn = get_db()

            try:
                cur = conn.cursor(
                    cursor_factory=RealDictCursor
                )

                cur.execute("""
                    SELECT
                        t.id,
                        t.receipt_amount,
                        t.discount_percent,
                        t.savings,
                        t.created_at,
                        p.name AS partner_name,
                        p.category
                    FROM transactions t
                    JOIN partners p
                    ON p.id = t.partner_id
                    WHERE t.telegram_id = %s
                    ORDER BY t.created_at DESC
                    LIMIT 100
                """, (telegram_id,))

                history = cur.fetchall()

            finally:
                conn.close()

            send_json(
                self,
                {
                    "ok": True,
                    "history": [
                        dict(item)
                        for item in history
                    ]
                }
            )

            return

        send_json(
            self,
            {
                "ok": False,
                "error": "not_found"
            },
            404
        )

    # -----------------------------------------------------
    # POST
    # -----------------------------------------------------

    def do_POST(self):

        parsed = urlparse(self.path)
        path = parsed.path

        data = read_json(self)

        # -------------------------------------------------
        # AUTH
        # -------------------------------------------------

        if path == "/api/auth":

            init_data = data.get("init_data")
            language = data.get("language", "ru")

            telegram_user = validate_telegram_init_data(
                init_data
            )

            if not telegram_user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "invalid_init_data"
                    },
                    401
                )
                return

            telegram_id = telegram_user["id"]

            first_name = telegram_user.get(
                "first_name",
                ""
            )

            last_name = telegram_user.get(
                "last_name",
                ""
            )

            name = (
                first_name + " " + last_name
            ).strip()

            username = telegram_user.get(
                "username"
            )

            upsert_user(
                telegram_id=telegram_id,
                name=name,
                username=username,
                language=language
            )

            user = get_user(telegram_id)

            send_json(
                self,
                {
                    "ok": True,
                    "user": dict(user)
                }
            )

            return

        # -------------------------------------------------
        # LANGUAGE
        # -------------------------------------------------

        if path == "/api/language":

            init_data = data.get("init_data")
            language = data.get("language", "ru")

            telegram_user = validate_telegram_init_data(
                init_data
            )

            if not telegram_user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "invalid_init_data"
                    },
                    401
                )
                return

            telegram_id = telegram_user["id"]

            update_language(
                telegram_id,
                language
            )

            send_json(
                self,
                {
                    "ok": True,
                    "language": language
                }
            )

            return

        # -------------------------------------------------
        # VERIFY MEMBER
        # -------------------------------------------------

        if path == "/api/verify-member":

            member_code = data.get("member_code")

            if not member_code:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "member_code_required"
                    },
                    400
                )
                return

            conn = get_db()

            try:
                cur = conn.cursor(
                    cursor_factory=RealDictCursor
                )

                cur.execute("""
                    SELECT
                        telegram_id,
                        name,
                        username,
                        member_code,
                        subscription_active
                    FROM users
                    WHERE member_code = %s
                """, (
                    member_code,
                ))

                user = cur.fetchone()

            finally:
                conn.close()

            if not user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "member_not_found"
                    },
                    404
                )
                return

            send_json(
                self,
                {
                    "ok": True,
                    "member": dict(user),
                    "active": bool(
                        user["subscription_active"]
                    )
                }
            )

            return

        # -------------------------------------------------
        # USE OFFER
        # -------------------------------------------------

        if path == "/api/use-offer":

            init_data = data.get("init_data")
            partner_id = data.get("partner_id")
            receipt_amount = data.get("receipt_amount")

            telegram_user = validate_telegram_init_data(
                init_data
            )

            if not telegram_user:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "invalid_init_data"
                    },
                    401
                )
                return

            if not partner_id:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "partner_id_required"
                    },
                    400
                )
                return

            if receipt_amount is None:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "receipt_amount_required"
                    },
                    400
                )
                return

            try:
                receipt_amount = float(
                    receipt_amount
                )

                if receipt_amount <= 0:
                    raise ValueError()

            except Exception:
                send_json(
                    self,
                    {
                        "ok": False,
                        "error": "invalid_receipt_amount"
                    },
                    400
                )
                return

            telegram_id = telegram_user["id"]

            conn = get_db()

            try:
                cur = conn.cursor(
                    cursor_factory=RealDictCursor
                )

                # Проверяем пользователя
                cur.execute("""
                    SELECT
                        telegram_id,
                        subscription_active
                    FROM users
                    WHERE telegram_id = %s
                """, (
                    telegram_id,
                ))

                user = cur.fetchone()

                if not user:
                    send_json(
                        self,
                        {
                            "ok": False,
                            "error": "user_not_found"
                        },
                        404
                    )
                    return

                if not user["subscription_active"]:
                    send_json(
                        self,
                        {
                            "ok": False,
                            "error": "subscription_inactive"
                        },
                        403
                    )
                    return

                # Получаем партнёра
                cur.execute("""
                    SELECT
                        id,
                        name,
                        discount_percent,
                        active
                    FROM partners
                    WHERE id = %s
                """, (
                    partner_id,
                ))

                partner = cur.fetchone()

                if not partner:
                    send_json(
                        self,
                        {
                            "ok": False,
                            "error": "partner_not_found"
                        },
                        404
                    )
                    return

                if not partner["active"]:
                    send_json(
                        self,
                        {
                            "ok": False,
                            "error": "partner_inactive"
                        },
                        403
                    )
                    return

                discount = float(
                    partner["discount_percent"]
                )

                savings = round(
                    receipt_amount * discount / 100,
                    2
                )

                # Сохраняем операцию
                cur.execute("""
                    INSERT INTO transactions
                    (
                        telegram_id,
                        partner_id,
                        receipt_amount,
                        discount_percent,
                        savings
                    )
                    VALUES (%s, %s, %s, %s, %s)
                    RETURNING id, created_at
                """, (
                    telegram_id,
                    partner_id,
                    receipt_amount,
                    discount,
                    savings
                ))

                transaction = cur.fetchone()

                # Обновляем общую экономию
                cur.execute("""
                    UPDATE users
                    SET
                        total_savings =
                            COALESCE(total_savings, 0)
                            + %s,
                        updated_at = CURRENT_TIMESTAMP
                    WHERE telegram_id = %s
                """, (
                    savings,
                    telegram_id
                ))

                conn.commit()

            finally:
                conn.close()

            send_json(
                self,
                {
                    "ok": True,
                    "transaction_id": transaction["id"],
                    "receipt_amount": receipt_amount,
                    "discount_percent": discount,
                    "savings": savings,
                    "created_at": transaction["created_at"]
                }
            )

            return

        send_json(
            self,
            {
                "ok": False,
                "error": "not_found"
            },
            404
        )


# =========================================================
# HTTP SERVER
# =========================================================

def run_http_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        APIHandler
    )

    print(
        f"HTTP server started on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# TELEGRAM BOT
# =========================================================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):

    user = update.effective_user

    upsert_user(
        telegram_id=user.id,
        name=user.full_name,
        username=user.username
    )

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
            ),
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

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Клуб привилегий, который даёт больше "
        "возможностей каждый день.\n\n"
        "Открывайте партнёров, пользуйтесь "
        "привилегиями и экономьте.",
        reply_markup=reply_markup
    )


# =========================================================
# CALLBACKS
# =========================================================

async def button_callback(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    if query.data == "categories":

        await query.message.reply_text(
            "📂 Категории BIZDE:\n\n"
            "🍽 Рестораны\n"
            "☕ Кафе\n"
            "💆 Красота\n"
            "❤️ Здоровье\n"
            "🎮 Развлечения\n"
            "🛍 Магазины\n"
            "🥊 Спорт"
        )

    elif query.data == "partners":

        conn = get_db()

        try:
            cur = conn.cursor()

            cur.execute("""
                SELECT
                    name,
                    category,
                    discount_percent
                FROM partners
                WHERE active = TRUE
                ORDER BY id
            """)

            partners = cur.fetchall()

        finally:
            conn.close()

        if not partners:
            text = "🏪 Пока нет активных партнёров."
        else:
            lines = ["🏪 Партнёры BIZDE:\n"]

            for partner in partners:
                lines.append(
                    f"• {partner[0]}\n"
                    f"  {partner[1]} · "
                    f"{partner[2]}%"
                )

            text = "\n\n".join(lines)

        await query.message.reply_text(
            text
        )

    elif query.data == "subscription":

        user = get_user(
            query.from_user.id
        )

        if user and user["subscription_active"]:
            status = "🟢 Активна"
        else:
            status = "⚪ Не активна"

        await query.message.reply_text(
            f"🎟 Ваша подписка\n\n"
            f"Статус: {status}\n\n"
            f"Member ID: "
            f"{user['member_code'] if user else '—'}"
        )


# =========================================================
# MAIN
# =========================================================

def main():

    print("Starting BIZDE.KZ...")

    init_database()

    # HTTP server для Render
    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()

    # Telegram application
    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_callback
        )
    )

    print("BIZDE bot started.")

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
