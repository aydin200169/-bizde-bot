from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

TOKEN = "8789277125: AAHk5Le4h1CAPy87AlVNm94MzXHmt6RlaSw"

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! 👋\n\n"
        "Добро пожаловать в BIZDE.KZ 🇰🇿"
    )

app = Application.builder().token(TOKEN).build()
app.add_handler(CommandHandler("start", start))

print("Бот запущен!")

app.run_polling()
