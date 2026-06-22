import logging
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

# Твой токен (вставь сюда)
TOKEN = "ТВОЙ_ТОКЕН_СЮДА"

# Включаем логи (чтобы знать, что бот сдох)
logging.basicConfig(level=logging.INFO)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # Самое уёбищное сообщение
    await update.message.reply_text("Привет, лох!")

def main():
    # Создаём приложение
    app = Application.builder().token(TOKEN).build()
    
    # Обработчик команды /start
    app.add_handler(CommandHandler("start", start))
    
    # Запускаем бота (вечный цикл)
    print("Бот запущен. Жди...")
    app.run_polling()

if __name__ == "__main__":
    main()
