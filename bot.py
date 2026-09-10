from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, WebAppInfo
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

TOKEN = 8789277125: AAHk5Le4h1CAPy87AlVNm94MzXHmt6RlaSw


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton(
                "📱 Открыть BIZDE",
                web_app=WebAppInfo(
                    url="https://aydin200169.github.io/-bizde-bot/"
                )
            )
        ],
        [
            InlineKeyboardButton("📂 Категории", callback_data="categories")
        ],
        [
            InlineKeyboardButton("🏪 Партнёры", callback_data="partners")
        ],
        [
            InlineKeyboardButton("🎟 Моя подписка", callback_data="subscription")
        ],
    ]

    await update.message.reply_text(
        "👋 Добро пожаловать в BIZDE.KZ!\n\n"
        "Экономь вместе с нашими партнёрами 🇰🇿",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )


async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    if query.data == "categories":
        keyboard = [
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
                    "⬅️ Назад",
                    callback_data="back"
                )
            ],
        ]

        await query.edit_message_text(
            "📂 Выбери категорию:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

    elif query.data == "partners":
        await query.edit_message_text(
            "🏪 Партнёры BIZDE.KZ\n\n"
            "Пока здесь пусто.\n"
            "Скоро добавим первых партнёров! 🔥"
        )

    elif query.data == "subscription":
        await query.edit_message_text(
            "🎟 Моя подписка\n\n"
            "Статус: ❌ Не активна"
        )

    elif query.data in ["cafes", "shops", "sport"]:
        await query.edit_message_text(
            "🏪 Партнёры этой категории пока добавляются.\n\n"
            "Скоро здесь появятся предложения 🔥"
        )

    elif query.data == "back":
        keyboard = [
            [
                InlineKeyboardButton(
                    "📱 Открыть BIZDE",
                    web_app=WebAppInfo(
                        url="https://aydin200169.github.io/-bizde-bot/"
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

        await query.edit_message_text(
            "🏠 Главное меню BIZDE.KZ",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )


app = Application.builder().token(TOKEN).build()

app.add_handler(CommandHandler("start", start))
app.add_handler(CallbackQueryHandler(button_handler))

print("BIZDE.KZ запущен!")

app.run_polling()
