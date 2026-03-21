import logging
import os
import json
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler, 
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ (Замените на ваши данные) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН_ЗДЕСЬ")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [385450206] # Ваш Telegram ID

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния диалогов
FUEL, ODO, SERVICE, REPAIR = range(4)

# --- РАБОТА С ТАБЛИЦАМИ ---
def get_ws(name):
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(name)

def get_driver(uid):
    try:
        recs = get_ws("Водители").get_all_records()
        return next((r for r in recs if str(r.get("telegram_id")) == str(uid)), None)
    except: return None

# --- МЕНЮ ---
def main_kb(uid):
    btns = [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
            [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
            [KeyboardButton("📊 Мой статус"), KeyboardButton("🚗 Все авто")]]
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# --- ОБЩИЕ КОМАНДЫ ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver(uid)
    if not driver:
        await update.message.reply_text(f"🚫 Доступ закрыт. Ваш ID: {uid}")
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Машина: {driver['plate']}", reply_markup=main_kb(uid))
    return ConversationHandler.END

async def my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return
    car_ws = get_ws("Автомобили").get_all_records()
    car = next((c for c in car_ws if str(c['plate']) == str(driver['plate'])), None)
    
    odo = car.get('odometer', 0)
    text = f"📊 <b>СТАТУС: {driver['plate']}</b>\n🛣 Текущий пробег: {odo:,} км".replace(",", " ")
    await update.message.reply_text(text, parse_mode="HTML", reply_markup=main_kb(update.effective_user.id))

# --- ЛОГИКА: ПРОБЕГ ---
async def odo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите текущий пробег (только цифры):")
    return ODO

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    # Если вместо цифр нажата другая кнопка меню — сбрасываем и идем в старт
    if text in ["⛽ Заправка", "⚙️ Сервис/ТО", "📊 Мой статус", "🚗 Все авто"]:
        return await start(update, ctx)
    
    val = "".join(filter(str.isdigit, text))
    if not val:
        await update.message.reply_text("⚠️ Введите пробег цифрами или нажмите /start для отмены.")
        return ODO
    
    driver = get_driver(update.effective_user.id)
    ws = get_ws("Автомобили")
    recs = ws.get_all_records()
    
    for i, r in enumerate(recs, start=2):
        if str(r['plate']) == str(driver['plate']):
            ws.update_cell(i, 5, int(val)) # Колонка E (Пробег)
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M")) # Колонка F
            break
            
    await update.message.reply_text(f"✅ Пробег {int(val):,} км сохранен!".replace(",", " "), reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЛОГИКА: СЕРВИС (ТО) ---
async def service_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Опишите выполненные работы и стоимость (например: 'Замена масла и фильтров, 1500 леев'):")
    return SERVICE

async def service_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text in ["⛽ Заправка", "📍 Пробег", "📊 Мой статус", "🚗 Все авто"]:
        return await start(update, ctx)
        
    driver = get_driver(update.effective_user.id)
    car_ws = get_ws("Автомобили").get_all_records()
    car = next((c for c in car_ws if str(c['plate']) == str(driver['plate'])), None)
    
    get_ws("История_ТО").append_row([
        datetime.now().strftime("%d.%m.%Y"),
        driver['plate'],
        driver['name'],
        car.get('odometer', 0),
        text
    ])
    
    await update.message.reply_text("✅ Данные ТО записаны в историю.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- АДМИН: ВЕСЬ ПАРК ---
async def all_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    try:
        cars = get_ws("Автомобили").get_all_records()
        res = ["🚗 <b>СОСТОЯНИЕ ПАРКА:</b>"]
        for c in cars:
            res.append(f"• <b>{c['plate']}</b>: {c.get('odometer', 0):,} км".replace(",", " "))
        await update.message.reply_text("\n".join(res), parse_mode="HTML")
    except Exception as e:
        await update.message.reply_text(f"Ошибка доступа к таблице: {e}")

# --- ЗАПУСК БОТА ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Сценарий Пробега
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_start)],
        states={ODO: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))

    # Сценарий ТО
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), service_start)],
        states={SERVICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), my_status))
    app.add_handler(MessageHandler(filters.Regex("^🚗 Все авто$"), all_cars))
    
    # Обработка любых других сообщений как возврат в меню
    app.add_handler(MessageHandler(filters.ALL, start))

    print("Бот запущен...")
    app.run_polling()

if __name__ == "__main__":
    main()
