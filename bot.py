import os
import random
import io
import json
from telegram import Update, InputFile, Bot
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    ContextTypes, ConversationHandler, filters
)
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
from fastapi import FastAPI, Request
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.environ["BOT_TOKEN"]
ARCHIVE_CHAT_ID = os.environ["ARCHIVE_CHAT_ID"]
TEXTS_FOLDER_ID = os.environ["READ_FOLDER_ID"]
GOOGLE_CREDENTIALS = json.loads(os.environ["GOOGLE_CREDENTIALS"])

SCOPES = ['https://www.googleapis.com/auth/drive.readonly']
credentials = service_account.Credentials.from_service_account_info(
    GOOGLE_CREDENTIALS, scopes=SCOPES
)
drive_service = build('drive', 'v3', credentials=credentials)

WAITING_PHOTO = 1
user_codes = {}
user_photo_counts = {}

app = FastAPI()
bot = Bot(token=BOT_TOKEN)
application = Application.builder().token(BOT_TOKEN).build()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        f""" 👋 Здравствуйте, {update.effective_user.first_name}!
Я рада, что вы согласились поучаствовать в затее по переписыванию Википедии :) Это академический проект, задача которого — создать тренировочный датасет для kraken-модели, которая будет транскрибировать рукописи на современном русском.

Бот не собирает персональные данные, не запоминает ваш ник и номер телефона.

Вот что теперь нужно сделать:
1️⃣ Нажмите /gettext . Вы получите случайный текст.

✍️ Перепишите этот текст *или его фрагмент*.

✅ Дождитесь подтверждения загрузки.

Приступим? Нажимайте /gettext
"""
    )

async def get_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    results = drive_service.files().list(
        q=f"'{TEXTS_FOLDER_ID}' in parents and mimeType='text/plain' and trashed = false",
        fields="files(id, name)"
    ).execute()
    files = results.get('files', [])
    if not files:
        await update.message.reply_text("Нет доступных текстов.")
        return ConversationHandler.END

    file = random.choice(files)
    file_id = file['id']
    file_name = file['name']
    code = file_name.replace('.txt', '')

    request = drive_service.files().get_media(fileId=file_id)
    fh = io.BytesIO()
    downloader = MediaIoBaseDownload(fh, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()

    fh.seek(0)
    text = fh.read().decode('utf-8')

    user_id = update.effective_user.id
    user_codes[user_id] = code
    user_photo_counts[user_id] = 0

    await update.message.reply_text(f"{text}\n\nВаш код: {code}")
    await update.message.reply_text("Теперь отправьте фото написанного от руки текста (JPG или PNG).")
    return WAITING_PHOTO

async def receive_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in user_codes:
        await update.message.reply_text("Сначала получите текст с помощью /gettext.")
        return ConversationHandler.END

    code = user_codes[user_id]
    user_photo_counts[user_id] += 1
    suffix = user_photo_counts[user_id]

    photo = update.message.photo[-1] if update.message.photo else None
    if not photo:
        await update.message.reply_text("Пожалуйста, отправьте изображение.")
        return WAITING_PHOTO

    file = await context.bot.get_file(photo.file_id)
    filename = f"{code}_{suffix}.jpg"
    await file.download_to_drive(filename)

    with open(filename, "rb") as img:
        await context.bot.send_photo(
            chat_id=ARCHIVE_CHAT_ID,
            photo=InputFile(img),
            caption=f"{code}_{suffix}"
        )

    os.remove(filename)
    await update.message.reply_text("Фото успешно загружено. Спасибо!")
    return ConversationHandler.END

conv_handler = ConversationHandler(
    entry_points=[CommandHandler("gettext", get_text)],
    states={WAITING_PHOTO: [MessageHandler(filters.PHOTO, receive_photo)]},
    fallbacks=[CommandHandler("start", start)]
)

application.add_handler(CommandHandler("start", start))
application.add_handler(conv_handler)

@app.post(f"/{BOT_TOKEN}")
async def webhook(request: Request):
    data = await request.json()
    update = Update.de_json(data, bot)
    await application.update_queue.put(update)
    return {"ok": True}
