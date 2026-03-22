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

# --- КОНФИГУРАЦИЯ ---
BOT_TOKEN = os.getenv("BOT_TOKEN", "8613265488:AAFe1sVGy8p7zCbeuI4y3mIbAxl8cXExAcE")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [385450206] # Ваш ID

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

# Состояния диалогов
ODO_S, WASH_D, WASH_C, SERV_S, ADMIN_P, FUEL_L, FUEL_C, FUEL_O = range(8)
MENU_BTNS = ["⛽ Заправка", "📍 Пробег", "⚙️ Сервис/ТО", "🧽 Мойка", "📊 Мой статус", "📋 История"]

# --- ИНСТРУМЕНТЫ ТАБЛИЦ ---
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

def is_recent(date_str):
    try:
        d = datetime.strptime(date_str.split()[0], "%d.%m.%Y")
        return datetime.now() - d <= timedelta(days=30)
    except: return False

def parse_val(text):
    return float("".join(filter(lambda x: x.isdigit() or x in ".,", text)).replace(",", "."))

# --- ПРОВЕРКА ДОКУМЕНТОВ ---
async def notify_docs(ctx, driver, car):
    today = datetime.now()
    msgs = []
    for label, key in [("🛡 ОСАГО", "insurance_exp"), ("📋 Техосмотр", "inspection_exp")]:
        exp = car.get(key)
        if exp:
            try:
                dt = datetime.strptime(str(exp), "%d.%m.%Y")
                diff = (dt - today).days
                if diff <= 10:
                    status = "🚨 СРОЧНО" if diff <= 0 else "⚠️ ВНИМАНИЕ"
                    msgs.append(f"{status}: {label} до {exp} ({max(0, diff)} дн.)")
            except: continue
    if msgs:
        text = f"🔔 <b>ДОКУМЕНТЫ {driver['plate']}:</b>\n" + "\n".join(msgs)
        await ctx.bot.send_message(driver['telegram_id'], text, parse_mode="HTML")
        for aid in ADMIN_IDS:
            await ctx.bot.send_message(aid, f"📢 Админ-отчет по {driver['plate']}:\n{text}", parse_mode="HTML")

# --- ГЛАВНОЕ МЕНЮ ---
def main_kb():
    return ReplyKeyboardMarkup([
        [KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
        [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🧽 Мойка")],
        [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]
    ], resize_keyboard=True)

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver:
        await update.message.reply_text(f"🚫 Доступ закрыт. Ваш ID: {update.effective_user.id}")
        return ConversationHandler.END
    await update.message.reply_text(f"🚜 Авто: {driver['plate']}. Чем помогу?", reply_markup=main_kb())
    return ConversationHandler.END

# --- 📊 МОЙ СТАТУС (ПЛАН) ---
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    car_recs = get_ws("Автомобили").get_all_records()
    car = next((c for c in car_recs if str(c['plate']).upper() == driver['plate'].upper()), None)
    odo = int(car.get('odometer', 0))
    
    # Документы
    doc_info = [f"🛡 ОСАГО: <b>{car.get('insurance_exp', '-')}</b>", f"📋 Техосмотр: <b>{car.get('inspection_exp', '-')}</b>"]
    
    # Регламент ТО
    serv_recs = get_ws("Сервис").get_all_records()
    plan = []
    for s in serv_recs:
        if str(s['plate']).upper() == driver['plate'].upper():
            nxt = int(s.get('next_service_odo', 0))
            rem = nxt - odo
            icon = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
            plan.append(f"{icon} {s['service_type']}: на {nxt:,} (ост. {rem:,} км)")

    text = (f"📊 <b>СТАТУС: {driver['plate']}</b>\n🛣 Пробег: {odo:,} км\n\n"
            f"📅 <b>Сроки:</b>\n" + "\n".join(doc_info) + "\n\n"
            f"🛠 <b>План ТО:</b>\n" + ("\n".join(plan) if plan else "Нет данных")).replace(",", " ")
    await update.message.reply_text(text, parse_mode="HTML")

# --- 📋 ИСТОРИЯ (ОТЧЕТ 30 ДНЕЙ) ---
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver(uid)
    is_admin = uid in ADMIN_IDS
    
    h_recs = get_ws("История_ТО").get_all_records()
    f_recs = get_ws("Заправки").get_all_records()
    
    lines, total = [], 0.0
    
    for r in h_recs:
        if (is_admin or str(r['plate']).upper() == driver['plate'].upper()) and is_recent(r['date']):
            c = parse_val(str(r.get('cost', 0)))
            total += c
            lines.append(f"• {r['date']} | {r['plate']} | {r['work_details']} | {c:,.0f} MDL")
            
    for f in f_recs:
        dt = f.get('date_time', '')
        if (is_admin or str(f['plate']).upper() == driver['plate'].upper()) and is_recent(dt):
            c = parse_val(str(f.get('cost', 0)))
            total += c
            lines.append(f"• {dt.split()[0]} | {f['plate']} | ⛽ Заправка {f['liters']}л | {c:,.0f} MDL")

    header = "👑 <b>ОТЧЕТ ПО ПАРКУ (30 дн.)</b>" if is_admin else "📋 <b>МОЯ ИСТОРИЯ (30 дн.)</b>"
    body = "\n".join(lines[-20:]) if lines else "Записей нет."
    footer = f"\n\n💰 <b>ИТОГО РАСХОДОВ: {total:,.2f} MDL</b>"
    
    await update.message.reply_text(f"{header}\n\n{body}{footer}".replace(",", " "), parse_mode="HTML")

# --- СЦЕНАРИЙ: ПРОБЕГ ---
async def odo_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите текущий пробег (только цифры):")
    return ODO_S

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await cmd_start(update, ctx)
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return ODO_S
    
    driver = get_driver(update.effective_user.id)
    ws = get_ws("Автомобили")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if str(r['plate']).upper() == driver['plate'].upper():
            ws.update_cell(i, 5, int(val))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            await notify_docs(ctx, driver, r)
            break
    await update.message.reply_text(f"✅ Пробег {int(val):,} км принят", reply_markup=main_kb())
    return ConversationHandler.END

# --- СЦЕНАРИЙ: МОЙКА ---
async def wash_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧽 Что мыли?")
    return WASH_D

async def wash_step2(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await cmd_start(update, ctx)
    ctx.user_data["w_desc"] = update.message.text
    await update.message.reply_text("💰 Стоимость (MDL):")
    return WASH_C

async def wash_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    cost = "".join(filter(str.isdigit, update.message.text))
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"МОЙКА: {ctx.user_data['w_desc']}", cost])
    await update.message.reply_text(f"✅ Мойка ({cost} MDL) записана", reply_markup=main_kb())
    return ConversationHandler.END

# --- СЦЕНАРИЙ: ТО ---
async def serv_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⚙️ Опишите работы (через запятую):")
    return SERV_S

async def serv_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    txt = update.message.text
    if txt in MENU_BTNS: return await cmd_start(update, ctx)
    driver = get_driver(update.effective_user.id)
    car = next((c for c in get_ws("Автомобили").get_all_records() if str(c['plate']).upper() == driver['plate'].upper()), None)
    odo = int(car.get('odometer', 0))
    
    # Умное обновление интервалов
    sws = get_ws("Сервис")
    updated_to = []
    for i, r in enumerate(sws.get_all_records(), start=2):
        if str(r['plate']).upper() == driver['plate'].upper() and str(r['service_type']).lower() in txt.lower():
            iv = int(r.get('interval', 10000))
            sws.update_cell(i, 3, odo)
            sws.update_cell(i, 5, odo + iv)
            updated_to.append(r['service_type'])

    hws = get_ws("История_ТО")
    hws.append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], odo, txt, ""])
    row = len(hws.get_all_values())
    
    await update.message.reply_text(f"✅ Отчет принят. Обновлено: {', '.join(updated_to) if updated_to else 'Без регламента'}")
    
    for aid in ADMIN_IDS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Ввести цену", callback_data=f"p_{row}")]])
        await ctx.bot.send_message(aid, f"⚙️ <b>ТО: {driver['plate']}</b>\nРаботы: {txt}", parse_mode="HTML", reply_markup=kb)
    return ConversationHandler.END

# --- АДМИН: ЦЕНА ---
async def admin_p_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["edit_row"] = query.data.split("_")[1]
    await query.message.reply_text("Введите сумму (MDL):")
    return ADMIN_P

async def admin_p_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    get_ws("История_ТО").update_cell(int(ctx.user_data["edit_row"]), 6, val)
    await update.message.reply_text(f"✅ {val} MDL сохранено.")
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    # Сценарии
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_init)],
        states={ODO_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]},
        fallbacks=[CommandHandler("start", cmd_start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧽 Мойка$"), wash_init)],
        states={WASH_D: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_step2)], WASH_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_save)]},
        fallbacks=[CommandHandler("start", cmd_start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), serv_init)],
        states={SERV_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, serv_save)]},
        fallbacks=[CommandHandler("start", cmd_start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_p_init, pattern="^p_")],
        states={ADMIN_P: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_p_save)]},
        fallbacks=[CommandHandler("start", cmd_start)]
    ))

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_status))
    app.add_handler(MessageHandler(filters.Regex("^📋 История$"), cmd_history))
    app.add_handler(MessageHandler(filters.ALL, cmd_start))

    app.run_polling()

if __name__ == "__main__":
    main()
