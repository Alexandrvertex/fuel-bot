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
BOT_TOKEN = os.getenv("BOT_TOKEN", "8613265488:AAFe1sVGy8p7zCbeuI4y3mIbAxl8cXExAcE")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [385450206] 

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)

# Состояния диалогов
(ODO_S, WASH_TYPE, WASH_C, FUEL_L, FUEL_C, FUEL_O, FUEL_ST,
 SERV_S, REPAIR_S, ADMIN_P, SERV_TYPE, SERV_ODO) = range(12)

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

# --- КЛАВИАТУРЫ ---
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

def get_wash_kb(selected):
    b_mark = "✅" if "body" in selected else "⬜"
    i_mark = "✅" if "interior" in selected else "⬜"
    kb = [[InlineKeyboardButton(f"{b_mark} Кузов", callback_data="t_body")],
          [InlineKeyboardButton(f"{i_mark} Салон", callback_data="t_interior")]]
    if selected:
        kb.append([InlineKeyboardButton("➡️ Подтвердить выбор", callback_data="wash_confirm")])
    return InlineKeyboardMarkup(kb)

# --- СТАРТ / СБРОС ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    driver = get_driver(update.effective_user.id)
    if not driver: return ConversationHandler.END
    await update.message.reply_text(f"✅ Привет, {driver['name']}!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- БАЗОВЫЕ КОМАНДЫ ---
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
    text = (f"📊 <b>СТАТУС: {driver['plate']}</b>\n🛣 Пробег: {odo:,} км\n\n🛠 <b>План регламентных работ:</b>\n" + ("\n".join(plan) if plan else "Регламент не задан")).replace(",", " ")
    await update.message.reply_text(text, parse_mode="HTML")

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver(uid)
    is_admin = uid in ADMIN_IDS
    h_recs, f_recs = get_ws("История_ТО").get_all_records(), get_ws("Заправки").get_all_records()
    lines, total = [], 0.0
    for r in h_recs:
        if is_admin or r['plate'] == driver['plate']:
            cost = parse_val(r.get('cost', 0))
            total += cost
            lines.append(f"• {r['date']} | {r['plate']} | {r['work_details']} | {cost:,.0f} MDL")
    for f in f_recs:
        if is_admin or f['plate'] == driver['plate']:
            f_date = str(f.get('date_time', '')).split()[0]
            cost = parse_val(f.get('cost', 0))
            total += cost
            lines.append(f"• {f_date} | {f['plate']} | ⛽ {f['liters']}л | {cost:,.0f} MDL")
    await update.message.reply_text(f"📋 <b>ИСТОРИЯ (30 дн)</b>\n\n" + ("\n".join(lines[-20:]) if lines else "Записей нет.") + f"\n\n💰 <b>ИТОГО: {total:,.2f} MDL</b>".replace(",", " "), parse_mode="HTML")

# --- ОТЧЕТЫ (СЕГОДНЯ И ГОД) ---
async def cmd_report_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    today_str = datetime.now().strftime("%d.%m.%Y")
    drivers_recs = get_ws("Водители").get_all_records()
    linked_plates = [str(r['plate']).upper() for r in drivers_recs if str(r.get('telegram_id')) == str(uid)]
    if not linked_plates: return

    cars_recs, serv_recs = get_ws("Автомобили").get_all_records(), get_ws("Сервис").get_all_records()
    hist_recs, fuel_recs = get_ws("История_ТО").get_all_records(), get_ws("Заправки").get_all_records()
    report_lines, grand_total = [f"👑 <b>ОТЧЕТ ЗА СЕГОДНЯ ({today_str})</b>\n"], 0.0

    for plate in linked_plates:
        car_info = next((c for c in cars_recs if str(c.get('plate', '')).upper() == plate), {})
        odo = int(car_info.get('odometer', 0))
        report_lines.append(f"🚗 <b>{plate}</b>\n📍 Пробег: {odo:,} км\n📄 Тех. осмотр до: {car_info.get('tech_inspection', '—')}\n🛡 Страховка до: {car_info.get('insurance', '—')}\n")
        
        car_total = sum(parse_val(r.get('cost', 0)) for r in hist_recs if str(r.get('plate','')).upper() == plate and r.get('date') == today_str)
        car_total += sum(parse_val(f.get('cost', 0)) for f in fuel_recs if str(f.get('plate','')).upper() == plate and str(f.get('date_time','')).split()[0] == today_str)
        grand_total += car_total
        report_lines.append(f"💰 Затраты сегодня: {car_total:,.0f} MDL")
        
        car_services = [s for s in serv_recs if str(s.get('plate', '')).upper() == plate]
        report_lines.append("🛠 <b>Сервис:</b>")
        for s in car_services:
            rem = int(s.get('next_service_odo', 0)) - odo
            icon = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
            report_lines.append(f"  {icon} {s.get('service_type')}: ост. {rem:,} км")
        report_lines.append("\n" + "—"*15 + "\n")
    report_lines.append(f"💵 <b>ИТОГО: {grand_total:,.2f} MDL</b>".replace(",", " "))
    await update.message.reply_text("\n".join(report_lines), parse_mode="HTML")

async def cmd_report_full(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    current_year = datetime.now().strftime("%Y")
    drivers_recs = get_ws("Водители").get_all_records()
    linked_plates = [str(r['plate']).upper() for r in drivers_recs if str(r.get('telegram_id')) == str(uid)]
    if not linked_plates: return

    hist_recs, fuel_recs = get_ws("История_ТО").get_all_records(), get_ws("Заправки").get_all_records()
    report_lines, grand_annual_total = [f"📊 <b>ГОДОВОЙ ОТЧЕТ ({current_year})</b>\n"], 0.0

    for plate in linked_plates:
        service_year = sum(parse_val(r.get('cost', 0)) for r in hist_recs if str(r.get('plate','')).upper() == plate and current_year in str(r.get('date','')))
        fuel_year = sum(parse_val(f.get('cost', 0)) for f in fuel_recs if str(f.get('plate','')).upper() == plate and current_year in str(f.get('date_time','')))
        car_total = fuel_year + service_year
        grand_annual_total += car_total
        report_lines.append(f"🚗 <b>{plate}</b>\n  ⛽ Заправки: {fuel_year:,.0f} MDL\n  🛠 Сервис: {service_year:,.0f} MDL\n  💰 <b>Итого: {car_total:,.0f} MDL</b>\n")
    report_lines.append(f"📈 <b>ИТОГО ПАРК: {grand_annual_total:,.2f} MDL</b>".replace(",", " "))
    await update.message.reply_text("\n".join(report_lines), parse_mode="HTML")

# --- ЛОГИКА ОПРОСОВ (МОЙКА, ПРОБЕГ, ЗАПРАВКА, ТО, РЕМОНТ) ---
async def wash_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["w_selected"] = []
    await update.message.reply_text("🧽 Тип мойки:", reply_markup=get_wash_kb([]))
    return WASH_TYPE

async def wash_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    choice = query.data.replace("t_", "")
    sel = ctx.user_data.get("w_selected", [])
    if choice in sel: sel.remove(choice)
    else: sel.append(choice)
    ctx.user_data["w_selected"] = sel
    await query.edit_message_reply_markup(reply_markup=get_wash_kb(sel))
    return WASH_TYPE

async def wash_confirm_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    mapping = {"body": "Кузов", "interior": "Салон"}
    ctx.user_data["w_final_desc"] = " + ".join([mapping[i] for i in ctx.user_data["w_selected"]])
    await query.edit_message_text(f"Выбрано: <b>{ctx.user_data['w_final_desc']}</b>\nВведите цену (MDL):", parse_mode="HTML")
    return WASH_C

async def wash_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"МОЙКА: {ctx.user_data['w_final_desc']}", update.message.text])
    await update.message.reply_text("✅ Сохранено", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def odo_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("📍 Текущий пробег:")
    return ODO_S

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    val = "".join(filter(str.isdigit, update.message.text))
    driver = get_driver(update.effective_user.id)
    ws = get_ws("Автомобили")
    for i, r in enumerate(ws.get_all_records(), 2):
        if str(r['plate']).upper() == driver['plate'].upper():
            ws.update_cell(i, 5, int(val))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            break
    await update.message.reply_text(f"✅ Сохранено: {val} км", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def fuel_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("⛽ Литры?")
    return FUEL_L

async def fuel_l_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_l"] = update.message.text
    await update.message.reply_text("💰 Сумма?")
    return FUEL_C

async def fuel_c_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_c"] = update.message.text
    await update.message.reply_text("📍 Пробег?")
    return FUEL_O

async def fuel_o_step(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    ctx.user_data["f_o"] = update.message.text
    await update.message.reply_text("🏢 АЗС?")
    return FUEL_ST

async def fuel_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    liters, cost = parse_val(ctx.user_data["f_l"]), parse_val(ctx.user_data["f_c"])
    row = [datetime.now().strftime("%d.%m.%Y %H:%M"), driver['plate'], driver['name'], str(driver['telegram_id']), liters, cost, round(cost/liters, 2) if liters > 0 else 0, ctx.user_data["f_o"], update.message.text]
    get_ws("Заправки").append_row(row)
    await update.message.reply_text("✅ Заправка записана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def work_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    kb = [[InlineKeyboardButton(f"🛠 {s['service_type']}", callback_data=f"svc_{s['service_type']}")] for s in get_ws("Сервис").get_all_records() if str(s['plate']).upper() == driver['plate'].upper()]
    await update.message.reply_text("Что сделали?", reply_markup=InlineKeyboardMarkup(kb))
    return SERV_TYPE

async def service_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["selected_service"] = query.data.replace("svc_", "")
    await query.edit_message_text(f"Выбрано: {ctx.user_data['selected_service']}\nПробег:")
    return SERV_ODO

async def service_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    odo, svc, driver = parse_val(update.message.text), ctx.user_data["selected_service"], get_driver(update.effective_user.id)
    sws = get_ws("Сервис")
    for i, r in enumerate(sws.get_all_records(), 2):
        if str(r['plate']).upper() == driver['plate'].upper() and str(r['service_type']) == svc:
            sws.update_cell(i, 3, int(odo))
            sws.update_cell(i, 5, int(odo) + int(r.get('interval', 10000)))
            break
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], int(odo), f"ТО: {svc}", "0"])
    await update.message.reply_text("✅ ТО сохранено", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def repair_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Что ремонтировали?")
    return REPAIR_S

async def repair_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"РЕМОНТ: {update.message.text}", "0"])
    await update.message.reply_text("✅ Записано", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    cancel = CommandHandler("start", start)
    handlers = [
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_init)], states={ODO_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]}, fallbacks=[cancel]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_init)], states={FUEL_L: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_l_step)], FUEL_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_c_step)], FUEL_O: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_o_step)], FUEL_ST: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_save)]}, fallbacks=[cancel]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🧽 Мойка$"), wash_init)], states={WASH_TYPE: [CallbackQueryHandler(wash_toggle, pattern="^t_"), CallbackQueryHandler(wash_confirm_selection, pattern="^wash_confirm")], WASH_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_save)]}, fallbacks=[cancel]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), work_init)], states={SERV_TYPE: [CallbackQueryHandler(service_selected, pattern="^svc_")], SERV_ODO: [MessageHandler(filters.TEXT & ~filters.COMMAND, service_save)]}, fallbacks=[cancel]),
        ConversationHandler(entry_points=[MessageHandler(filters.Regex("^🛠 Ремонт$"), repair_init)], states={REPAIR_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, repair_save)]}, fallbacks=[cancel])
    ]
    for h in handlers: app.add_handler(h)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_status))
    app.add_handler(MessageHandler(filters.Regex("^📋 История$"), cmd_history))
    app.add_handler(MessageHandler(filters.Regex("^👑 Отчёт сегодня$"), cmd_report_today))
    app.add_handler(MessageHandler(filters.Regex("^🚗 Все авто$"), cmd_report_full))
    app.run_polling()

if __name__ == "__main__": main()
