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


# =========================================================
# SETTINGS
# =========================================================

TOKEN = os.environ.get("BOT_TOKEN")
DATABASE_URL = os.environ.get("DATABASE_URL")

OWNER_ID = 1882252883

WEB_APP_URL = "https://aydin200169.github.io/-bizde-bot/"
PORT = int(os.environ.get("PORT", "10000"))

ALLOWED_ORIGIN = "https://aydin200169.github.io"


# =========================================================
# DATABASE
# =========================================================

def get_db():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL is not set")

    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()

    try:
        cur = conn.cursor()

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
                registered_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                partner_id INTEGER,
                partner_name TEXT DEFAULT '',
                receipt_amount NUMERIC(12,2) NOT NULL,
                discount_percent NUMERIC(5,2) NOT NULL,
                savings NUMERIC(12,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                added_by BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        migrations = [
            ("users", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("users", "member_code", "TEXT"),
            ("users", "subscription_active", "BOOLEAN DEFAULT FALSE"),
            ("users", "total_savings", "NUMERIC(12,2) DEFAULT 0"),
            ("users", "terms_accepted", "BOOLEAN DEFAULT FALSE"),
            ("users", "language", "TEXT DEFAULT 'ru'"),
            ("users", "phone", "TEXT DEFAULT ''"),
            ("users", "username", "TEXT DEFAULT ''"),
            ("partners", "photo_url", "TEXT DEFAULT ''"),
            ("partners", "active", "BOOLEAN DEFAULT TRUE"),
        ]

        for table, column, definition in migrations:
            cur.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS {column} {definition}
                """
            )

        cur.execute("""
            INSERT INTO admins (telegram_id, added_by)
            VALUES (%s, %s)
            ON CONFLICT (telegram_id) DO NOTHING
        """, (OWNER_ID, OWNER_ID))

        cur.execute("SELECT COUNT(*) FROM partners")
        count = cur.fetchone()[0]

        if count == 0:
            partners = [
                (
                    "Partner 1",
                    "restaurant",
                    "Ресторан восточной кухни",
                    20,
                    "Привилегия для участников BIZDE.KZ",
                ),
                (
                    "VERO Café",
                    "cafe",
                    "Кофе и десерты",
                    20,
                    "Привилегия для участников BIZDE.KZ",
                ),
                (
                    "FITROOM",
                    "sport",
                    "Спорт и тренировки",
                    15,
                    "Привилегия для участников BIZDE.KZ",
                ),
                (
                    "Beauty Room",
                    "beauty",
                    "Красота и уход",
                    15,
                    "Привилегия для участников BIZDE.KZ",
                ),
            ]

            for partner in partners:
                cur.execute("""
                    INSERT INTO partners
                    (
                        name,
                        category,
                        description,
                        discount_percent,
                        conditions
                    )
                    VALUES (%s, %s, %s, %s, %s)
                """, partner)

        conn.commit()

    finally:
        conn.close()


# =========================================================
# ADMIN SYSTEM
# =========================================================

def is_owner(telegram_id):
    try:
        return int(telegram_id) == OWNER_ID
    except Exception:
        return False


def is_admin(telegram_id):
    if is_owner(telegram_id):
        return True

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            SELECT 1
            FROM admins
            WHERE telegram_id = %s
        """, (telegram_id,))

        return cur.fetchone() is not None

    finally:
        conn.close()


def add_admin(telegram_id, added_by):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            INSERT INTO admins
            (
                telegram_id,
                added_by
            )
            VALUES (%s, %s)
            ON CONFLICT (telegram_id) DO NOTHING
        """, (telegram_id, added_by))

        conn.commit()

        return cur.rowcount > 0

    finally:
        conn.close()


def remove_admin(telegram_id):
    if int(telegram_id) == OWNER_ID:
        return False

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM admins
            WHERE telegram_id = %s
        """, (telegram_id,))

        conn.commit()

        return cur.rowcount > 0

    finally:
        conn.close()


def get_admins():
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT
                telegram_id,
                added_by,
                created_at
            FROM admins
            ORDER BY created_at
        """)

        return cur.fetchall()

    finally:
        conn.close()


# =========================================================
# USERS
# =========================================================

def generate_member_code():
    return "BIZDE-" + secrets.token_hex(4).upper()


def get_user(telegram_id):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        return cur.fetchone()

    finally:
        conn.close()


def get_user_by_member_code(member_code):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM users
            WHERE member_code = %s
        """, (member_code,))

        return cur.fetchone()

    finally:
        conn.close()


def upsert_user(
    telegram_id,
    name="",
    username="",
    phone="",
    language="ru",
    terms_accepted=False
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        existing = cur.fetchone()

        if existing:
            cur.execute("""
                UPDATE users
                SET
                    name = COALESCE(NULLIF(%s, ''), name),
                    username = COALESCE(NULLIF(%s, ''), username),
                    phone = COALESCE(NULLIF(%s, ''), phone),
                    language = COALESCE(NULLIF(%s, ''), language),
                    terms_accepted =
                        CASE
                            WHEN %s = TRUE THEN TRUE
                            ELSE terms_accepted
                        END,
                    updated_at = CURRENT_TIMESTAMP
                WHERE telegram_id = %s
                RETURNING *
            """, (
                name,
                username,
                phone,
                language,
                terms_accepted,
                telegram_id
            ))

        else:
            member_code = generate_member_code()

            cur.execute("""
                INSERT INTO users
                (
                    telegram_id,
                    name,
                    username,
                    phone,
                    language,
                    member_code,
                    terms_accepted
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                RETURNING *
            """, (
                telegram_id,
                name,
                username,
                phone,
                language,
                member_code,
                terms_accepted
            ))

        user = cur.fetchone()

        conn.commit()

        return user

    finally:
        conn.close()


# =========================================================
# TELEGRAM INIT DATA
# =========================================================

def validate_telegram_init_data(init_data):
    if not init_data or not TOKEN:
        return None

    try:
        parsed = dict(
            urllib.parse.parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )

        received_hash = parsed.pop("hash", None)

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(parsed.items())
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

        user_json = parsed.get("user")

        if not user_json:
            return None

        return json.loads(user_json)

    except Exception:
        return None


# =========================================================
# PARTNERS
# =========================================================

def get_partners(category=None):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        if category:
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

        return cur.fetchall()

    finally:
        conn.close()


def get_all_partners():
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM partners
            ORDER BY id DESC
        """)

        return cur.fetchall()

    finally:
        conn.close()


def create_partner(
    name,
    category,
    description,
    discount_percent,
    conditions,
    photo_url=""
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            INSERT INTO partners
            (
                name,
                category,
                description,
                discount_percent,
                conditions,
                photo_url,
                active
            )
            VALUES (%s, %s, %s, %s, %s, %s, TRUE)
            RETURNING *
        """, (
            name,
            category,
            description,
            discount_percent,
            conditions,
            photo_url
        ))

        partner = cur.fetchone()

        conn.commit()

        return partner

    finally:
        conn.close()


def update_partner(
    partner_id,
    name,
    category,
    description,
    discount_percent,
    conditions,
    photo_url=""
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            UPDATE partners
            SET
                name = %s,
                category = %s,
                description = %s,
                discount_percent = %s,
                conditions = %s,
                photo_url = %s
            WHERE id = %s
            RETURNING *
        """, (
            name,
            category,
            description,
            discount_percent,
            conditions,
            photo_url,
            partner_id
        ))

        partner = cur.fetchone()

        conn.commit()

        return partner

    finally:
        conn.close()


def toggle_partner(partner_id):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            UPDATE partners
            SET active = NOT active
            WHERE id = %s
            RETURNING *
        """, (partner_id,))

        partner = cur.fetchone()

        conn.commit()

        return partner

    finally:
        conn.close()


def delete_partner(partner_id):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            DELETE FROM partners
            WHERE id = %s
        """, (partner_id,))

        deleted = cur.rowcount > 0

        conn.commit()

        return deleted

    finally:
        conn.close()


# =========================================================
# HISTORY
# =========================================================

def get_history(telegram_id):
    conn = get_db()

    try:
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
        """, (telegram_id,))

        return cur.fetchall()

    finally:
        conn.close()


# =========================================================
# LANGUAGE
# =========================================================

def update_language(telegram_id, language):
    if language not in ("ru", "kk", "en"):
        language = "ru"

    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET
                language = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = %s
        """, (language, telegram_id))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# MEMBER
# =========================================================

def verify_member(member_code):
    user = get_user_by_member_code(member_code)

    if not user:
        return None

    return {
        "name": user["name"],
        "member_code": user["member_code"],
        "subscription_active": bool(
            user["subscription_active"]
        ),
        "telegram_id": user["telegram_id"],
        "total_savings": float(
            user["total_savings"] or 0
        )
    }


# =========================================================
# SUBSCRIPTION
# =========================================================

def set_subscription(telegram_id, active):
    conn = get_db()

    try:
        cur = conn.cursor()

        cur.execute("""
            UPDATE users
            SET
                subscription_active = %s,
                updated_at = CURRENT_TIMESTAMP
            WHERE telegram_id = %s
        """, (active, telegram_id))

        conn.commit()

    finally:
        conn.close()


# =========================================================
# TRANSACTIONS
# =========================================================

def create_transaction(
    telegram_id,
    partner_id,
    receipt_amount
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        user = cur.fetchone()

        if not user:
            raise ValueError("Пользователь не найден")

        if not user["subscription_active"]:
            raise ValueError("Подписка не активна")

        cur.execute("""
            SELECT *
            FROM partners
            WHERE id = %s
            AND active = TRUE
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError("Партнёр не найден")

        amount = float(receipt_amount)

        if amount <= 0:
            raise ValueError("Сумма должна быть больше 0")

        discount = float(
            partner["discount_percent"] or 0
        )

        savings = round(
            amount * discount / 100,
            2
        )

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
            VALUES (%s, %s, %s, %s, %s, %s)
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
            RETURNING total_savings
        """, (savings, telegram_id))

        total_savings = cur.fetchone()["total_savings"]

        conn.commit()

        return {
            "partner_name": partner["name"],
            "receipt_amount": amount,
            "discount_percent": discount,
            "savings": savings,
            "total_savings": float(total_savings)
        }

    finally:
        conn.close()


def partner_use_offer(
    member_code,
    partner_id,
    receipt_amount
):
    conn = get_db()

    try:
        cur = conn.cursor(cursor_factory=RealDictCursor)

        cur.execute("""
            SELECT *
            FROM users
            WHERE member_code = %s
        """, (member_code,))

        user = cur.fetchone()

        if not user:
            raise ValueError("Участник не найден")

        if not user["subscription_active"]:
            raise ValueError(
                "Подписка участника не активна"
            )

        cur.execute("""
            SELECT *
            FROM partners
            WHERE id = %s
            AND active = TRUE
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError("Партнёр не найден")

        amount = float(receipt_amount)

        if amount <= 0:
            raise ValueError(
                "Сумма должна быть больше 0"
            )

        discount = float(
            partner["discount_percent"] or 0
        )

        savings = round(
            amount * discount / 100,
            2
        )

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
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (
            user["telegram_id"],
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
            RETURNING total_savings
        """, (
            savings,
            user["telegram_id"]
        ))

        total_savings = cur.fetchone()["total_savings"]

        conn.commit()

        return {
            "user_name": user["name"],
            "member_code": user["member_code"],
            "partner_name": partner["name"],
            "receipt_amount": amount,
            "discount_percent": discount,
            "savings": savings,
            "total_savings": float(total_savings)
        }

    finally:
        conn.close()


# =========================================================
# HTTP HELPERS
# =========================================================

def json_response(handler, data, status=200):
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
        "Content-Length",
        str(len(body))
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

    handler.end_headers()

    handler.wfile.write(body)


def read_json(handler):
    length = int(
        handler.headers.get(
            "Content-Length",
            0
        )
    )

    if length <= 0:
        return {}

    raw = handler.rfile.read(length)

    return json.loads(
        raw.decode("utf-8")
    )


# =========================================================
# HTTP SERVER
# =========================================================

class RequestHandler(BaseHTTPRequestHandler):

    def log_message(self, format, *args):
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
            "Content-Type"
        )

        self.send_header(
            "Access-Control-Allow-Methods",
            "GET, POST, OPTIONS"
        )

        self.end_headers()

    def do_GET(self):

        try:
            parsed = urlparse(self.path)

            path = parsed.path

            params = parse_qs(
                parsed.query
            )

            # -------------------------------------------------
            # HEALTH CHECK
            # -------------------------------------------------

            if path == "/":
                json_response(
                    self,
                    {
                        "status": "ok",
                        "service": "BIZDE.KZ"
                    }
                )
                return

            # -------------------------------------------------
            # USER
            # -------------------------------------------------

            if path == "/api/user":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if tg_user:

                    user = get_user(
                        tg_user["id"]
                    )

                    json_response(
                        self,
                        {
                            "success": True,
                            "user":
                                dict(user)
                                if user
                                else None
                        }
                    )
                    return

                # Compatibility with old frontend
                telegram_id = params.get(
                    "telegram_id",
                    [None]
                )[0]

                if not telegram_id:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "init_data required"
                        },
                        401
                    )
                    return

                user = get_user(
                    int(telegram_id)
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "user":
                            dict(user)
                            if user
                            else None
                    }
                )
                return

            # -------------------------------------------------
            # PUBLIC PARTNERS
            # -------------------------------------------------

            if path == "/api/partners":

                category = params.get(
                    "category",
                    [None]
                )[0]

                partners = get_partners(
                    category
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partners": partners
                    }
                )
                return

            # -------------------------------------------------
            # ADMIN PARTNERS
            # -------------------------------------------------

            if path == "/api/admin/partners":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                if not is_admin(
                    tg_user["id"]
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Нет доступа к админ-панели"
                        },
                        403
                    )
                    return

                partners = get_all_partners()

                json_response(
                    self,
                    {
                        "success": True,
                        "partners": partners
                    }
                )
                return

            # -------------------------------------------------
            # HISTORY
            # -------------------------------------------------

            if path == "/api/history":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Invalid init_data"
                        },
                        401
                    )
                    return

                history = get_history(
                    tg_user["id"]
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "history": history
                    }
                )
                return

            json_response(
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
                e
            )

            json_response(
                self,
                {
                    "success": False,
                    "error": str(e)
                },
                500
            )

    def do_POST(self):

        try:

            path = urlparse(
                self.path
            ).path

            data = read_json(
                self
            )

            # =================================================
            # ADMIN CREATE PARTNER
            # =================================================

            if path == "/api/admin/partners/create":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                if not is_admin(
                    tg_user["id"]
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Нет доступа"
                        },
                        403
                    )
                    return

                name = str(
                    data.get(
                        "name",
                        ""
                    )
                ).strip()

                category = str(
                    data.get(
                        "category",
                        ""
                    )
                ).strip()

                description = str(
                    data.get(
                        "description",
                        ""
                    )
                ).strip()

                conditions = str(
                    data.get(
                        "conditions",
                        ""
                    )
                ).strip()

                photo_url = str(
                    data.get(
                        "photo_url",
                        ""
                    )
                ).strip()

                discount = float(
                    data.get(
                        "discount_percent",
                        0
                    )
                )

                if not name:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Введите название партнёра"
                        },
                        400
                    )
                    return

                if discount < 0 or discount > 100:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Привилегия должна быть от 0 до 100%"
                        },
                        400
                    )
                    return

                partner = create_partner(
                    name,
                    category,
                    description,
                    discount,
                    conditions,
                    photo_url
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner": partner
                    }
                )
                return

            # =================================================
            # ADMIN UPDATE PARTNER
            # =================================================

            if path == "/api/admin/partners/update":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                if not is_admin(
                    tg_user["id"]
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Нет доступа"
                        },
                        403
                    )
                    return

                partner_id = int(
                    data["id"]
                )

                name = str(
                    data.get(
                        "name",
                        ""
                    )
                ).strip()

                category = str(
                    data.get(
                        "category",
                        ""
                    )
                ).strip()

                description = str(
                    data.get(
                        "description",
                        ""
                    )
                ).strip()

                conditions = str(
                    data.get(
                        "conditions",
                        ""
                    )
                ).strip()

                photo_url = str(
                    data.get(
                        "photo_url",
                        ""
                    )
                ).strip()

                discount = float(
                    data.get(
                        "discount_percent",
                        0
                    )
                )

                if not name:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Введите название партнёра"
                        },
                        400
                    )
                    return

                if discount < 0 or discount > 100:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Привилегия должна быть от 0 до 100%"
                        },
                        400
                    )
                    return

                partner = update_partner(
                    partner_id,
                    name,
                    category,
                    description,
                    discount,
                    conditions,
                    photo_url
                )

                if not partner:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Партнёр не найден"
                        },
                        404
                    )
                    return

                json_response(
                    self,
                    {
                        "success": True,
                        "partner": partner
                    }
                )
                return

            # =================================================
            # ADMIN TOGGLE PARTNER
            # =================================================

            if path == "/api/admin/partners/toggle":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                if not is_admin(
                    tg_user["id"]
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Нет доступа"
                        },
                        403
                    )
                    return

                partner_id = int(
                    data["id"]
                )

                partner = toggle_partner(
                    partner_id
                )

                if not partner:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Партнёр не найден"
                        },
                        404
                    )
                    return

                json_response(
                    self,
                    {
                        "success": True,
                        "partner": partner
                    }
                )
                return

            # =================================================
            # ADMIN DELETE PARTNER
            # =================================================

            if path == "/api/admin/partners/delete":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                if not is_admin(
                    tg_user["id"]
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Нет доступа"
                        },
                        403
                    )
                    return

                partner_id = int(
                    data["id"]
                )

                deleted = delete_partner(
                    partner_id
                )

                if not deleted:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Партнёр не найден"
                        },
                        404
                    )
                    return

                json_response(
                    self,
                    {
                        "success": True
                    }
                )
                return

            # =================================================
            # AUTH
            # =================================================

            if path == "/api/auth":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Invalid Telegram init data"
                        },
                        401
                    )
                    return

                existing = get_user(
                    tg_user["id"]
                )

                phone = str(
                    data.get(
                        "phone",
                        ""
                    )
                ).strip()

                terms_accepted = bool(
                    data.get(
                        "terms_accepted",
                        False
                    )
                )

                if not existing and (
                    not phone or
                    not terms_accepted
                ):

                    json_response(
                        self,
                        {
                            "success": False,
                            "needs_registration": True,
                            "telegram_user": {
                                "id": tg_user["id"],
                                "first_name":
                                    tg_user.get(
                                        "first_name",
                                        ""
                                    ),
                                "last_name":
                                    tg_user.get(
                                        "last_name",
                                        ""
                                    ),
                                "username":
                                    tg_user.get(
                                        "username",
                                        ""
                                    )
                            }
                        }
                    )
                    return

                user = upsert_user(
                    telegram_id=tg_user["id"],
                    name=data.get(
                        "name",
                        tg_user.get(
                            "first_name",
                            ""
                        )
                    ),
                    username=tg_user.get(
                        "username",
                        ""
                    ),
                    phone=phone,
                    language=data.get(
                        "language",
                        "ru"
                    ),
                    terms_accepted=terms_accepted
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "user": dict(user)
                    }
                )
                return

            # =================================================
            # LANGUAGE
            # =================================================

            if path == "/api/language":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Invalid init data"
                        },
                        401
                    )
                    return

                language = data.get(
                    "language",
                    "ru"
                )

                update_language(
                    tg_user["id"],
                    language
                )

                json_response(
                    self,
                    {
                        "success": True
                    }
                )
                return

            # =================================================
            # VERIFY MEMBER
            # =================================================

            if path == "/api/verify-member":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                member_code = str(
                    data.get(
                        "member_code",
                        ""
                    )
                ).strip().upper()

                if not member_code:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Введите код участника"
                        },
                        400
                    )
                    return

                result = verify_member(
                    member_code
                )

                if not result:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Участник не найден"
                        },
                        404
                    )
                    return

                json_response(
                    self,
                    {
                        "success": True,
                        "member": result,
                        "user": result
                    }
                )
                return

            # =================================================
            # USE OFFER
            # =================================================

            if path == "/api/use-offer":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Invalid init data"
                        },
                        401
                    )
                    return

                result = create_transaction(
                    telegram_id=tg_user["id"],
                    partner_id=int(
                        data["partner_id"]
                    ),
                    receipt_amount=float(
                        data["receipt_amount"]
                    )
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "result": result
                    }
                )
                return

            # =================================================
            # PARTNER USE OFFER
            # =================================================

            if path == "/api/partner-use-offer":

                init_data = data.get(
                    "init_data",
                    ""
                )

                tg_user = validate_telegram_init_data(
                    init_data
                )

                if not tg_user:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Неверные данные Telegram"
                        },
                        401
                    )
                    return

                result = partner_use_offer(
                    member_code=str(
                        data.get(
                            "member_code",
                            ""
                        )
                    ).strip().upper(),
                    partner_id=int(
                        data["partner_id"]
                    ),
                    receipt_amount=float(
                        data["receipt_amount"]
                    )
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "result": result,

                        # Compatibility
                        "partner_name":
                            result["partner_name"],
                        "receipt_amount":
                            result["receipt_amount"],
                        "discount_percent":
                            result["discount_percent"],
                        "savings":
                            result["savings"],
                        "total_savings":
                            result["total_savings"],
                        "partner": {
                            "name":
                                result["partner_name"]
                        }
                    }
                )
                return

            # =================================================
            # NOT FOUND
            # =================================================

            json_response(
                self,
                {
                    "success": False,
                    "error": "Not found"
                },
                404
            )

        except ValueError as e:

            json_response(
                self,
                {
                    "success": False,
                    "error": str(e)
                },
                400
            )

        except Exception as e:

            print(
                "POST ERROR:",
                e
            )

            json_response(
                self,
                {
                    "success": False,
                    "error": str(e)
                },
                500
            )


# =========================================================
# TELEGRAM BOT
# =========================================================

async def start(update, context):

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

    await update.message.reply_text(
        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


async def button_handler(update, context):

    query = update.callback_query

    await query.answer()

    if query.data == "categories":

        keyboard = [
            [
                InlineKeyboardButton(
                    "🍽 Рестораны",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "☕ Кафе",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "🏋️ Спорт",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    "💆 Красота",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ]
        ]

        await query.message.reply_text(
            "Выбери категорию:",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    elif query.data == "partners":

        keyboard = [
            [
                InlineKeyboardButton(
                    "📱 Открыть BIZDE",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ]
        ]

        await query.message.reply_text(
            "Все привилегии доступны внутри BIZDE.KZ 👇",
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    elif query.data == "subscription":

        telegram_id = query.from_user.id

        user = get_user(
            telegram_id
        )

        if not user:

            text = (
                "Ты ещё не зарегистрирован.\n"
                "Открой BIZDE и пройди регистрацию."
            )

        else:

            status = (
                "Активна ✅"
                if user["subscription_active"]
                else "Не активна ❌"
            )

            text = (
                "🎟 Моя привилегия\n\n"
                f"Статус: {status}\n"
                f"Код: {user['member_code']}"
            )

        keyboard = [
            [
                InlineKeyboardButton(
                    "📱 Открыть BIZDE",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ]
        ]

        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )


# =========================================================
# ADMIN COMMAND
# =========================================================

async def admin_command(update, context):

    telegram_id = update.effective_user.id

    if not is_admin(telegram_id):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    admin_url = (
        WEB_APP_URL.rstrip("/")
        + "/admin.html"
    )

    keyboard = [
        [
            InlineKeyboardButton(
                "⚙️ Открыть админку",
                web_app=WebAppInfo(
                    url=admin_url
                )
            )
        ]
    ]

    await update.message.reply_text(
        "🔐 Панель администратора BIZDE.KZ\n\n"
        "Управление партнёрами:",
        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# ADD ADMIN
# =========================================================

async def add_admin_command(
    update,
    context
):

    telegram_id = update.effective_user.id

    if not is_owner(telegram_id):

        await update.message.reply_text(
            "⛔ Только главный владелец может "
            "добавлять администраторов."
        )
        return

    if not context.args:

        await update.message.reply_text(
            "Используй:\n\n"
            "/addadmin TELEGRAM_ID"
        )
        return

    try:

        new_admin_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Telegram ID должен состоять "
            "только из цифр."
        )
        return

    if new_admin_id == OWNER_ID:

        await update.message.reply_text(
            "Это уже главный владелец 👑"
        )
        return

    added = add_admin(
        new_admin_id,
        telegram_id
    )

    if added:

        await update.message.reply_text(
            "✅ Администратор добавлен.\n\n"
            f"Telegram ID: {new_admin_id}"
        )

    else:

        await update.message.reply_text(
            "ℹ️ Этот пользователь уже "
            "является администратором."
        )


# =========================================================
# REMOVE ADMIN
# =========================================================

async def remove_admin_command(
    update,
    context
):

    telegram_id = update.effective_user.id

    if not is_owner(telegram_id):

        await update.message.reply_text(
            "⛔ Только главный владелец может "
            "удалять администраторов."
        )
        return

    if not context.args:

        await update.message.reply_text(
            "Используй:\n\n"
            "/removeadmin TELEGRAM_ID"
        )
        return

    try:

        admin_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Telegram ID должен состоять "
            "только из цифр."
        )
        return

    if admin_id == OWNER_ID:

        await update.message.reply_text(
            "❌ Главного владельца удалить нельзя."
        )
        return

    removed = remove_admin(
        admin_id
    )

    if removed:

        await update.message.reply_text(
            "✅ Администратор удалён."
        )

    else:

        await update.message.reply_text(
            "ℹ️ Такой администратор не найден."
        )


# =========================================================
# ADMINS
# =========================================================

async def admins_command(
    update,
    context
):

    telegram_id = update.effective_user.id

    if not is_admin(telegram_id):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    admins = get_admins()

    text = (
        "👥 Администраторы BIZDE.KZ\n\n"
    )

    for index, admin in enumerate(
        admins,
        start=1
    ):

        admin_id = int(
            admin["telegram_id"]
        )

        if admin_id == OWNER_ID:

            role = "👑 Главный владелец"

        else:

            role = "🛡 Администратор"

        text += (
            f"{index}. {role}\n"
            f"ID: {admin_id}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# ACTIVATE
# =========================================================

async def activate(
    update,
    context
):

    telegram_id = update.effective_user.id

    if not is_admin(telegram_id):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    if not context.args:

        await update.message.reply_text(
            "Используй:\n"
            "/activate TELEGRAM_ID"
        )
        return

    try:

        user_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Telegram ID должен состоять "
            "только из цифр."
        )
        return

    set_subscription(
        user_id,
        True
    )

    await update.message.reply_text(
        "Подписка активирована ✅"
    )


# =========================================================
# DEACTIVATE
# =========================================================

async def deactivate(
    update,
    context
):

    telegram_id = update.effective_user.id

    if not is_admin(telegram_id):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )
        return

    if not context.args:

        await update.message.reply_text(
            "Используй:\n"
            "/deactivate TELEGRAM_ID"
        )
        return

    try:

        user_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Telegram ID должен состоять "
            "только из цифр."
        )
        return

    set_subscription(
        user_id,
        False
    )

    await update.message.reply_text(
        "Подписка отключена ❌"
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
            "BOT_TOKEN is not set"
        )

    if not DATABASE_URL:

        raise RuntimeError(
            "DATABASE_URL is not set"
        )

    init_db()

    threading.Thread(
        target=run_http_server,
        daemon=True
    ).start()

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
            "addadmin",
            add_admin_command
        )
    )

    application.add_handler(
        CommandHandler(
            "removeadmin",
            remove_admin_command
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
            "activate",
            activate
        )
    )

    application.add_handler(
        CommandHandler(
            "deactivate",
            deactivate
        )
    )

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print(
        "BIZDE.KZ bot started successfully"
    )

    application.run_polling(
        drop_pending_updates=True
    )


# =========================================================
# START
# =========================================================

if __name__ == "__main__":
    main()
