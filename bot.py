import os
import json
import hmac
import hashlib
import secrets
import threading
import urllib.parse

from datetime import datetime, timedelta, timezone
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


# =========================================================
# НАСТРОЙКИ
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

OWNER_ID = 1882252883

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"
ALLOWED_ORIGIN = "https://aydin200169.github.io"

PORT = int(os.environ.get("PORT", "10000"))

QR_LIFETIME_SECONDS = 60


# =========================================================
# DATABASE
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not configured")

    return psycopg2.connect(
        DATABASE_URL,
        sslmode="require"
    )


def init_db():

    with db() as conn:
        with conn.cursor() as cur:

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
                    registered_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW()
                )
            """)

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
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            # Добавляем telegram_id, если старой таблице его не хватает
            cur.execute("""
                ALTER TABLE partners
                ADD COLUMN IF NOT EXISTS telegram_id BIGINT
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS
                partners_telegram_id_unique
                ON partners(telegram_id)
                WHERE telegram_id IS NOT NULL
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    partner_id INTEGER,
                    partner_name TEXT,
                    receipt_amount NUMERIC(12,2) NOT NULL,
                    discount_percent NUMERIC(5,2) DEFAULT 0,
                    savings NUMERIC(12,2) DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS admins (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    added_by BIGINT,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS qr_tokens (
                    id SERIAL PRIMARY KEY,
                    token TEXT UNIQUE NOT NULL,
                    user_telegram_id BIGINT NOT NULL,
                    partner_id INTEGER,
                    created_at TIMESTAMP NOT NULL,
                    expires_at TIMESTAMP NOT NULL,
                    used BOOLEAN DEFAULT FALSE,
                    used_at TIMESTAMP,
                    used_by_partner BIGINT
                )
            """)

            # OWNER автоматически является админом
            cur.execute("""
                INSERT INTO admins (
                    telegram_id,
                    added_by
                )
                VALUES (%s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (OWNER_ID, OWNER_ID))

            # =================================================
            # DEMO PARTNERS
            # =================================================

            cur.execute("""
                SELECT COUNT(*)
                FROM partners
            """)

            count = cur.fetchone()[0]

            if count == 0:

                demo_partners = [
                    (
                        "Partner 1",
                        "restaurant",
                        "Привилегии BIZDE.KZ",
                        20,
                        "Скидка для участников BIZDE.KZ",
                    ),
                    (
                        "VERO Café",
                        "cafe",
                        "Кофе, десерты и приятная атмосфера",
                        20,
                        "Скидка действует для участников клуба",
                    ),
                    (
                        "FITROOM",
                        "sport",
                        "Спорт и тренировки",
                        15,
                        "Специальные условия для участников BIZDE.KZ",
                    ),
                    (
                        "Beauty Room",
                        "beauty",
                        "Красота и уход",
                        15,
                        "Привилегии клуба BIZDE.KZ",
                    ),
                ]

                for partner in demo_partners:

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


# =========================================================
# HELPERS
# =========================================================

def generate_member_code():

    while True:

        code = "BZ-" + secrets.token_hex(4).upper()

        with db() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    "SELECT 1 FROM users WHERE member_code = %s",
                    (code,)
                )

                if not cur.fetchone():
                    return code


def get_user(telegram_id):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM users
                WHERE telegram_id = %s
            """, (telegram_id,))

            return cur.fetchone()


def get_user_by_member_code(code):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM users
                WHERE member_code = %s
            """, (code,))

            return cur.fetchone()


def upsert_user(
    telegram_id,
    name=None,
    username=None,
    phone=None,
    language=None,
    terms_accepted=None
):

    existing = get_user(telegram_id)

    if not existing:
        member_code = generate_member_code()

        with db() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO users (
                        telegram_id,
                        name,
                        username,
                        phone,
                        language,
                        member_code,
                        terms_accepted
                    )
                    VALUES (%s,%s,%s,%s,%s,%s,%s)
                """, (
                    telegram_id,
                    name,
                    username,
                    phone,
                    language or "ru",
                    member_code,
                    bool(terms_accepted)
                    if terms_accepted is not None
                    else False
                ))

            conn.commit()

    else:

        fields = []
        values = []

        if name is not None:
            fields.append("name = %s")
            values.append(name)

        if username is not None:
            fields.append("username = %s")
            values.append(username)

        if phone is not None:
            fields.append("phone = %s")
            values.append(phone)

        if language is not None:
            fields.append("language = %s")
            values.append(language)

        if terms_accepted is not None:
            fields.append("terms_accepted = %s")
            values.append(terms_accepted)

        fields.append("updated_at = NOW()")

        if fields:

            values.append(telegram_id)

            with db() as conn:
                with conn.cursor() as cur:

                    cur.execute(
                        f"""
                        UPDATE users
                        SET {", ".join(fields)}
                        WHERE telegram_id = %s
                        """,
                        values
                    )

                conn.commit()

    return get_user(telegram_id)


# =========================================================
# ADMINS
# =========================================================

def is_owner(telegram_id):

    return int(telegram_id) == OWNER_ID


def is_admin(telegram_id):

    if is_owner(telegram_id):
        return True

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT 1
                FROM admins
                WHERE telegram_id = %s
            """, (telegram_id,))

            return cur.fetchone() is not None


def add_admin(telegram_id, added_by):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO admins (
                    telegram_id,
                    added_by
                )
                VALUES (%s,%s)
                ON CONFLICT (telegram_id)
                DO NOTHING
            """, (
                telegram_id,
                added_by
            ))

        conn.commit()


def remove_admin(telegram_id):

    if int(telegram_id) == OWNER_ID:
        return False

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM admins
                WHERE telegram_id = %s
            """, (telegram_id,))

            deleted = cur.rowcount > 0

        conn.commit()

    return deleted


def get_admins():

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM admins
                ORDER BY created_at ASC
            """)

            return cur.fetchall()


# =========================================================
# PARTNERS
# =========================================================

def get_partners():

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE active = TRUE
                ORDER BY id DESC
            """)

            return cur.fetchall()


def get_all_partners_admin():

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                ORDER BY id DESC
            """)

            return cur.fetchall()


def get_partner_by_id(partner_id):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE id = %s
            """, (partner_id,))

            return cur.fetchone()


def get_partner_by_telegram_id(telegram_id):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE telegram_id = %s
                LIMIT 1
            """, (telegram_id,))

            return cur.fetchone()


def create_partner(data):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                INSERT INTO partners (
                    name,
                    category,
                    description,
                    discount_percent,
                    conditions,
                    photo_url,
                    active
                )
                VALUES (%s,%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (
                data.get("name", "Partner"),
                data.get("category", ""),
                data.get("description", ""),
                float(data.get("discount_percent", 0) or 0),
                data.get("conditions", ""),
                data.get("photo_url", ""),
                bool(data.get("active", True))
            ))

            result = cur.fetchone()

        conn.commit()

    return result


def update_partner(partner_id, data):

    allowed = [
        "name",
        "category",
        "description",
        "discount_percent",
        "conditions",
        "photo_url",
        "active",
    ]

    fields = []
    values = []

    for key in allowed:

        if key in data:

            fields.append(f"{key} = %s")

            value = data[key]

            if key == "discount_percent":
                value = float(value or 0)

            if key == "active":
                value = bool(value)

            values.append(value)

    if not fields:
        return get_partner_by_id(partner_id)

    values.append(partner_id)

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute(
                f"""
                UPDATE partners
                SET {", ".join(fields)}
                WHERE id = %s
                RETURNING *
                """,
                values
            )

            result = cur.fetchone()

        conn.commit()

    return result


def toggle_partner(partner_id, active):

    return update_partner(
        partner_id,
        {"active": active}
    )


def delete_partner(partner_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                DELETE FROM partners
                WHERE id = %s
            """, (partner_id,))

            deleted = cur.rowcount > 0

        conn.commit()

    return deleted


def assign_partner(partner_id, telegram_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE partners
                SET telegram_id = NULL
                WHERE telegram_id = %s
            """, (telegram_id,))

            cur.execute("""
                UPDATE partners
                SET telegram_id = %s
                WHERE id = %s
            """, (
                telegram_id,
                partner_id
            ))

        conn.commit()


def unassign_partner(partner_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE partners
                SET telegram_id = NULL
                WHERE id = %s
            """, (partner_id,))

        conn.commit()


def get_role(telegram_id):

    if is_owner(telegram_id):
        return "owner"

    if is_admin(telegram_id):
        return "admin"

    partner = get_partner_by_telegram_id(telegram_id)

    if partner:
        return "partner"

    return "user"


# =========================================================
# USERS ADMIN
# =========================================================

def get_all_users_admin():

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM users
                ORDER BY registered_at DESC
            """)

            return cur.fetchall()


def update_language(telegram_id, language):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET language = %s,
                    updated_at = NOW()
                WHERE telegram_id = %s
            """, (
                language,
                telegram_id
            ))

        conn.commit()


def set_subscription(telegram_id, active):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET subscription_active = %s,
                    updated_at = NOW()
                WHERE telegram_id = %s
            """, (
                bool(active),
                telegram_id
            ))

        conn.commit()

    return get_user(telegram_id)


# =========================================================
# TRANSACTIONS
# =========================================================

def get_history(telegram_id):

    with db() as conn:
        with conn.cursor(cursor_factory=RealDictCursor) as cur:

            cur.execute("""
                SELECT *
                FROM transactions
                WHERE telegram_id = %s
                ORDER BY created_at DESC
            """, (telegram_id,))

            return cur.fetchall()


def create_transaction(
    telegram_id,
    partner_id,
    partner_name,
    receipt_amount,
    discount_percent,
    used_by_partner=None
):

    receipt_amount = float(receipt_amount)
    discount_percent = float(discount_percent)

    savings = round(
        receipt_amount * discount_percent / 100,
        2
    )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO transactions (
                    telegram_id,
                    partner_id,
                    partner_name,
                    receipt_amount,
                    discount_percent,
                    savings
                )
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                telegram_id,
                partner_id,
                partner_name,
                receipt_amount,
                discount_percent,
                savings
            ))

            cur.execute("""
                UPDATE users
                SET total_savings =
                    COALESCE(total_savings, 0) + %s,
                    updated_at = NOW()
                WHERE telegram_id = %s
            """, (
                savings,
                telegram_id
            ))

        conn.commit()

    return savings


# =========================================================
# QR
# =========================================================

def create_qr_token(user_telegram_id, partner_id=None):

    token = secrets.token_urlsafe(32)

    # В БД оставляем обычный UTC datetime.
    # В frontend возвращаем Unix timestamp в миллисекундах.
    now = datetime.utcnow()

    expires_at = now + timedelta(
        seconds=QR_LIFETIME_SECONDS
    )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO qr_tokens (
                    token,
                    user_telegram_id,
                    partner_id,
                    created_at,
                    expires_at,
                    used
                )
                VALUES (%s,%s,%s,%s,%s,FALSE)
            """, (
                token,
                user_telegram_id,
                partner_id,
                now,
                expires_at
            ))

        conn.commit()

    expires_at_ms = int(
        expires_at.replace(
            tzinfo=timezone.utc
        ).timestamp() * 1000
    )

    return {
        "token": token,
        "expires_at": expires_at_ms,
        "expires_in": QR_LIFETIME_SECONDS
    }


def verify_qr_token(token, partner_id=None):

    if not token:
        return {
            "success": False,
            "error": "QR token отсутствует"
        }

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT
                    q.*,
                    u.name,
                    u.username,
                    u.phone,
                    u.member_code,
                    u.subscription_active,
                    p.name AS partner_name,
                    p.discount_percent,
                    p.conditions
                FROM qr_tokens q
                LEFT JOIN users u
                    ON u.telegram_id = q.user_telegram_id
                LEFT JOIN partners p
                    ON p.id = q.partner_id
                WHERE q.token = %s
            """, (token,))

            qr = cur.fetchone()

    if not qr:
        return {
            "success": False,
            "error": "QR-код не найден"
        }

    if qr["used"]:
        return {
            "success": False,
            "error": "QR-код уже использован"
        }

    expires_at = qr["expires_at"]

    if isinstance(expires_at, datetime):

        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(
                tzinfo=timezone.utc
            )

        if datetime.now(timezone.utc) >= expires_at:

            return {
                "success": False,
                "error": "QR-код истёк"
            }

    if partner_id is not None:
        if qr["partner_id"] is not None:
            if int(qr["partner_id"]) != int(partner_id):
                return {
                    "success": False,
                    "error": "QR-код предназначен для другого партнёра"
                }

    return {
        "success": True,
        "qr": dict(qr)
    }


def confirm_qr_transaction(
    token,
    partner_telegram_id,
    receipt_amount
):

    with db() as conn:

        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT
                    q.*,
                    u.subscription_active,
                    p.name AS partner_name,
                    p.discount_percent,
                    p.conditions
                FROM qr_tokens q
                LEFT JOIN users u
                    ON u.telegram_id = q.user_telegram_id
                LEFT JOIN partners p
                    ON p.id = q.partner_id
                WHERE q.token = %s
                FOR UPDATE
            """, (token,))

            qr = cur.fetchone()

            if not qr:
                return {
                    "success": False,
                    "error": "QR-код не найден"
                }

            if qr["used"]:
                return {
                    "success": False,
                    "error": "QR-код уже использован"
                }

            expires_at = qr["expires_at"]

            if isinstance(expires_at, datetime):

                if expires_at.tzinfo is None:
                    expires_at = expires_at.replace(
                        tzinfo=timezone.utc
                    )

                if datetime.now(timezone.utc) >= expires_at:
                    return {
                        "success": False,
                        "error": "QR-код истёк"
                    }

            partner = get_partner_by_telegram_id(
                partner_telegram_id
            )

            if not partner:
                return {
                    "success": False,
                    "error": "Партнёр не найден"
                }

            if not qr["subscription_active"]:
                return {
                    "success": False,
                    "error": "Подписка клиента не активна"
                }

            discount = float(
                partner["discount_percent"] or 0
            )

            amount = float(receipt_amount)

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
                VALUES (%s,%s,%s,%s,%s,%s)
            """, (
                qr["user_telegram_id"],
                partner["id"],
                partner["name"],
                amount,
                discount,
                savings
            ))

            cur.execute("""
                UPDATE users
                SET total_savings =
                    COALESCE(total_savings, 0) + %s,
                    updated_at = NOW()
                WHERE telegram_id = %s
            """, (
                savings,
                qr["user_telegram_id"]
            ))

            cur.execute("""
                UPDATE qr_tokens
                SET used = TRUE,
                    used_at = NOW(),
                    used_by_partner = %s
                WHERE token = %s
            """, (
                partner_telegram_id,
                token
            ))

        conn.commit()

    return {
        "success": True,
        "receipt_amount": amount,
        "discount_percent": discount,
        "savings": savings
    }


# =========================================================
# LEGACY PARTNER OPERATION
# =========================================================

def partner_use_offer(
    user_telegram_id,
    partner_id,
    receipt_amount
):

    partner = get_partner_by_id(partner_id)

    if not partner:
        return {
            "success": False,
            "error": "Партнёр не найден"
        }

    user = get_user(user_telegram_id)

    if not user:
        return {
            "success": False,
            "error": "Пользователь не найден"
        }

    if not user["subscription_active"]:
        return {
            "success": False,
            "error": "Подписка не активна"
        }

    savings = create_transaction(
        user_telegram_id,
        partner_id,
        partner["name"],
        receipt_amount,
        partner["discount_percent"]
    )

    return {
        "success": True,
        "savings": savings,
        "discount_percent": float(
            partner["discount_percent"]
        )
    }


# =========================================================
# MEMBER VERIFICATION
# =========================================================

def verify_member(member_code):

    user = get_user_by_member_code(member_code)

    if not user:
        return {
            "success": False,
            "error": "Участник не найден"
        }

    return {
        "success": True,
        "user": dict(user)
    }


# =========================================================
# TELEGRAM WEB APP AUTH
# =========================================================

def validate_init_data(init_data):

    if not init_data:
        return None

    try:

        parsed = urllib.parse.parse_qs(
            init_data,
            keep_blank_values=True
        )

        received_hash = parsed.get(
            "hash",
            [None]
        )[0]

        if not received_hash:
            return None

        data_pairs = []

        for key in parsed:

            if key == "hash":
                continue

            value = parsed[key][0]

            data_pairs.append(
                f"{key}={value}"
            )

        data_check_string = "\n".join(
            sorted(data_pairs)
        )

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

        auth_date = parsed.get(
            "auth_date",
            [None]
        )[0]

        if auth_date:

            if (
                datetime.now(timezone.utc).timestamp()
                - int(auth_date)
                > 86400
            ):
                return None

        user_data = parsed.get(
            "user",
            [None]
        )[0]

        if not user_data:
            return None

        return json.loads(user_data)

    except Exception:
        return None


def get_request_user(headers, body=None):

    init_data = headers.get(
        "X-Telegram-Init-Data"
    )

    if not init_data and body:
        init_data = body.get("initData")

    return validate_init_data(
        init_data
    )


# =========================================================
# JSON RESPONSE
# =========================================================

def json_response(
    handler,
    data,
    status=200
):

    payload = json.dumps(
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
        "Content-Type, X-Telegram-Init-Data"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
    )

    handler.send_header(
        "Content-Length",
        str(len(payload))
    )

    handler.end_headers()

    handler.wfile.write(payload)


# =========================================================
# HTTP SERVER
# =========================================================

class RequestHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
        return

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            ALLOWED_ORIGIN
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Telegram-Init-Data"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.end_headers()

    def read_json(self):

        try:

            length = int(
                self.headers.get(
                    "Content-Length",
                    0
                )
            )

            raw = self.rfile.read(length)

            if not raw:
                return {}

            return json.loads(
                raw.decode("utf-8")
            )

        except Exception:
            return {}

    def do_GET():

        pass


# =========================================================
# REPLACE GET METHOD
# =========================================================

def http_get(self):

    try:

        parsed = urlparse(self.path)

        path = parsed.path

        query = parse_qs(
            parsed.query
        )

        # -----------------------------------------------
        # HEALTH CHECK
        # -----------------------------------------------

        if path == "/":

            return json_response(
                self,
                {
                    "success": True,
                    "service": "BIZDE.KZ API",
                    "status": "online"
                }
            )

        # -----------------------------------------------
        # USER
        # -----------------------------------------------

        if path == "/api/user":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            telegram_id = int(
                user_data["id"]
            )

            user = get_user(
                telegram_id
            )

            return json_response(
                self,
                {
                    "success": True,
                    "user": dict(user)
                    if user else None
                }
            )

        # -----------------------------------------------
        # ROLE
        # -----------------------------------------------

        if path == "/api/role":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            telegram_id = int(
                user_data["id"]
            )

            role = get_role(
                telegram_id
            )

            return json_response(
                self,
                {
                    "success": True,
                    "role": role
                }
            )

        # -----------------------------------------------
        # PARTNERS
        # -----------------------------------------------

        if path == "/api/partners":

            partners = get_partners()

            return json_response(
                self,
                {
                    "success": True,
                    "partners": [
                        dict(x)
                        for x in partners
                    ]
                }
            )

        # -----------------------------------------------
        # HISTORY
        # -----------------------------------------------

        if path == "/api/history":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            history = get_history(
                int(user_data["id"])
            )

            return json_response(
                self,
                {
                    "success": True,
                    "history": [
                        dict(x)
                        for x in history
                    ]
                }
            )

        # -----------------------------------------------
        # PARTNER ME
        # -----------------------------------------------

        if path == "/api/partner/me":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            telegram_id = int(
                user_data["id"]
            )

            partner = get_partner_by_telegram_id(
                telegram_id
            )

            if not partner:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Партнёрский аккаунт не найден"
                    },
                    403
                )

            return json_response(
                self,
                {
                    "success": True,
                    "partner": dict(partner)
                }
            )

        # -----------------------------------------------
        # ADMIN PARTNERS
        # -----------------------------------------------

        if path == "/api/admin/partners":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            if not is_admin(
                int(user_data["id"])
            ):

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Forbidden"
                    },
                    403
                )

            partners = get_all_partners_admin()

            return json_response(
                self,
                {
                    "success": True,
                    "partners": [
                        dict(x)
                        for x in partners
                    ]
                }
            )

        # -----------------------------------------------
        # ADMIN USERS
        # -----------------------------------------------

        if path == "/api/admin/users":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            if not is_admin(
                int(user_data["id"])
            ):

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Forbidden"
                    },
                    403
                )

            users = get_all_users_admin()

            return json_response(
                self,
                {
                    "success": True,
                    "users": [
                        dict(x)
                        for x in users
                    ]
                }
            )

        # -----------------------------------------------
        # ADMIN ADMINS
        # -----------------------------------------------

        if path == "/api/admin/admins":

            user_data = get_request_user(
                self.headers
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            if not is_admin(
                int(user_data["id"])
            ):

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Forbidden"
                    },
                    403
                )

            admins = get_admins()

            return json_response(
                self,
                {
                    "success": True,
                    "admins": [
                        dict(x)
                        for x in admins
                    ]
                }
            )

        return json_response(
            self,
            {
                "success": False,
                "error": "Not found"
            },
            404
        )

    except Exception as e:

        print(
            "GET ERROR:",
            repr(e)
        )

        return json_response(
            self,
            {
                "success": False,
                "error": str(e)
            },
            500
        )


RequestHandler.do_GET = http_get


# =========================================================
# HTTP POST
# =========================================================

def http_post(self):

    try:

        parsed = urlparse(
            self.path
        )

        path = parsed.path

        body = self.read_json()

        # -----------------------------------------------
        # AUTH
        # -----------------------------------------------

        if path == "/api/auth":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Telegram authentication failed"
                    },
                    401
                )

            telegram_id = int(
                user_data["id"]
            )

            user = upsert_user(
                telegram_id=telegram_id,
                name=(
                    user_data.get("first_name", "")
                    + " "
                    + user_data.get("last_name", "")
                ).strip(),
                username=user_data.get("username")
            )

            return json_response(
                self,
                {
                    "success": True,
                    "user": dict(user),
                    "role": get_role(telegram_id)
                }
            )

        # -----------------------------------------------
        # LANGUAGE
        # -----------------------------------------------

        if path == "/api/language":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            language = body.get(
                "language",
                "ru"
            )

            update_language(
                int(user_data["id"]),
                language
            )

            return json_response(
                self,
                {
                    "success": True
                }
            )

        # -----------------------------------------------
        # CREATE QR
        # -----------------------------------------------

        if path == "/api/qr/create":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            telegram_id = int(
                user_data["id"]
            )

            user = get_user(
                telegram_id
            )

            if not user:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Пользователь не найден"
                    },
                    404
                )

            partner_id = body.get(
                "partner_id"
            )

            if partner_id:
                try:
                    partner_id = int(
                        partner_id
                    )
                except Exception:
                    partner_id = None

            qr = create_qr_token(
                telegram_id,
                partner_id
            )

            return json_response(
                self,
                {
                    "success": True,
                    "qr": qr
                }
            )

        # -----------------------------------------------
        # VERIFY QR
        # -----------------------------------------------

        if path == "/api/qr/verify":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            partner = get_partner_by_telegram_id(
                int(user_data["id"])
            )

            if not partner:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Партнёр не найден"
                    },
                    403
                )

            result = verify_qr_token(
                body.get("token"),
                partner["id"]
            )

            return json_response(
                self,
                result
            )

        # -----------------------------------------------
        # CONFIRM QR
        # -----------------------------------------------

        if path == "/api/qr/confirm":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            partner_id = int(
                user_data["id"]
            )

            result = confirm_qr_transaction(
                body.get("token"),
                partner_id,
                body.get("receipt_amount", 0)
            )

            return json_response(
                self,
                result
            )

        # -----------------------------------------------
        # VERIFY MEMBER
        # -----------------------------------------------

        if path == "/api/verify-member":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            result = verify_member(
                body.get("member_code")
            )

            return json_response(
                self,
                result
            )

        # -----------------------------------------------
        # USE OFFER
        # -----------------------------------------------

        if path == "/api/use-offer":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            result = partner_use_offer(
                int(user_data["id"]),
                int(body.get("partner_id")),
                float(body.get("receipt_amount", 0))
            )

            return json_response(
                self,
                result
            )

        # -----------------------------------------------
        # PARTNER USE OFFER
        # -----------------------------------------------

        if path == "/api/partner-use-offer":

            user_data = get_request_user(
                self.headers,
                body
            )

            if not user_data:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Unauthorized"
                    },
                    401
                )

            partner = get_partner_by_telegram_id(
                int(user_data["id"])
            )

            if not partner:

                return json_response(
                    self,
                    {
                        "success": False,
                        "error": "Партнёр не найден"
                    },
                    403
                )

            result = partner_use_offer(
                int(body.get("telegram_id")),
                partner["id"],
                float(body.get("receipt_amount", 0))
            )

            return json_response(
                self,
                result
            )

        # =================================================
        # ADMIN AUTH CHECK
        # =================================================

        user_data = get_request_user(
            self.headers,
            body
        )

        if not user_data:

            return json_response(
                self,
                {
                    "success": False,
                    "error": "Unauthorized"
                },
                401
            )

        admin_id = int(
            user_data["id"]
        )

        if not is_admin(admin_id):

            return json_response(
                self,
                {
                    "success": False,
                    "error": "Forbidden"
                },
                403
            )

        # -----------------------------------------------
        # CREATE PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/create":

            partner = create_partner(
                body
            )

            return json_response(
                self,
                {
                    "success": True,
                    "partner": dict(partner)
                }
            )

        # -----------------------------------------------
        # UPDATE PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/update":

            partner_id = int(
                body.get("id")
            )

            partner = update_partner(
                partner_id,
                body
            )

            return json_response(
                self,
                {
                    "success": True,
                    "partner": dict(partner)
                    if partner else None
                }
            )

        # -----------------------------------------------
        # DELETE PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/delete":

            partner_id = int(
                body.get("id")
            )

            deleted = delete_partner(
                partner_id
            )

            return json_response(
                self,
                {
                    "success": deleted
                }
            )

        # -----------------------------------------------
        # TOGGLE PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/toggle":

            partner_id = int(
                body.get("id")
            )

            active = bool(
                body.get("active")
            )

            partner = toggle_partner(
                partner_id,
                active
            )

            return json_response(
                self,
                {
                    "success": True,
                    "partner": dict(partner)
                }
            )

        # -----------------------------------------------
        # ASSIGN PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/assign":

            partner_id = int(
                body.get("partner_id")
            )

            telegram_id = int(
                body.get("telegram_id")
            )

            assign_partner(
                partner_id,
                telegram_id
            )

            return json_response(
                self,
                {
                    "success": True
                }
            )

        # -----------------------------------------------
        # UNASSIGN PARTNER
        # -----------------------------------------------

        if path == "/api/admin/partners/unassign":

            partner_id = int(
                body.get("partner_id")
            )

            unassign_partner(
                partner_id
            )

            return json_response(
                self,
                {
                    "success": True
                }
            )

        # -----------------------------------------------
        # ACTIVATE SUBSCRIPTION
        # -----------------------------------------------

        if path == "/api/admin/subscription":

            telegram_id = int(
                body.get("telegram_id")
            )

            active = bool(
                body.get("active")
            )

            user = set_subscription(
                telegram_id,
                active
            )

            return json_response(
                self,
                {
                    "success": True,
                    "user": dict(user)
                    if user else None
                }
            )

        # -----------------------------------------------
        # ADD ADMIN
        # -----------------------------------------------

        if path == "/api/admin/add":

            telegram_id = int(
                body.get("telegram_id")
            )

            add_admin(
                telegram_id,
                admin_id
            )

            return json_response(
                self,
                {
                    "success": True
                }
            )

        # -----------------------------------------------
        # REMOVE ADMIN
        # -----------------------------------------------

        if path == "/api/admin/remove":

            telegram_id = int(
                body.get("telegram_id")
            )

            removed = remove_admin(
                telegram_id
            )

            return json_response(
                self,
                {
                    "success": removed
                }
            )

        return json_response(
            self,
            {
                "success": False,
                "error": "Not found"
            },
            404
        )

    except Exception as e:

        print(
            "POST ERROR:",
            repr(e)
        )

        return json_response(
            self,
            {
                "success": False,
                "error": str(e)
            },
            500
        )


RequestHandler.do_POST = http_post


# =========================================================
# TELEGRAM BOT
# =========================================================

async def start(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    telegram_id = user.id

    upsert_user(
        telegram_id=telegram_id,
        name=(
            user.first_name or ""
        ) + (
            " " + user.last_name
            if user.last_name
            else ""
        ),
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

    # Партнёрский кабинет
    partner = get_partner_by_telegram_id(
        telegram_id
    )

    if partner:

        keyboard.append([
            InlineKeyboardButton(
                "🤝 Кабинет партнёра",
                web_app=WebAppInfo(
                    url=WEB_APP_URL.rstrip("/")
                    + "/partner.html"
                )
            )
        ])

    # Админка
    if is_admin(telegram_id):

        keyboard.append([
            InlineKeyboardButton(
                "⚙️ Админ-панель",
                web_app=WebAppInfo(
                    url=WEB_APP_URL.rstrip("/")
                    + "/admin.html"
                )
            )
        ])

    reply_markup = InlineKeyboardMarkup(
        keyboard
    )

    await update.message.reply_text(
        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Экономь вместе с нашими партнёрами.\n"
        "Получай привилегии, специальные условия "
        "и пользуйся преимуществами клуба.",
        reply_markup=reply_markup
    )


# =========================================================
# /PARTNER
# =========================================================

async def partner_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    partner = get_partner_by_telegram_id(
        user.id
    )

    if not partner:

        await update.message.reply_text(
            "❌ Ваш Telegram-аккаунт "
            "не привязан к партнёру."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "🤝 Открыть кабинет партнёра",
                web_app=WebAppInfo(
                    url=WEB_APP_URL.rstrip("/")
                    + "/partner.html"
                )
            )
        ]
    ]

    await update.message.reply_text(
        f"🏪 {partner['name']}\n\n"
        f"Привилегия: "
        f"{partner['discount_percent']}%\n\n"
        "Через кабинет вы можете проверять "
        "QR-коды участников и оформлять "
        "использование привилегий.",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# /ADMIN
# =========================================================

async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    keyboard = [
        [
            InlineKeyboardButton(
                "⚙️ Открыть админ-панель",
                web_app=WebAppInfo(
                    url=WEB_APP_URL.rstrip("/")
                    + "/admin.html"
                )
            )
        ]
    ]

    await update.message.reply_text(
        "⚙️ Админ-панель BIZDE.KZ",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# /ADDADMIN
# =========================================================

async def addadmin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_owner(user.id):

        await update.message.reply_text(
            "❌ Только владелец может добавлять администраторов."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/addadmin TELEGRAM_ID"
        )

        return

    try:

        telegram_id = int(
            context.args[0]
        )

        add_admin(
            telegram_id,
            user.id
        )

        await update.message.reply_text(
            "✅ Администратор добавлен."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# /REMOVEADMIN
# =========================================================

async def removeadmin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_owner(user.id):

        await update.message.reply_text(
            "❌ Только владелец может удалять администраторов."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/removeadmin TELEGRAM_ID"
        )

        return

    try:

        telegram_id = int(
            context.args[0]
        )

        if telegram_id == OWNER_ID:

            await update.message.reply_text(
                "❌ Нельзя удалить владельца."
            )

            return

        removed = remove_admin(
            telegram_id
        )

        await update.message.reply_text(
            "✅ Администратор удалён."
            if removed
            else "⚠️ Администратор не найден."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# /ADMINS
# =========================================================

async def admins_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    admins = get_admins()

    text = "👥 Администраторы:\n\n"

    for admin in admins:

        text += (
            f"• {admin['telegram_id']}\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# /SETPARTNER
# =========================================================

async def setpartner_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    if len(context.args) < 2:

        await update.message.reply_text(
            "Использование:\n"
            "/setpartner PARTNER_ID TELEGRAM_ID"
        )

        return

    try:

        partner_id = int(
            context.args[0]
        )

        telegram_id = int(
            context.args[1]
        )

        partner = get_partner_by_id(
            partner_id
        )

        if not partner:

            await update.message.reply_text(
                "❌ Партнёр не найден."
            )

            return

        assign_partner(
            partner_id,
            telegram_id
        )

        await update.message.reply_text(
            "✅ Партнёр успешно привязан."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# /UNSETPARTNER
# =========================================================

async def unsetpartner_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/unsetpartner PARTNER_ID"
        )

        return

    try:

        partner_id = int(
            context.args[0]
        )

        unassign_partner(
            partner_id
        )

        await update.message.reply_text(
            "✅ Партнёр отвязан."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# /ACTIVATE
# =========================================================

async def activate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/activate TELEGRAM_ID"
        )

        return

    try:

        telegram_id = int(
            context.args[0]
        )

        set_subscription(
            telegram_id,
            True
        )

        await update.message.reply_text(
            "✅ Подписка активирована."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# /DEACTIVATE
# =========================================================

async def deactivate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "❌ Доступ запрещён."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n"
            "/deactivate TELEGRAM_ID"
        )

        return

    try:

        telegram_id = int(
            context.args[0]
        )

        set_subscription(
            telegram_id,
            False
        )

        await update.message.reply_text(
            "✅ Подписка деактивирована."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ Ошибка: {e}"
        )


# =========================================================
# CALLBACKS
# =========================================================

async def callback_handler(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    query = update.callback_query

    await query.answer()

    user = query.from_user

    if query.data == "categories":

        await query.message.reply_text(
            "📂 Категории\n\n"
            "🍽 Рестораны\n"
            "☕ Кафе\n"
            "🏋️ Спорт\n"
            "💄 Красота\n"
            "🛍 Магазины\n"
            "🎯 Другое"
        )

    elif query.data == "partners":

        partners = get_partners()

        if not partners:

            await query.message.reply_text(
                "Пока нет партнёров."
            )

            return

        text = "🏪 Партнёры BIZDE.KZ\n\n"

        for partner in partners:

            text += (
                f"• {partner['name']}\n"
                f"  Привилегия: "
                f"{partner['discount_percent']}%\n\n"
            )

        await query.message.reply_text(
            text
        )

    elif query.data == "subscription":

        user_data = get_user(
            user.id
        )

        if not user_data:

            await query.message.reply_text(
                "Пользователь не найден."
            )

            return

        status = (
            "🟢 Активна"
            if user_data["subscription_active"]
            else
            "🔴 Не активна"
        )

        await query.message.reply_text(
            f"🎟 Ваша подписка\n\n"
            f"Статус: {status}\n"
            f"Код участника: "
            f"{user_data['member_code']}\n"
            f"Всего сэкономлено: "
            f"{user_data['total_savings']} ₸"
        )


# =========================================================
# HTTP SERVER
# =========================================================

def run_http_server():

    server = HTTPServer(
        ("0.0.0.0", PORT),
        RequestHandler
    )

    print(
        f"HTTP server started on port {PORT}"
    )

    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

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

    init_db()

    print("Database ready.")

    http_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    http_thread.start()

    print(
        "Starting Telegram bot..."
    )

    application = (
        Application
        .builder()
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
        CommandHandler(
            "admin",
            admin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "partner",
            partner_command
        )
    )

    application.add_handler(
        CommandHandler(
            "addadmin",
            addadmin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "removeadmin",
            removeadmin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "admins",
            admins_command
        )
    )

    application.add_handler(
        CommandHandler(
            "setpartner",
            setpartner_command
        )
    )

    application.add_handler(
        CommandHandler(
            "unsetpartner",
            unsetpartner_command
        )
    )

    application.add_handler(
        CommandHandler(
            "activate",
            activate_command
        )
    )

    application.add_handler(
        CommandHandler(
            "deactivate",
            deactivate_command
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            callback_handler
        )
    )

    print(
        "BIZDE.KZ bot is running."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
