import logging
import os
import json
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
# Укажите ваш ID или несколько через запятую
ADMIN_IDS = [385450206] 

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния
(STATE_FUEL_LITERS, STATE_FUEL_COST, STATE_FUEL_ODO, STATE_FUEL_STATION, 
 STATE_ODO_ONLY, STATE_REPAIR_DESC, STATE_SERVICE_SELECT, STATE_SERVICE_COST) = range(8)

SERVICE_OPTIONS = ["Замена масла мотор", "Замена фильтр масляный", "Замена фильтр топливный", "Замена фильтр воздушный", "Комплект ГРМ"]

# --- API ---
def get_worksheet(name):
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(name)

def get_driver_info(uid):
    try:
        recs = get_worksheet("Водители").get_all_records()
        for r in recs:
            if str(r.get("telegram_id")) == str(uid): return r
    except Exception as e: log.error(f"Ошибка Водители: {e}")
    return None

def get_car_info(plate):
    try:
        recs = get_worksheet("Автомобили").get_all_records()
        for r in recs:
            if str(r.get("plate")).strip().upper() == str(plate).strip().upper(): return r
    except Exception as e: log.error(f"Ошибка Автомобили: {e}")
    return None

# --- УВЕДОМЛЕНИЯ ---
async def check_alerts(ctx, driver, current_odo):
    plate = str(driver.get("plate")).strip().upper()
    try:
        services = get_worksheet("Сервис").get_all_records()
        alerts = []
        for s in services:
            if str(s.get("plate")).strip().upper() == plate:
                next_odo = int(s.get("next_service_odo") or 0)
                if next_odo == 0: continue
                remain = next_odo - current_odo
                if remain <= 0: alerts.append(f"🚨 <b>ALARM!</b> {s['service_type']} (просрочено {abs(remain)} км)")
                elif remain <= 1000: alerts.append(f"⚠️ <b>ВНИМАНИЕ!</b> {s['service_type']} через {remain} км")
        if alerts:
            text = f"🚗 <b>{plate}</b>\nПробег: {current_odo:,} км\n\n" + "\n".join(alerts)
            await ctx.bot.send_message(driver['telegram_id'], text, parse_mode="HTML")
            for aid in ADMIN_IDS:
                await ctx.bot.send_message(aid, f"🔔 Админ-инфо:\n{text}", parse_mode="HTML")
    except Exception as e: log.error(f"Ошибка алертов: {e}")

def update_car_odo(plate, odo):
    try:
        ws = get_worksheet("Автомобили")
        recs = ws.get_all_records()
        for i, r in enumerate(recs, start=2):
            if str(r.get("plate")).strip().upper() == str(plate).strip().upper():
                ws.update_cell(i, 5, odo)
                ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
                return True
    except Exception as e: log.error(f"Ошибка пробега: {e}")
    return False

# --- КЛАВИАТУРА ---
def main_kb(uid):
    btns = [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
            [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
            [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]]
    if uid in ADMIN_IDS: btns.append([KeyboardButton("🚗 Все авто")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# --- ФУНКЦИИ ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    if not driver:
        await update.message.reply_text(f"🚫 Доступ запрещен. ID: {update.effective_user.id}")
        return ConversationHandler.END
    await update.message.reply_text(f"✅ Машина: {driver['plate']}", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    car = get_car_info(driver['plate'])
    odo = int(car.get('odometer', 0))
    servs = get_worksheet("Сервис").get_all_records()
    lines = [f"📊 <b>{driver['plate']}</b> | {odo:,} км\n"]
    for s in servs:
        if str(s.get("plate")).strip().upper() == str(driver['plate']).strip().upper():
            rem = int(s.get("next_service_odo") or 0) - odo
            icon = "✅" if rem > 1000 else ("⚠️" if rem > 0 else "🚨")
            lines.append(f"{icon} {s['service_type']}: {rem:,} км")
    await update.message.reply_text("\n".join(lines).replace(",", " "), parse_mode="HTML")

# --- СЦЕНАРИЙ ПРОБЕГ ---
async def odo_req(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите новый пробег:")
    return STATE_ODO_ONLY

async def odo_sav(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    odo = int("".join(filter(str.isdigit, update.message.text)))
    driver = get_driver_info(update.effective_user.id)
    update_car_odo(driver['plate'], odo)
    await update.message.reply_text(f"✅ Пробег {odo} сохранен", reply_markup=main_kb(update.effective_user.id))
    await check_alerts(ctx, driver, odo)
    return ConversationHandler.END

# --- СЦЕНАРИЙ ТО ---
async def to_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["sel"] = []
    kb = [[InlineKeyboardButton("✅ Выбрать работы", callback_data="to_sel")]]
    await update.message.reply_text("⚙️ Меню ТО", reply_markup=InlineKeyboardMarkup(kb))
    return STATE_SERVICE_SELECT

async def to_callback(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    sel = ctx.user_data.get("sel", [])
    if query.data == "to_sel" or query.data.startswith("o_"):
        if query.data.startswith("o_"):
            item = query.data.replace("o_", "")
            if item in sel: sel.remove(item)
            else: sel.append(item)
        ctx.user_data["sel"] = sel
        kb = [[InlineKeyboardButton(f"{'✅ ' if i in sel else ''}{i}", callback_data=f"o_{i}")] for i in SERVICE_OPTIONS]
        kb.append([InlineKeyboardButton("📥 ПОДТВЕРДИТЬ", callback_data="confirm")])
        await query.edit_message_text("Выберите работы:", reply_markup=InlineKeyboardMarkup(kb))
        return STATE_SERVICE_SELECT
    if query.data == "confirm":
        if not sel: return STATE_SERVICE_SELECT
        await query.message.reply_text(f"🛠 Выбрано: {', '.join(sel)}\nВведите общую стоимость:")
        return STATE_SERVICE_COST

async def to_fin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    car = get_car_info(driver['plate'])
    odo = int(car.get("odometer", 0))
    sel = ctx.user_data["sel"]
    ws = get_worksheet("Сервис")
    recs = ws.get_all_records()
    for i, r in enumerate(recs, start=2):
        if str(r.get("plate")).strip().upper() == str(driver['plate']).strip().upper() and r.get("service_type") in sel:
            ws.update_cell(i, 3, odo) # last
            ws.update_cell(i, 5, odo + int(r.get("interval", 10000))) # next
    get_worksheet("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], odo, ", ".join(sel), update.message.text])
    await update.message.reply_text("✅ Записано!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Сначала регистрируем диалоги (ConversationHandlers)
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_req)],
        states={STATE_ODO_ONLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_sav)]},
        fallbacks=[CommandHandler("start", start)]
    ))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), to_init)],
        states={
            STATE_SERVICE_SELECT: [CallbackQueryHandler(to_callback)],
            STATE_SERVICE_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, to_fin)]
        },
        fallbacks=[CommandHandler("start", start)]
    ))

    # Потом — одиночные команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), my_status))
    
    # Если нажата любая другая кнопка, которая не попала в обработку — возвращаем в старт
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, start))

    app.run_polling()

if __name__ == "__main__":
    main()
