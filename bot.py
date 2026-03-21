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

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8613265488:AAFe1sVGy8p7zCbeuI4y3mIbAxl8cXExAcE")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "385450206").split(",") if x]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния для диалогов
(STATE_FUEL_LITERS, STATE_FUEL_COST, STATE_FUEL_ODO, STATE_FUEL_STATION, 
 STATE_ODO_KM, STATE_REPAIR_DESC, STATE_SERVICE_DESC, STATE_SERVICE_COST) = range(8)

# --- РАБОТА С ТАБЛИЦАМИ ---
def get_sheet():
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SHEET_ID)

def get_worksheet(name):
    return get_sheet().worksheet(name)

def get_driver_info(telegram_id):
    try:
        records = get_worksheet("Водители").get_all_records()
        for r in records:
            if str(r.get("telegram_id")) == str(telegram_id): return r
    except Exception as e: log.error(f"get_driver_info: {e}")
    return None

def get_car_info(plate):
    try:
        records = get_worksheet("Автомобили").get_all_records()
        for r in records:
            if r.get("plate", "").upper() == plate.upper(): return r
    except Exception as e: log.error(f"get_car_info: {e}")
    return None

# Функция проверки остатка до ТО
def check_service_remain(plate):
    try:
        current_odo = int(get_car_info(plate).get("odometer", 0))
        services = get_worksheet("Сервис").get_all_records()
        car_services = [s for s in services if s.get("plate", "").upper() == plate.upper()]
        
        report = []
        for s in car_services:
            next_odo = int(s.get("next_service_odo", 0))
            remain = next_odo - current_odo
            report.append(f"• {s.get('service_type')}: <b>{remain:,} км</b>".replace(",", " "))
        return "\n".join(report) if report else "Регламент не настроен."
    except: return "Ошибка расчета ТО."

# Сохранение истории ТО
def save_service_history(driver, desc, cost):
    ws_hist = get_worksheet("История_ТО")
    ws_serv = get_worksheet("Сервис")
    car = get_car_info(driver['plate'])
    current_odo = int(car.get("odometer", 0))
    now = datetime.now().strftime("%d.%m.%Y")
    
    # Пишем в историю
    ws_hist.append_row([now, driver['plate'], driver['name'], current_odo, desc, cost])
    
    # Обновляем в листе Сервис (сбрасываем пробег последнего ТО)
    records = ws_serv.get_all_records()
    for i, r in enumerate(records, start=2):
        if r.get("plate", "").upper() == driver['plate'].upper():
            ws_serv.update_cell(i, 3, current_odo) # last_service_odo
            new_next = current_odo + int(r.get("interval", 10000))
            ws_serv.update_cell(i, 5, new_next)    # next_service_odo

# --- КЛАВИАТУРЫ ---
def main_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
        [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
        [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]
    ], resize_keyboard=True)

def admin_keyboard():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
        [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
        [KeyboardButton("👑 Отчёт сегодня"), KeyboardButton("🚗 Все авто")],
        [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]
    ], resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text(f"👋 Вы не зарегистрированы.\nID: {uid}")
        return
    kb = admin_keyboard() if uid in ADMIN_IDS else main_keyboard()
    await update.message.reply_text(f"✅ Привет, <b>{driver['name']}</b>!", parse_mode="HTML", reply_markup=kb)

# --- ЛОГИКА СЕРВИСА ---
async def service_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    remain_text = check_service_remain(driver['plate'])
    kb = ReplyKeyboardMarkup([[KeyboardButton("✅ Выполнил ТО"), KeyboardButton("⬅️ Назад")]], resize_keyboard=True)
    await update.message.reply_text(
        f"⚙️ <b>Регламент ТО · {driver['plate']}</b>\n\nОсталось до обслуживания:\n{remain_text}",
        parse_mode="HTML", reply_markup=kb
    )

async def service_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📝 Опишите, что было сделано?\n<i>(Напр: замена масла Shell, масляный фильтр)</i>", parse_mode="HTML")
    return STATE_SERVICE_DESC

async def service_get_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["serv_desc"] = update.message.text
    await update.message.reply_text("💰 Введите общую стоимость запчастей и работ (MDL):")
    return STATE_SERVICE_COST

async def service_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cost = update.message.text
    driver = get_driver_info(update.effective_user.id)
    try:
        save_service_history(driver, ctx.user_data["serv_desc"], cost)
        await update.message.reply_text("✅ Запись внесена в сервисную книжку!", reply_markup=main_keyboard())
    except Exception as e:
        await update.message.reply_text(f"❌ Ошибка: {e}")
    return ConversationHandler.END

# --- ЛОГИКА РЕМОНТА ---
async def repair_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 <b>Заявка на ремонт</b>\nОпишите проблему или поломку:", parse_mode="HTML")
    return STATE_REPAIR_DESC

async def repair_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    desc = update.message.text
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    for admin_id in ADMIN_IDS:
        await ctx.bot.send_message(admin_id, f"🚨 <b>ПОЛОМКА!</b>\nВодитель: {driver['name']}\nАвто: {driver['plate']}\nОписание: {desc}", parse_mode="HTML")
    await update.message.reply_text("✅ Сообщение отправлено Александру.", reply_markup=main_keyboard())
    return ConversationHandler.END

# (Остальные функции заправки, пробега и т.д. остаются из твоего кода, но с мелкими правками для ТО)
# [Вставь здесь свои функции fuel_start, fuel_get_liters и т.д. из своего файла]

# --- ДОПОЛНЕНИЕ В КОНЕЦ ЗАПРАВКИ ---
# В функции fuel_get_station в самом конце, перед return, добавь:
# remain = check_service_remain(driver['plate'])
# await update.message.reply_text(f"💡 Памятка по ТО:\n{remain}", parse_mode="HTML")

async def unknown_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "⚙️ Сервис/ТО": await service_menu(update, ctx)
    elif text == "⬅️ Назад": await cmd_start(update, ctx)
    elif text == "🛠 Ремонт": await repair_start(update, ctx)
    # ... твои остальные elif ...

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Конверсейшн для ТО
    service_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✅ Выполнил ТО$"), service_start)],
        states={
            STATE_SERVICE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_get_desc)],
            STATE_SERVICE_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_finish)],
        },
        fallbacks=[CommandHandler("cancel", cmd_start)],
    )

    # Конверсейшн для Ремонта
    repair_handler = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛠 Ремонт$"), repair_start)],
        states={
            STATE_REPAIR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, repair_finish)],
        },
        fallbacks=[CommandHandler("cancel", cmd_start)],
    )

    # Добавь их в app.add_handler перед MessageHandler
    app.add_handler(service_handler)
    app.add_handler(repair_handler)
    # ... остальные хендлеры ...
    app.run_polling()
