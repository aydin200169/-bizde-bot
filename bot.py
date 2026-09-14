import os
import json
import hmac
import hashlib
import secrets
import threading
import urllib.parse

from decimal import Decimal
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import urlparse

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
PARTNER_PAGE = WEB_APP_URL.rstrip("/") + "/partner.html"
ADMIN_PAGE = WEB_APP_URL.rstrip("/") + "/admin.html"

ALLOWED_ORIGIN = "https://aydin200169.github.io"

PORT = int(os.environ.get("PORT", "10000"))

QR_LIFETIME_SECONDS = 60


# =========================================================
# DATABASE
# =========================================================

def db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL не настроен")

    return psycopg2.connect(DATABASE_URL)


def init_db():
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    name TEXT DEFAULT '',
                    username TEXT DEFAULT '',
                    phone TEXT DEFAULT '',
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
                    category TEXT DEFAULT '',
                    description TEXT DEFAULT '',
                    discount_percent NUMERIC(5,2) DEFAULT 0,
                    conditions TEXT DEFAULT '',
                    photo_url TEXT DEFAULT '',
                    active BOOLEAN DEFAULT TRUE,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """)

            cur.execute("""
                CREATE TABLE IF NOT EXISTS transactions (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT NOT NULL,
                    partner_id INTEGER,
                    partner_name TEXT DEFAULT '',
                    receipt_amount NUMERIC(12,2) DEFAULT 0,
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

            cur.execute("""
                ALTER TABLE partners
                ADD COLUMN IF NOT EXISTS telegram_id BIGINT
            """)

            cur.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS partners_telegram_id_unique
                ON partners(telegram_id)
                WHERE telegram_id IS NOT NULL
            """)

            cur.execute("""
                INSERT INTO admins (telegram_id, added_by)
                VALUES (%s, %s)
                ON CONFLICT (telegram_id) DO NOTHING
            """, (OWNER_ID, OWNER_ID))

        conn.commit()

    seed_partners()


def seed_partners():
    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("SELECT COUNT(*) FROM partners")
            count = cur.fetchone()[0]

            if count == 0:

                partners = [
                    (
                        "Partner 1",
                        "restaurant",
                        "Демонстрационный партнёр BIZDE.KZ",
                        20,
                        "Скидка действует для участников BIZDE.KZ.",
                        ""
                    ),
                    (
                        "VERO Café",
                        "cafe",
                        "Кофе, десерты и уютная атмосфера.",
                        20,
                        "Скидка для активных участников клуба.",
                        ""
                    ),
                    (
                        "FITROOM",
                        "sport",
                        "Спорт и тренировки.",
                        15,
                        "Условия уточняются у партнёра.",
                        ""
                    ),
                    (
                        "Beauty Room",
                        "beauty",
                        "Услуги красоты.",
                        15,
                        "Предъявите активное членство BIZDE.KZ.",
                        ""
                    )
                ]

                for partner in partners:
                    cur.execute("""
                        INSERT INTO partners
                        (
                            name,
                            category,
                            description,
                            discount_percent,
                            conditions,
                            photo_url
                        )
                        VALUES (%s,%s,%s,%s,%s,%s)
                    """, partner)

        conn.commit()


# =========================================================
# USERS
# =========================================================

def generate_member_code():

    while True:

        code = "BZ-" + "".join(
            secrets.choice(
                "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
            )
            for _ in range(8)
        )

        with db() as conn:
            with conn.cursor() as cur:

                cur.execute(
                    "SELECT 1 FROM users WHERE member_code=%s",
                    (code,)
                )

                if not cur.fetchone():
                    return code


def get_user(telegram_id):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM users
                WHERE telegram_id=%s
            """, (telegram_id,))

            return cur.fetchone()


def get_user_by_member_code(code):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM users
                WHERE member_code=%s
            """, (code,))

            return cur.fetchone()


def upsert_user(
    telegram_id,
    name="",
    username="",
    phone=None,
    terms_accepted=None
):

    existing = get_user(telegram_id)

    if not existing:

        member_code = generate_member_code()

        with db() as conn:
            with conn.cursor() as cur:

                cur.execute("""
                    INSERT INTO users
                    (
                        telegram_id,
                        name,
                        username,
                        phone,
                        member_code,
                        terms_accepted
                    )
                    VALUES (%s,%s,%s,%s,%s,%s)
                """, (
                    telegram_id,
                    name or "",
                    username or "",
                    phone or "",
                    member_code,
                    bool(terms_accepted)
                    if terms_accepted is not None
                    else False
                ))

            conn.commit()

        return get_user(telegram_id)

    fields = []
    values = []

    if name:
        fields.append("name=%s")
        values.append(name)

    if username is not None:
        fields.append("username=%s")
        values.append(username or "")

    if phone is not None:
        fields.append("phone=%s")
        values.append(phone)

    if terms_accepted is not None:
        fields.append("terms_accepted=%s")
        values.append(bool(terms_accepted))

    fields.append("updated_at=NOW()")

    values.append(telegram_id)

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                f"""
                UPDATE users
                SET {", ".join(fields)}
                WHERE telegram_id=%s
                """,
                tuple(values)
            )

        conn.commit()

    return get_user(telegram_id)


# =========================================================
# ADMIN
# =========================================================

def is_owner(telegram_id):
    try:
        return int(telegram_id) == OWNER_ID
    except (TypeError, ValueError):
        return False


def is_admin(telegram_id):

    if is_owner(telegram_id):
        return True

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                "SELECT 1 FROM admins WHERE telegram_id=%s",
                (telegram_id,)
            )

            return cur.fetchone() is not None


def add_admin(telegram_id, added_by):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO admins
                (
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

            cur.execute(
                "DELETE FROM admins WHERE telegram_id=%s",
                (telegram_id,)
            )

            result = cur.rowcount > 0

        conn.commit()

    return result


def get_admins():

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

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
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE active=TRUE
                ORDER BY id DESC
            """)

            return cur.fetchall()


def get_all_partners_admin():

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                ORDER BY id DESC
            """)

            return cur.fetchall()


def get_partner_by_id(partner_id):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE id=%s
            """, (partner_id,))

            return cur.fetchone()


def get_partner_by_telegram_id(telegram_id):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM partners
                WHERE telegram_id=%s
                AND active=TRUE
                LIMIT 1
            """, (telegram_id,))

            return cur.fetchone()


def get_role(telegram_id):

    if is_owner(telegram_id):
        return "owner"

    if is_admin(telegram_id):
        return "admin"

    if get_partner_by_telegram_id(telegram_id):
        return "partner"

    return "user"


def create_partner(
    name,
    category="",
    description="",
    discount_percent=0,
    conditions="",
    photo_url=""
):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                INSERT INTO partners
                (
                    name,
                    category,
                    description,
                    discount_percent,
                    conditions,
                    photo_url
                )
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (
                name,
                category,
                description,
                discount_percent,
                conditions,
                photo_url
            ))

            result = cur.fetchone()

        conn.commit()

    return result


def update_partner(
    partner_id,
    name=None,
    category=None,
    description=None,
    discount_percent=None,
    conditions=None,
    photo_url=None,
    active=None
):

    fields = []
    values = []

    data = {
        "name": name,
        "category": category,
        "description": description,
        "discount_percent": discount_percent,
        "conditions": conditions,
        "photo_url": photo_url,
        "active": active
    }

    for field, value in data.items():

        if value is not None:

            fields.append(f"{field}=%s")
            values.append(value)

    if not fields:
        return get_partner_by_id(partner_id)

    values.append(partner_id)

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                f"""
                UPDATE partners
                SET {", ".join(fields)}
                WHERE id=%s
                """,
                tuple(values)
            )

        conn.commit()

    return get_partner_by_id(partner_id)


def delete_partner(partner_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute(
                "DELETE FROM partners WHERE id=%s",
                (partner_id,)
            )

            result = cur.rowcount > 0

        conn.commit()

    return result


def assign_partner(partner_id, telegram_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE partners
                SET telegram_id=NULL
                WHERE telegram_id=%s
            """, (telegram_id,))

            cur.execute("""
                UPDATE partners
                SET telegram_id=%s
                WHERE id=%s
            """, (
                telegram_id,
                partner_id
            ))

            result = cur.rowcount > 0

        conn.commit()

    return result


def unassign_partner(partner_id):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE partners
                SET telegram_id=NULL
                WHERE id=%s
            """, (partner_id,))

            result = cur.rowcount > 0

        conn.commit()

    return result


# =========================================================
# USERS / SUBSCRIPTION
# =========================================================

def get_all_users_admin():

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

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
                SET language=%s,
                    updated_at=NOW()
                WHERE telegram_id=%s
            """, (
                language,
                telegram_id
            ))

        conn.commit()

    return get_user(telegram_id)


def set_subscription(telegram_id, active):

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                UPDATE users
                SET subscription_active=%s,
                    updated_at=NOW()
                WHERE telegram_id=%s
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
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                SELECT *
                FROM transactions
                WHERE telegram_id=%s
                ORDER BY created_at DESC
                LIMIT 100
            """, (telegram_id,))

            return cur.fetchall()


def create_transaction(
    telegram_id,
    partner_id,
    partner_name,
    receipt_amount,
    discount_percent,
    savings
):

    with db() as conn:
        with conn.cursor(
            cursor_factory=RealDictCursor
        ) as cur:

            cur.execute("""
                INSERT INTO transactions
                (
                    telegram_id,
                    partner_id,
                    partner_name,
                    receipt_amount,
                    discount_percent,
                    savings
                )
                VALUES (%s,%s,%s,%s,%s,%s)
                RETURNING *
            """, (
                telegram_id,
                partner_id,
                partner_name,
                receipt_amount,
                discount_percent,
                savings
            ))

            result = cur.fetchone()

            cur.execute("""
                UPDATE users
                SET total_savings =
                    COALESCE(total_savings,0)+%s,
                    updated_at=NOW()
                WHERE telegram_id=%s
            """, (
                savings,
                telegram_id
            ))

        conn.commit()

    return result


# =========================================================
# QR
# =========================================================

def create_qr_token(
    user_telegram_id,
    partner_id=None
):

    token = secrets.token_urlsafe(32)

    now = datetime.utcnow()

    expires_at = now + timedelta(
        seconds=QR_LIFETIME_SECONDS
    )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                INSERT INTO qr_tokens
                (
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


def verify_qr_token(
    token,
    partner_telegram_id=None
):

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
                    u.total_savings
                FROM qr_tokens q
                JOIN users u
                ON u.telegram_id=q.user_telegram_id
                WHERE q.token=%s
                LIMIT 1
            """, (token,))

            row = cur.fetchone()

    if not row:
        return {
            "valid": False,
            "error": "QR-код не найден"
        }

    if row["used"]:
        return {
            "valid": False,
            "error": "QR-код уже использован"
        }

    if row["expires_at"] < datetime.utcnow():
        return {
            "valid": False,
            "error": "QR-код истёк"
        }

    if partner_telegram_id is not None:

        partner = get_partner_by_telegram_id(
            partner_telegram_id
        )

        if not partner:
            return {
                "valid": False,
                "error": "Доступ только для партнёра"
            }

        if row["partner_id"] is not None:

            if int(row["partner_id"]) != int(partner["id"]):

                return {
                    "valid": False,
                    "error": "QR-код предназначен для другого партнёра"
                }

    return {
        "valid": True,
        "qr": row
    }


def confirm_qr_transaction(
    token,
    partner_telegram_id,
    receipt_amount
):

    result = verify_qr_token(
        token,
        partner_telegram_id
    )

    if not result["valid"]:
        return result

    qr = result["qr"]

    if not qr["subscription_active"]:
        return {
            "valid": False,
            "error": "Подписка пользователя неактивна"
        }

    partner = get_partner_by_telegram_id(
        partner_telegram_id
    )

    if not partner:
        return {
            "valid": False,
            "error": "Партнёр не найден"
        }

    try:
        amount = float(receipt_amount)
    except (TypeError, ValueError):

        return {
            "valid": False,
            "error": "Некорректная сумма чека"
        }

    if amount <= 0:

        return {
            "valid": False,
            "error": "Сумма должна быть больше 0"
        }

    discount = float(
        partner["discount_percent"] or 0
    )

    savings = round(
        amount * discount / 100,
        2
    )

    with db() as conn:
        with conn.cursor() as cur:

            cur.execute("""
                SELECT used, expires_at
                FROM qr_tokens
                WHERE token=%s
                FOR UPDATE
            """, (token,))

            locked = cur.fetchone()

            if not locked:
                return {
                    "valid": False,
                    "error": "QR-код не найден"
                }

            if locked[0]:
                return {
                    "valid": False,
                    "error": "QR-код уже использован"
                }

            if locked[1] < datetime.utcnow():
                return {
                    "valid": False,
                    "error": "QR-код истёк"
                }

            cur.execute("""
                INSERT INTO transactions
                (
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
                    COALESCE(total_savings,0)+%s,
                    updated_at=NOW()
                WHERE telegram_id=%s
            """, (
                savings,
                qr["user_telegram_id"]
            ))

            cur.execute("""
                UPDATE qr_tokens
                SET used=TRUE,
                    used_at=NOW(),
                    used_by_partner=%s
                WHERE token=%s
            """, (
                partner_telegram_id,
                token
            ))

        conn.commit()

    return {
        "valid": True,
        "success": True,
        "receipt_amount": amount,
        "discount_percent": discount,
        "savings": savings,
        "partner": partner["name"],
        "user": {
            "telegram_id": qr["user_telegram_id"],
            "name": qr["name"],
            "member_code": qr["member_code"]
        }
    }


# =========================================================
# MEMBER
# =========================================================

def verify_member(member_code):

    user = get_user_by_member_code(
        member_code
    )

    if not user:
        return {
            "valid": False,
            "error": "Участник не найден"
        }

    return {
        "valid": True,
        "user": user
    }


# =========================================================
# TELEGRAM INIT DATA
# =========================================================

def validate_init_data(init_data):

    if not init_data:
        raise ValueError(
            "Telegram initData отсутствует"
        )

    if not TOKEN:
        raise ValueError(
            "BOT_TOKEN не настроен"
        )

    data = dict(
        urllib.parse.parse_qsl(
            init_data,
            keep_blank_values=True
        )
    )

    received_hash = data.pop(
        "hash",
        None
    )

    if not received_hash:
        raise ValueError(
            "В initData отсутствует hash"
        )

    data_check_string = "\n".join(
        f"{key}={data[key]}"
        for key in sorted(data.keys())
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
        raise ValueError(
            "Неверный Telegram initData"
        )

    if "auth_date" in data:

        try:
            auth_date = int(
                data["auth_date"]
            )
        except ValueError:
            raise ValueError(
                "Некорректный auth_date"
            )

        current = int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )

        if current - auth_date > 86400:
            raise ValueError(
                "Telegram initData устарел"
            )

    if "user" not in data:
        raise ValueError(
            "Telegram user отсутствует"
        )

    try:
        return json.loads(
            data["user"]
        )

    except Exception:
        raise ValueError(
            "Некорректный Telegram user"
        )


def get_request_user(
    headers,
    body=None
):

    init_data = (
        headers.get(
            "X-Telegram-Init-Data"
        )
        or headers.get(
            "X-Telegram-Web-App-Init-Data"
        )
    )

    if not init_data and body:
        init_data = body.get(
            "initData"
        )

    if not init_data and body:
        init_data = body.get(
            "init_data"
        )

    return validate_init_data(
        init_data
    )


def authenticate(
    handler,
    body=None
):

    try:

        tg_user = get_request_user(
            handler.headers,
            body
        )

        return (
            tg_user,
            int(tg_user["id"])
        )

    except Exception as e:

        print(
            "AUTH ERROR:",
            repr(e)
        )

        return None, None


# =========================================================
# JSON
# =========================================================

def json_safe(value):

    if isinstance(value, datetime):
        return value.isoformat()

    if isinstance(value, Decimal):
        return float(value)

    if isinstance(value, list):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, tuple):
        return [
            json_safe(item)
            for item in value
        ]

    if isinstance(value, dict):
        return {
            str(key): json_safe(val)
            for key, val in value.items()
        }

    return value


def json_response(
    handler,
    data,
    status=200
):

    payload = json.dumps(
        json_safe(data),
        ensure_ascii=False
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
        "Content-Type, X-Telegram-Init-Data, X-Telegram-Web-App-Init-Data"
    )

    handler.send_header(
        "Access-Control-Allow-Methods",
        "GET, POST, OPTIONS"
    )

    handler.send_header(
        "Access-Control-Allow-Credentials",
        "true"
    )

    handler.end_headers()

    handler.wfile.write(
        payload
    )


def read_json(handler):

    try:

        length = int(
            handler.headers.get(
                "Content-Length",
                "0"
            )
        )

        if length <= 0:
            return {}

        raw = handler.rfile.read(
            length
        )

        return json.loads(
            raw.decode("utf-8")
        )

    except Exception as e:

        print(
            "JSON READ ERROR:",
            repr(e)
        )

        return {}


def error_response(
    handler,
    message,
    status=400
):

    return json_response(
        handler,
        {
            "success": False,
            "error": message
        },
        status
    )


# =========================================================
# HTTP HANDLER
# =========================================================

class RequestHandler(
    BaseHTTPRequestHandler
):

    def log_message(
        self,
        format,
        *args
    ):

        print(
            "%s - %s"
            % (
                self.address_string(),
                format % args
            )
        )

    def do_OPTIONS(self):

        self.send_response(204)

        self.send_header(
            "Access-Control-Allow-Origin",
            ALLOWED_ORIGIN
        )

        self.send_header(
            "Access-Control-Allow-Headers",
            "Content-Type, X-Telegram-Init-Data, X-Telegram-Web-App-Init-Data"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.send_header(
            "Access-Control-Allow-Credentials",
            "true"
        )

        self.end_headers()

    def do_GET(self):

        try:
            handle_get(self)

        except Exception as e:

            print(
                "GET ERROR:",
                repr(e)
            )

            error_response(
                self,
                "Ошибка сервера",
                500
            )

    def do_POST(self):

        try:
            handle_post(self)

        except Exception as e:

            print(
                "POST ERROR:",
                repr(e)
            )

            error_response(
                self,
                "Ошибка сервера",
                500
            )


# =========================================================
# GET
# =========================================================

def handle_get(handler):

    path = urlparse(
        handler.path
    ).path

    # -----------------------------------------------------
    # HEALTH CHECK
    # -----------------------------------------------------

    if path == "/":

        return json_response(
            handler,
            {
                "success": True,
                "service": "BIZDE.KZ",
                "status": "online"
            }
        )

    # -----------------------------------------------------
    # PUBLIC PARTNERS
    # -----------------------------------------------------

    if path == "/api/partners":

        return json_response(
            handler,
            {
                "success": True,
                "partners": get_partners()
            }
        )

    # -----------------------------------------------------
    # USER
    # -----------------------------------------------------

    if path == "/api/user":

        tg_user, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Откройте BIZDE через Telegram",
                401
            )

        user = get_user(
            telegram_id
        )

        if not user:

            name = tg_user.get(
                "first_name",
                ""
            )

            if tg_user.get("last_name"):

                name += (
                    " "
                    + tg_user["last_name"]
                )

            user = upsert_user(
                telegram_id,
                name.strip(),
                tg_user.get(
                    "username",
                    ""
                )
            )

        return json_response(
            handler,
            {
                "success": True,
                "user": user,
                "role": get_role(
                    telegram_id
                )
            }
        )

    # -----------------------------------------------------
    # ROLE
    # -----------------------------------------------------

    if path == "/api/role":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        return json_response(
            handler,
            {
                "success": True,
                "role": get_role(
                    telegram_id
                ),
                "partner":
                    get_partner_by_telegram_id(
                        telegram_id
                    )
            }
        )

    # -----------------------------------------------------
    # HISTORY
    # -----------------------------------------------------

    if path == "/api/history":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        return json_response(
            handler,
            {
                "success": True,
                "history":
                    get_history(
                        telegram_id
                    )
            }
        )

    # -----------------------------------------------------
    # PARTNER ME
    # -----------------------------------------------------

    if path == "/api/partner/me":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        partner = get_partner_by_telegram_id(
            telegram_id
        )

        if not partner:

            return error_response(
                handler,
                "Этот Telegram-аккаунт не назначен партнёром",
                403
            )

        return json_response(
            handler,
            {
                "success": True,
                "role": "partner",
                "partner": partner
            }
        )

    # -----------------------------------------------------
    # ADMIN PARTNERS
    # -----------------------------------------------------

    if path == "/api/admin/partners":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        return json_response(
            handler,
            {
                "success": True,
                "partners":
                    get_all_partners_admin()
            }
        )

    # -----------------------------------------------------
    # ADMIN USERS
    # -----------------------------------------------------

    if path == "/api/admin/users":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        return json_response(
            handler,
            {
                "success": True,
                "users":
                    get_all_users_admin()
            }
        )

    # -----------------------------------------------------
    # ADMINS
    # -----------------------------------------------------

    if path == "/api/admin/admins":

        _, telegram_id = authenticate(
            handler
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        return json_response(
            handler,
            {
                "success": True,
                "admins": get_admins()
            }
        )

    return error_response(
        handler,
        "Not found",
        404
    )


# =========================================================
# POST
# =========================================================

def handle_post(handler):

    path = urlparse(
        handler.path
    ).path

    body = read_json(
        handler
    )

    # -----------------------------------------------------
    # AUTH
    # -----------------------------------------------------

    if path == "/api/auth":

        tg_user, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Откройте BIZDE через Telegram",
                401
            )

        name = tg_user.get(
            "first_name",
            ""
        )

        if tg_user.get("last_name"):

            name += (
                " "
                + tg_user["last_name"]
            )

        user = upsert_user(
            telegram_id,
            name.strip(),
            tg_user.get(
                "username",
                ""
            )
        )

        return json_response(
            handler,
            {
                "success": True,
                "user": user,
                "role": get_role(
                    telegram_id
                )
            }
        )

    # -----------------------------------------------------
    # REGISTRATION
    # -----------------------------------------------------

    if path == "/api/register":

        tg_user, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Откройте BIZDE через Telegram",
                401
            )

        name = str(
            body.get(
                "name",
                ""
            )
        ).strip()

        phone = str(
            body.get(
                "phone",
                ""
            )
        ).strip()

        terms = bool(
            body.get(
                "terms_accepted",
                False
            )
        )

        if not name:

            return error_response(
                handler,
                "Введите имя"
            )

        if not phone:

            return error_response(
                handler,
                "Введите номер телефона"
            )

        if not terms:

            return error_response(
                handler,
                "Необходимо принять условия"
            )

        user = upsert_user(
            telegram_id,
            name,
            tg_user.get(
                "username",
                ""
            ),
            phone,
            True
        )

        return json_response(
            handler,
            {
                "success": True,
                "registered": True,
                "user": user,
                "role": get_role(
                    telegram_id
                )
            }
        )

    # -----------------------------------------------------
    # LANGUAGE
    # -----------------------------------------------------

    if path == "/api/language":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        language = str(
            body.get(
                "language",
                "ru"
            )
        ).lower()

        if language not in (
            "ru",
            "kk",
            "en"
        ):
            language = "ru"

        return json_response(
            handler,
            {
                "success": True,
                "user":
                    update_language(
                        telegram_id,
                        language
                    )
            }
        )

    # -----------------------------------------------------
    # QR CREATE
    # -----------------------------------------------------

    if path == "/api/qr/create":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        partner_id = body.get(
            "partner_id"
        )

        if partner_id is not None:

            try:
                partner_id = int(
                    partner_id
                )

            except (TypeError, ValueError):

                return error_response(
                    handler,
                    "Некорректный partner_id"
                )

        qr = create_qr_token(
            telegram_id,
            partner_id
        )

        return json_response(
            handler,
            {
                "success": True,
                "qr": qr
            }
        )

    # -----------------------------------------------------
    # QR VERIFY
    # -----------------------------------------------------

    if path == "/api/qr/verify":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        partner = get_partner_by_telegram_id(
            telegram_id
        )

        if not partner:

            return error_response(
                handler,
                "Доступ только для партнёра",
                403
            )

        token = str(
            body.get(
                "token",
                ""
            )
        ).strip()

        result = verify_qr_token(
            token,
            telegram_id
        )

        if not result["valid"]:

            return error_response(
                handler,
                result["error"]
            )

        qr = result["qr"]

        return json_response(
            handler,
            {
                "success": True,
                "valid": True,
                "user": {
                    "telegram_id":
                        qr["user_telegram_id"],
                    "name":
                        qr["name"],
                    "username":
                        qr["username"],
                    "phone":
                        qr["phone"],
                    "member_code":
                        qr["member_code"],
                    "subscription_active":
                        qr["subscription_active"],
                    "total_savings":
                        qr["total_savings"]
                },
                "partner": partner
            }
        )

    # -----------------------------------------------------
    # QR CONFIRM
    # -----------------------------------------------------

    if path == "/api/qr/confirm":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not get_partner_by_telegram_id(
            telegram_id
        ):

            return error_response(
                handler,
                "Доступ только для партнёра",
                403
            )

        result = confirm_qr_transaction(
            str(
                body.get(
                    "token",
                    ""
                )
            ).strip(),
            telegram_id,
            body.get(
                "receipt_amount"
            )
        )

        if not result["valid"]:

            return error_response(
                handler,
                result["error"]
            )

        return json_response(
            handler,
            result
        )

    # -----------------------------------------------------
    # VERIFY MEMBER
    # -----------------------------------------------------

    if path == "/api/verify-member":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not get_partner_by_telegram_id(
            telegram_id
        ):

            return error_response(
                handler,
                "Доступ только для партнёра",
                403
            )

        result = verify_member(
            str(
                body.get(
                    "member_code",
                    ""
                )
            ).strip()
        )

        return json_response(
            handler,
            result,
            200 if result["valid"] else 404
        )

    # =====================================================
    # ADMIN
    # =====================================================

    # CREATE PARTNER
    if path == "/api/admin/partners/create":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        name = str(
            body.get(
                "name",
                ""
            )
        ).strip()

        if not name:

            return error_response(
                handler,
                "Название партнёра обязательно"
            )

        try:

            discount = float(
                body.get(
                    "discount_percent",
                    0
                )
            )

        except (TypeError, ValueError):

            discount = 0

        partner = create_partner(
            name,
            str(
                body.get(
                    "category",
                    ""
                )
            ),
            str(
                body.get(
                    "description",
                    ""
                )
            ),
            discount,
            str(
                body.get(
                    "conditions",
                    ""
                )
            ),
            str(
                body.get(
                    "photo_url",
                    ""
                )
            )
        )

        return json_response(
            handler,
            {
                "success": True,
                "partner": partner
            }
        )

    # UPDATE PARTNER
    if path == "/api/admin/partners/update":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            partner_id = int(
                body.get(
                    "id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный ID"
            )

        discount = body.get(
            "discount_percent"
        )

        if discount is not None:

            try:

                discount = float(
                    discount
                )

            except (TypeError, ValueError):

                return error_response(
                    handler,
                    "Некорректная скидка"
                )

        partner = update_partner(
            partner_id,
            body.get("name"),
            body.get("category"),
            body.get("description"),
            discount,
            body.get("conditions"),
            body.get("photo_url"),
            body.get("active")
        )

        return json_response(
            handler,
            {
                "success": True,
                "partner": partner
            }
        )

    # DELETE PARTNER
    if path == "/api/admin/partners/delete":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            partner_id = int(
                body.get(
                    "id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный ID"
            )

        return json_response(
            handler,
            {
                "success":
                    delete_partner(
                        partner_id
                    )
            }
        )

    # ASSIGN PARTNER
    if path == "/api/admin/partners/assign":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            partner_id = int(
                body.get(
                    "partner_id"
                )
            )

            target_id = int(
                body.get(
                    "telegram_id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректные данные"
            )

        result = assign_partner(
            partner_id,
            target_id
        )

        return json_response(
            handler,
            {
                "success": result,
                "partner":
                    get_partner_by_id(
                        partner_id
                    )
            }
        )

    # UNASSIGN PARTNER
    if path == "/api/admin/partners/unassign":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            partner_id = int(
                body.get(
                    "partner_id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный ID"
            )

        return json_response(
            handler,
            {
                "success":
                    unassign_partner(
                        partner_id
                    )
            }
        )

    # SUBSCRIPTION
    if path == "/api/admin/subscription":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            target_id = int(
                body.get(
                    "telegram_id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный Telegram ID"
            )

        user = set_subscription(
            target_id,
            bool(
                body.get(
                    "active",
                    False
                )
            )
        )

        return json_response(
            handler,
            {
                "success": True,
                "user": user
            }
        )

    # ADD ADMIN
    if path == "/api/admin/add":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            target_id = int(
                body.get(
                    "telegram_id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный Telegram ID"
            )

        add_admin(
            target_id,
            telegram_id
        )

        return json_response(
            handler,
            {
                "success": True,
                "admins": get_admins()
            }
        )

    # REMOVE ADMIN
    if path == "/api/admin/remove":

        _, telegram_id = authenticate(
            handler,
            body
        )

        if not telegram_id:

            return error_response(
                handler,
                "Неавторизовано",
                401
            )

        if not is_admin(telegram_id):

            return error_response(
                handler,
                "Нет доступа",
                403
            )

        try:

            target_id = int(
                body.get(
                    "telegram_id"
                )
            )

        except (TypeError, ValueError):

            return error_response(
                handler,
                "Некорректный Telegram ID"
            )

        if target_id == OWNER_ID:

            return error_response(
                handler,
                "Нельзя удалить владельца"
            )

        return json_response(
            handler,
            {
                "success":
                    remove_admin(
                        target_id
                    ),
                "admins":
                    get_admins()
            }
        )

    return error_response(
        handler,
        "Not found",
        404
    )


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

    name = user.first_name or ""

    if user.last_name:
        name += " " + user.last_name

    upsert_user(
        user.id,
        name.strip(),
        user.username or ""
    )

    keyboard = [

        [
            InlineKeyboardButton(
                "👤 Войти как пользователь",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                )
            )
        ],

        [
            InlineKeyboardButton(
                "🤝 Войти как партнёр",
                web_app=WebAppInfo(
                    url=PARTNER_PAGE
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

    if is_admin(user.id):

        keyboard.append([
            InlineKeyboardButton(
                "⚙️ Администратор",
                web_app=WebAppInfo(
                    url=ADMIN_PAGE
                )
            )
        ])

    await update.message.reply_text(

        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Платформа привилегий для участников клуба.\n"
        "Экономьте больше. Получайте больше привилегий.",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def admin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user:
        return

    if not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ к админ-панели закрыт."
        )

        return

    await update.message.reply_text(

        "⚙️ Административная панель BIZDE.KZ",

        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Открыть админ-панель",
                    web_app=WebAppInfo(
                        url=ADMIN_PAGE
                    )
                )
            ]
        ])
    )


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
            "⛔ Этот Telegram-аккаунт не назначен партнёром."
        )

        return

    await update.message.reply_text(

        "🤝 Кабинет партнёра BIZDE.KZ",

        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "Открыть кабинет партнёра",
                    web_app=WebAppInfo(
                        url=PARTNER_PAGE
                    )
                )
            ]
        ])
    )


async def addadmin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_owner(user.id):

        await update.message.reply_text(
            "⛔ Только владелец может добавлять администраторов."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n/addadmin TELEGRAM_ID"
        )

        return

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "Некорректный Telegram ID."
        )

        return

    add_admin(
        target_id,
        user.id
    )

    await update.message.reply_text(
        f"✅ Администратор {target_id} добавлен."
    )


async def removeadmin_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_owner(user.id):

        await update.message.reply_text(
            "⛔ Только владелец может удалять администраторов."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n/removeadmin TELEGRAM_ID"
        )

        return

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "Некорректный Telegram ID."
        )

        return

    if target_id == OWNER_ID:

        await update.message.reply_text(
            "Нельзя удалить владельца."
        )

        return

    remove_admin(
        target_id
    )

    await update.message.reply_text(
        f"✅ Администратор {target_id} удалён."
    )


async def admins_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ закрыт."
        )

        return

    admins = get_admins()

    text = "⚙️ Администраторы:\n\n"

    for admin in admins:

        mark = (
            " 👑"
            if int(
                admin["telegram_id"]
            ) == OWNER_ID
            else ""
        )

        text += (
            f"• {admin['telegram_id']}{mark}\n"
        )

    await update.message.reply_text(
        text
    )


async def setpartner_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ закрыт."
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

    except ValueError:

        await update.message.reply_text(
            "Некорректные ID."
        )

        return

    if assign_partner(
        partner_id,
        telegram_id
    ):

        await update.message.reply_text(
            "✅ Партнёр назначен."
        )

    else:

        await update.message.reply_text(
            "❌ Не удалось назначить партнёра."
        )


async def unsetpartner_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ закрыт."
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

    except ValueError:

        await update.message.reply_text(
            "Некорректный ID."
        )

        return

    unassign_partner(
        partner_id
    )

    await update.message.reply_text(
        "✅ Партнёр отключён."
    )


async def activate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ закрыт."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n/activate TELEGRAM_ID"
        )

        return

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "Некорректный Telegram ID."
        )

        return

    set_subscription(
        target_id,
        True
    )

    await update.message.reply_text(
        f"✅ Подписка {target_id} активирована."
    )


async def deactivate_command(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE
):

    user = update.effective_user

    if not user or not is_admin(user.id):

        await update.message.reply_text(
            "⛔ Доступ закрыт."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Использование:\n/deactivate TELEGRAM_ID"
        )

        return

    try:

        target_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "Некорректный Telegram ID."
        )

        return

    set_subscription(
        target_id,
        False
    )

    await update.message.reply_text(
        f"✅ Подписка {target_id} отключена."
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

    if query.data == "categories":

        partners = get_partners()

        categories = sorted(
            set(
                p["category"]
                for p in partners
                if p["category"]
            )
        )

        if categories:

            text = (
                "📂 Категории BIZDE.KZ:\n\n"
                + "\n".join(
                    "• " + category
                    for category in categories
                )
            )

        else:

            text = "Категорий пока нет."

        await query.message.reply_text(
            text
        )

    elif query.data == "partners":

        partners = get_partners()

        if not partners:

            await query.message.reply_text(
                "Партнёров пока нет."
            )

            return

        text = "🏪 Партнёры BIZDE.KZ:\n\n"

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
            query.from_user.id
        )

        if not user:

            await query.message.reply_text(
                "Откройте BIZDE через Telegram."
            )

            return

        status = (
            "🟢 Активна"
            if user["subscription_active"]
            else "🔴 Неактивна"
        )

        await query.message.reply_text(

            "🎟 Моя подписка BIZDE.KZ\n\n"
            f"Статус: {status}\n"
            f"Код участника: {user['member_code']}\n"
            f"Накоплено: "
            f"{float(user['total_savings'] or 0):.2f} ₸"
        )


# =========================================================
# HTTP SERVER
# =========================================================

def run_http_server():

    server = HTTPServer(
        (
            "0.0.0.0",
            PORT
        ),
        RequestHandler
    )

    print(
        f"BIZDE API запущен на порту {PORT}"
    )

    server.serve_forever()


# =========================================================
# MAIN
# =========================================================

def main():

    if not TOKEN:

        raise RuntimeError(
            "BOT_TOKEN не установлен"
        )

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL не установлен"
        )

    print(
        "Запуск BIZDE.KZ..."
    )

    init_db()

    server_thread = threading.Thread(
        target=run_http_server,
        daemon=True
    )

    server_thread.start()

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
        "Telegram bot запущен."
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
