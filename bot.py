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
# Если запускаете локально, впишите значения. В облаке (Render/Heroku) используйте Env Vars.
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "385450206").split(",") if x]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния
(STATE_FUEL_LITERS, STATE_FUEL_COST, STATE_FUEL_ODO, STATE_FUEL_STATION, 
 STATE_ODO_ONLY, STATE_REPAIR_DESC, STATE_SERVICE_SELECT, STATE_SERVICE_COST) = range(8)

# Должно совпадать с названиями в таблице 'Сервис' (Колонка B)
SERVICE_OPTIONS = [
    "Замена масла мотор", "Замена фильтр масляный", 
    "Замена фильтр топливный", "Замена фильтр воздушный", "Комплект ГРМ"
]

# --- GOOGLE SHEETS API ---
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
            if str(r.get("telegram_id")).strip() == str(telegram_id).strip(): return r
    except: return None

def get_car_info(plate):
    try:
        records = get_worksheet("Автомобили").get_all_records()
        for r in records:
            if str(r.get("plate", "")).strip().upper() == str(plate).strip().upper(): return r
    except: return None

# --- СИСТЕМА УВЕДОМЛЕНИЙ ---
async def check_and_notify(ctx, driver, current_odo):
    plate = str(driver.get("plate")).strip().upper()
    try:
        services = get_worksheet("Сервис").get_all_records()
        alerts = []
        for s in services:
            if str(s.get("plate")).strip().upper() == plate:
                next_odo = int(s.get("next_service_odo") or 0)
                if next_odo == 0: continue
                remain = next_odo - current_odo
                if remain <= 0:
                    alerts.append(f"🚨 <b>ALARM!</b>\nПросрочено: <b>{s['service_type']}</b> на {abs(remain)} км")
                elif remain <= 1000:
                    alerts.append(f"⚠️ <b>ВНИМАНИЕ</b>\nЧерез {remain} км нужно: <b>{s['service_type']}</b>")
        if alerts:
            msg = f"🚗 <b>{plate}</b> (Пробег: {current_odo:,} км)\n\n" + "\n".join(alerts)
            await ctx.bot.send_message(driver['telegram_id'], msg.replace(",", " "), parse_mode="HTML")
            for aid in ADMIN_IDS:
                await ctx.bot.send_message(aid, f"🔔 Уведомление по парку:\n{msg}".replace(",", " "), parse_mode="HTML")
    except Exception as e: log.error(f"Ошибка уведомлений: {e}")

# --- ОБНОВЛЕНИЕ ПРОБЕГА ---
def save_odometer(driver, odo):
    try:
        ws = get_worksheet("Автомобили")
        records = ws.get_all_records()
        target_plate = str(driver.get("plate", "")).strip().upper()
        for i, r in enumerate(records, start=2):
            if str(r.get("plate", "")).strip().upper() == target_plate:
                ws.update_cell(i, 5, odo) # Колонка E
                ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
                return True
    except: return False

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
        await update.message.reply_text(f"👋 Доступ закрыт. Ваш ID: {uid}")
        return
    await update.message.reply_text(f"✅ Привет, {driver['name']}!", reply_markup=main_kb(uid))

async def cmd_my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    car = get_car_info(driver['plate'])
    plate = str(driver['plate']).strip().upper()
    current_odo = int(car.get('odometer', 0))
    services = get_worksheet("Сервис").get_all_records()
    car_serv = [s for s in services if str(s.get("plate")).strip().upper() == plate]
    
    lines = [f"📊 <b>СТАТУС: {plate}</b>", f"🛣 Пробег: {current_odo:,} км", ""]
    for s in car_serv:
        rem = int(s.get("next_service_odo") or 0) - current_odo
        icon = "✅" if rem > 1000 else ("⚠️" if rem > 0 else "🚨")
        lines.append(f"{icon} {s['service_type']}: <b>{rem:,} км</b>")
    await update.message.reply_text("\n".join(lines).replace(",", " "), parse_mode="HTML")

# --- ДИАЛОГ: ПРОБЕГ ---
async def odo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите текущий пробег:")
    return STATE_ODO_ONLY

async def odo_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return STATE_ODO_ONLY
    odo = int(val)
    driver = get_driver_info(update.effective_user.id)
    save_odometer(driver, odo)
    await update.message.reply_text(f"✅ Пробег {odo:,} км сохранен!", reply_markup=main_kb(update.effective_user.id))
    await check_and_notify(ctx, driver, odo)
    return ConversationHandler.END

# --- ДИАЛОГ: ЗАПРАВКА ---
async def fuel_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["driver"] = get_driver_info(update.effective_user.id)
    await update.message.reply_text("⛽ Сколько литров?")
    return STATE_FUEL_LITERS

async def fuel_liters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: ctx.user_data["liters"] = float(update.message.text.replace(",", "."))
    except: return STATE_FUEL_LITERS
    await update.message.reply_text("Сумма (MDL):")
    return STATE_FUEL_COST

async def fuel_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try: ctx.user_data["cost"] = float(update.message.text.replace(",", "."))
    except: return STATE_FUEL_COST
    await update.message.reply_text("Пробег (км):")
    return STATE_FUEL_ODO

async def fuel_odo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return STATE_FUEL_ODO
    ctx.user_data["odo"] = int(val)
    await update.message.reply_text("АЗS (Название):")
    return STATE_FUEL_STATION

async def fuel_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    d, odo = ctx.user_data["driver"], ctx.user_data["odo"]
    ws = get_worksheet("Заправки")
    price = round(ctx.user_data["cost"] / ctx.user_data["liters"], 2)
    ws.append_row([datetime.now().strftime("%d.%m.%Y %H:%M"), d['plate'], d['name'], d['telegram_id'], ctx.user_data["liters"], ctx.user_data["cost"], price, odo, update.message.text])
    save_odometer(d, odo)
    await update.message.reply_text("✅ Заправка записана!", reply_markup=main_kb(update.effective_user.id))
    await check_and_notify(ctx, d, odo)
    return ConversationHandler.END

# --- ДИАЛОГ: СЕРВИС (ТО) ---
async def service_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["selected_to"] = []
    kb = [[InlineKeyboardButton("✅ Выполнил работы", callback_data="to_select")], [InlineKeyboardButton("⬅️ Назад", callback_data="to_exit")]]
    await update.message.reply_text("⚙️ <b>Обслуживание автомобиля</b>", reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def service_selector(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    selected = ctx.user_data.get("selected_to", [])
    kb = []
    for opt in SERVICE_OPTIONS:
        t = "✅ " if opt in selected else ""
        kb.append([InlineKeyboardButton(f"{t}{opt}", callback_data=f"opt_{opt}")])
    kb.append([InlineKeyboardButton("📥 ПОДТВЕРДИТЬ", callback_data="opt_confirm")])
    await update.callback_query.edit_message_text("Отметьте выполненные работы:", reply_markup=InlineKeyboardMarkup(kb))

async def service_cb(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = ctx.user_data.get("selected_to", [])
    if data == "to_select":
        await service_selector(update, ctx)
        return STATE_SERVICE_SELECT
    if data == "to_exit":
        await query.message.delete()
        return ConversationHandler.END
    if data.startswith("opt_"):
        act = data.replace("opt_", "")
        if act == "confirm":
            if not selected: return STATE_SERVICE_SELECT
            await query.message.reply_text(f"🛠 Выбрано: {', '.join(selected)}\nВведите стоимость (MDL):")
            return STATE_SERVICE_COST
        else:
            if act in selected: selected.remove(act)
            else: selected.append(act)
            ctx.user_data["selected_to"] = selected
            await service_selector(update, ctx)
            return STATE_SERVICE_SELECT

async def service_done(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    plate = str(driver['plate']).strip().upper()
    curr_odo = int(get_car_info(plate).get("odometer", 0))
    selected = ctx.user_data["selected_to"]
    ws = get_worksheet("Сервис")
    recs = ws.get_all_records()
    for i, r in enumerate(recs, start=2):
        if str(r.get("plate")).strip().upper() == plate and r.get("service_type") in selected:
            inv = int(r.get("interval", 10000))
            ws.update_cell(i, 3, curr_odo)
            ws.update_cell(i, 5, curr_odo + inv)
    get_worksheet("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), plate, driver['name'], curr_odo, ", ".join(selected), update.message.text])
    await update.message.reply_text("✅ Данные обновлены!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- АДМИН-ФУНКЦИИ ---
async def admin_all_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    cars = get_worksheet("Автомобили").get_all_records()
    serv = get_worksheet("Сервис").get_all_records()
    report = ["🚗 <b>СОСТОЯНИЕ ПАРКА:</b>\n"]
    for c in cars:
        p = str(c['plate']).strip().upper()
        odo = int(c.get('odometer', 0))
        report.append(f"<b>{p}</b>: {odo:,} км")
        c_serv = [s for s in serv if str(s.get('plate')).strip().upper() == p]
        for s in c_serv:
            rem = int(s.get('next_service_odo') or 0) - odo
            if rem < 1500:
                icon = "🚨" if rem <= 0 else "⚠️"
                report.append(f"  └ {icon} {s['service_type']}: {rem:,} км")
        report.append("")
    await update.message.reply_text("\n".join(report).replace(",", " "), parse_mode="HTML")

# --- ГЛАВНЫЙ ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_start)],
        states={STATE_ODO_ONLY: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_finish)]},
        fallbacks=[CommandHandler("cancel", cmd_start)]))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_start)],
        states={
            STATE_FUEL_LITERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_liters)],
            STATE_FUEL_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_cost)],
            STATE_FUEL_ODO: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_odo)],
            STATE_FUEL_STATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_finish)]},
        fallbacks=[CommandHandler("cancel", cmd_start)]))

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), service_init)],
        states={
            STATE_SERVICE_SELECT: [CallbackQueryHandler(service_cb)],
            STATE_SERVICE_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_done)]},
        fallbacks=[CommandHandler("cancel", cmd_start)]))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_my_status))
    app.add_handler(MessageHandler(filters.Regex("^🚗 Все авто$"), admin_all_cars))
    app.add_handler(MessageHandler(filters.Regex("^🛠 Ремонт$"), lambda u,c: u.message.reply_text("🛠 Опишите поломку:"))) # Упрощенно
    
    app.run_polling()

if __name__ == "__main__":
    main()
