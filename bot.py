import os
import json
import hmac
import hashlib
import secrets
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse, parse_qs

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


# ============================================================
# SETTINGS
# ============================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

# Telegram ID администратора
ADMIN_ID = int(os.environ.get("ADMIN_ID", "0"))

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"
PORT = int(os.environ.get("PORT", "10000"))

ALLOWED_ORIGIN = "https://aydin200169.github.io"


# ============================================================
# DATABASE CONNECTION
# ============================================================

def get_connection():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(DATABASE_URL)


# ============================================================
# DATABASE INIT
# ============================================================

def init_database():
    conn = get_connection()
    cur = conn.cursor()

    # USERS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT UNIQUE NOT NULL,
            name TEXT,
            username TEXT,
            phone TEXT,
            language TEXT DEFAULT 'ru',
            member_code TEXT UNIQUE,
            subscription_active BOOLEAN DEFAULT FALSE,
            total_savings NUMERIC(12,2) DEFAULT 0,
            terms_accepted BOOLEAN DEFAULT FALSE,
            registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # USERS MIGRATIONS
    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS username TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS phone TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS language TEXT DEFAULT 'ru'
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS member_code TEXT
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS subscription_active BOOLEAN DEFAULT FALSE
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS total_savings NUMERIC(12,2) DEFAULT 0
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS terms_accepted BOOLEAN DEFAULT FALSE
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """)

    cur.execute("""
        ALTER TABLE users
        ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    """)

    # PARTNERS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS partners (
            id SERIAL PRIMARY KEY,
            name TEXT NOT NULL,
            category TEXT,
            description TEXT,
            discount_percent NUMERIC(5,2) DEFAULT 0,
            conditions TEXT,
            photo_url TEXT,
            active BOOLEAN DEFAULT TRUE,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # TRANSACTIONS
    cur.execute("""
        CREATE TABLE IF NOT EXISTS transactions (
            id SERIAL PRIMARY KEY,
            telegram_id BIGINT NOT NULL,
            partner_id INTEGER,
            partner_name TEXT,
            receipt_amount NUMERIC(12,2),
            discount_percent NUMERIC(5,2),
            savings NUMERIC(12,2),
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)

    # TRANSACTIONS MIGRATIONS
    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS partner_name TEXT
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS receipt_amount NUMERIC(12,2)
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS discount_percent NUMERIC(5,2)
    """)

    cur.execute("""
        ALTER TABLE transactions
        ADD COLUMN IF NOT EXISTS savings NUMERIC(12,2)
    """)

    # SEED PARTNERS
    cur.execute("SELECT COUNT(*) FROM partners")
    count = cur.fetchone()[0]

    if count == 0:
        partners = [
            (
                "Partner 1",
                "restaurant",
                "Ресторан восточной кухни",
                20,
                "Привилегия для участников BIZDE.KZ"
            ),
            (
                "VERO Café",
                "cafe",
                "Кофе и десерты",
                20,
                "Специальные условия для участников клуба"
            ),
            (
                "FITROOM",
                "sport",
                "Спорт и тренировки",
                15,
                "Привилегия для участников BIZDE.KZ"
            ),
            (
                "Beauty Room",
                "beauty",
                "Красота и уход",
                15,
                "Специальные условия для участников"
            )
        ]

        for partner in partners:
            cur.execute("""
                INSERT INTO partners (
                    name,
                    category,
                    description,
                    discount_percent,
                    conditions
                )
                VALUES (%s, %s, %s, %s, %s)
            """, partner)

    conn.commit()

    cur.close()
    conn.close()

    print("Database initialized successfully.")


# ============================================================
# MEMBER CODE
# ============================================================

def generate_member_code():
    while True:
        code = "BIZDE-" + secrets.token_hex(4).upper()

        conn = get_connection()
        cur = conn.cursor()

        cur.execute("""
            SELECT id
            FROM users
            WHERE member_code = %s
        """, (code,))

        exists = cur.fetchone()

        cur.close()
        conn.close()

        if not exists:
            return code


# ============================================================
# GET USER
# ============================================================

def get_user(telegram_id):
    conn = get_connection()
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
            terms_accepted,
            registered_at,
            updated_at
        FROM users
        WHERE telegram_id = %s
    """, (telegram_id,))

    user = cur.fetchone()

    cur.close()
    conn.close()

    if user:
        return dict(user)

    return None


# ============================================================
# CREATE / UPDATE USER
# ============================================================

def upsert_user(
    telegram_id,
    name=None,
    username=None,
    phone=None,
    language=None,
    terms_accepted=None
):
    existing = get_user(telegram_id)

    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if existing:
        member_code = existing.get("member_code")

        if not member_code:
            member_code = generate_member_code()

        cur.execute("""
            UPDATE users
            SET
                name = COALESCE(%s, name),
                username = COALESCE(%s, username),
                phone = COALESCE(%s, phone),
                language = COALESCE(%s, language),
                terms_accepted = COALESCE(%s, terms_accepted),
                member_code = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = %s
            RETURNING *
        """, (
            name,
            username,
            phone,
            language,
            terms_accepted,
            member_code,
            telegram_id
        ))

        user = cur.fetchone()

    else:
        member_code = generate_member_code()

        cur.execute("""
            INSERT INTO users (
                telegram_id,
                name,
                username,
                phone,
                language,
                member_code,
                subscription_active,
                total_savings,
                terms_accepted
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                FALSE,
                0,
                COALESCE(%s, FALSE)
            )
            RETURNING *
        """, (
            telegram_id,
            name,
            username,
            phone,
            language or "ru",
            member_code,
            terms_accepted
        ))

        user = cur.fetchone()

    conn.commit()

    cur.close()
    conn.close()

    return dict(user)


# ============================================================
# TELEGRAM INIT DATA VALIDATION
# ============================================================

def validate_telegram_init_data(init_data):
    if not init_data or not TOKEN:
        return None

    try:
        parsed = urllib.parse.parse_qsl(
            init_data,
            keep_blank_values=True
        )

        data = dict(parsed)

        received_hash = data.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={data[key]}"
            for key in sorted(data.keys())
        )

        secret_key = hmac.new(
            b"WebAppData",
            TOKEN.encode("utf-8"),
            hashlib.sha256
        ).digest()

        calculated_hash = hmac.new(
            secret_key,
            data_check_string.encode("utf-8"),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(
            calculated_hash,
            received_hash
        ):
            return None

        telegram_user = {}

        if "user" in data:
            telegram_user = json.loads(data["user"])

        telegram_id = telegram_user.get("id")

        if not telegram_id:
            return None

        return {
            "telegram_id": int(telegram_id),
            "name": telegram_user.get("first_name", ""),
            "username": telegram_user.get("username", ""),
            "language": telegram_user.get(
                "language_code",
                "ru"
            )
        }

    except Exception as e:
        print("INIT DATA ERROR:", e)
        return None


# ============================================================
# PARTNERS
# ============================================================

def get_partners(category=None):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    if category and category != "all":
        cur.execute("""
            SELECT *
            FROM partners
            WHERE active = TRUE
            AND category = %s
            ORDER BY id
        """, (category,))
    else:
        cur.execute("""
            SELECT *
            FROM partners
            WHERE active = TRUE
            ORDER BY id
        """)

    partners = cur.fetchall()

    cur.close()
    conn.close()

    return [dict(partner) for partner in partners]


# ============================================================
# HISTORY
# ============================================================

def get_history(telegram_id):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            id,
            partner_id,
            partner_name,
            receipt_amount,
            discount_percent,
            savings,
            created_at
        FROM transactions
        WHERE telegram_id = %s
        ORDER BY created_at DESC
        LIMIT 50
    """, (telegram_id,))

    history = cur.fetchall()

    cur.close()
    conn.close()

    return [dict(item) for item in history]


# ============================================================
# LANGUAGE
# ============================================================

def update_language(telegram_id, language):
    conn = get_connection()
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

    cur.close()
    conn.close()


# ============================================================
# VERIFY MEMBER
# ============================================================

def verify_member(member_code):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT
            id,
            telegram_id,
            name,
            username,
            phone,
            member_code,
            subscription_active,
            total_savings
        FROM users
        WHERE member_code = %s
    """, (member_code,))

    user = cur.fetchone()

    cur.close()
    conn.close()

    if user:
        return dict(user)

    return None


# ============================================================
# SUBSCRIPTION
# ============================================================

def set_subscription(telegram_id, active):
    conn = get_connection()
    cur = conn.cursor()

    cur.execute("""
        UPDATE users
        SET
            subscription_active = %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_id = %s
    """, (
        active,
        telegram_id
    ))

    changed = cur.rowcount

    conn.commit()

    cur.close()
    conn.close()

    return changed > 0


# ============================================================
# CREATE TRANSACTION
# ============================================================

def create_transaction(
    telegram_id,
    partner_id,
    receipt_amount
):
    conn = get_connection()
    cur = conn.cursor(cursor_factory=RealDictCursor)

    cur.execute("""
        SELECT *
        FROM users
        WHERE telegram_id = %s
    """, (telegram_id,))

    user = cur.fetchone()

    if not user:
        cur.close()
        conn.close()

        return {
            "ok": False,
            "error": "USER_NOT_FOUND"
        }

    if not user["subscription_active"]:
        cur.close()
        conn.close()

        return {
            "ok": False,
            "error": "SUBSCRIPTION_INACTIVE"
        }

    cur.execute("""
        SELECT *
        FROM partners
        WHERE id = %s
        AND active = TRUE
    """, (partner_id,))

    partner = cur.fetchone()

    if not partner:
        cur.close()
        conn.close()

        return {
            "ok": False,
            "error": "PARTNER_NOT_FOUND"
        }

    try:
        amount = float(receipt_amount)
    except (TypeError, ValueError):
        cur.close()
        conn.close()

        return {
            "ok": False,
            "error": "INVALID_RECEIPT_AMOUNT"
        }

    if amount <= 0:
        cur.close()
        conn.close()

        return {
            "ok": False,
            "error": "INVALID_RECEIPT_AMOUNT"
        }

    discount = float(
        partner["discount_percent"] or 0
    )

    savings = round(
        amount * discount / 100,
        2
    )

    cur.execute("""
        INSERT INTO transactions (
            telegram_id,
            partner_id,
            partner_name,
            receipt_amount,
            discount_percent,
            savings
        )
        VALUES (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s
        )
    """, (
        telegram_id,
        partner["id"],
        partner["name"],
        amount,
        discount,
        savings
    ))

    cur.execute("""
        UPDATE users
        SET
            total_savings =
                COALESCE(total_savings, 0) + %s,
            updated_at = CURRENT_TIMESTAMP
        WHERE telegram_id = %s
    """, (
        savings,
        telegram_id
    ))

    conn.commit()

    cur.close()
    conn.close()

    return {
        "ok": True,
        "partner_name": partner["name"],
        "receipt_amount": amount,
        "discount_percent": discount,
        "savings": savings
    }


# ============================================================
# HTTP SERVER
# ============================================================

class RequestHandler(BaseHTTPRequestHandler):

    def send_json(self, status, data):
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

        self.wfile.write(body)

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

    def read_json(self):
        length = int(
            self.headers.get(
                "Content-Length",
                0
            )
        )

        if length <= 0:
            return {}

        body = self.rfile.read(length)

        return json.loads(
            body.decode("utf-8")
        )

    # ========================================================
    # GET
    # ========================================================

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        query = parse_qs(parsed.query)

        # HEALTH CHECK
        if path == "/":
            self.send_json(
                200,
                {
                    "ok": True,
                    "service": "BIZDE.KZ",
                    "status": "online"
                }
            )
            return

        # USER
        if path == "/api/user":
            try:
                telegram_id = int(
                    query.get(
                        "telegram_id",
                        [0]
                    )[0]
                )

                user = get_user(telegram_id)

                if not user:
                    self.send_json(
                        404,
                        {
                            "ok": False,
                            "error": "USER_NOT_FOUND"
                        }
                    )
                    return

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "user": user
                    }
                )

            except Exception as e:
                print("USER ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        # PARTNERS
        if path == "/api/partners":
            try:
                category = query.get(
                    "category",
                    [None]
                )[0]

                partners = get_partners(category)

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "partners": partners
                    }
                )

            except Exception as e:
                print("PARTNERS ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        # HISTORY
        if path == "/api/history":
            try:
                init_data = query.get(
                    "init_data",
                    [""]
                )[0]

                auth = validate_telegram_init_data(
                    init_data
                )

                if not auth:
                    self.send_json(
                        401,
                        {
                            "ok": False,
                            "error": "INVALID_INIT_DATA"
                        }
                    )
                    return

                history = get_history(
                    auth["telegram_id"]
                )

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "history": history
                    }
                )

            except Exception as e:
                print("HISTORY ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        self.send_json(
            404,
            {
                "ok": False,
                "error": "NOT_FOUND"
            }
        )

    # ========================================================
    # POST
    # ========================================================

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        try:
            data = self.read_json()
        except Exception:
            self.send_json(
                400,
                {
                    "ok": False,
                    "error": "INVALID_JSON"
                }
            )
            return

        # ====================================================
        # AUTH
        # ====================================================

        if path == "/api/auth":
            try:
                init_data = data.get("init_data")

                language = data.get(
                    "language",
                    "ru"
                )

                phone = data.get("phone")

                name_from_form = data.get("name")

                terms_accepted = data.get(
                    "terms_accepted"
                )

                auth = validate_telegram_init_data(
                    init_data
                )

                if not auth:
                    self.send_json(
                        401,
                        {
                            "ok": False,
                            "error": "INVALID_INIT_DATA"
                        }
                    )
                    return

                name = (
                    name_from_form
                    or auth["name"]
                )

                user = upsert_user(
                    telegram_id=auth["telegram_id"],
                    name=name,
                    username=auth["username"],
                    phone=phone,
                    language=language,
                    terms_accepted=terms_accepted
                )

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "user": user
                    }
                )

            except Exception as e:
                print("AUTH ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        # ====================================================
        # LANGUAGE
        # ====================================================

        if path == "/api/language":
            try:
                init_data = data.get(
                    "init_data"
                )

                language = data.get(
                    "language",
                    "ru"
                )

                auth = validate_telegram_init_data(
                    init_data
                )

                if not auth:
                    self.send_json(
                        401,
                        {
                            "ok": False,
                            "error": "INVALID_INIT_DATA"
                        }
                    )
                    return

                update_language(
                    auth["telegram_id"],
                    language
                )

                self.send_json(
                    200,
                    {
                        "ok": True
                    }
                )

            except Exception as e:
                print("LANGUAGE ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        # ====================================================
        # VERIFY MEMBER
        # ====================================================

        if path == "/api/verify-member":
            try:
                member_code = data.get(
                    "member_code"
                )

                if not member_code:
                    self.send_json(
                        400,
                        {
                            "ok": False,
                            "error": "MEMBER_CODE_REQUIRED"
                        }
                    )
                    return

                user = verify_member(
                    member_code
                )

                if not user:
                    self.send_json(
                        404,
                        {
                            "ok": False,
                            "error": "MEMBER_NOT_FOUND"
                        }
                    )
                    return

                self.send_json(
                    200,
                    {
                        "ok": True,
                        "user": user
                    }
                )

            except Exception as e:
                print("VERIFY ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        # ====================================================
        # USE OFFER
        # ====================================================

        if path == "/api/use-offer":
            try:
                init_data = data.get(
                    "init_data"
                )

                partner_id = int(
                    data.get("partner_id")
                )

                receipt_amount = float(
                    data.get("receipt_amount")
                )

                auth = validate_telegram_init_data(
                    init_data
                )

                if not auth:
                    self.send_json(
                        401,
                        {
                            "ok": False,
                            "error": "INVALID_INIT_DATA"
                        }
                    )
                    return

                if receipt_amount <= 0:
                    self.send_json(
                        400,
                        {
                            "ok": False,
                            "error": "INVALID_RECEIPT_AMOUNT"
                        }
                    )
                    return

                result = create_transaction(
                    telegram_id=auth["telegram_id"],
                    partner_id=partner_id,
                    receipt_amount=receipt_amount
                )

                if not result["ok"]:
                    self.send_json(
                        400,
                        result
                    )
                    return

                self.send_json(
                    200,
                    result
                )

            except Exception as e:
                print("USE OFFER ERROR:", e)

                self.send_json(
                    500,
                    {
                        "ok": False,
                        "error": str(e)
                    }
                )

            return

        self.send_json(
            404,
            {
                "ok": False,
                "error": "NOT_FOUND"
            }
        )


# ============================================================
# HTTP SERVER
# ============================================================

def run_http_server():
    server = HTTPServer(
        ("0.0.0.0", PORT),
        RequestHandler
    )

    print(
        f"HTTP server started on port {PORT}"
    )

    server.serve_forever()


# ============================================================
# TELEGRAM BOT
# ============================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    user = update.effective_user

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
        "Экономь вместе с нашими партнёрами "
        "и получай больше привилегий.\n\n"
        "Открой приложение, чтобы начать.",
        reply_markup=reply_markup
    )

    try:
        upsert_user(
            telegram_id=user.id,
            name=user.first_name,
            username=user.username,
            language="ru"
        )
    except Exception as e:
        print("START USER ERROR:", e)


# ============================================================
# ADMIN - ACTIVATE SUBSCRIPTION
# ============================================================

async def activate_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n\n"
            "/activate ID\n\n"
            "Например:\n"
            "/activate 123456789"
        )
        return

    try:
        telegram_id = int(
            context.args[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Telegram ID должен быть числом."
        )
        return

    success = set_subscription(
        telegram_id,
        True
    )

    if success:
        await update.message.reply_text(
            "✅ Подписка активирована."
        )
    else:
        await update.message.reply_text(
            "❌ Пользователь не найден."
        )


# ============================================================
# ADMIN - DEACTIVATE SUBSCRIPTION
# ============================================================

async def deactivate_subscription(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    if update.effective_user.id != ADMIN_ID:
        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    if not context.args:
        await update.message.reply_text(
            "Использование:\n\n"
            "/deactivate ID\n\n"
            "Например:\n"
            "/deactivate 123456789"
        )
        return

    try:
        telegram_id = int(
            context.args[0]
        )
    except ValueError:
        await update.message.reply_text(
            "❌ Telegram ID должен быть числом."
        )
        return

    success = set_subscription(
        telegram_id,
        False
    )

    if success:
        await update.message.reply_text(
            "✅ Подписка отключена."
        )
    else:
        await update.message.reply_text(
            "❌ Пользователь не найден."
        )


# ============================================================
# BUTTON HANDLER
# ============================================================

async def button_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):
    query = update.callback_query

    await query.answer()

    if query.data == "categories":

        await query.message.reply_text(
            "📂 Категории BIZDE.KZ\n\n"
            "🍽 Рестораны\n"
            "☕ Кафе\n"
            "✦ Красота\n"
            "♡ Здоровье\n"
            "★ Развлечения\n"
            "◇ Магазины\n"
            "◉ Спорт"
        )

    elif query.data == "partners":

        partners = get_partners()

        if not partners:
            await query.message.reply_text(
                "Партнёров пока нет."
            )
            return

        text = "🏪 Партнёры BIZDE.KZ\n\n"

        for partner in partners:
            text += (
                f"• {partner['name']} — "
                f"{partner['discount_percent']}%\n"
            )

        await query.message.reply_text(
            text
        )

    elif query.data == "subscription":

        user = get_user(
            update.effective_user.id
        )

        if user and user["subscription_active"]:
            text = (
                "🎟 Ваша подписка активна.\n\n"
                "Вы можете пользоваться "
                "привилегиями BIZDE.KZ."
            )
        else:
            text = (
                "🎟 Подписка пока не активна.\n\n"
                "Оформление подписки будет "
                "добавлено на следующем этапе."
            )

        await query.message.reply_text(
            text
        )


# ============================================================
# MAIN
# ============================================================

def main():

    if not TOKEN:
        raise RuntimeError(
            "BOT_TOKEN is not configured"
        )

    if not DATABASE_URL:
        raise RuntimeError(
            "DATABASE_URL is not configured"
        )

    print("Initializing database...")

    init_database()

    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()

    print("Starting Telegram bot...")

    application = (
        Application.builder()
        .token(TOKEN)
        .build()
    )

    # START
    application.add_handler(
        CommandHandler(
            "start",
            start
        )
    )

    # ADMIN ACTIVATE
    application.add_handler(
        CommandHandler(
            "activate",
            activate_subscription
        )
    )

    # ADMIN DEACTIVATE
    application.add_handler(
        CommandHandler(
            "deactivate",
            deactivate_subscription
        )
    )

    # BUTTONS
    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print("BIZDE.KZ bot started.")

    application.run_polling(
        drop_pending_updates=True
    )


# ============================================================
# START
# ============================================================

if __name__ == "__main__":
    main()
