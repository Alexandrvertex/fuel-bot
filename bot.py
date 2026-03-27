import logging
import os
import json
import re
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
(CHOOSING_CAR, ODO_S, WASH_TYPE, WASH_C, FUEL_L, FUEL_C, FUEL_O, FUEL_ST,
 SERV_S, REPAIR_S, ADMIN_P, SERV_TYPE, SERV_ODO) = range(13)

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

def get_user_cars(uid):
    """Возвращает список словарей [{'plate': '...', 'name': 'BMW X5 (ABC 123)'}]"""
    try:
        driver_recs = get_ws("Водители").get_all_records()
        my_plates = [str(r['plate']).upper() for r in driver_recs if str(r.get("telegram_id")) == str(uid)]
        
        if not my_plates: return []
        
        car_recs = get_ws("Автомобили").get_all_records()
        user_cars = []
        for plate in my_plates:
            car = next((c for c in car_recs if str(c.get('plate', '')).upper() == plate), None)
            if car:
                # Собираем имя из колонок brand и model (если они есть) или используем госномер
                brand = car.get('brand', '')
                model = car.get('model', '')
                full_name = f"{brand} {model} ({plate})".strip() if brand else plate
                user_cars.append({'plate': plate, 'display': full_name})
            else:
                user_cars.append({'plate': plate, 'display': plate})
        return user_cars
    except Exception as e:
        logging.error(f"Ошибка получения списка авто: {e}")
        return []

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

async def select_car_kb(update: Update, ctx: ContextTypes.DEFAULT_TYPE, next_state):
    user_cars = get_user_cars(update.effective_user.id)
    if not user_cars:
        await update.message.reply_text("❌ Доступ запрещен. Вы не найдены в таблице.")
        return ConversationHandler.END
    
    if len(user_cars) == 1:
        ctx.user_data["active_plate"] = user_cars[0]['plate']
        # Вызываем функцию инициализации напрямую в зависимости от того, куда шли
        if next_state == ODO_S: return await odo_init(update, ctx)
        if next_state == FUEL_L: return await fuel_init(update, ctx)
        if next_state == WASH_TYPE: return await wash_init(update, ctx)
        if next_state == SERV_TYPE: return await work_init(update, ctx)
        if next_state == REPAIR_S: return await repair_init(update, ctx)
        return ConversationHandler.END

    # Если машин несколько — рисуем кнопки с названиями
    ctx.user_data["car_map"] = {c['display']: c['plate'] for c in user_cars}
    kb = [[KeyboardButton(c['display'])] for c in user_cars]
    await update.message.reply_text(
        "🚗 Выберите автомобиль:", 
        reply_markup=ReplyKeyboardMarkup(kb, one_time_keyboard=True, resize_keyboard=True)
    )
    ctx.user_data["next_step_after_car"] = next_state
    return CHOOSING_CAR

# --- СТАРТ / СБРОС ---
async def start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data.clear()
    user_cars = get_user_cars(update.effective_user.id)
    if not user_cars:
        await update.message.reply_text("❌ Доступ запрещен.")
        return ConversationHandler.END
    await update.message.reply_text("✅ Бот готов к работе.", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ОБРАБОТЧИК ВЫБОРА МАШИНЫ ---
async def car_chosen(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    choice = update.message.text
    car_map = ctx.user_data.get("car_map", {})
    
    if choice not in car_map:
        await update.message.reply_text("Пожалуйста, используйте кнопки выбора авто.")
        return CHOOSING_CAR
        
    ctx.user_data["active_plate"] = car_map[choice]
    nxt = ctx.user_data.get("next_step_after_car")
    
    # Перенаправляем в нужную ветку
    if nxt == ODO_S: return await odo_init(update, ctx)
    if nxt == FUEL_L: return await fuel_init(update, ctx)
    if nxt == WASH_TYPE: return await wash_init(update, ctx)
    if nxt == SERV_TYPE: return await work_init(update, ctx)
    if nxt == REPAIR_S: return await repair_init(update, ctx)
    return ConversationHandler.END

# --- КОМАНДЫ (СТАТУС И ИСТОРИЯ) ---
async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    user_cars = get_user_cars(update.effective_user.id)
    if not user_cars: return
    
    cars_recs = get_ws("Автомобили").get_all_records()
    serv_recs = get_ws("Сервис").get_all_records()
    results = []

    for car_entry in user_cars:
        plate = car_entry['plate']
        car = next((c for c in cars_recs if str(c['plate']).upper() == plate), None)
        if not car: continue
        
        odo = int(car.get('odometer', 0))
        plan = []
        for s in serv_recs:
            if str(s['plate']).upper() == plate:
                nxt = int(s.get('next_service_odo', 0))
                rem = nxt - odo
                status = "🚨" if rem <= 0 else ("⚠️" if rem < 1000 else "✅")
                plan.append(f"{status} {s['service_type']}: {nxt:,} км (ост. {rem:,} км)")
        
        results.append(f"📊 <b>{car_entry['display']}</b>\n🛣 Пробег: {odo:,} км\n" + ("\n".join(plan) if plan else "Регламент не задан"))

    await update.message.reply_text(("\n\n" + "—"*10 + "\n").join(results).replace(",", " "), parse_mode="HTML")

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    user_cars = get_user_cars(uid)
    my_plates = [c['plate'] for c in user_cars]
    is_admin = uid in ADMIN_IDS
    
    h_recs, f_recs = get_ws("История_ТО").get_all_records(), get_ws("Заправки").get_all_records()
    lines, total = [], 0.0
    
    for r in h_recs:
        p = str(r.get('plate','')).upper()
        if is_admin or p in my_plates:
            cost = parse_val(r.get('cost', 0))
            total += cost
            lines.append(f"• {r['date']} | {p} | {r['work_details']} | {cost:,.0f} MDL")
            
    for f in f_recs:
        p = str(f.get('plate','')).upper()
        if is_admin or p in my_plates:
            f_date = str(f.get('date_time', '')).split()[0]
            cost = parse_val(f.get('cost', 0))
            total += cost
            lines.append(f"• {f_date} | {p} | ⛽ {f['liters']}л | {cost:,.0f} MDL")

    await update.message.reply_text(f"📋 <b>ИСТОРИЯ ЗАТРАТ</b>\n\n" + ("\n".join(lines[-20:]) if lines else "Нет записей") + f"\n\n💰 <b>ИТОГО: {total:,.2f} MDL</b>".replace(",", " "), parse_mode="HTML")

# --- ЛОГИКА ВВОДА (ПРОБЕГ, ЗАПРАВКА И Т.Д.) ---
async def entry_odo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await select_car_kb(update, ctx, ODO_S)

async def odo_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"📍 [{ctx.user_data['active_plate']}]\nВведите текущий пробег:", reply_markup=main_kb(update.effective_user.id))
    return ODO_S

async def odo_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    val = "".join(filter(str.isdigit, update.message.text))
    plate = ctx.user_data["active_plate"]
    ws = get_ws("Автомобили")
    for i, r in enumerate(ws.get_all_records(), 2):
        if str(r['plate']).upper() == plate:
            ws.update_cell(i, 5, int(val))
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            break
    await update.message.reply_text(f"✅ Пробег {int(val):,} км сохранен", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def entry_fuel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await select_car_kb(update, ctx, FUEL_L)

async def fuel_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(f"⛽ [{ctx.user_data['active_plate']}]\nСколько литров?")
    return FUEL_L

async def fuel_save(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    plate = ctx.user_data["active_plate"]
    liters = parse_val(ctx.user_data.get("f_l", "0"))
    cost = parse_val(ctx.user_data.get("f_c", "0"))
    # (Остальной код заправки аналогичен предыдущим версиям...)
    row = [datetime.now().strftime("%d.%m.%Y %H:%M"), plate, "Водитель", str(update.effective_user.id), liters, cost, round(cost/liters, 2) if liters > 0 else 0, ctx.user_data.get("f_o", "0"), update.message.text]
    get_ws("Заправки").append_row(row)
    await update.message.reply_text("✅ Заправка записана", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

async def entry_wash(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    return await select_car_kb(update, ctx, WASH_TYPE)

async def wash_init(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    ctx.user_data["w_selected"] = []
    await update.message.reply_text(f"🧽 [{ctx.user_data['active_plate']}]\nТип мойки:", reply_markup=get_wash_kb([]))
    return WASH_TYPE

# (Остальные функции моек, ТО и Ремонтов вызываются через entry_... аналогично)

def get_wash_kb(selected):
    b_mark = "✅" if "body" in selected else "⬜"
    i_mark = "✅" if "interior" in selected else "⬜"
    kb = [[InlineKeyboardButton(f"{b_mark} Кузов", callback_data="t_body")],
          [InlineKeyboardButton(f"{i_mark} Салон", callback_data="t_interior")]]
    if selected:
        kb.append([InlineKeyboardButton("➡️ Подтвердить", callback_data="wash_confirm")])
    return InlineKeyboardMarkup(kb)

async def wash_toggle(update, ctx):
    query = update.callback_query; await query.answer()
    choice = query.data.replace("t_", "")
    sel = ctx.user_data.get("w_selected", [])
    if choice in sel: sel.remove(choice)
    else: sel.append(choice)
    ctx.user_data["w_selected"] = sel
    await query.edit_message_reply_markup(reply_markup=get_wash_kb(sel))
    return WASH_TYPE

async def wash_confirm_selection(update, ctx):
    query = update.callback_query; await query.answer()
    mapping = {"body": "Кузов", "interior": "Салон"}
    ctx.user_data["w_final_desc"] = " + ".join([mapping[i] for i in ctx.user_data["w_selected"]])
    await query.edit_message_text(f"Выбрано: <b>{ctx.user_data['w_final_desc']}</b>\nВведите цену:", parse_mode="HTML")
    return WASH_C

async def wash_save(update, ctx):
    if update.message.text in MENU_BTNS: return await start(update, ctx)
    plate = ctx.user_data["active_plate"]
    get_ws("История_ТО").append_row([datetime.now().strftime("%d.%m.%Y"), plate, "Водитель", "-", f"МОЙКА: {ctx.user_data.get('w_final_desc')}", update.message.text])
    await update.message.reply_text("✅ Сохранено", reply_markup=main_kb(update.effective_user.id))
    return ConversationHandler.END

# --- ЗАПУСК ---
def main():
    app = Application.builder().token(BOT_TOKEN).build()
    cancel = CommandHandler("start", start)

    conv_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📍 Пробег$"), entry_odo),
            MessageHandler(filters.Regex("^⛽ Заправка$"), entry_fuel),
            MessageHandler(filters.Regex("^🧽 Мойка$"), entry_wash),
            MessageHandler(filters.Regex("^⚙️ Сервис/ТО$"), lambda u, c: select_car_kb(u, c, SERV_TYPE)),
            MessageHandler(filters.Regex("^🛠 Ремонт$"), lambda u, c: select_car_kb(u, c, REPAIR_S))
        ],
        states={
            CHOOSING_CAR: [MessageHandler(filters.TEXT & ~filters.COMMAND, car_chosen)],
            ODO_S: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_save)],
            FUEL_L: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: (c.user_data.update({"f_l": u.message.text}), FUEL_C)[1])],
            FUEL_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: (c.user_data.update({"f_c": u.message.text}), FUEL_O)[1])],
            FUEL_O: [MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: (c.user_data.update({"f_o": u.message.text}), FUEL_ST)[1])],
            FUEL_ST: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_save)],
            WASH_TYPE: [CallbackQueryHandler(wash_toggle, pattern="^t_"), CallbackQueryHandler(wash_confirm_selection, pattern="^wash_confirm")],
            WASH_C: [MessageHandler(filters.TEXT & ~filters.COMMAND, wash_save)],
        },
        fallbacks=[cancel]
    )

    app.add_handler(conv_handler)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.Regex("^📊 Мой статус$"), cmd_status))
    app.add_handler(MessageHandler(filters.Regex("^📋 История$"), cmd_history))
    app.add_handler(MessageHandler(filters.Regex("^👑 Отчёт сегодня$"), lambda u, c: start(u, c))) # Для примера
    app.run_polling()

if __name__ == "__main__": main()
