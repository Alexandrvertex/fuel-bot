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
    except: return None

def get_car_info(plate):
    try:
        records = get_worksheet("Автомобили").get_all_records()
        for r in records:
            if str(r.get("plate", "")).upper() == plate.upper(): return r
    except: return None

# --- АВТОМАТИКА ТО ---
def check_service_remain(plate):
    try:
        car = get_car_info(plate)
        current_odo = int(car.get("odometer", 0))
        services = get_worksheet("Сервис").get_all_records()
        car_services = [s for s in services if str(s.get("plate", "")).upper() == plate.upper()]
        
        if not car_services: return "Регламент не настроен."
        
        report = []
        for s in car_services:
            try:
                next_odo = int(s.get("next_service_odo", 0))
                remain = next_odo - current_odo
                status = "✅"
                if remain < 1000: status = "⚠️"
                if remain <= 0: status = "🚨"
                report.append(f"{status} {s.get('service_type')}: <b>{remain:,} км</b>".replace(",", " "))
            except: continue
        return "\n".join(report)
    except: return "Ошибка расчета."

def save_service_done(driver, desc, cost):
    ws_hist = get_worksheet("История_ТО")
    ws_serv = get_worksheet("Сервис")
    car = get_car_info(driver['plate'])
    current_odo = int(car.get("odometer", 0))
    now = datetime.now().strftime("%d.%m.%Y")
    
    # 1. Запись в Историю_ТО
    ws_hist.append_row([now, driver['plate'], driver['name'], current_odo, desc, cost])
    
    # 2. Пересчет следующего ТО
    records = ws_serv.get_all_records()
    for i, r in enumerate(records, start=2):
        if str(r.get("plate", "")).upper() == driver['plate'].upper():
            interval = int(r.get("interval", 10000))
            next_odo = current_odo + interval
            ws_serv.update_cell(i, 3, current_odo) # last_service_odo
            ws_serv.update_cell(i, 5, next_odo)    # next_service_odo (САМ РАССЧИТАЛ)

# --- ФУНКЦИИ ЗАПРАВКИ ---
def save_refuel(driver, liters, cost, odo, station):
    ws = get_worksheet("Заправки")
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    price = round(cost / liters, 2) if liters else 0
    ws.append_row([now, driver.get("plate",""), driver.get("name",""), driver.get("telegram_id",""), liters, cost, price, odo, station])
    return price

def save_odometer(driver, odo):
    ws = get_worksheet("Автомобили")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if str(r.get("plate", "")).upper() == driver.get("plate", "").upper():
            ws.update_cell(i, 5, odo) # Колонка E
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            return True
    return False

# --- КЛАВИАТУРЫ ---
def main_kb(uid):
    btns = [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
            [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
            [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]]
    if uid in ADMIN_IDS:
        btns.append([KeyboardButton("👑 Отчёт сегодня"), KeyboardButton("🚗 Все авто")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# --- ОБРАБОТЧИКИ ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text(f"Вы не в системе. ID: {uid}")
        return
    await update.message.reply_text(f"✅ Привет, {driver['name']}!", reply_markup=main_kb(uid))

# Диалог ТО
async def service_menu(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    remain = check_service_remain(driver['plate'])
    kb = ReplyKeyboardMarkup([[KeyboardButton("✅ Выполнил ТО"), KeyboardButton("⬅️ Назад")]], resize_keyboard=True)
    await update.message.reply_text(f"⚙️ <b>ТО · {driver['plate']}</b>\n\n{remain}", parse_mode="HTML", reply_markup=kb)

async def service_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Что сделали? (напр: замена масла, фильтры):")
    return STATE_SERVICE_DESC

async def service_get_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["serv_desc"] = update.message.text
    await update.message.reply_text("Стоимость работ (MDL):")
    return STATE_SERVICE_COST

async def service_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    save_service_done(driver, ctx.user_data["serv_desc"], update.message.text)
    await update.message.reply_text("✅ Данные обновлены!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# Диалог Ремонта
async def repair_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Опишите поломку:")
    return STATE_REPAIR_DESC

async def repair_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    for aid in ADMIN_IDS:
        await ctx.bot.send_message(aid, f"🚨 <b>РЕМОНТ</b>\n{driver['name']} ({driver['plate']})\n{update.message.text}", parse_mode="HTML")
    await update.message.reply_text("✅ Отправлено Александру.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# Диалог Заправки
async def fuel_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    ctx.user_data["driver"] = driver
    await update.message.reply_text(f"⛽ Заправка {driver['plate']}. Введите литры:")
    return STATE_FUEL_LITERS

async def fuel_get_liters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["liters"] = float(update.message.text.replace(",", "."))
    await update.message.reply_text("Сумма (MDL):")
    return STATE_FUEL_COST

async def fuel_get_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["cost"] = float(update.message.text.replace(",", "."))
    await update.message.reply_text("Пробег (км):")
    return STATE_FUEL_ODO

async def fuel_get_odo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["odo"] = int(update.message.text.replace(" ", ""))
    await update.message.reply_text("Название АЗС:")
    return STATE_FUEL_STATION

async def fuel_get_station(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = ctx.user_data["driver"]
    save_refuel(driver, ctx.user_data["liters"], ctx.user_data["cost"], ctx.user_data["odo"], update.message.text)
    save_odometer(driver, ctx.user_data["odo"])
    remain = check_service_remain(driver['plate'])
    await update.message.reply_text(f"✅ Сохранено!\n\n💡 <b>Статус ТО:</b>\n{remain}", parse_mode="HTML", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ОСНОВНОЙ ЦИКЛ ---
async def handle_text(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    t = update.message.text
    if t == "⚙️ Сервис/ТО": await service_menu(update, ctx)
    elif t == "🛠 Ремонт": await repair_start(update, ctx)
    elif t == "⬅️ Назад": await cmd_start(update, ctx)
    else: await update.message.reply_text("Используйте меню 👇")

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_start)],
        states={
            STATE_FUEL_LITERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_liters)],
            STATE_FUEL_COST:   [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_cost)],
            STATE_FUEL_ODO:    [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_odo)],
            STATE_FUEL_STATION:[MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_station)],
        }, fallbacks=[CommandHandler("cancel", cmd_start)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✅ Выполнил ТО$"), service_start)],
        states={
            STATE_SERVICE_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_get_desc)],
            STATE_SERVICE_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_finish)],
        }, fallbacks=[CommandHandler("cancel", cmd_start)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🛠 Ремонт$"), repair_start)],
        states={STATE_REPAIR_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, repair_finish)]},
        fallbacks=[CommandHandler("cancel", cmd_start)]
    ))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling()

if __name__ == "__main__":
    main()
