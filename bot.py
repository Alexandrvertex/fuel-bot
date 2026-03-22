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

# Состояния диалогов
(ODO_S, WASH_D, WASH_C, FUEL_L, FUEL_C, FUEL_O, FUEL_ST,
 SERV_S, REPAIR_S, ADMIN_P) = range(10)

# Список кнопок для проверки выхода из диалога
MENU_BTNS = [
    "⛽ Заправка", "📍 Пробег", "⚙️ Сервис/ТО", "🛠 Ремонт", 
    "🧽 Мойка", "📊 Мой статус", "📋 История", "👑 Отчёт сегодня", "🚗 Все авто"
]

# --- ИНСТРУМЕНТЫ GOOGLE TABLES ---
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

def parse_val(text):
    try: return float("".join(filter(lambda x: x.isdigit() or x in ".,", str(text))).replace(",", "."))
    except: return 0.0

# --- ГЛАВНОЕ МЕНЮ ---
def main_kb(uid):
    btns = [
        [KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
        [KeyboardButton("⚙️ Сервис/ТО"), KeyboardButton("🛠 Ремонт")],
        [KeyboardButton("🧽 Мойка"), KeyboardButton("📊 Мой статус")],
        [KeyboardButton("📋 История")]
    ]
    if uid in ADMIN_IDS:
        btns.append([KeyboardButton("👑 Отчёт сегодня"), KeyboardButton("🚗 Все авто")])
    return ReplyKeyboardMarkup(btns, resize_keyboard=True)

async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return ConversationHandler.END
    await update.message.reply_text(f"✅ Привет, {driver['name']}!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- 📊 МОЙ СТАТУС (ПЛАН РАБОТ) ---
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return
    car = next((c for c in get_ws("Автомобили").get_all_records() if str(c['plate']).upper() == driver['plate'].upper()), None)
    odo = int(car.get('odometer', 0))
    
    plan = []
    for s in get_ws("Сервис").get_all_records():
        if str(s['plate']).upper() == driver['plate'].upper():
            nxt = int(s.get('next_service_odo', 0))
            rem = nxt - odo
            status = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
            plan.append(f"{status} {s['service_type']}: {nxt:,} км (ост. {rem:,} км)")

    text = (f"📊 <b>СТАТУС: {driver['plate']}</b>\n🛣 Пробег: {odo:,} км\n\n"
            f"🛠 <b>План регламентных работ:</b>\n" + ("\n".join(plan) if plan else "Регламент не задан")).replace(",", " ")
    await update.message.reply_text(text, parse_mode="HTML")

# --- 🚗 ВСЕ АВТО (АДМИН-ОТЧЕТ) ---
async def cmd_all_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS: return
    cars = get_ws("Автомобили").get_all_records()
    services = get_ws("Сервис").get_all_records()
    
    report = ["🚗 <b>СОСТОЯНИЕ ПАРКА:</b>\n"]
    for c in cars:
        plate = c['plate']
        odo = int(c.get('odometer', 0))
        report.append(f"<b>{plate}</b> ({odo:,} км):".replace(",", " "))
        
        car_tasks = [s for s in services if str(s['plate']).upper() == plate.upper()]
        for s in car_tasks:
            rem = int(s.get('next_service_odo', 0)) - odo
            icon = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
            report.append(f" {icon} {s['service_type']}: {int(s.get('next_service_odo', 0)):,} км")
        report.append("") 
    await update.message.reply_text("\n".join(report), parse_mode="HTML")

# --- 📋 ИСТОРИЯ И ОТЧЕТ СЕГОДНЯ ---
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver(uid)
    is_admin = uid in ADMIN_IDS
    today_only = "сегодня" in update.message.text.lower()
    
    h_recs = get_ws("История_ТО").get_all_records()
    f_recs = get_ws("Заправки").get_all_records()
    lines, total = [], 0.0
    
    filter_date = datetime.now().strftime("%d.%m.%Y")
    
    for r in h_recs:
        if (is_admin or r['plate'] == driver['plate']):
            if today_only and r['date'] != filter_date: continue
            cost = parse_val(r.get('cost', 0))
            total += cost
            lines.append(f"• {r['date']} | {r['plate']} | {r['work_details']} | {cost:,.0f} MDL")
            
    for f in f_recs:
        if (is_admin or f['plate'] == driver['plate']):
            f_date = str(f.get('date_time', '')).split()[0]
            if today_only and f_date != filter_date: continue
            cost = parse_val(f.get('cost', 0))
            total += cost
            lines.append(f"• {f_date} | {f['plate']} | ⛽ {f['liters']}л | {cost:,.0f} MDL")

    title = "👑 <b>ОТЧЕТ ЗА СЕГОДНЯ</b>" if today_only else "📋 <b>ИСТОРИЯ (30 дн)</b>"
    await update.message.reply_text(f"{title}\n\n" + ("\n".join(lines[-20:]) if lines else "Записей нет.") + f"\n\n💰 <b>ИТОГО: {total:,.2f} MDL</b>".replace(",", " "), parse_mode="HTML")

# --- СЦЕНАРИИ ВВОДА ДАННЫХ ---

# 1. Пробег
async def odo_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Введите текущий пробег (только цифры):")
    return ODO_S

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    val = "".join(filter(str.isdigit, update.message.text))
    if not val: return ODO_S
    driver = get_driver(update.effective_user.id)
    ws = get_ws("Автомобили")
    for i, r in enumerate(ws.get_all_records(), 2):
        if str(r['plate']).upper() == driver['plate'].upper():
            ws.update_cell(i, 5, int(val))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            break
    await update.message.reply_text(f"✅ Пробег {int(val):,} км сохранен", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# 2. Заправка (Исправлено под структуру таблицы из 9 колонок)
async def fuel_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛽ Сколько литров заправили?")
    return FUEL_L

async def fuel_l_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_l"] = update.message.text
    await update.message.reply_text("💰 Сумма (MDL):")
    return FUEL_C

async def fuel_c_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_c"] = update.message.text
    await update.message.reply_text("📍 Пробег при заправке:")
    return FUEL_O

async def fuel_o_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_o"] = update.message.text
    await update.message.reply_text("🏢 Название АЗС (например, Petrom):")
    return FUEL_ST

async def fuel_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    
    driver = get_driver(update.effective_user.id)
    
    liters = parse_val(ctx.user_data.get("f_l", "0"))
    cost = parse_val(ctx.user_data.get("f_c", "0"))
    odo = ctx.user_data.get("f_o", "0")
    station = update.message.text
    
    price_per_liter = round(cost / liters, 2) if liters > 0 else 0
    
    # Запись строго в соответствии с колонками (A-I)
    row = [
        datetime.now().strftime("%d.%m.%Y %H:%M"), 
        driver['plate'], 
        driver['name'], 
        str(driver['telegram_id']), 
        liters, 
        cost, 
        price_per_liter, 
        odo, 
        station
    ]
    
    get_ws("Заправки").append_row(row)
    await update.message.reply_text("✅ Заправка записана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# 3. Мойка
async def wash_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🧽 Что именно мыли?")
    return WASH_D

async def wash_d_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["w_d"] = update.message.text
    await update.message.reply_text("💰 Стоимость (MDL):")
    return WASH_C

async def wash_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"МОЙКА: {ctx.user_data['w_d']}", update.message.text])
    await update.message.reply_text(f"✅ Мойка ({update.message.text} MDL) записана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# 4. Сервис и Ремонт
async def work_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["is_serv"] = "Сервис" in update.message.text
    await update.message.reply_text("⚙️ Опишите выполненные работы:")
    return SERV_S if ctx.user_data["is_serv"] else REPAIR_S

async def work_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    txt = update.message.text
    driver = get_driver(update.effective_user.id)
    car = next((c for c in get_ws("Автомобили").get_all_records() if str(c['plate']).upper() == driver['plate'].upper()), None)
    odo = car.get('odometer', 0)
    
    if ctx.user_data.get("is_serv"):
        sws = get_ws("Сервис")
        for i, r in enumerate(sws.get_all_records(), 2):
            if str(r['plate']).upper() == driver['plate'].upper() and str(r['service_type']).lower() in txt.lower():
                iv = int(r.get('interval', 10000))
                sws.update_cell(i, 3, odo)
                sws.update_cell(i, 5, int(odo) + iv)

    hws = get_ws("История_ТО")
    hws.append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], odo, txt, ""])
    row = len(hws.get_all_values())
    
    await update.message.reply_text("✅ Запись создана. Ждем цену от админа.", reply_markup=main_kb(update.effective_user.id))
    for aid in ADMIN_IDS:
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("💰 Ввести цену", callback_data=f"p_{row}")]])
        await ctx.bot.send_message(aid, f"🔧 <b>НОВАЯ РАБОТА: {driver['plate']}</b>\n{txt}", parse_mode="HTML", reply_markup=kb)
    return ConversationHandler.END

# 5. Админ: Ввод цены
async def admin_p_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["e_row"] = query.data.split("_")[1]
    await query.message.reply_text("Введите сумму (MDL):")
    return ADMIN_P

async def admin_p_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    val = "".join(filter(str.isdigit, update.message.text))
    get_ws("История_ТО").update_cell(int(ctx.user_data["e_row"]), 6, val)
    await update.message.reply_text(f"✅ Цена {val} MDL сохранена.")
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()

    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_init)],
        states={ODO_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_init)],
        states={
            FUEL_L: [MessageHandler(filters.TEXT, fuel_l_step)], 
            FUEL_C: [MessageHandler(filters.TEXT, fuel_c_step)], 
            FUEL_O: [MessageHandler(filters.TEXT, fuel_o_step)],
            FUEL_ST: [MessageHandler(filters.TEXT, fuel_save)]
        },
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🧽 Мойка$"), wash_init)],
        states={WASH_D: [MessageHandler(filters.TEXT, wash_d_step)], WASH_C: [MessageHandler(filters.TEXT, wash_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(⚙️ Сервис/ТО|🛠 Ремонт)$"), work_init)],
        states={SERV_S: [MessageHandler(filters.TEXT, work_save)], REPAIR_S: [MessageHandler(filters.TEXT, work_save)]},
        fallbacks=[MessageHandler(filters.ALL, start)]
    ))
    app.add_handler(ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_p_init, pattern="^p_")],
        states={ADMIN_P: [MessageHandler(filters.TEXT, admin_p_save)]},
        fallbacks=[CommandHandler("start", start)]
    ))

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_status))
    app.add_handler(MessageHandler(filters.Regex("^🚗 Все авто$"), cmd_all_cars))
    app.add_handler(MessageHandler(filters.Regex("^(📋 История|👑 Отчёт сегодня)$"), cmd_history))
    
    app.add_handler(MessageHandler(filters.ALL, start))
    print("🚀 Бот VanillaАвтомобили запущен")
    app.run_polling()

if __name__ == "__main__": main()
