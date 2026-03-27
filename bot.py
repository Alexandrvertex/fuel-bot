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
    
    kb = [
        [InlineKeyboardButton(f"{b_mark} Кузов", callback_data="t_body")],
        [InlineKeyboardButton(f"{i_mark} Салон", callback_data="t_interior")]
    ]
    if selected:
        kb.append([InlineKeyboardButton("➡️ Подтвердить выбор", callback_data="wash_confirm")])
    return InlineKeyboardMarkup(kb)

# --- БАЗОВЫЕ КОМАНДЫ ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return ConversationHandler.END
    await update.message.reply_text(f"✅ Привет, {driver['name']}!", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

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

# --- ИСТОРИЯ (ТОЛЬКО ОБЩАЯ) ---
async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver(uid)
    is_admin = uid in ADMIN_IDS
    
    h_recs = get_ws("История_ТО").get_all_records()
    f_recs = get_ws("Заправки").get_all_records()
    lines, total = [], 0.0
    
    for r in h_recs:
        if (is_admin or r['plate'] == driver['plate']):
            cost = parse_val(r.get('cost', 0))
            total += cost
            lines.append(f"• {r['date']} | {r['plate']} | {r['work_details']} | {cost:,.0f} MDL")
            
    for f in f_recs:
        if (is_admin or f['plate'] == driver['plate']):
            f_date = str(f.get('date_time', '')).split()[0]
            cost = parse_val(f.get('cost', 0))
            total += cost
            lines.append(f"• {f_date} | {f['plate']} | ⛽ {f['liters']}л | {cost:,.0f} MDL")

    await update.message.reply_text(f"📋 <b>ИСТОРИЯ (30 дн)</b>\n\n" + ("\n".join(lines[-20:]) if lines else "Записей нет.") + f"\n\n💰 <b>ИТОГО: {total:,.2f} MDL</b>".replace(",", " "), parse_mode="HTML")

# --- НОВЫЙ ОТЧЕТ ЗА СЕГОДНЯ ---
async def cmd_report_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    today_str = datetime.now().strftime("%d.%m.%Y")
    
    # Находим все привязанные авто к этому пользователю
    drivers_recs = get_ws("Водители").get_all_records()
    linked_plates = [str(r['plate']).upper() for r in drivers_recs if str(r.get('telegram_id')) == str(uid)]
    
    if not linked_plates:
        await update.message.reply_text("❌ За вашим профилем не закреплено ни одного автомобиля.")
        return

    # Загружаем данные из таблиц один раз для скорости
    cars_recs = get_ws("Автомобили").get_all_records()
    serv_recs = get_ws("Сервис").get_all_records()
    hist_recs = get_ws("История_ТО").get_all_records()
    fuel_recs = get_ws("Заправки").get_all_records()

    report_lines = [f"👑 <b>ОТЧЕТ ЗА СЕГОДНЯ ({today_str})</b>\n"]
    grand_total = 0.0

    for plate in linked_plates:
        car_info = next((c for c in cars_recs if str(c.get('plate', '')).upper() == plate), {})
        odo = int(car_info.get('odometer', 0))
        tech_insp = car_info.get('tech_inspection', 'Нет данных')
        insurance = car_info.get('insurance', 'Нет данных')

        report_lines.append(f"🚗 <b>{plate}</b>")
        report_lines.append(f"📍 Пробег: {odo:,} км".replace(",", " "))
        report_lines.append(f"📄 Тех. осмотр до: {tech_insp}")
        report_lines.append(f"🛡 Страховка до: {insurance}\n")

        # Затраты за сегодня
        car_total = 0.0
        expenses_text = []

        for r in hist_recs:
            if str(r.get('plate', '')).upper() == plate and r.get('date') == today_str:
                cost = parse_val(r.get('cost', 0))
                car_total += cost
                expenses_text.append(f"  • {r.get('work_details', 'Работы')} | {cost:,.0f} MDL")

        for f in fuel_recs:
            f_date = str(f.get('date_time', '')).split()[0]
            if str(f.get('plate', '')).upper() == plate and f_date == today_str:
                cost = parse_val(f.get('cost', 0))
                car_total += cost
                expenses_text.append(f"  • ⛽ {f.get('liters', 0)}л | {cost:,.0f} MDL")

        if expenses_text:
            report_lines.append("💰 <b>Затраты за сегодня:</b>")
            report_lines.extend(expenses_text)
            report_lines.append(f"  <i>Итого по авто: {car_total:,.0f} MDL</i>\n")
        else:
            report_lines.append("💰 Затраты за сегодня: 0 MDL\n")

        grand_total += car_total

        # Остаток до сервиса
        report_lines.append("🛠 <b>Остаток до сервиса:</b>")
        car_services = [s for s in serv_recs if str(s.get('plate', '')).upper() == plate]
        if car_services:
            for s in car_services:
                nxt = int(s.get('next_service_odo', 0))
                rem = nxt - odo
                icon = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
                report_lines.append(f"  {icon} {s.get('service_type', 'ТО')}: ост. {rem:,} км".replace(",", " "))
        else:
            report_lines.append("  Регламент не задан")
            
        report_lines.append("\n" + "—"*20 + "\n")

    report_lines.append(f"💵 <b>ОБЩИЕ ЗАТРАТЫ ЗА СЕГОДНЯ: {grand_total:,.2f} MDL</b>".replace(",", " "))

    await update.message.reply_text("\n".join(report_lines), parse_mode="HTML")

# --- ЛОГИКА МОЙКИ ---
async def wash_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["w_selected"] = []
    await update.message.reply_text("🧽 Выберите тип мойки (можно оба):", reply_markup=get_wash_kb([]))
    return WASH_TYPE

async def wash_toggle(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    choice = query.data.replace("t_", "")
    selected = ctx.user_data.get("w_selected", [])
    
    if choice in selected:
        selected.remove(choice)
    else:
        selected.append(choice)
    
    ctx.user_data["w_selected"] = selected
    await query.edit_message_reply_markup(reply_markup=get_wash_kb(selected))
    return WASH_TYPE

async def wash_confirm_selection(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    
    mapping = {"body": "Кузов", "interior": "Салон"}
    readable = [mapping[item] for item in ctx.user_data["w_selected"]]
    ctx.user_data["w_final_desc"] = " + ".join(readable)
    
    await query.edit_message_text(
        f"Выбрано: <b>{ctx.user_data['w_final_desc']}</b>\n\nВведите общую стоимость (MDL):", 
        parse_mode="HTML"
    )
    return WASH_C

async def wash_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    cost = update.message.text
    desc = ctx.user_data.get("w_final_desc", "Мойка")
    
    get_ws("История_ТО").append_row([
        datetime.now().strftime("%d.%m.%Y"), 
        driver['plate'], 
        driver['name'], 
        "-", 
        f"МОЙКА: {desc}", 
        cost
    ])
    await update.message.reply_text(
        f"✅ Данные сохранены: {desc} за {cost} MDL", 
        reply_markup=main_kb(update.effective_user.id)
    )
    return ConversationHandler.END

# --- ЛОГИКА ПРОБЕГА ---
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

# --- ЛОГИКА ЗАПРАВКИ ---
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
    await update.message.reply_text("🏢 Название АЗС:")
    return FUEL_ST

async def fuel_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    liters = parse_val(ctx.user_data.get("f_l", "0"))
    cost = parse_val(ctx.user_data.get("f_c", "0"))
    odo = ctx.user_data.get("f_o", "0")
    row = [
        datetime.now().strftime("%d.%m.%Y %H:%M"), 
        driver['plate'], 
        driver['name'], 
        str(driver['telegram_id']), 
        liters, 
        cost, 
        round(cost/liters, 2) if liters > 0 else 0, 
        odo, 
        update.message.text
    ]
    get_ws("Заправки").append_row(row)
    await update.message.reply_text("✅ Заправка записана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЛОГИКА СЕРВИСА ---
async def work_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    driver = get_driver(update.effective_user.id)
    if not driver: return ConversationHandler.END
    services = [s for s in get_ws("Сервис").get_all_records() if str(s['plate']).upper() == driver['plate'].upper()]
    if not services:
        await update.message.reply_text("❌ Регламент не задан в таблице.")
        return ConversationHandler.END
    keyboard = [[InlineKeyboardButton(f"🛠 {s['service_type']}", callback_data=f"svc_{s['service_type']}")] for s in services]
    await update.message.reply_text("Выберите выполненную операцию:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SERV_TYPE

async def service_selected(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ctx.user_data["selected_service"] = query.data.replace("svc_", "")
    await query.edit_message_text(f"Выбрано: <b>{ctx.user_data['selected_service']}</b>\nВведите текущий пробег:", parse_mode="HTML")
    return SERV_ODO

async def service_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    odo_val = parse_val(update.message.text)
    service_name = ctx.user_data["selected_service"]
    driver = get_driver(update.effective_user.id)
    sws = get_ws("Сервис")
    for i, r in enumerate(sws.get_all_records(), 2):
        if str(r['plate']).upper() == driver['plate'].upper() and str(r['service_type']) == service_name:
            iv = int(r.get('interval', 10000))
            sws.update_cell(i, 3, int(odo_val))
            sws.update_cell(i, 5, int(odo_val) + iv)
            break
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], int(odo_val), f"ТО: {service_name}", "0"])
    await update.message.reply_text("✅ Данные ТО сохранены", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЛОГИКА РЕМОНТА ---
async def repair_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🛠 Опишите что именно ремонтировали:")
    return REPAIR_S

async def repair_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    driver = get_driver(update.effective_user.id)
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), driver['plate'], driver['name'], "-", f"РЕМОНТ: {update.message.text}", "0"])
    await update.message.reply_text("✅ Запись о ремонте создана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- АДМИН: ЦЕНА ---
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

    handlers = [
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^📍 Пробег$"), odo_init)],
            states={ODO_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)]},
            fallbacks=[MessageHandler(filters.ALL, start)]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_init)],
            states={
                FUEL_L: [MessageHandler(filters.TEXT, fuel_l_step)], 
                FUEL_C: [MessageHandler(filters.TEXT, fuel_c_step)], 
                FUEL_O: [MessageHandler(filters.TEXT, fuel_o_step)], 
                FUEL_ST: [MessageHandler(filters.TEXT, fuel_save)]
            },
            fallbacks=[MessageHandler(filters.ALL, start)]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^🧽 Мойка$"), wash_init)],
            states={
                WASH_TYPE: [
                    CallbackQueryHandler(wash_toggle, pattern="^t_"),
                    CallbackQueryHandler(wash_confirm_selection, pattern="^wash_confirm")
                ],
                WASH_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_save)]
            },
            fallbacks=[MessageHandler(filters.ALL, start)]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), work_init)],
            states={
                SERV_TYPE: [CallbackQueryHandler(service_selected, pattern="^svc_")], 
                SERV_ODO: [MessageHandler(filters.TEXT, service_save)]
            },
            fallbacks=[MessageHandler(filters.ALL, start)]
        ),
        ConversationHandler(
            entry_points=[MessageHandler(filters.Regex("^🛠 Ремонт$"), repair_init)],
            states={REPAIR_S: [MessageHandler(filters.TEXT, repair_save)]},
            fallbacks=[MessageHandler(filters.ALL, start)]
        ),
        ConversationHandler(
            entry_points=[CallbackQueryHandler(admin_p_init, pattern="^p_")],
            states={ADMIN_P: [MessageHandler(filters.TEXT, admin_p_save)]},
            fallbacks=[CommandHandler("start", start)]
        )
    ]

    for h in handlers: app.add_handler(h)

    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_status))
    app.add_handler(MessageHandler(filters.Regex("^📋 История$"), cmd_history))
    app.add_handler(MessageHandler(filters.Regex("^👑 Отчёт сегодня$"), cmd_report_today))
    app.add_handler(MessageHandler(filters.Regex("^🚗 Все авто$"), cmd_status)) # Если нужно, можно тоже вынести логику "Все авто"
    
    app.run_polling()

if __name__ == "__main__":
    main()
