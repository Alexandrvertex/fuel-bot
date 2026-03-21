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

# --- НАСТРОЙКИ (Брать из переменных окружения или вписать свои) ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "ВАШ_ТОКЕН")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "385450206").split(",") if x]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния диалогов
(STATE_FUEL_LITERS, STATE_FUEL_COST, STATE_FUEL_ODO, STATE_FUEL_STATION, 
 STATE_ODO_ONLY, STATE_REPAIR_DESC, STATE_SERVICE_SELECT, STATE_SERVICE_COST) = range(8)

# Список работ для выбора (должен совпадать с колонкой B в листе 'Сервис')
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

# --- ЛОГИКА УВЕДОМЛЕНИЙ ---
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
            msg = f"🚗 <b>{plate}</b> (Пробег: {current_odo:,})\n\n" + "\n".join(alerts)
            await ctx.bot.send_message(driver['telegram_id'], msg, parse_mode="HTML")
            for aid in ADMIN_IDS:
                await ctx.bot.send_message(aid, f"🔔 Отчет для админа:\n{msg}", parse_mode="HTML")
    except Exception as e: log.error(f"Ошибка уведомлений: {e}")

# --- СОХРАНЕНИЕ ДАННЫХ ---
def save_odometer(driver, odo):
    ws = get_worksheet("Автомобили")
    records = ws.get_all_records()
    target_plate = str(driver.get("plate", "")).strip().upper()
    for i, r in enumerate(records, start=2):
        if str(r.get("plate", "")).strip().upper() == target_plate:
            ws.update_cell(i, 5, odo) # Колонка E - odometer
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

# --- ОБРАБОТЧИКИ МЕНЮ ---
async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text(f"👋 Доступ закрыт. ID: {uid}")
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
    if not car_serv: lines.append("⚙️ Регламент не настроен.")
    else:
        for s in car_serv:
            rem = int(s.get("next_service_odo") or 0) - current_odo
            icon = "✅" if rem > 1000 else ("⚠️" if rem > 0 else "🚨")
            lines.append(f"{icon} {s['service_type']}: <b>{rem:,} км</b>")
    
    await update.message.reply_text("\n".join(lines).replace(",", " "), parse_mode="HTML")

# --- ЛОГИКА ТО (ВЫБОР НЕСКОЛЬКИХ ПУНКТОВ) ---
async def service_start_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["selected_to"] = []
    await show_to_keyboard(update, ctx)
    return STATE_SERVICE_SELECT

async def show_to_keyboard(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    selected = ctx.user_data.get("selected_to", [])
    kb = []
    for opt in SERVICE_OPTIONS:
        t = "✅ " if opt in selected else ""
        kb.append([InlineKeyboardButton(f"{t}{opt}", callback_data=f"to_{opt}")])
    kb.append([InlineKeyboardButton("📥 ПОДТВЕРДИТЬ", callback_data="to_confirm")])
    
    text = "⚙️ <b>Выберите выполненные работы:</b>"
    if update.callback_query: await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")
    else: await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode="HTML")

async def service_button_click(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    selected = ctx.user_data.get("selected_to", [])

    if data.startswith("to_"):
        action = data.replace("to_", "")
        if action == "confirm":
            if not selected:
                await query.message.reply_text("⚠️ Выберите хотя бы один пункт!")
                return STATE_SERVICE_SELECT
            await query.message.reply_text("💰 Введите общую стоимость (MDL):")
            return STATE_SERVICE_COST
        else:
            if action in selected: selected.remove(action)
            else: selected.append(action)
            ctx.user_data["selected_to"] = selected
            await show_to_keyboard(update, ctx)
            return STATE_SERVICE_SELECT

async def service_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver_info(update.effective_user.id)
    cost = update.message.text
    selected = ctx.user_data.get("selected_to")
    car = get_car_info(driver['plate'])
    curr_odo = int(car.get("odometer", 0))
    plate = str(driver['plate']).strip().upper()
    
    ws_serv = get_worksheet("Сервис")
    recs = ws_serv.get_all_records()
    
    for i, r in enumerate(recs, start=2):
        if str(r.get("plate")).strip().upper() == plate and r.get("service_type") in selected:
            interv = int(r.get("interval", 10000))
            ws_serv.update_cell(i, 3, curr_odo) # last
            ws_serv.update_cell(i, 5, curr_odo + interv) # next
            
    get_worksheet("История_ТО").append_row([
        datetime.now().strftime("%d.%m.%Y"), plate, driver['name'], curr_odo, ", ".join(selected), cost
    ])
    await update.message.reply_text("✅ Данные обновлены!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ПРОБЕГ И ЗАПРАВКА ---
async def odo_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    if not val:
        await update.message.reply_text("⚠️ Введите число.")
        return STATE_ODO_ONLY
    odo = int(val)
    driver = get_driver_info(update.effective_user.id)
    save_odometer(driver, odo)
    await update.message.reply_text(f"📍 Пробег {odo:,} км сохранен!".replace(","," "))
    await check_and_notify(ctx, driver, odo)
    return ConversationHandler.END

async def fuel_finish(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    # (Упрощенная логика записи для экономии места, сохраняет все данные из context)
    d = ctx.user_data["driver"]
    odo = ctx.user_data["odo"]
    ws = get_worksheet("Заправки")
    price = round(ctx.user_data["cost"] / ctx.user_data["liters"], 2)
    ws.append_row([datetime.now().strftime("%d.%m.%Y %H:%M"), d['plate'], d['name'], d['telegram_id'], ctx.user_data["liters"], ctx.user_data["cost"], price, odo, update.message.text])
    save_odometer(d, odo)
    await update.message.reply_text("⛽ Заправка сохранена!")
    await check_and_notify(ctx, d, odo)
    return ConversationHandler.END

# --- ГЛАВНЫЙ ЦИКЛ ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Диалог ТО
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^✅ Выполнил ТО$"), service_start_selection)],
        states={
            STATE_SERVICE_SELECT: [CallbackQueryHandler(service_button_click)],
            STATE_SERVICE_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_finish)]
        },
        fallbacks=[CommandHandler("cancel", cmd_start)]
    ))

    # Остальные диалоги (Пробег, Заправка, Ремонт) добавить аналогично предыдущим версиям
    # ... (код сокращен для краткости, структура остается прежней)

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: cmd_my_status(u, c) if u.message.text == "📊 Мой статус" else None))
    
    app.run_polling()

if __name__ == "__main__":
    main()
