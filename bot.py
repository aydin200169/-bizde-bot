import os
import json
import hmac
import hashlib
import secrets
import threading
import urllib.parse
from datetime import datetime, timedelta

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
PORT = int(os.environ.get("PORT", "10000"))

ALLOWED_ORIGIN = "https://aydin200169.github.io"

QR_LIFETIME_SECONDS = 60


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

        # -----------------------------------------------------
        # USERS
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # PARTNERS
        # -----------------------------------------------------

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

        # -----------------------------------------------------
        # TRANSACTIONS
        # -----------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT NOT NULL,
                partner_id INTEGER,
                partner_name TEXT DEFAULT '',
                receipt_amount NUMERIC(12,2) NOT NULL,
                discount_percent NUMERIC(12,2) NOT NULL,
                savings NUMERIC(12,2) NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -----------------------------------------------------
        # ADMINS
        # -----------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS admins (
                id SERIAL PRIMARY KEY,
                telegram_id BIGINT UNIQUE NOT NULL,
                added_by BIGINT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # -----------------------------------------------------
        # MIGRATIONS USERS
        # -----------------------------------------------------

        migrations = [
            ("users", "updated_at", "TIMESTAMP DEFAULT CURRENT_TIMESTAMP"),
            ("users", "member_code", "TEXT"),
            ("users", "subscription_active", "BOOLEAN DEFAULT FALSE"),
            ("users", "total_savings", "NUMERIC(12,2) DEFAULT 0"),
            ("users", "terms_accepted", "BOOLEAN DEFAULT FALSE"),
            ("users", "language", "TEXT DEFAULT 'ru'"),
            ("users", "phone", "TEXT DEFAULT ''"),
            ("users", "username", "TEXT DEFAULT ''"),
        ]

        for table, column, definition in migrations:
            cur.execute(
                f"""
                ALTER TABLE {table}
                ADD COLUMN IF NOT EXISTS {column} {definition}
                """
            )

        # -----------------------------------------------------
        # MIGRATIONS PARTNERS
        #
        # telegram_id = Telegram ID сотрудника партнёра
        # -----------------------------------------------------

        cur.execute("""
            ALTER TABLE partners
            ADD COLUMN IF NOT EXISTS telegram_id BIGINT
        """)

        # Уникальность Telegram ID партнёра.
        # NULL разрешён для партнёров, которым ещё
        # не назначен аккаунт.
        cur.execute("""
            CREATE UNIQUE INDEX IF NOT EXISTS
            idx_partners_telegram_id
            ON partners(telegram_id)
            WHERE telegram_id IS NOT NULL
        """)

        # -----------------------------------------------------
        # QR TOKENS
        # -----------------------------------------------------

        cur.execute("""
            CREATE TABLE IF NOT EXISTS qr_tokens (
                id SERIAL PRIMARY KEY,
                token TEXT UNIQUE NOT NULL,
                user_telegram_id BIGINT NOT NULL,
                partner_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                expires_at TIMESTAMP NOT NULL,
                used BOOLEAN DEFAULT FALSE,
                used_at TIMESTAMP,
                used_by_partner BIGINT
            )
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_qr_tokens_token
            ON qr_tokens(token)
        """)

        cur.execute("""
            CREATE INDEX IF NOT EXISTS
            idx_qr_tokens_user
            ON qr_tokens(user_telegram_id)
        """)

        # -----------------------------------------------------
        # OWNER
        # -----------------------------------------------------

        cur.execute("""
            INSERT INTO admins (telegram_id, added_by)
            VALUES (%s, %s)
            ON CONFLICT (telegram_id) DO NOTHING
        """, (
            OWNER_ID,
            OWNER_ID
        ))

        # -----------------------------------------------------
        # DEMO PARTNERS
        # -----------------------------------------------------

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
                    "",
                    True
                ),
                (
                    "VERO Café",
                    "cafe",
                    "Кофе и десерты",
                    20,
                    "Привилегия для участников BIZDE.KZ",
                    "",
                    True
                ),
                (
                    "FITROOM",
                    "sport",
                    "Спорт и тренировки",
                    15,
                    "Привилегия для участников BIZDE.KZ",
                    "",
                    True
                ),
                (
                    "Beauty Room",
                    "beauty",
                    "Красота и уход",
                    15,
                    "Привилегия для участников BIZDE.KZ",
                    "",
                    True
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
                        conditions,
                        photo_url,
                        active
                    )
                    VALUES (%s, %s, %s, %s, %s, %s, %s)
                """, partner)

        conn.commit()

    finally:
        conn.close()


# =========================================================
# ADMINS
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
        """, (
            telegram_id,
            added_by
        ))

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
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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
# PARTNER ACCOUNTS
# =========================================================

def get_partner_by_telegram_id(telegram_id):

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM partners
            WHERE telegram_id = %s
              AND active = TRUE
            LIMIT 1
        """, (telegram_id,))

        return cur.fetchone()

    finally:
        conn.close()


def get_partner_by_id(partner_id):

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM partners
            WHERE id = %s
        """, (partner_id,))

        return cur.fetchone()

    finally:
        conn.close()


def assign_partner( partner_id, telegram_id ):

    partner_id = int(partner_id)
    telegram_id = int(telegram_id)

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        # Проверяем партнёра
        cur.execute("""
            SELECT *
            FROM partners
            WHERE id = %s
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError("Партнёр не найден")

        # Проверяем, не привязан ли этот Telegram
        # к другому партнёру
        cur.execute("""
            SELECT id, name
            FROM partners
            WHERE telegram_id = %s
              AND id <> %s
        """, (
            telegram_id,
            partner_id
        ))

        another = cur.fetchone()

        if another:
            raise ValueError(
                "Этот Telegram ID уже привязан к другому партнёру"
            )

        cur.execute("""
            UPDATE partners
            SET telegram_id = %s
            WHERE id = %s
            RETURNING *
        """, (
            telegram_id,
            partner_id
        ))

        result = cur.fetchone()

        conn.commit()

        return result

    finally:
        conn.close()


def unassign_partner(partner_id):

    partner_id = int(partner_id)

    conn = get_db()

    try:
        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            UPDATE partners
            SET telegram_id = NULL
            WHERE id = %s
            RETURNING *
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError("Партнёр не найден")

        conn.commit()

        return partner

    finally:
        conn.close()


def get_role(telegram_id):

    telegram_id = int(telegram_id)

    if telegram_id == OWNER_ID:
        return "owner"

    if is_admin(telegram_id):
        return "admin"

    partner = get_partner_by_telegram_id(
        telegram_id
    )

    if partner:
        return "partner"

    return "user"


# =========================================================
# TELEGRAM AUTH
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

        received_hash = parsed.pop(
            "hash",
            None
        )

        if not received_hash:
            return None

        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value in sorted(
                parsed.items()
            )
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

        # -------------------------------------------------
        # Проверяем auth_date.
        # Старый init_data не принимаем.
        # -------------------------------------------------

        auth_date = parsed.get("auth_date")

        if auth_date:

            try:

                auth_timestamp = int(
                    auth_date
                )

                current_timestamp = int(
                    datetime.utcnow().timestamp()
                )

                # 24 часа
                if (
                    current_timestamp
                    - auth_timestamp
                    > 86400
                ):
                    return None

            except Exception:
                return None

        user_json = parsed.get("user")

        if not user_json:
            return None

        return json.loads(
            user_json
        )

    except Exception:
        return None


def get_admin_from_init_data(init_data):

    tg_user = validate_telegram_init_data(
        init_data
    )

    if not tg_user:
        return None

    telegram_id = tg_user.get("id")

    if not telegram_id:
        return None

    if not is_admin(telegram_id):
        return None

    return tg_user


def get_partner_from_init_data(init_data):

    tg_user = validate_telegram_init_data(
        init_data
    )

    if not tg_user:
        return None

    telegram_id = tg_user.get("id")

    if not telegram_id:
        return None

    partner = get_partner_by_telegram_id(
        telegram_id
    )

    if not partner:
        return None

    return {
        "telegram_user": tg_user,
        "partner": partner
    }


# =========================================================
# USERS
# =========================================================

def generate_member_code():

    return (
        "BIZDE-"
        + secrets.token_hex(4).upper()
    )


def get_user(telegram_id):

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        existing = cur.fetchone()

        if existing:

            cur.execute("""
                UPDATE users SET
                    name = COALESCE(
                        NULLIF(%s, ''),
                        name
                    ),
                    username = COALESCE(
                        NULLIF(%s, ''),
                        username
                    ),
                    phone = COALESCE(
                        NULLIF(%s, ''),
                        phone
                    ),
                    language = COALESCE(
                        NULLIF(%s, ''),
                        language
                    ),
                    terms_accepted =
                        CASE
                            WHEN %s = TRUE
                            THEN TRUE
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


def get_all_users_admin():

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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
            ORDER BY registered_at DESC
        """)

        return cur.fetchall()

    finally:
        conn.close()


def update_language(
    telegram_id,
    language
):

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


def set_subscription(
    telegram_id,
    active
):

    conn = get_db()

    try:

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

        conn.commit()

        return cur.rowcount > 0

    finally:
        conn.close()


# =========================================================
# PARTNERS
# =========================================================

def get_partners(category=None):

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


def get_all_partners_admin():

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM partners
            ORDER BY id
        """)

        return cur.fetchall()

    finally:
        conn.close()


def create_partner(data):

    name = str(
        data.get("name", "")
    ).strip()

    if not name:
        raise ValueError(
            "Название партнёра обязательно"
        )

    category = str(
        data.get("category", "")
    ).strip()

    description = str(
        data.get("description", "")
    ).strip()

    conditions = str(
        data.get("conditions", "")
    ).strip()

    photo_url = str(
        data.get("photo_url", "")
    ).strip()

    try:
        discount = float(
            data.get(
                "discount_percent",
                0
            )
        )
    except Exception:
        raise ValueError(
            "Скидка должна быть числом"
        )

    if discount < 0 or discount > 100:
        raise ValueError(
            "Скидка должна быть от 0 до 100"
        )

    active = bool(
        data.get(
            "active",
            True
        )
    )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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
            name,
            category,
            description,
            discount,
            conditions,
            photo_url,
            active
        ))

        partner = cur.fetchone()

        conn.commit()

        return partner

    finally:
        conn.close()


def update_partner(
    partner_id,
    data
):

    try:
        partner_id = int(partner_id)
    except Exception:
        raise ValueError(
            "Неверный ID партнёра"
        )

    name = str(
        data.get("name", "")
    ).strip()

    if not name:
        raise ValueError(
            "Название партнёра обязательно"
        )

    category = str(
        data.get("category", "")
    ).strip()

    description = str(
        data.get("description", "")
    ).strip()

    conditions = str(
        data.get("conditions", "")
    ).strip()

    photo_url = str(
        data.get("photo_url", "")
    ).strip()

    try:

        discount = float(
            data.get(
                "discount_percent",
                0
            )
        )

    except Exception:

        raise ValueError(
            "Скидка должна быть числом"
        )

    if discount < 0 or discount > 100:
        raise ValueError(
            "Скидка должна быть от 0 до 100"
        )

    active = bool(
        data.get(
            "active",
            True
        )
    )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            UPDATE partners
            SET
                name = %s,
                category = %s,
                description = %s,
                discount_percent = %s,
                conditions = %s,
                photo_url = %s,
                active = %s
            WHERE id = %s
            RETURNING *
        """, (
            name,
            category,
            description,
            discount,
            conditions,
            photo_url,
            active,
            partner_id
        ))

        partner = cur.fetchone()

        if not partner:
            raise ValueError(
                "Партнёр не найден"
            )

        conn.commit()

        return partner

    finally:
        conn.close()


def toggle_partner(
    partner_id,
    active
):

    try:
        partner_id = int(partner_id)
    except Exception:
        raise ValueError(
            "Неверный ID партнёра"
        )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            UPDATE partners
            SET active = %s
            WHERE id = %s
            RETURNING *
        """, (
            bool(active),
            partner_id
        ))

        partner = cur.fetchone()

        if not partner:
            raise ValueError(
                "Партнёр не найден"
            )

        conn.commit()

        return partner

    finally:
        conn.close()


def delete_partner(partner_id):

    try:
        partner_id = int(partner_id)
    except Exception:
        raise ValueError(
            "Неверный ID партнёра"
        )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        # Мягкое удаление
        cur.execute("""
            UPDATE partners
            SET active = FALSE
            WHERE id = %s
            RETURNING *
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError(
                "Партнёр не найден"
            )

        conn.commit()

        return partner

    finally:
        conn.close()


# =========================================================
# TRANSACTIONS
# =========================================================

def get_history(telegram_id):

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

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


def create_transaction(
    telegram_id,
    partner_id,
    receipt_amount
):

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM users
            WHERE telegram_id = %s
        """, (telegram_id,))

        user = cur.fetchone()

        if not user:
            raise ValueError(
                "Пользователь не найден"
            )

        if not user["subscription_active"]:
            raise ValueError(
                "Подписка не активна"
            )

        cur.execute("""
            SELECT *
            FROM partners
            WHERE id = %s
              AND active = TRUE
        """, (partner_id,))

        partner = cur.fetchone()

        if not partner:
            raise ValueError(
                "Партнёр не найден"
            )

        amount = float(
            receipt_amount
        )

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
            VALUES
            (
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
                    COALESCE(
                        total_savings,
                        0
                    ) + %s,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE telegram_id = %s
            RETURNING total_savings
        """, (
            savings,
            telegram_id
        ))

        total_savings = cur.fetchone()[
            "total_savings"
        ]

        conn.commit()

        return {
            "partner_name":
                partner["name"],

            "receipt_amount":
                amount,

            "discount_percent":
                discount,

            "savings":
                savings,

            "total_savings":
                float(total_savings)
        }

    finally:
        conn.close()


# =========================================================
# OLD PARTNER OPERATION
# =========================================================

def partner_use_offer(
    member_code,
    partner_id,
    receipt_amount,
    partner_telegram_id
):

    partner = get_partner_by_telegram_id(
        partner_telegram_id
    )

    if not partner:
        raise ValueError(
            "Нет доступа партнёра"
        )

    if int(partner["id"]) != int(partner_id):
        raise ValueError(
            "Этот партнёр не может проводить операцию"
        )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT *
            FROM users
            WHERE member_code = %s
        """, (member_code,))

        user = cur.fetchone()

        if not user:
            raise ValueError(
                "Участник не найден"
            )

        if not user["subscription_active"]:
            raise ValueError(
                "Подписка участника не активна"
            )

        amount = float(
            receipt_amount
        )

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
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
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
                    COALESCE(
                        total_savings,
                        0
                    ) + %s,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE telegram_id = %s
            RETURNING total_savings
        """, (
            savings,
            user["telegram_id"]
        ))

        total_savings = cur.fetchone()[
            "total_savings"
        ]

        conn.commit()

        return {
            "user_name":
                user["name"],

            "member_code":
                user["member_code"],

            "partner_name":
                partner["name"],

            "receipt_amount":
                amount,

            "discount_percent":
                discount,

            "savings":
                savings,

            "total_savings":
                float(total_savings)
        }

    finally:
        conn.close()


# =========================================================
# MEMBER VERIFY
# =========================================================

def verify_member(member_code):

    user = get_user_by_member_code(
        member_code
    )

    if not user:
        return None

    return {
        "name":
            user["name"],

        "member_code":
            user["member_code"],

        "subscription_active":
            bool(
                user["subscription_active"]
            ),

        "telegram_id":
            user["telegram_id"],

        "total_savings":
            float(
                user["total_savings"] or 0
            )
    }


# =========================================================
# QR
# =========================================================

def create_qr_token(
    user_telegram_id,
    partner_id=None
):

    user = get_user(
        user_telegram_id
    )

    if not user:
        raise ValueError(
            "Пользователь не найден"
        )

    if not user["subscription_active"]:
        raise ValueError(
            "Подписка не активна"
        )

    if partner_id is not None:

        partner = get_partner_by_id(
            int(partner_id)
        )

        if not partner:
            raise ValueError(
                "Партнёр не найден"
            )

        if not partner["active"]:
            raise ValueError(
                "Партнёр сейчас недоступен"
            )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        # Старые неиспользованные QR больше
        # не принимаются.
        cur.execute("""
            UPDATE qr_tokens
            SET used = TRUE,
                used_at = CURRENT_TIMESTAMP
            WHERE user_telegram_id = %s
              AND used = FALSE
        """, (
            user_telegram_id,
        ))

        token = secrets.token_urlsafe(32)

        expires_at = (
            datetime.utcnow()
            + timedelta(
                seconds=QR_LIFETIME_SECONDS
            )
        )

        cur.execute("""
            INSERT INTO qr_tokens
            (
                token,
                user_telegram_id,
                partner_id,
                expires_at
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s
            )
            RETURNING *
        """, (
            token,
            user_telegram_id,
            partner_id,
            expires_at
        ))

        qr = cur.fetchone()

        conn.commit()

        return {
            "token":
                qr["token"],

            "expires_at":
                qr["expires_at"],

            "expires_in":
                QR_LIFETIME_SECONDS,

            "partner_id":
                qr["partner_id"]
        }

    finally:
        conn.close()


def verify_qr_token(
    token,
    partner_telegram_id
):

    partner = get_partner_by_telegram_id(
        partner_telegram_id
    )

    if not partner:
        raise ValueError(
            "Нет доступа партнёра"
        )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        cur.execute("""
            SELECT
                q.*,
                u.name AS user_name,
                u.member_code,
                u.subscription_active,
                u.total_savings,
                p.name AS partner_name,
                p.discount_percent,
                p.active AS partner_active
            FROM qr_tokens q
            JOIN users u
                ON u.telegram_id =
                   q.user_telegram_id
            LEFT JOIN partners p
                ON p.id = q.partner_id
            WHERE q.token = %s
            FOR UPDATE
        """, (token,))

        qr = cur.fetchone()

        if not qr:
            raise ValueError(
                "QR-код не найден"
            )

        if qr["used"]:
            raise ValueError(
                "QR-код уже использован"
            )

        if qr["expires_at"] < datetime.utcnow():
            raise ValueError(
                "QR-код истёк"
            )

        if not qr["subscription_active"]:
            raise ValueError(
                "Подписка участника не активна"
            )

        # Если QR привязан к конкретному партнёру
        if qr["partner_id"] is not None:

            if int(qr["partner_id"]) != int(
                partner["id"]
            ):
                raise ValueError(
                    "Этот QR предназначен для другого партнёра"
                )

        conn.commit()

        return {
            "valid": True,

            "user": {
                "name":
                    qr["user_name"],

                "member_code":
                    qr["member_code"],

                "subscription_active":
                    bool(
                        qr["subscription_active"]
                    ),

                "total_savings":
                    float(
                        qr["total_savings"] or 0
                    )
            },

            "partner": {
                "id":
                    partner["id"],

                "name":
                    partner["name"],

                "discount_percent":
                    float(
                        partner[
                            "discount_percent"
                        ] or 0
                    )
            },

            "expires_at":
                qr["expires_at"]

        }

    finally:
        conn.close()


def confirm_qr_transaction(
    token,
    partner_telegram_id,
    receipt_amount
):

    partner = get_partner_by_telegram_id(
        partner_telegram_id
    )

    if not partner:
        raise ValueError(
            "Нет доступа партнёра"
        )

    amount = float(
        receipt_amount
    )

    if amount <= 0:
        raise ValueError(
            "Сумма должна быть больше 0"
        )

    conn = get_db()

    try:

        cur = conn.cursor(
            cursor_factory=RealDictCursor
        )

        # Блокируем QR до окончания операции.
        # Это защищает от двойного использования.
        cur.execute("""
            SELECT
                q.*,
                u.name AS user_name,
                u.member_code,
                u.subscription_active,
                u.total_savings
            FROM qr_tokens q
            JOIN users u
                ON u.telegram_id =
                   q.user_telegram_id
            WHERE q.token = %s
            FOR UPDATE
        """, (token,))

        qr = cur.fetchone()

        if not qr:
            raise ValueError(
                "QR-код не найден"
            )

        if qr["used"]:
            raise ValueError(
                "QR-код уже использован"
            )

        if qr["expires_at"] < datetime.utcnow():
            raise ValueError(
                "QR-код истёк"
            )

        if not qr["subscription_active"]:
            raise ValueError(
                "Подписка участника не активна"
            )

        # Если QR был создан для конкретного
        # партнёра, проверяем совпадение.
        if qr["partner_id"] is not None:

            if int(qr["partner_id"]) != int(
                partner["id"]
            ):
                raise ValueError(
                    "QR предназначен для другого партнёра"
                )

        discount = float(
            partner["discount_percent"] or 0
        )

        savings = round(
            amount * discount / 100,
            2
        )

        # Создаём транзакцию
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
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
        """, (
            qr["user_telegram_id"],
            partner["id"],
            partner["name"],
            amount,
            discount,
            savings
        ))

        # Обновляем общую экономию
        cur.execute("""
            UPDATE users
            SET
                total_savings =
                    COALESCE(
                        total_savings,
                        0
                    ) + %s,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE telegram_id = %s
            RETURNING total_savings
        """, (
            savings,
            qr["user_telegram_id"]
        ))

        total_savings = cur.fetchone()[
            "total_savings"
        ]

        # Самое важное:
        # QR становится использованным
        # только после успешной операции.
        cur.execute("""
            UPDATE qr_tokens
            SET
                used = TRUE,
                used_at = CURRENT_TIMESTAMP,
                used_by_partner = %s
            WHERE id = %s
        """, (
            partner_telegram_id,
            qr["id"]
        ))

        conn.commit()

        return {
            "success": True,

            "user_name":
                qr["user_name"],

            "member_code":
                qr["member_code"],

            "partner_name":
                partner["name"],

            "receipt_amount":
                amount,

            "discount_percent":
                discount,

            "savings":
                savings,

            "total_savings":
                float(
                    total_savings
                )
        }

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()


# =========================================================
# JSON
# =========================================================

def json_response(
    handler,
    data,
    status=200
):

    body = json.dumps(
        data,
        ensure_ascii=False,
        default=str
    ).encode("utf-8")

    handler.send_response(
        status
    )

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

    handler.wfile.write(
        body
    )


def read_json(handler):

    length = int(
        handler.headers.get(
            "Content-Length",
            0
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


# =========================================================
# HTTP SERVER
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

    # =====================================================
    # OPTIONS
    # =====================================================

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

    # =====================================================
    # GET
    # =====================================================

    def do_GET(self):

        try:

            parsed = urlparse(
                self.path
            )

            path = parsed.path

            params = parse_qs(
                parsed.query
            )

            # -------------------------------------------------
            # HEALTH
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
            #
            # Теперь рекомендуется использовать init_data.
            # Если telegram_id передан, показываем только
            # этого пользователя.
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
                            "user":
                                dict(user)
                                if user
                                else None
                        }
                    )

                    return

                # Совместимость со старым frontend
                telegram_id = params.get(
                    "telegram_id",
                    [None]
                )[0]

                if not telegram_id:

                    json_response(
                        self,
                        {
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
                        "user":
                            dict(user)
                            if user
                            else None
                    }
                )

                return

            # -------------------------------------------------
            # ROLE
            # -------------------------------------------------

            if path == "/api/role":

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
                            "error":
                                "Invalid init_data"
                        },
                        401
                    )

                    return

                telegram_id = tg_user["id"]

                role = get_role(
                    telegram_id
                )

                partner = None

                if role == "partner":

                    partner = get_partner_by_telegram_id(
                        telegram_id
                    )

                json_response(
                    self,
                    {
                        "success": True,
                        "role": role,
                        "telegram_id":
                            telegram_id,
                        "partner":
                            dict(partner)
                            if partner
                            else None
                    }
                )

                return

            # -------------------------------------------------
            # PARTNERS
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
                        "partners":
                            partners
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
                        "history":
                            history
                    }
                )

                return

            # -------------------------------------------------
            # PARTNER PROFILE
            # -------------------------------------------------

            if path == "/api/partner/me":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                auth = get_partner_from_init_data(
                    init_data
                )

                if not auth:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа партнёра"
                        },
                        403
                    )

                    return

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(
                                auth["partner"]
                            )
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

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partners = get_all_partners_admin()

                json_response(
                    self,
                    {
                        "success": True,
                        "partners":
                            partners
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN USERS
            # -------------------------------------------------

            if path == "/api/admin/users":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                users = get_all_users_admin()

                json_response(
                    self,
                    {
                        "success": True,
                        "users":
                            users
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN LIST
            # -------------------------------------------------

            if path == "/api/admin/admins":

                init_data = params.get(
                    "init_data",
                    [""]
                )[0]

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                admins = get_admins()

                json_response(
                    self,
                    {
                        "success": True,
                        "admins":
                            admins
                    }
                )

                return

            # -------------------------------------------------
            # NOT FOUND
            # -------------------------------------------------

            json_response(
                self,
                {
                    "error":
                        "Not found"
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
                    "error":
                        str(e)
                },
                500
            )

    # =====================================================
    # POST
    # =====================================================

    def do_POST(self):

        try:

            path = urlparse(
                self.path
            ).path

            data = read_json(
                self
            )

            # -------------------------------------------------
            # AUTH
            # -------------------------------------------------

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
                            "error":
                                "Invalid Telegram init data"
                        },
                        401
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

                    phone=data.get(
                        "phone",
                        ""
                    ),

                    language=data.get(
                        "language",
                        "ru"
                    ),

                    terms_accepted=bool(
                        data.get(
                            "terms_accepted",
                            False
                        )
                    )
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "user":
                            dict(user),
                        "role":
                            get_role(
                                tg_user["id"]
                            )
                    }
                )

                return

            # -------------------------------------------------
            # LANGUAGE
            # -------------------------------------------------

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
                            "error":
                                "Invalid init data"
                        },
                        401
                    )

                    return

                update_language(
                    tg_user["id"],
                    data.get(
                        "language",
                        "ru"
                    )
                )

                json_response(
                    self,
                    {
                        "success":
                            True
                    }
                )

                return

            # -------------------------------------------------
            # CREATE QR
            # -------------------------------------------------

            if path == "/api/qr/create":

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
                            "error":
                                "Invalid init_data"
                        },
                        401
                    )

                    return

                partner_id = data.get(
                    "partner_id"
                )

                if partner_id is not None:
                    partner_id = int(
                        partner_id
                    )

                result = create_qr_token(
                    tg_user["id"],
                    partner_id
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "qr":
                            result
                    }
                )

                return

            # -------------------------------------------------
            # VERIFY QR
            # -------------------------------------------------

            if path == "/api/qr/verify":

                init_data = data.get(
                    "init_data",
                    ""
                )

                auth = get_partner_from_init_data(
                    init_data
                )

                if not auth:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа партнёра"
                        },
                        403
                    )

                    return

                token = str(
                    data.get(
                        "token",
                        ""
                    )
                ).strip()

                if not token:

                    json_response(
                        self,
                        {
                            "error":
                                "QR token required"
                        },
                        400
                    )

                    return

                result = verify_qr_token(
                    token,
                    auth["partner"]["telegram_id"]
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "result":
                            result
                    }
                )

                return

            # -------------------------------------------------
            # CONFIRM QR
            # -------------------------------------------------

            if path == "/api/qr/confirm":

                init_data = data.get(
                    "init_data",
                    ""
                )

                auth = get_partner_from_init_data(
                    init_data
                )

                if not auth:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа партнёра"
                        },
                        403
                    )

                    return

                token = str(
                    data.get(
                        "token",
                        ""
                    )
                ).strip()

                if not token:

                    json_response(
                        self,
                        {
                            "error":
                                "QR token required"
                        },
                        400
                    )

                    return

                result = confirm_qr_transaction(
                    token,
                    auth["partner"]["telegram_id"],
                    float(
                        data["receipt_amount"]
                    )
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "result":
                            result
                    }
                )

                return

            # -------------------------------------------------
            # VERIFY MEMBER
            #
            # Теперь защищено авторизацией партнёра.
            # -------------------------------------------------

            if path == "/api/verify-member":

                init_data = data.get(
                    "init_data",
                    ""
                )

                auth = get_partner_from_init_data(
                    init_data
                )

                if not auth:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа партнёра"
                        },
                        403
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
                        "member":
                            result,
                        "user":
                            result,
                        "partner":
                            {
                                "id":
                                    auth["partner"]["id"],
                                "name":
                                    auth["partner"]["name"],
                                "discount_percent":
                                    float(
                                        auth[
                                            "partner"
                                        ][
                                            "discount_percent"
                                        ] or 0
                                    )
                            }
                    }
                )

                return

            # -------------------------------------------------
            # USE OFFER
            # -------------------------------------------------

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
                            "error":
                                "Invalid init_data"
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
                        "result":
                            result
                    }
                )

                return

            # -------------------------------------------------
            # OLD PARTNER USE OFFER
            #
            # Оставляем для совместимости,
            # но теперь он требует init_data партнёра.
            # -------------------------------------------------

            if path == "/api/partner-use-offer":

                init_data = data.get(
                    "init_data",
                    ""
                )

                auth = get_partner_from_init_data(
                    init_data
                )

                if not auth:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа партнёра"
                        },
                        403
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
                    ),

                    partner_telegram_id=
                        auth[
                            "partner"
                        ][
                            "telegram_id"
                        ]
                )

                response = {
                    "success": True,
                    "result":
                        result
                }

                response.update(
                    result
                )

                response["partner"] = {
                    "name":
                        result[
                            "partner_name"
                        ]
                }

                json_response(
                    self,
                    response
                )

                return

            # -------------------------------------------------
            # ADMIN CREATE PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = create_partner(
                    data
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN UPDATE PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners/update":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = update_partner(
                    data.get("id"),
                    data
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN DELETE PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners/delete":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = delete_partner(
                    data.get("id")
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN TOGGLE PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners/toggle":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = toggle_partner(
                    data.get("id"),
                    data.get(
                        "active",
                        False
                    )
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN ASSIGN PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners/assign":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = assign_partner(
                    data["partner_id"],
                    data["telegram_id"]
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN UNASSIGN PARTNER
            # -------------------------------------------------

            if path == "/api/admin/partners/unassign":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                partner = unassign_partner(
                    data["partner_id"]
                )

                json_response(
                    self,
                    {
                        "success": True,
                        "partner":
                            dict(partner)
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN SUBSCRIPTION
            # -------------------------------------------------

            if path == "/api/admin/users/subscription":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                telegram_id = int(
                    data["telegram_id"]
                )

                active = bool(
                    data.get(
                        "active",
                        False
                    )
                )

                updated = set_subscription(
                    telegram_id,
                    active
                )

                if not updated:

                    json_response(
                        self,
                        {
                            "success": False,
                            "error":
                                "Пользователь не найден"
                        },
                        404
                    )

                    return

                json_response(
                    self,
                    {
                        "success": True,
                        "subscription_active":
                            active
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN ADD ADMIN
            # -------------------------------------------------

            if path == "/api/admin/admins/add":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                # Только OWNER может добавлять
                # новых админов через API.
                if not is_owner(
                    admin["id"]
                ):

                    json_response(
                        self,
                        {
                            "error":
                                "Только владелец может добавлять админов"
                        },
                        403
                    )

                    return

                new_admin_id = int(
                    data["telegram_id"]
                )

                added = add_admin(
                    new_admin_id,
                    admin["id"]
                )

                json_response(
                    self,
                    {
                        "success":
                            True,
                        "added":
                            added
                    }
                )

                return

            # -------------------------------------------------
            # ADMIN REMOVE ADMIN
            # -------------------------------------------------

            if path == "/api/admin/admins/remove":

                init_data = data.get(
                    "init_data",
                    ""
                )

                admin = get_admin_from_init_data(
                    init_data
                )

                if not admin:

                    json_response(
                        self,
                        {
                            "error":
                                "Нет доступа"
                        },
                        403
                    )

                    return

                if not is_owner(
                    admin["id"]
                ):

                    json_response(
                        self,
                        {
                            "error":
                                "Только владелец может удалять админов"
                        },
                        403
                    )

                    return

                admin_id = int(
                    data["telegram_id"]
                )

                if admin_id == OWNER_ID:

                    json_response(
                        self,
                        {
                            "error":
                                "Главного владельца удалить нельзя"
                        },
                        400
                    )

                    return

                removed = remove_admin(
                    admin_id
                )

                json_response(
                    self,
                    {
                        "success":
                            True,
                        "removed":
                            removed
                    }
                )

                return

            # -------------------------------------------------
            # NOT FOUND
            # -------------------------------------------------

            json_response(
                self,
                {
                    "error":
                        "Not found"
                },
                404
            )

        except ValueError as e:

            json_response(
                self,
                {
                    "success": False,
                    "error":
                        str(e)
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
                    "error":
                        str(e)
                },
                500
            )


# =========================================================
# TELEGRAM COMMANDS
# =========================================================

async def start(
    update,
    context
):

    telegram_id = update.effective_user.id

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

    # Если это партнёр
    if get_role(telegram_id) == "partner":

        keyboard.append([
            InlineKeyboardButton(
                "🏪 Кабинет партнёра",
                web_app=WebAppInfo(
                    url=WEB_APP_URL
                )
            )
        ])

    # Если админ
    if is_admin(telegram_id):

        keyboard.append([
            InlineKeyboardButton(
                "⚙️ Админка",
                web_app=WebAppInfo(
                    url=
                        WEB_APP_URL.rstrip("/")
                        + "/admin.html"
                )
            )
        ])

    await update.message.reply_text(

        "Добро пожаловать в BIZDE.KZ 🇰🇿\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",

        reply_markup=InlineKeyboardMarkup(
            keyboard
        )
    )


# =========================================================
# PARTNER COMMAND
# =========================================================

async def partner_command(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    partner = get_partner_by_telegram_id(
        telegram_id
    )

    if not partner:

        await update.message.reply_text(
            "⛔ Этот Telegram аккаунт "
            "не привязан к партнёру BIZDE.KZ."
        )

        return

    await update.message.reply_text(

        "🏪 Кабинет партнёра BIZDE.KZ\n\n"
        f"Партнёр: {partner['name']}\n"
        f"Привилегия: "
        f"{float(partner['discount_percent'] or 0):g}%",

        reply_markup=InlineKeyboardMarkup([
            [
                InlineKeyboardButton(
                    "🏪 Открыть кабинет",
                    web_app=WebAppInfo(
                        url=WEB_APP_URL
                    )
                )
            ]
        ])
    )


# =========================================================
# CALLBACKS
# =========================================================

async def button_handler(
    update,
    context
):

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

            "Все привилегии доступны "
            "внутри BIZDE.KZ 👇",

            reply_markup=InlineKeyboardMarkup(
                keyboard
            )
        )

    elif query.data == "subscription":

        telegram_id = (
            query.from_user.id
        )

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
                else
                "Не активна ❌"
            )

            text = (

                "🎟 Моя привилегия\n\n"

                f"Статус: {status}\n"

                f"Код: "
                f"{user['member_code']}"
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

async def admin_command(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

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

        "🔐 Панель администратора "
        "BIZDE.KZ\n\n"
        "Управление партнёрами, "
        "пользователями и доступами.",

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

    telegram_id = (
        update.effective_user.id
    )

    if not is_owner(
        telegram_id
    ):

        await update.message.reply_text(
            "⛔ Только главный владелец "
            "может добавлять администраторов."
        )

        return

    if not context.args:

        await update.message.reply_text(

            "Используй:\n\n"
            "/addadmin TELEGRAM_ID\n\n"
            "Например:\n"
            "/addadmin 123456789"
        )

        return

    try:

        new_admin_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ Telegram ID должен "
            "состоять только из цифр."
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
            f"Telegram ID: {new_admin_id}\n\n"
            "Теперь он может использовать /admin."
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

    telegram_id = (
        update.effective_user.id
    )

    if not is_owner(
        telegram_id
    ):

        await update.message.reply_text(

            "⛔ Только главный владелец "
            "может удалять администраторов."
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

            "❌ Telegram ID должен "
            "состоять только из цифр."
        )

        return

    if admin_id == OWNER_ID:

        await update.message.reply_text(

            "❌ Главного владельца "
            "удалить нельзя."
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
            "ℹ️ Такой администратор "
            "не найден."
        )


# =========================================================
# ADMINS
# =========================================================

async def admins_command(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

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

        role = (
            "👑 Главный владелец"
            if admin_id == OWNER_ID
            else
            "🛡 Администратор"
        )

        text += (
            f"{index}. {role}\n"
            f"ID: {admin_id}\n\n"
        )

    await update.message.reply_text(
        text
    )


# =========================================================
# SET PARTNER
# =========================================================

async def set_partner_command(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return

    if len(context.args) < 2:

        await update.message.reply_text(

            "Используй:\n\n"
            "/setpartner PARTNER_ID TELEGRAM_ID\n\n"
            "Например:\n"
            "/setpartner 1 123456789"
        )

        return

    try:

        partner_id = int(
            context.args[0]
        )

        partner_telegram_id = int(
            context.args[1]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ ID должны состоять только из цифр."
        )

        return

    try:

        partner = assign_partner(
            partner_id,
            partner_telegram_id
        )

        await update.message.reply_text(

            "✅ Партнёр успешно привязан.\n\n"

            f"Партнёр: {partner['name']}\n"
            f"ID партнёра: {partner['id']}\n"
            f"Telegram ID: "
            f"{partner['telegram_id']}\n\n"

            "Теперь этот Telegram аккаунт "
            "может войти в кабинет партнёра."
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )


# =========================================================
# UNSET PARTNER
# =========================================================

async def unset_partner_command(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

        await update.message.reply_text(
            "⛔ Нет доступа."
        )

        return

    if not context.args:

        await update.message.reply_text(
            "Используй:\n\n"
            "/unsetpartner PARTNER_ID"
        )

        return

    try:

        partner_id = int(
            context.args[0]
        )

    except ValueError:

        await update.message.reply_text(
            "❌ ID должен быть числом."
        )

        return

    try:

        partner = unassign_partner(
            partner_id
        )

        await update.message.reply_text(

            "✅ Доступ партнёра отключён.\n\n"
            f"Партнёр: {partner['name']}"
        )

    except Exception as e:

        await update.message.reply_text(
            f"❌ {e}"
        )


# =========================================================
# ACTIVATE
# =========================================================

async def activate(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

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
            "❌ Telegram ID должен "
            "состоять только из цифр."
        )

        return

    updated = set_subscription(
        user_id,
        True
    )

    if updated:

        await update.message.reply_text(
            "Подписка активирована ✅"
        )

    else:

        await update.message.reply_text(
            "❌ Пользователь не найден."
        )


# =========================================================
# DEACTIVATE
# =========================================================

async def deactivate(
    update,
    context
):

    telegram_id = (
        update.effective_user.id
    )

    if not is_admin(
        telegram_id
    ):

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
            "❌ Telegram ID должен "
            "состоять только из цифр."
        )

        return

    updated = set_subscription(
        user_id,
        False
    )

    if updated:

        await update.message.reply_text(
            "Подписка отключена ❌"
        )

    else:

        await update.message.reply_text(
            "❌ Пользователь не найден."
        )


# =========================================================
# RENDER HTTP
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

    # -----------------------------------------------------
    # COMMANDS
    # -----------------------------------------------------

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
            "setpartner",
            set_partner_command
        )
    )

    application.add_handler(
        CommandHandler(
            "unsetpartner",
            unset_partner_command
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

    # -----------------------------------------------------
    # CALLBACKS
    # -----------------------------------------------------

    application.add_handler(
        CallbackQueryHandler(
            button_handler
        )
    )

    print(
        "BIZDE bot started"
    )

    application.run_polling(
        drop_pending_updates=True
    )


if __name__ == "__main__":
    main()
