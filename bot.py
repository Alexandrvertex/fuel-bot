import logging
import os
import json
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

# --- НАСТРОЙКИ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8613265488:AAFe1sVGy8p7zCbeuI4y3mIbAxl8cXExAcE")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [385450206] 

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния
ODO_STEP, SERV_STEP, WASH_DESC, WASH_COST, ADMIN_PRICE = range(5)
MENU_BTNS = ["⛽ Заправка", "📍 Пробег", "⚙️ Сервис/ТО", "🧽 Мойка", "📊 Мой статус", "📋 История"]

# --- API ---
def get_ws(name):
    scopes = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    creds_json = json.loads(os.getenv("GOOGLE_CREDENTIALS"))
    creds = Credentials.from_service_account_info(creds_json, scopes=scopes)
    return gspread.authorize(creds).open_by_key(SHEET_ID).worksheet(name)

def get_driver(uid):
    recs = get_ws("Водители").get_all_records()
    return next((r for r in recs if str(r.get("telegram_id")) == str(uid)), None)

# --- ПРОВЕРКА ДОКУМЕНТОВ (ОСАГО / ТО) ---
async def check_docs(ctx, driver, car_data):
    today = datetime.now()
    alerts = []
    # Колонки в 'Автомобили': insurance_exp и inspection_exp
    for label, key in [("ОСАГО", "insurance_exp"), ("Техосмотр", "inspection_exp")]:
        exp_str = car_data.get(key)
        if exp_str:
            try:
                exp_date = datetime.strptime(str(exp_str), "%d.%m.%Y")
                diff = (exp_date - today).days
                if diff <= 10:
                    status = "🚨 СРОЧНО" if diff <= 0 else "⚠️ ВНИМАНИЕ"
                    alerts.append(f"{status}: <b>{label}</b> заканчивается {exp_str} ({max(0, diff)} дн.)")
            except: continue
    if alerts:
        msg = f"📋 <b>ДОКУМЕНТЫ {driver['plate']}:</b>\n" + "\n".join(alerts)
        await ctx.bot.send_message(driver['telegram_id'], msg, parse_mode="HTML")
        for aid in ADMIN_IDS:
            await ctx.bot.send_message(aid, f"📢 Админ-инфо по {driver['plate']}:\n{msg}", parse_mode="HTML")

# --- КЛАВИАТУРА ---
def main_kb(uid):
    btns = [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
            [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🧽 Мойка")],
            [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]]
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

# --- ОСНОВНЫЕ КОМАНДЫ ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return ConversationHandler.END
    await update.message.reply_text(f"✅ Машина: {driver['plate']}", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def my_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    car_ws = get_ws("Автомобили").get_all_records()
    car = next((c for c in car_ws if str(c['plate']).upper() == str(driver['plate']).upper()), None)
    await check_docs(ctx, driver, car)
    odo = car.get('odometer', 0)
    await update.message.reply_text(f"📊 <b>{driver['plate']}</b>\n🛣 Пробег: {odo:,} км".replace(",", " "), parse_mode="HTML")

# --- СЦЕНАРИЙ: ПРОБЕГ ---
async def odo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите текущий пробег:")
    return ODO_STEP

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return ODO_STEP
    
    driver = get_driver(update.effective_user.id)
    ws = get_ws("Автомобили")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if str(r['plate']).upper() == driver['plate'].upper():
            ws.update_cell(i, 5, int(val))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            await check_docs(ctx, driver, r)
            break
    await update.message.reply_text(f"✅ Пробег {int(val):,} сохранен", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- СЦЕНАРИЙ: МОЙКА ---
async def wash_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧽 Что мыли? (например: 'Кузов + салон'):")
    return WASH_DESC

async def wash_desc(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["wash_txt"] = update.message.text
    await update.message.reply_text("💰 Сколько стоило (MDL)?")
    return WASH_COST

async def wash_fin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cost = "".join(filter(str.isdigit, update.message.text))
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"МОЙКА: {ctx.user_data['wash_txt']}", cost])
    await update.message.reply_text(f"✅ Записано: мойка {cost} MDL", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- СЦЕНАРИЙ: СЕРВИС/ТО ---
async def serv_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Опишите работы (например: 'Замена масла мотор'):")
    return SERV_STEP

async def serv_fin(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt in MENU_BTNS: return await start(update, ctx)
    
    driver = get_driver(update.effective_user.id)
    car_ws = get_ws("Автомобили").get_all_records()
    car = next((c for c in car_ws if str(c['plate']).upper() == str(driver['plate']).upper()), None)
    odo = int(car.get('odometer', 0))
    
    # Выборочное обновление интервалов
    serv_ws = get_ws("Сервис")
    updated = []
    for i, r in enumerate(serv_ws.get_all_records(), start=2):
        if str(r['plate']).upper() == driver['plate'].upper() and str(r['service_type']).lower() in txt.lower():
            inv = int(r.get('interval', 10000))
            serv_ws.update_cell(i, 3, odo)
            serv_ws.update_cell(i, 5, odo + inv)
            updated.append(r['service_type'])

    # Запись в историю (цена пустая)
    hist_ws = get_ws("История_ТО")
    hist_ws.append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], odo, txt, ""])
    row_idx = len(hist_ws.get_all_values())
    
    await update.message.reply_text(f"✅ Отчет принят. Обновлено ТО: {', '.join(updated) if updated else 'Общих регламентов не найдено'}", reply_markup=main_kb(update.effective_user.id))
    
    # Уведомление админу с кнопкой ввода цены
    for aid in ADMIN_IDS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Ввести цену", callback_data=f"setprice_{row_idx}")]])
        await ctx.bot.send_message(aid, f"⚙️ <b>НОВОЕ ТО</b>\nАвто: {driver['plate']}\nРаботы: {txt}", parse_mode="HTML", reply_markup=kb)
    return ConversationHandler.END

# --- ЛОГИКА АДМИНА: ВВОД ЦЕНЫ ---
async def admin_price_btn(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["edit_row"] = query.data.split("_")[1]
    await query.message.reply_text("Введите сумму (числом) для этого ТО:")
    return ADMIN_PRICE

async def admin_price_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return ADMIN_PRICE
    get_ws("История_ТО").update_cell(int(ctx.user_data["edit_row"]), 6, val)
    await update.message.reply_text(f"✅ Цена {val} MDL сохранена в историю.")
    return ConversationHandler.END

# --- MAIN ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Сценарии
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_start)],
        states={ODO_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧽 Мойка$"), wash_start)],
        states={
            WASH_DESC: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_desc)],
            WASH_COST: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_fin)]
        }, fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), serv_start)],
        states={SERV_STEP: [MessageHandler(filters.TEXT & ~filters.COMMAND, serv_fin)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_price_btn, pattern="^setprice_")],
        states={ADMIN_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_price_save)]},
        fallbacks=[CommandHandler("start", start)]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), my_status))
    app.add_handler(MessageHandler(filters.ALL, start))

    print("🚀 Бот активен")
    app.run_polling()

if __name__ == "__main__":
    main()
