import logging
import os
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, KeyboardButton
from telegram.ext import (
    Application, CommandHandler, MessageHandler,
    filters, ContextTypes, ConversationHandler
)
import gspread
from google.oauth2.service_account import Credentials

BOT_TOKEN = os.getenv("BOT_TOKEN", "8613265488:AAFe1sVGy8p7zCbeuI4y3mIbAxl8cXExAcE")
SHEET_ID  = os.getenv("SHEET_ID",  "100axoRGeQQnpYKZzb7k_hWStxueXF0yP88kQlZbHHAI")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "385450206").split(",") if x]

logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s", level=logging.INFO)
log = logging.getLogger(__name__)

STATE_FUEL_LITERS, STATE_FUEL_COST, STATE_FUEL_ODO, STATE_FUEL_STATION, STATE_ODO_KM = range(5)

def get_sheet():
    scopes = [
        "https://spreadsheets.google.com/feeds",
        "https://www.googleapis.com/auth/drive",
    ]
    creds = Credentials.from_service_account_file("credentials.json", scopes=scopes)
    return gspread.authorize(creds).open_by_key(SHEET_ID)

def get_worksheet(name):
    return get_sheet().worksheet(name)

def get_driver_info(telegram_id):
    try:
        records = get_worksheet("Водители").get_all_records()
        for r in records:
            if str(r.get("telegram_id")) == str(telegram_id):
                return r
    except Exception as e:
        log.error(f"get_driver_info: {e}")
    return None

def get_car_info(plate):
    try:
        records = get_worksheet("Автомобили").get_all_records()
        for r in records:
            if r.get("plate", "").upper() == plate.upper():
                return r
    except Exception as e:
        log.error(f"get_car_info: {e}")
    return None

def save_refuel(driver, liters, cost, odo, station):
    ws = get_worksheet("Заправки")
    now = datetime.now().strftime("%d.%m.%Y %H:%M")
    price = round(cost / liters, 2) if liters else 0
    ws.append_row([now, driver.get("plate",""), driver.get("name",""), driver.get("telegram_id",""), liters, cost, price, odo, station])
    return price

def save_odometer(driver, odo):
    ws = get_worksheet("Автомобили")
    for i, r in enumerate(ws.get_all_records(), start=2):
        if r.get("plate", "").upper() == driver.get("plate", "").upper():
            ws.update_cell(i, 5, odo)
            ws.update_cell(i, 6, datetime.now().strftime("%d.%m.%Y %H:%M"))
            return True
    return False

def get_stats_for_car(plate, limit=5):
    records = get_worksheet("Заправки").get_all_records()
    filtered = [r for r in records if r.get("plate", "").upper() == plate.upper()]
    return filtered[-limit:]

def get_all_stats_today():
    records = get_worksheet("Заправки").get_all_records()
    today = datetime.now().strftime("%d.%m.%Y")
    return [r for r in records if str(r.get("date_time", "")).startswith(today)]

def format_money(v):
    return f"{int(v):,}".replace(",", " ") + " MDL"

def format_liters(v):
    return f"{float(v):.1f} л"

def main_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
         [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")]],
        resize_keyboard=True
    )

def admin_keyboard():
    return ReplyKeyboardMarkup(
        [[KeyboardButton("⛽ Заправка"), KeyboardButton("📍 Пробег")],
         [KeyboardButton("📊 Мой статус"), KeyboardButton("📋 История")],
         [KeyboardButton("👑 Отчёт сегодня"), KeyboardButton("🚗 Все авто")]],
        resize_keyboard=True
    )

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text(
            f"👋 Вы не зарегистрированы в системе.\nВаш Telegram ID: <code>{uid}</code>",
            parse_mode="HTML"
        )
        return
    kb = admin_keyboard() if uid in ADMIN_IDS else main_keyboard()
    role = "👑 Администратор" if uid in ADMIN_IDS else "🚗 Водитель"
    await update.message.reply_text(
        f"✅ Добро пожаловать, <b>{driver['name']}</b>!\n\nРоль: {role}\nАвтомобиль: <b>{driver['plate']}</b>\n\nВыберите действие:",
        parse_mode="HTML", reply_markup=kb
    )

async def fuel_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text("❌ Вы не зарегистрированы.")
        return ConversationHandler.END
    ctx.user_data["driver"] = driver
    await update.message.reply_text(
        f"⛽ <b>Заправка · {driver['plate']}</b>\n\nВведите <b>литры</b>:\n<i>Пример: 45.5</i>",
        parse_mode="HTML"
    )
    return STATE_FUEL_LITERS

async def fuel_get_liters(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        liters = float(update.message.text.replace(",", "."))
        if liters <= 0 or liters > 500:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите число литров, например: 45.5")
        return STATE_FUEL_LITERS
    ctx.user_data["liters"] = liters
    await update.message.reply_text(
        f"✅ Литры: <b>{liters} л</b>\n\nВведите <b>сумму</b> в леях (MDL):\n<i>Пример: 560</i>",
        parse_mode="HTML"
    )
    return STATE_FUEL_COST

async def fuel_get_cost(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        cost = float(update.message.text.replace(",", ".").replace(" ", ""))
        if cost <= 0 or cost > 100000:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите сумму, например: 560")
        return STATE_FUEL_COST
    ctx.user_data["cost"] = cost
    await update.message.reply_text(
        f"✅ Сумма: <b>{format_money(cost)}</b>\n\nВведите <b>одометр</b> (км):\n<i>Или напишите «пропустить»</i>",
        parse_mode="HTML"
    )
    return STATE_FUEL_ODO

async def fuel_get_odo(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip().lower()
    if text in ("пропустить", "skip", "-"):
        ctx.user_data["odo"] = 0
    else:
        try:
            ctx.user_data["odo"] = int(text.replace(" ", ""))
        except:
            await update.message.reply_text("❌ Введите км или напишите «пропустить»")
            return STATE_FUEL_ODO
    await update.message.reply_text(
        "Введите название <b>АЗС</b>:\n<i>Или напишите «пропустить»</i>",
        parse_mode="HTML"
    )
    return STATE_FUEL_STATION

async def fuel_get_station(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()
    station = "" if text.lower() in ("пропустить", "skip", "-") else text
    driver = ctx.user_data["driver"]
    liters = ctx.user_data["liters"]
    cost   = ctx.user_data["cost"]
    odo    = ctx.user_data["odo"]
    try:
        price = save_refuel(driver, liters, cost, odo, station)
        if odo > 0:
            save_odometer(driver, odo)
        odo_str     = f"\n📍 Одометр: <b>{odo:,} км</b>".replace(",", " ") if odo else ""
        station_str = f"\n🏪 АЗС: <b>{station}</b>" if station else ""
        await update.message.reply_text(
            f"✅ <b>Заправка сохранена!</b>\n\n🚗 {driver['plate']}\n⛽ {format_liters(liters)}\n💰 {format_money(cost)}\n📈 {price} MDL/л{odo_str}{station_str}",
            parse_mode="HTML", reply_markup=main_keyboard()
        )
        for admin_id in ADMIN_IDS:
            try:
                await ctx.bot.send_message(
                    admin_id,
                    f"🔔 <b>Новая заправка</b>\n🚗 {driver['plate']} · {driver['name']}\n⛽ {format_liters(liters)} · {format_money(cost)} · {price} MDL/л{odo_str}{station_str}",
                    parse_mode="HTML"
                )
            except:
                pass
    except Exception as e:
        log.error(f"save error: {e}")
        await update.message.reply_text("❌ Ошибка сохранения.", reply_markup=main_keyboard())
    return ConversationHandler.END

async def odo_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text("❌ Вы не зарегистрированы.")
        return ConversationHandler.END
    ctx.user_data["driver"] = driver
    await update.message.reply_text(
        f"📍 <b>Пробег · {driver['plate']}</b>\n\nВведите <b>одометр</b> в км:\n<i>Пример: 45230</i>",
        parse_mode="HTML"
    )
    return STATE_ODO_KM

async def odo_get_km(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        odo = int(update.message.text.strip().replace(" ", ""))
        if odo <= 0 or odo > 9999999:
            raise ValueError
    except:
        await update.message.reply_text("❌ Введите число км")
        return STATE_ODO_KM
    driver = ctx.user_data["driver"]
    try:
        save_odometer(driver, odo)
        await update.message.reply_text(
            f"✅ Одометр обновлён!\n\n🚗 {driver['plate']}\n📍 <b>{odo:,} км</b>".replace(",", " "),
            parse_mode="HTML", reply_markup=main_keyboard()
        )
    except Exception as e:
        log.error(f"odo save: {e}")
        await update.message.reply_text("❌ Ошибка. Попробуйте позже.")
    return ConversationHandler.END

async def cmd_status(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text("❌ Вы не зарегистрированы.")
        return
    car = get_car_info(driver.get("plate", ""))
    records = get_stats_for_car(driver.get("plate", ""), 3)
    total_liters = sum(float(r.get("liters", 0)) for r in records)
    total_cost   = sum(float(r.get("cost", 0)) for r in records)
    odo_str = f"📍 Одометр: <b>{int(car['odometer']):,} км</b>\n".replace(",", " ") if car and car.get("odometer") else ""
    await update.message.reply_text(
        f"📊 <b>Статус · {driver['plate']}</b>\n\n👤 {driver.get('name','—')}\n{odo_str}\n📈 Последние {len(records)} заправки:\n⛽ {format_liters(total_liters)}\n💰 {format_money(total_cost)}",
        parse_mode="HTML", reply_markup=main_keyboard()
    )

async def cmd_history(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    driver = get_driver_info(uid)
    if not driver:
        await update.message.reply_text("❌ Вы не зарегистрированы.")
        return
    records = get_stats_for_car(driver.get("plate", ""), 5)
    if not records:
        await update.message.reply_text("📋 История пуста.")
        return
    lines = [f"📋 <b>История · {driver['plate']}</b>\n"]
    for r in reversed(records):
        lines.append(
            f"──────────────\n📅 {r.get('date_time','—')}\n"
            f"⛽ {format_liters(r.get('liters',0))} · {format_money(r.get('cost',0))}\n"
            f"📈 {r.get('price_per_liter','—')} MDL/л"
            + (f"\n🏪 {r['station']}" if r.get('station') else "")
        )
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=main_keyboard())

async def cmd_report_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    records = get_all_stats_today()
    if not records:
        await update.message.reply_text("📊 Сегодня заправок не было.")
        return
    total_liters = sum(float(r.get("liters", 0)) for r in records)
    total_cost   = sum(float(r.get("cost", 0)) for r in records)
    lines = [f"👑 <b>Отчёт за {datetime.now().strftime('%d.%m.%Y')}</b>\n"]
    lines.append(f"📊 Заправок: <b>{len(records)}</b>\n⛽ {format_liters(total_liters)}\n💰 {format_money(total_cost)}\n")
    by_car = {}
    for r in records:
        p = r.get("plate", "—")
        if p not in by_car:
            by_car[p] = {"liters": 0, "cost": 0, "driver": r.get("driver_name", "—")}
        by_car[p]["liters"] += float(r.get("liters", 0))
        by_car[p]["cost"]   += float(r.get("cost", 0))
    for plate, d in by_car.items():
        lines.append(f"🚗 <b>{plate}</b> · {d['driver']}\n   ⛽ {format_liters(d['liters'])} · {format_money(d['cost'])}")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_keyboard())

async def cmd_all_cars(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Только для администраторов.")
        return
    try:
        records = get_worksheet("Автомобили").get_all_records()
        if not records:
            await update.message.reply_text("🚗 Список пуст.")
            return
        lines = ["🚗 <b>Все автомобили</b>\n"]
        for r in records:
            odo = f"{int(r.get('odometer',0)):,}".replace(",", " ") if r.get("odometer") else "—"
            lines.append(f"──────────────\n🚗 <b>{r.get('plate','—')}</b> · {r.get('model','—')}\n👤 {r.get('driver_name','—')}\n📍 {odo} км")
        await update.message.reply_text("\n".join(lines), parse_mode="HTML", reply_markup=admin_keyboard())
    except Exception as e:
        log.error(f"all_cars: {e}")
        await update.message.reply_text("❌ Ошибка.")

async def cancel(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    kb = admin_keyboard() if uid in ADMIN_IDS else main_keyboard()
    await update.message.reply_text("❌ Отменено.", reply_markup=kb)
    return ConversationHandler.END

async def unknown_message(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    uid  = update.effective_user.id
    kb   = admin_keyboard() if uid in ADMIN_IDS else main_keyboard()
    if text == "⛽ Заправка":
        await fuel_start(update, ctx)
    elif text == "📍 Пробег":
        await odo_start(update, ctx)
    elif text == "📊 Мой статус":
        await cmd_status(update, ctx)
    elif text == "📋 История":
        await cmd_history(update, ctx)
    elif text == "👑 Отчёт сегодня":
        await cmd_report_today(update, ctx)
    elif text == "🚗 Все авто":
        await cmd_all_cars(update, ctx)
    else:
        await update.message.reply_text("Выберите действие из меню 👇", reply_markup=kb)

def main():
    app = Application.builder().token(BOT_TOKEN).build()

    refuel_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^⛽ Заправка$"), fuel_start),
        ],
        states={
            STATE_FUEL_LITERS:  [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_liters)],
            STATE_FUEL_COST:    [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_cost)],
            STATE_FUEL_ODO:     [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_odo)],
            STATE_FUEL_STATION: [MessageHandler(filters.TEXT & ~filters.COMMAND, fuel_get_station)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    odo_handler = ConversationHandler(
        entry_points=[
            MessageHandler(filters.Regex("^📍 Пробег$"), odo_start),
        ],
        states={
            STATE_ODO_KM: [MessageHandler(filters.TEXT & ~filters.COMMAND, odo_get_km)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("cancel", cancel))
    app.add_handler(refuel_handler)
    app.add_handler(odo_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, unknown_message))

    log.info("Bot started!")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()
