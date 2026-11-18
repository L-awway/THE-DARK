import asyncio
import logging
import json
import os
import random
import copy
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from datetime import datetime, timedelta
from aiogram.types import FSInputFile, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton

# ===================================================
# 1. НАСТРОЙКИ
# ===================================================
BOT_TOKEN = "8983642305:AAH4fec67NMDL3zlE2LdgUyianxhxN_YA1E"
CHAT_ID = -1002734456748
ADMINS = ["anicardDark", "DanielDerecha", "Dasyero", "Ahahanta", "Zhongli_3112"]
MAX_SCORE = 16
LOW_SCORE_THRESHOLD = 12
MAX_WARNINGS = 3
MAX_SKIPS = 2
TIMEZONE_OFFSET = 3
DATA_FILE = "data.json"

# ===================================================
# 2. РАБОТА С ДАННЫМИ
# ===================================================
def load_data():
    if os.path.exists(DATA_FILE):
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"users": {}, "auto_raids": {}}

def save_data(data):
    with open(DATA_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

db = load_data()
users = db.setdefault("users", {})

def get_next_user_id():
    used_ids = set()
    for uid in users.keys():
        if uid.startswith("user_"):
            try:
                used_ids.add(int(uid.split("_")[1]))
            except:
                pass
    next_num = 1
    while next_num in used_ids:
        next_num += 1
    return f"user_{next_num}"

# ===== ГЛОБАЛЬНЫЕ ПЕРЕМЕННЫЕ =====
last_notify_hour = None
raid_event = asyncio.Event()
raid2_event = asyncio.Event()
raid3_event = asyncio.Event()
raid_bonus_until = None

default_raids = {
    1: {"time": "10:55", "members": []}
}

auto_raids = {}

def load_raids():
    global auto_raids
    if "auto_raids" in db and db["auto_raids"]:
        auto_raids = {int(k): v for k, v in db["auto_raids"].items()}
    else:
        auto_raids = copy.deepcopy(default_raids)
        db["auto_raids"] = auto_raids
        save_data(db)

def save_raids():
    db["auto_raids"] = auto_raids
    save_data(db)

def reset_raids():
    global auto_raids
    for num in default_raids:
        if num in auto_raids:
            auto_raids[num]["time"] = default_raids[num]["time"]
        else:
            auto_raids[num] = copy.deepcopy(default_raids[num])
    save_raids()

load_raids()

# ===================================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ===================================================
def get_user_by_username(username):
    username_lower = username.lower()
    for uid, data in users.items():
        if data["username"].lower() == username_lower:
            return uid, data
    return None, None

def ensure_user_exists(username):
    uid, data = get_user_by_username(username)
    if uid is None:
        uid = get_next_user_id()
        users[uid] = {
            "username": username,
            "today_score": 0,
            "warnings": 0,
            "skips": 0,
            "history": {str(d): None for d in range(1, 32)},
            "weekly_history": {},
            "report_time": {},
            "bank": 0,
            "bank_blocked_until": None,
            "last_bank_accrual_day": None
        }
        save_data(db)
    return uid, users[uid]

def accrue_bank(user_data, score):
    day_num = str(get_season_day())
    details = []

    blocked_until = user_data.get("bank_blocked_until")
    if blocked_until:
        today_str = get_moscow_time().strftime("%Y-%m-%d")
        if today_str <= blocked_until:
            return 0, ["⛔ Блокировка: начисления приостановлены"]

    if user_data.get("last_bank_accrual_day") == day_num:
        return 0, ["Уже начислено сегодня"]

    if score == 16: base_delta = 1.0
    elif score == 14: base_delta = 0.5
    elif score == 13: base_delta = 0.25
    elif score == 12: base_delta = 0.0
    elif score in (10, 11): base_delta = -0.25
    elif score in (8, 9): base_delta = -0.5
    elif score in (6, 7): base_delta = -0.75
    else: base_delta = -1.0

    is_raid_active = False
    if raid_bonus_until is not None and get_moscow_time() < raid_bonus_until:
        is_raid_active = True

    total_delta = 0.0

    if is_raid_active:
        if base_delta > 0:
            details.append(f"+{base_delta} пт (база за {score} очков)")
            details.append("🔥 x2 (бонус рейда на базу)")
            total_delta += base_delta * 2
        elif base_delta == 0:
            details.append("0 пт (база за 12 очков — норма)")
        else:
            details.append(f"🛡️ Минус за базу ({base_delta} пт) отменён (рейд)")
    else:
        if base_delta != 0:
            details.append(f"{base_delta} пт (база за {score} очков)")
        elif score == 12:
            details.append("0 пт (база за 12 очков — норма)")
        total_delta += base_delta

    last_score = user_data.get("last_day_score", None)
    if last_score is not None:
        diff = score - last_score
        if diff > 0:
            if last_score >= 8:
                growth_bonus = round(diff * 0.1, 2)
                total_delta += growth_bonus
                details.append(f"+{growth_bonus} пт (рост с {last_score} до {score})")
        elif diff < 0:
            abs_diff = abs(diff)
            if last_score == 16 and score <= 14:
                drop_penalty = round((abs_diff - 1) * 0.1, 2)
            else:
                drop_penalty = round(abs_diff * 0.1, 2)
            if is_raid_active:
                details.append(f"🛡️ Падение ({last_score}→{score}) отменено (рейд)")
            else:
                total_delta -= drop_penalty
                details.append(f"-{drop_penalty} пт (падение с {last_score} до {score})")

    streaks = user_data.setdefault("streaks", {"s16": 0, "s14": 0})
    if score == 16:
        streaks["s16"] += 1
        streaks["s14"] = 0
        if streaks["s16"] > 1:
            streak_bonus = round((streaks["s16"] - 1) * 0.15, 2)
            total_delta += streak_bonus
            details.append(f"+{streak_bonus} пт (серия 16: день {streaks['s16']})")
    elif score == 14:
        streaks["s14"] += 1
        streaks["s16"] = 0
        if streaks["s14"] > 1:
            streak_bonus = round((streaks["s14"] - 1) * 0.1, 2)
            total_delta += streak_bonus
            details.append(f"+{streak_bonus} пт (серия 14: день {streaks['s14']})")
    else:
        streaks["s16"] = 0
        streaks["s14"] = 0

    user_data["last_day_score"] = score
    total_delta = round(total_delta, 2)

    if total_delta != 0:
        current_bank = round(user_data.get("bank", 0), 2)
        new_bank = round(current_bank + total_delta, 2)
        if new_bank < 0: new_bank = 0
        user_data["bank"] = new_bank
        user_data["last_bank_accrual_day"] = day_num

    return total_delta, details

# ===================================================
# 3. ДАТА И СЕЗОНЫ
# ===================================================
def get_moscow_time():
    return datetime.utcnow() + timedelta(hours=3)

def get_season_day():
    now = get_moscow_time()
    if now.day >= 5:
        season_start = datetime(now.year, now.month, 5, 0, 0)
    else:
        if now.month == 1:
            season_start = datetime(now.year - 1, 12, 5, 0, 0)
        else:
            season_start = datetime(now.year, now.month - 1, 5, 0, 0)
    day_num = (now - season_start).days + 1
    return min(day_num, 31)

# ===================================================
# 4. БОТ
# ===================================================
logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ===================================================
# 5. КОМАНДЫ ДЛЯ ВСЕХ
# ===================================================
@dp.message(Command("старт"))
async def start_cmd(message: types.Message):
    try:
        video = FSInputFile("video.mp4")
        await message.reply_video(video=video, caption="👋 Добро пожаловать в бота клана The Dark Wars of Light!")
    except Exception as e:
        print(f"⚠️ Видео не загрузилось: {e}")

    await message.reply(
        "📌 КОМАНДЫ ДЛЯ ВСЕХ:\n"
        "/и 14 — сдать отчёт (можно слитно, напр. /и14)\n"
        "/я — моя статистика\n"
        "/банк — мой банк\n"
        "/топовость — топ-5 клана\n"
        "/стата @Nick — статистика игрока\n"
        "/прогулы — список прогульщиков\n"
        "/сливы — кто часто <12 очков\n"
        "/дедлайн — кто не отыграл\n"
        "/диаграмма — график сдачи отчётов\n\n"
        "🚀 РЕЙДЫ (ЗАПИСЬ):\n"
        "По умолчанию рейд проходит в **10:55**.\n"
        "/+рейд (или /+рейд1) — записаться на рейд\n"
        "/+рейд2, /+рейд3 — если админ добавил дополнительные рейды\n"
        "/-рейд (и т.д.) — отписаться\n"
        "/рейдинфо (и т.д.) — кто записался\n\n"
        "👑 КОМАНДЫ ДЛЯ АДМИНОВ:\n"
        "/добавить @Nick\n"
        "/удалить @Nick\n"
        "/исправить @Nick 14\n"
        "/куплено @Nick 5 — списать\n"
        "/штраф @Nick 5 — блокировка банка\n"
        "/отменаштрафа @Nick — снять блокировку\n"
        "/смена @Старый @Новый — сменить юзернейм\n"
        "/состав\n\n"
        "⚙️ УПРАВЛЕНИЕ РЕЙДАМИ:\n"
        "/создатьрейд 4 23:00 — создать/перенести рейд №4\n"
        "/отменарейдаплана 1 — отменить рейд №1 на сегодня\n"
        "*(В 00:00 время рейдов сбрасывается, участники сохраняются)*"
    )

banana_commands = ["и", "И", "i", "I"] + [f"и{i}" for i in range(17)] + [f"И{i}" for i in range(17)] + [f"i{i}" for i in range(17)] + [f"I{i}" for i in range(17)]

@dp.message(Command(commands=banana_commands))
async def report_score(message: types.Message):
    text = message.text.strip()
    parts = text.split(maxsplit=1)
    command = parts[0]

    if len(parts) > 1:
        args_raw = parts[1].strip()
    else:
        args_raw = command[2:]

    if "@" in args_raw and not args_raw.startswith("@"):
        after_at = args_raw.split("@")[1]
        if after_at and after_at[0].isalpha():
            args_raw = args_raw.split("@")[0].strip()
        else:
            await message.reply("❗ Неверный формат. Используйте: /и 14")
            return

    target_username = None

    if args_raw.startswith("@"):
        args = args_raw.split()
        target_username = args[0]
        if len(args) < 2:
            await message.reply("❗ Используйте: /и @Nick 14")
            return
        try:
            score = int(args[1])
        except:
            await message.reply("❗ Используйте: /и @Nick 14")
            return
        if message.from_user.username not in ADMINS:
            await message.reply("⛔ Только админы могут писать за других.")
            return
    else:
        try:
            score = int(args_raw.split()[0])
        except:
            await message.reply("❗ Используйте: /и 14")
            return
        username = "@" + message.from_user.username if message.from_user.username else None
        if not username:
            await message.reply("❌ У вас не установлен юзернейм!")
            return
        uid, data = get_user_by_username(username)
        if uid is None:
            await message.reply("❌ Вы не зарегистрированы в клане!")
            return
        target_username = username

    if score == 15:
        await message.reply("❗ Ошибка: 15 очков физически невозможно набрать. Проверь подсчёт!")
        return
    if score < 0 or score > MAX_SCORE:
        await message.reply(f"❗ Очки должны быть от 0 до {MAX_SCORE}")
        return

    uid, user_data = ensure_user_exists(target_username)
    day_num = str(get_season_day())

    if user_data["history"].get(day_num) is not None:
        await message.reply("❗ Ты уже сдал отчёт сегодня!")
        return

    user_data["history"][day_num] = score
    user_data["today_score"] = score
    user_data.setdefault("report_time", {})[day_num] = get_moscow_time().strftime("%H:%M")
    today_date_str = get_moscow_time().strftime("%Y-%m-%d")
    user_data.setdefault("weekly_history", {})[today_date_str] = score
    save_data(db)

    lines = []

    if score == 0:
        user_data["warnings"] += 1
        lines.append(f"😅 {user_data['username']} — 0 очков! Неудачный день. Слив #{user_data['warnings']}")
        if user_data["warnings"] >= MAX_WARNINGS:
            await bot.send_message(CHAT_ID, f"⚠️ {user_data['username']} — {user_data['warnings']} дней с результатом <12!")
    elif score < LOW_SCORE_THRESHOLD:
        user_data["warnings"] += 1
        lines.append(f"⚠️ {user_data['username']} — {score}/{MAX_SCORE} — ниже 12! Слив #{user_data['warnings']}")
        if user_data["warnings"] >= MAX_WARNINGS:
            await bot.send_message(CHAT_ID, f"⚠️ {user_data['username']} — {user_data['warnings']} дней с результатом <12!")
    else:
        lines.append(f"✅ {user_data['username']} — {score}/{MAX_SCORE}!")

    accrued, bank_details = accrue_bank(user_data, score)
    lines.append(f"🏦 Баланс: {user_data['bank']} пт")
    if accrued > 0:
        lines.append(f"💰 Итого начислено: +{accrued} пт")
    elif accrued < 0:
        lines.append(f"💰 Итого списано: {accrued} пт")
    else:
        lines.append(f"💰 Без изменений")

    if bank_details:
        if "Уже начислено сегодня" not in bank_details[0] and "Блокировка" not in bank_details[0]:
            lines.append("🔍 Из чего сложилось:")
            for detail in bank_details:
                lines.append(f"• {detail}")

    if score == 16:
        motivation = random.choice(["🔥 Только победы достойны твоего имени!", "🔥 Ты оформил серию из одних побед!", "💪 Идеальный день! 16 из 16!", "👑 Ты — гордость клана! 16/16!", "⚡ Ни одного поражения! Так держать!"])
    elif score == 14:
        motivation = random.choice(["✊ Сегодня ты непобедим, присылай свой бой с ничьей!", "😎 14 из 16! Одна ничья — не беда!"])
    elif score == 13:
        motivation = random.choice(["😔 Враг повергнул тебя единожды, присылай ход этого поражения!", "🤏 13 из 16! Чуть-чуть не хватило до идеала!"])
    elif score == 12:
        motivation = random.choice(["🤝 У тебя ничьи в клановых сражениях, хорошо не поражения, но прислать ход боя, где ты не одержал победу стоит", "📝 12 из 16! Пришли бои, где не было победы!"])
    elif score == 11:
        motivation = "⚔️ Где-то ты не одержал победы, присылай этот бой"
    elif 7 <= score <= 10:
        motivation = random.choice(["📉 Где-то ты проиграл. И, наверное, сыграл вничью? - присылай бои!", "⚠️ Не лучший день, но мы разберёмся! Присылай бои!"])
    elif score == 6:
        motivation = "🚨 Ты проиграл два сражения - срочно пришли их!"
    elif score == 5:
        motivation = "🆘 Либо у тебя победа и ничья с поражениями, либо вообще их три 0-0! - анализ этих боёв необходим!"
    elif 1 <= score <= 4:
        motivation = random.choice(["💀 Мало хороших боёв/// - ты знаешь что делать", "😬 Сегодня тяжёлый день. Присылай бои для разбора!"])
    else:
        motivation = random.choice(["☠️ ЭТО ПОЛНОЕ ФАТАЛИТИ", "💀 0 из 16... Присылай бои, разберём!"])

    lines.append(motivation)
    await message.reply("\n".join(lines))
    save_data(db)

@dp.message(Command("я"))
async def my_stats(message: types.Message):
    username = "@" + message.from_user.username if message.from_user.username else None
    if not username:
        await message.reply("❌ Установите юзернейм.")
        return

    uid, data = get_user_by_username(username)
    if uid is None:
        await message.reply("❌ Нет данных.")
        return

    now = get_moscow_time()
    start_of_week = now - timedelta(days=now.weekday())
    start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)

    weekly_scores = []
    for date_str, sc in data.get("weekly_history", {}).items():
        try:
            date_obj = datetime.strptime(date_str, "%Y-%m-%d")
            if date_obj >= start_of_week:
                weekly_scores.append(sc)
        except:
            pass

    week_total = sum(weekly_scores)
    scores = [s for s in data["history"].values() if isinstance(s, int)]
    total = sum(scores) if scores else 0
    avg = round(total / len(scores), 1) if scores else 0

    await message.reply(
        f"📊 Статистика {data['username']}:\n"
        f"Сегодня: {data['today_score']}/{MAX_SCORE}\n"
        f"📅 За эту неделю: {week_total} очков ({len(weekly_scores)} дней)\n"
        f"🏦 Банк: {data.get('bank', 0)} пт\n"
        f"Прогулов: {data['skips']}/{MAX_SKIPS}\n"
        f"Предупреждений: {data['warnings']} (из {MAX_WARNINGS})\n"
        f"Средний балл: {avg}\n"
        f"Дней в сезоне: {len(scores)}"
    )

@dp.message(Command("топовость"))
async def top_cmd(message: types.Message):
    if not users:
        await message.reply("❌ Нет данных.")
        return
    sorted_users = sorted(users.items(), key=lambda x: sum(s for s in x[1]["history"].values() if isinstance(s, int)), reverse=True)
    top = sorted_users[:5]
    text = "🏆 ТОП-5 КЛАНА:\n"
    for i, (uid, data) in enumerate(top, 1):
        scores = [s for s in data["history"].values() if isinstance(s, int)]
        total = sum(scores) if scores else 0
        avg = round(total / len(scores), 1) if scores else 0
        medal = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i-1]
        clean_username = data['username'].replace('@', '')
        text += f"{medal} {clean_username} — {total} очков (ср. {avg})\n"
    await message.reply(text)

@dp.message(Command("стата"))
async def player_stats(message: types.Message):
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❗ Используйте: /стата @Nickname")
        return
    username = args[1]
    if not username.startswith("@"):
        username = "@" + username
    for uid, data in users.items():
        if data["username"].lower() == username.lower():
            now = get_moscow_time()
            start_of_week = now - timedelta(days=now.weekday())
            start_of_week = start_of_week.replace(hour=0, minute=0, second=0, microsecond=0)
            weekly_scores = []
            for date_str, sc in data.get("weekly_history", {}).items():
                try:
                    date_obj = datetime.strptime(date_str, "%Y-%m-%d")
                    if date_obj >= start_of_week:
                        weekly_scores.append(sc)
                except:
                    pass
            week_total = sum(weekly_scores)
            scores = [s for s in data["history"].values() if isinstance(s, int)]
            total = sum(scores) if scores else 0
            avg = round(total / len(scores), 1) if scores else 0
            await message.reply(
                f"📊 Статистика {data['username']}:\n"
                f"Сегодня: {data['today_score']}/{MAX_SCORE}\n"
                f"📅 За эту неделю: {week_total} очков ({len(weekly_scores)} дней)\n"
                f"🏦 Банк: {data.get('bank', 0)} пт\n"
                f"Прогулов: {data['skips']}/{MAX_SKIPS}\n"
                f"Предупреждений: {data['warnings']} (из {MAX_WARNINGS})\n"
                f"Средний балл: {avg}\n"
                f"Дней в сезоне: {len(scores)}"
            )
            return
    await message.reply(f"❌ Игрок {username} не найден.")

@dp.message(Command("прогулы"))
async def skip_list(message: types.Message):
    skip_list_data = []
    for uid, data in users.items():
        if data["skips"] > 0:
            clean_username = data['username'].replace('@', '')
            skip_list_data.append((clean_username, data["skips"]))
    if not skip_list_data:
        await message.reply("✅ Нет игроков с прогулами!")
        return
    skip_list_data.sort(key=lambda x: x[1], reverse=True)
    text = "🚫 РЕЙТИНГ ПРОГУЛЬЩИКОВ\n\n"
    for i, (username, count) in enumerate(skip_list_data, 1):
        medal = "🔴 " if count >= 3 else ("🟡 " if count >= 2 else "🟢 ")
        text += f"{i}. {medal}{username} — {count}\n"
    await message.reply(text)

@dp.message(Command("сливы"))
async def warning_list(message: types.Message):
    warning_data = []
    for uid, data in users.items():
        if data["warnings"] > 0:
            clean_username = data['username'].replace('@', '')
            warning_data.append((clean_username, data["warnings"]))
    if not warning_data:
        await message.reply("✅ Все показывают хорошие результаты!")
        return
    warning_data.sort(key=lambda x: x[1], reverse=True)
    text = "📊 РЕЙТИНГ СЛИВОВ (дней с <12 очков), для трёх и более необходимо разобраться с причиной низкой результативности\n\n"
    for i, (username, count) in enumerate(warning_data, 1):
        medal = "🔴 " if count >= 3 else ("🟡 " if count >= 2 else "🟢 ")
        text += f"{i}. {medal}{username} — {count} дней\n"
    await message.reply(text)

@dp.message(Command("время"))
async def show_time(message: types.Message):
    now = get_moscow_time()
    deadline = now.replace(hour=23, minute=59, second=0, microsecond=0)
    time_left = (deadline - now).total_seconds()
    time_str = f"{int(time_left // 3600)} ч {int((time_left % 3600) // 60)} мин" if time_left > 0 else "⏰ Прошёл"
    await message.reply(
        f"🕐 Время (МСК): {now.strftime('%H:%M:%S')}\n"
        f"📅 Дата: {now.strftime('%d.%m.%Y')}\n"
        f"📆 День сезона: {get_season_day()}/31\n"
        f"⏳ До дедлайна: {time_str}"
    )

@dp.message(Command("дедлайн"))
async def show_deadline(message: types.Message):
    now = get_moscow_time()
    day_num = str(get_season_day())
    missing = [data["username"] for uid, data in users.items() if data["history"].get(day_num) is None]
    msg = f"⏳ До дедлайна осталось: {23 - now.hour} ч {59 - now.minute} мин\n\n"
    if missing:
        msg += f"🚫 Не отыграли ({len(missing)} чел.):\n" + "\n".join(missing)
    else:
        msg += "✅ Все отыграли!"
    await message.reply(msg)

@dp.message(Command("банк"))
async def my_bank(message: types.Message):
    username = "@" + message.from_user.username if message.from_user.username else None
    if not username:
        await message.reply("❌ Установите юзернейм.")
        return
    uid, data = get_user_by_username(username)
    if uid is None:
        await message.reply("❌ Вы не зарегистрированы!")
        return
    bank = data.get("bank", 0)
    blocked_until = data.get("bank_blocked_until")
    blocked_text = f"\n⛔ Блокировка до {blocked_until}" if blocked_until and get_moscow_time().strftime("%Y-%m-%d") <= blocked_until else ""
    await message.reply(f"🏦 Банк {data['username']}\n\nБаланс: {bank} пт{blocked_text}")

# ===================================================
# ДИАГРАММА
# ===================================================
@dp.message(Command("диаграмма"))
async def show_diagram(message: types.Message):
    day_num = str(get_season_day())
    data_points = []
    for uid, data in users.items():
        score = data["history"].get(day_num)
        r_time = data.get("report_time", {}).get(day_num)
        if isinstance(score, int) and r_time:
            clean_name = data["username"].replace("@", "")
            data_points.append((clean_name, score, r_time))

    if not data_points:
        await message.reply("📊 Сегодня ещё никто не сдал отчёт, диаграмма пуста.")
        return

    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt

        plt.figure(figsize=(10, 6))
        times, scores, names = [], [], []
        for name, score, r_time in data_points:
            h, m = map(int, r_time.split(':'))
            times.append(h + m / 60.0)
            scores.append(score)
            names.append(name)

        plt.scatter(times, scores, color='#ff5722', s=100, edgecolors='black', alpha=0.8, zorder=3)
        for i, name in enumerate(names):
            plt.annotate(name, (times[i], scores[i]), textcoords="offset points", xytext=(0,10), ha='center', fontsize=8, fontweight='bold', color='#333333')

        plt.title(f'📊 Результаты за День {day_num}', fontsize=14, fontweight='bold')
        plt.xlabel('Время сдачи (МСК)', fontsize=12)
        plt.ylabel('Очки (0-16)', fontsize=12)
        plt.xlim(0, 24)
        plt.ylim(0, 17)
        plt.xticks(range(0, 25, 2), [f"{i:02d}:00" for i in range(0, 25, 2)])
        plt.yticks(range(0, 17, 2))
        plt.grid(True, linestyle='--', alpha=0.6, zorder=1)

        import io
        img_buf = io.BytesIO()
        plt.savefig(img_buf, format='png', dpi=150, bbox_inches='tight')
        img_buf.seek(0)
        plt.close()

        photo = BufferedInputFile(img_buf.read(), filename=f"diagram_{day_num}.png")
        await message.reply_photo(photo, caption=f"📈 График сдачи отчётов за {day_num} день сезона")

    except ImportError:
        text = f"📊 ДИАГРАММА ЗА ДЕНЬ {day_num}:\n\n"
        data_points.sort(key=lambda x: x[2])
        for name, score, r_time in data_points:
            bar = "█" * score
            text += f"[{r_time}] {name} — {score}/16 {bar}\n"
        await message.reply(text + "\n💡 Для красивого графика установите библиотеку matplotlib.")

# ===================================================
# РЕЙДЫ
# ===================================================
@dp.message(Command(commands=["+рейд", "+рейд1", "рейд1", "+рейд2", "рейд2", "+рейд3", "рейд3", "+рейд4", "рейд4", "+рейд5", "рейд5"]))
async def join_raid_general(message: types.Message):
    text = message.text.split()
    if len(text) > 1 and text[1].isdigit():
        raid_num = int(text[1])
    else:
        cmd = text[0].replace("/", "").replace("+", "")
        import re
        match = re.search(r'\d+', cmd)
        raid_num = int(match.group()) if match else 1

    if raid_num not in auto_raids:
        await message.reply(f"❌ Рейд №{raid_num} не существует.")
        return

    username = "@" + message.from_user.username if message.from_user.username else None
    if not username:
        await message.reply("❌ У вас нет юзернейма!")
        return

    raid_time_str = auto_raids[raid_num]["time"]
    raid_h, raid_m = map(int, raid_time_str.split(":"))
    now = get_moscow_time()
    raid_time_today = now.replace(hour=raid_h, minute=raid_m, second=0, microsecond=0)

    if now > raid_time_today + timedelta(minutes=35):
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="✅ Да, на завтра", callback_data=f"raid_next_day_{raid_num}")],
            [InlineKeyboardButton(text="❌ Нет, отмена", callback_data="raid_cancel")]
        ])
        await message.reply(
            f"⏰ Рейд №{raid_num} на сегодня уже прошёл!\n"
            f"Хотите записаться на завтра в это же время ({raid_time_str})?",
            reply_markup=keyboard
        )
        return

    raid = auto_raids[raid_num]
    if username in raid["members"]:
        await message.reply(f"❌ Ты уже записан на Рейд №{raid_num} ({raid['time']})!")
        return

    raid["members"].append(username)
    save_raids()
    await message.reply(f"✅ {username} записан на Рейд №{raid_num} в {raid['time']}!\n👥 Всего: {len(raid['members'])}")

@dp.callback_query(lambda c: c.data.startswith("raid_next_day_"))
async def process_raid_next_day(callback: types.CallbackQuery):
    raid_num = int(callback.data.split("_")[-1])
    username = "@" + callback.from_user.username if callback.from_user.username else None
    if not username:
        await callback.answer("❌ У вас нет юзернейма!", show_alert=True)
        return
    if raid_num not in auto_raids:
        await callback.answer("❌ Рейд не существует.", show_alert=True)
        return
    raid = auto_raids[raid_num]
    if username in raid["members"]:
        await callback.answer("❌ Ты уже записан на этот рейд!", show_alert=True)
        return
    raid["members"].append(username)
    save_raids()
    await callback.message.edit_text(
        f"✅ {username} записан на Рейд №{raid_num} на завтра в {raid['time']}!\n"
        f"👥 Всего записано: {len(raid['members'])}"
    )
    await callback.answer()

@dp.callback_query(lambda c: c.data == "raid_cancel")
async def process_raid_cancel(callback: types.CallbackQuery):
    await callback.message.edit_text("❌ Запись отменена. Возвращайтесь позже!")
    await callback.answer()

@dp.message(Command(commands=["-рейд", "-рейд1", "рейд1-", "-рейд2", "рейд2-", "-рейд3", "рейд3-"]))
async def leave_raid_general(message: types.Message):
    text = message.text.split()
    if len(text) > 1 and text[1].isdigit():
        raid_num = int(text[1])
    else:
        cmd = text[0].replace("/", "").replace("-", "")
        import re
        match = re.search(r'\d+', cmd)
        raid_num = int(match.group()) if match else 1

    if raid_num not in auto_raids:
        await message.reply(f"❌ Рейд №{raid_num} не существует.")
        return

    username = "@" + message.from_user.username if message.from_user.username else None
    raid = auto_raids[raid_num]
    if username not in raid["members"]:
        await message.reply(f"❌ Ты не записан на Рейд №{raid_num}.")
        return

    raid["members"].remove(username)
    save_raids()
    await message.reply(f"✅ {username} отписан от Рейда №{raid_num}.\n👥 Осталось: {len(raid['members'])}")

@dp.message(Command(commands=["рейдинфо", "рейдинфо1", "рейд1инфо", "рейдинфо2", "рейдинфо3"]))
async def raid_info_general(message: types.Message):
    text = message.text.split()
    if len(text) > 1 and text[1].isdigit():
        raid_num = int(text[1])
    else:
        cmd = text[0].replace("/", "")
        import re
        match = re.search(r'\d+', cmd)
        raid_num = int(match.group()) if match else 1

    if raid_num not in auto_raids:
        await message.reply(f"❌ Рейд №{raid_num} не существует.")
        return

    raid = auto_raids[raid_num]
    members = [m.replace("@", "") for m in raid["members"]]
    members_str = "\n".join(members) if members else "❌ Никто не записался"
    await message.reply(f"📋 ИНФО О РЕЙДЕ №{raid_num} ({raid['time']})\n👥 Участники ({len(members)}):\n{members_str}")

# ===================================================
# ШУТОЧНЫЕ КОМАНДЫ
# ===================================================
@dp.message(Command(commands=["банан", "бананы", "БАНАН", "БАНАНЫ"]))
async def banana_cmd(message: types.Message):
    await message.reply("АМ-АМ-АМ")

@dp.message(Command(commands=["виноград", "винограды", "ВИНОГРАД", "ВИНОГРАДЫ"]))
async def grape_cmd(message: types.Message):
    await message.reply("Спасибо, хорошо!")

@dp.message(Command(commands=["ч", "Ч", "ch", "Ch", "CH"]))
async def boss_cmd(message: types.Message):
    await message.reply("Ты просто БОСС!")

@dp.message(Command(commands=["хамам", "ХАМАМ", "Hamam", "HAMAM"]))
async def hamam_cmd(message: types.Message):
    await message.reply("ОДИН БАНАН!")

# ===================================================
# 6. АДМИН-КОМАНДЫ
# ===================================================
def is_admin(username):
    return username in ADMINS

@dp.message(Command("добавить"))
async def add_user(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❗ Используйте: /добавить @Nickname")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    for uid, data in users.items():
        if data["username"].lower() == username.lower():
            await message.reply(f"❌ {username} уже в клане.")
            return
    new_id = get_next_user_id()
    users[new_id] = {
        "username": username,
        "today_score": 0,
        "warnings": 0,
        "skips": 0,
        "history": {str(d): None for d in range(1, 32)},
        "weekly_history": {},
        "report_time": {},
        "bank": 0,
        "bank_blocked_until": None,
        "last_bank_accrual_day": None
    }
    save_data(db)
    await message.reply(f"✅ {username} добавлен в клан!")

@dp.message(Command("удалить"))
async def remove_user(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❗ Используйте: /удалить @Nickname")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    for uid, data in list(users.items()):
        if data["username"].lower() == username.lower():
            del users[uid]
            save_data(db)
            await message.reply(f"✅ {username} удалён.")
            return
    await message.reply(f"❌ {username} не найден.")

@dp.message(Command("смена"))
async def change_username(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❗ Используйте: /смена @Старый @Новый")
        return
    old_username = args[1] if args[1].startswith("@") else "@" + args[1]
    new_username = args[2] if args[2].startswith("@") else "@" + args[2]
    uid_new, _ = get_user_by_username(new_username)
    if uid_new is not None:
        await message.reply(f"❌ {new_username} уже занят.")
        return
    uid, data = get_user_by_username(old_username)
    if uid is None:
        await message.reply(f"❌ {old_username} не найден.")
        return
    data["username"] = new_username
    save_data(db)
    await message.reply(f"✅ Юзернейм изменён: {old_username} → {new_username}\n🏦 Банк: {data.get('bank', 0)} пт\n📊 Прогулы: {data['skips']}, Предупреждения: {data['warnings']}")

@dp.message(Command("исправить"))
async def fix_score(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❗ Используйте: /исправить @Nickname 14")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    try:
        new_score = int(args[2])
        if new_score < 0 or new_score > MAX_SCORE or new_score == 15:
            raise ValueError
    except:
        await message.reply("❗ Неверное число (от 0 до 16, кроме 15).")
        return

    for uid, data in users.items():
        if data["username"].lower() == username.lower():
            day_num = str(get_season_day())
            old_score = data["history"].get(day_num)

            if old_score is not None:
                if old_score != "SKIP":
                    old_delta = 0
                    if old_score == 16: old_delta = 1.0
                    elif old_score in (14, 15): old_delta = 0.5
                    elif old_score == 13: old_delta = 0.25
                    elif old_score == 12: old_delta = 0.0
                    elif old_score in (10, 11): old_delta = -0.25
                    elif old_score in (8, 9): old_delta = -0.5
                    elif old_score in (6, 7): old_delta = -0.75
                    else: old_delta = -1.0

                    if old_delta > 0 and raid_bonus_until is not None:
                        data["bank"] = round(data.get("bank", 0) - (old_delta * 2), 2)
                    else:
                        data["bank"] = round(data.get("bank", 0) - old_delta, 2)
                    if data["bank"] < 0: data["bank"] = 0

                    streaks = data.get("streaks", {"s16": 0, "s14": 0})
                    if old_score == 16 and streaks["s16"] > 0:
                        streaks["s16"] -= 1
                    elif old_score == 14 and streaks["s14"] > 0:
                        streaks["s14"] -= 1

                    if old_score < LOW_SCORE_THRESHOLD and data.get("warnings", 0) > 0:
                        data["warnings"] -= 1
                else:
                    if data.get("skips", 0) > 0:
                        data["skips"] -= 1

            data["history"][day_num] = new_score
            data["today_score"] = new_score
            data["last_bank_accrual_day"] = None

            today_date_str = get_moscow_time().strftime("%Y-%m-%d")
            if "weekly_history" in data and today_date_str in data["weekly_history"]:
                data["weekly_history"][today_date_str] = new_score

            new_delta, _ = accrue_bank(data, new_score)

            if new_score < LOW_SCORE_THRESHOLD:
                data["warnings"] = data.get("warnings", 0) + 1
                if data["warnings"] >= MAX_WARNINGS:
                    await bot.send_message(CHAT_ID, f"⚠️ {data['username']} — {data['warnings']} дней с результатом <12! (после исправления)")

            save_data(db)
            bank_info = ""
            if new_delta > 0: bank_info = f"\n🏦 Начислено: +{new_delta} пт"
            elif new_delta < 0: bank_info = f"\n🏦 Списано: {new_delta} пт"
            await message.reply(f"✅ {username}: {old_score} → {new_score} исправлено.{bank_info}")
            return
    await message.reply(f"❌ {username} не найден.")

@dp.message(Command("куплено"))
async def bank_purchase(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❗ Используйте: /куплено @Nickname 5")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    try:
        amount = float(args[2].replace(",", "."))
    except:
        await message.reply("❗ Неверное число.")
        return
    uid, data = get_user_by_username(username)
    if uid is None:
        await message.reply(f"❌ {username} не найден.")
        return
    current_bank = round(data.get("bank", 0), 2)
    if amount > 0:
        if current_bank < amount:
            await message.reply(f"❌ Недостаточно средств (Баланс: {current_bank})")
            return
        new_bank = round(current_bank - amount, 2)
        data["bank"] = new_bank
        await message.reply(f"✅ Списано {amount} пт. Баланс: {new_bank}")
    else:
        add_amount = abs(amount)
        new_bank = round(current_bank + add_amount, 2)
        data["bank"] = new_bank
        await message.reply(f"✅ Начислено {add_amount} пт. Баланс: {new_bank}")
    save_data(db)

@dp.message(Command("штраф"))
async def bank_penalty(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❗ Используйте: /штраф @Nickname 5")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    try:
        days = int(args[2])
        if days <= 0: raise ValueError
    except:
        await message.reply("❗ Введите число дней > 0.")
        return
    uid, data = get_user_by_username(username)
    if uid is None:
        await message.reply(f"❌ {username} не найден.")
        return
    blocked_until = (get_moscow_time() + timedelta(days=days)).strftime("%Y-%m-%d")
    data["bank_blocked_until"] = blocked_until
    save_data(db)
    await message.reply(f"🚫 Блокировка на {days} дней до {blocked_until}")

@dp.message(Command("отменаштрафа"))
async def bank_unblock(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 2:
        await message.reply("❗ Используйте: /отменаштрафа @Nickname")
        return
    username = args[1] if args[1].startswith("@") else "@" + args[1]
    uid, data = get_user_by_username(username)
    if uid is None:
        await message.reply(f"❌ {username} не найден.")
        return
    if not data.get("bank_blocked_until"):
        await message.reply("ℹ️ Нет блокировки.")
        return
    data["bank_blocked_until"] = None
    save_data(db)
    await message.reply(f"✅ Блокировка снята.")

# ===================================================
# АДМИНСКИЕ РЕЙДЫ
# ===================================================
@dp.message(Command("отменарейдаплана"))
async def cancel_plan_raid(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 2 or not args[1].isdigit():
        await message.reply("❗ Используйте: /отменарейдаплана <номер> (например, /отменарейдаплана 1)")
        return
    raid_num = int(args[1])
    if raid_num not in auto_raids:
        await message.reply(f"❌ Рейд №{raid_num} не существует.")
        return
    del auto_raids[raid_num]
    save_raids()
    await message.reply(f"🗑️ Плановый Рейд №{raid_num} отменён на сегодня.")

@dp.message(Command("создатьрейд"))
async def create_plan_raid(message: types.Message):
    if not is_admin(message.from_user.username):
        await message.reply("⛔ Только для админов.")
        return
    args = message.text.split()
    if len(args) < 3:
        await message.reply("❗ Используйте: /создатьрейд <номер> <ЧЧ:ММ> (например, /создатьрейд 4 23:00)")
        return
    try:
        raid_num = int(args[1])
        new_time = args[2]
        hour, minute = map(int, new_time.split(":"))
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except:
        await message.reply("❗ Неверный формат. Пример: /создатьрейд 4 23:00")
        return
    auto_raids[raid_num] = {"time": f"{hour:02d}:{minute:02d}", "members": []}
    save_raids()
    await message.reply(f"🚀 Плановый Рейд №{raid_num} создан на {hour:02d}:{minute:02d}!\nЗаписывайтесь: `/+рейд{raid_num}`")

@dp.message(Command("состав"))
async def show_roster(message: types.Message):
    if not users:
        await message.reply("❌ В клане нет игроков.")
        return
    day_num = str(get_season_day())
    players = []
    for uid, data in users.items():
        clean_username = data['username'].replace('@', '')
        today_history = data["history"].get(day_num)
        today_score = data['today_score']
        players.append((clean_username, today_score, today_history))
    players.sort(key=lambda x: (x[2] is None, -x[1] if x[1] is not None else 0))
    text = "👥 СОСТАВ КЛАНА:\n\n"
    for username, score, today_history in players:
        if today_history is None:
            text += f"{username}\n"
        else:
            if score == 16: icon = "❤️‍🔥"
            elif score >= 13: icon = "❤️"
            elif score == 12: icon = "✅"
            elif score in (10, 11): icon = "🟡"
            elif 5 <= score <= 9: icon = "⚠️"
            elif 1 <= score <= 4: icon = "❌"
            else: icon = "💀"
            text += f"{username} — {score}/{MAX_SCORE} {icon}\n"
    await message.reply(text)

# ===================================================
# 7. ФОНОВЫЕ ФУНКЦИИ
# ===================================================
async def reset_season():
    now = get_moscow_time()
    if now.day != 5: return
    current_season_key = now.strftime("%Y-%m")
    if db.get("last_season_reset") == current_season_key: return
    for uid, data in users.items():
        data["history"] = {str(d): None for d in range(1, 32)}
        data["warnings"] = 0
        data["skips"] = 0
        data["today_score"] = 0
        data["weekly_history"] = {}
        data["last_bank_accrual_day"] = None
        data["report_time"] = {}
        data["last_day_score"] = None
        data["streaks"] = {"s16": 0, "s14": 0}
    db["last_season_reset"] = current_season_key
    save_data(db)
    await bot.send_message(CHAT_ID, "🎉 НОВЫЙ СЕЗОН НАЧАЛСЯ!\n📅 Все результаты и прогулы обнулены.\n🏦 Банк сохранён.")

async def reset_today_scores():
    now = get_moscow_time()
    if now.day == 5: return
    yesterday_day = str(get_season_day() - 1)
    if yesterday_day == "0":
        for uid, data in users.items():
            data["today_score"] = 0
        save_data(db)
        await bot.send_message(CHAT_ID, "🔄 Новый день! Все результаты обнулены до 0/16.")
        return
    for uid, data in users.items():
        score = data["history"].get(yesterday_day)
        if isinstance(score, int):
            data["last_day_score"] = score
        elif score == "SKIP":
            data["last_day_score"] = 0
        else:
            data["last_day_score"] = None
        if score == "SKIP":
            data["today_score"] = 0
            continue
        if score is None:
            data["skips"] = data.get("skips", 0) + 1
            current_bank = data.get("bank", 0)
            new_bank = round(current_bank - 2.0, 2)
            if new_bank < 0: new_bank = 0
            data["bank"] = new_bank
            data["history"][yesterday_day] = "SKIP"
            if data["skips"] == 1:
                await bot.send_message(CHAT_ID, f"⚠️ {data['username']} получил 1-й прогул! Штраф: -2 пт. Баланс: {data['bank']} пт.\n❗️Внимание: ещё один прогул — и вопрос об исключении будет решать администрация.")
            elif data["skips"] >= 2:
                await bot.send_message(CHAT_ID, f"🚫 {data['username']} получил 2-й прогул! Штраф: -2 пт. Баланс: {data['bank']} пт.\n⚖️ Игрок достиг лимита прогулов. Решение об исключении за администрацией.")
        data["today_score"] = 0
    reset_raids()
    save_data(db)
    await bot.send_message(CHAT_ID, "🔄 Новый день! Все результаты обнулены до 0/16.")
    print(f"🔄 {now.strftime('%H:%M')} — прогулы посчитаны, рейды сброшены")

async def send_deadline_notification():
    now = get_moscow_time()
    day_num = str(get_season_day())
    missing = [data["username"] for uid, data in users.items() if data["history"].get(day_num) is None]
    deadline = now.replace(hour=23, minute=59, second=0, microsecond=0)
    time_left = (deadline - now).total_seconds()
    time_str = f"{int(time_left // 3600)} ч {int((time_left % 3600) // 60)} мин" if time_left > 0 else "⏰ Дедлайн прошёл!"
    msg = f"⏳ До дедлайна (23:59 МСК): {time_str}\n\n"
    msg += f"🚫 Не отыграли ({len(missing)} чел.):\n" + "\n".join(missing) if missing else "✅ Все отыграли!"
    await bot.send_message(CHAT_ID, msg)

async def activate_raid_bonus():
    global raid_bonus_until
    raid_bonus_until = get_moscow_time() + timedelta(minutes=35)
    await bot.send_message(CHAT_ID, f"✨ БОНУС ×2 АКТИВЕН!\n⏰ До: {raid_bonus_until.strftime('%H:%M')} МСК")

async def deactivate_raid_bonus():
    global raid_bonus_until
    if raid_bonus_until is not None and get_moscow_time() < raid_bonus_until: return
    raid_bonus_until = None
    await bot.send_message(CHAT_ID, "⏰ Бонус ×2 завершён!")

async def send_raid_notification(raid_num):
    if raid_num not in auto_raids: return
    raid = auto_raids[raid_num]
    members = [m if m.startswith("@") else "@" + m for m in raid["members"]]
    members_str = "\n".join(members) if members else "❌ Никто не записался"
    await bot.send_message(CHAT_ID, f"🚀 РЕЙД №{raid_num} ({raid['time']}) ЧЕРЕЗ 5 МИНУТ!\n👥 Участники ({len(members)}):\n{members_str}")

async def start_raid(raid_num):
    if raid_num not in auto_raids: return
    time_str = auto_raids[raid_num]["time"]
    await activate_raid_bonus()
    await bot.send_message(CHAT_ID, f"🚀 РЕЙД №{raid_num} ({time_str}) НАЧАЛСЯ!\nСдавайте отчеты! Бонус ×2 и отсутствие штрафов действуют 35 минут.")
    auto_raids[raid_num]["members"] = []
    save_raids()

# ===================================================
# 8. ФОНОВАЯ ЗАДАЧА
# ===================================================
async def background_tasks():
    print("🔄 Фоновая задача запущена")
    while True:
        now = get_moscow_time()
        events = [
            (18, 0, send_deadline_notification),
            (21, 0, send_deadline_notification),
            (23, 0, send_deadline_notification),
            (0, 0, reset_today_scores),
            (0, 1, reset_season)
        ]
        for raid_num, raid_data in auto_raids.items():
            try:
                start_h, start_m = map(int, raid_data["time"].split(":"))
                notify_h, notify_m = start_h, start_m - 5
                if notify_m < 0:
                    notify_h -= 1
                    notify_m += 60
                events.append((notify_h, notify_m, lambda n=raid_num: send_raid_notification(n)))
                events.append((start_h, start_m, lambda n=raid_num: start_raid(n)))
                end_h, end_m = start_h, start_m + 35
                if end_m >= 60:
                    end_h += end_m // 60
                    end_m = end_m % 60
                events.append((end_h, end_m, deactivate_raid_bonus))
            except:
                pass
        next_event_time = None
        next_event_func = None
        for hour, minute, func in events:
            event_time = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if event_time <= now: event_time += timedelta(days=1)
            if next_event_time is None or event_time < next_event_time:
                next_event_time = event_time
                next_event_func = func
        wait_seconds = (next_event_time - now).total_seconds()
        raid_event.clear()
        raid2_event.clear()
        raid3_event.clear()
        try:
            done, pending = await asyncio.wait(
                [
                    asyncio.create_task(raid_event.wait()),
                    asyncio.create_task(raid2_event.wait()),
                    asyncio.create_task(raid3_event.wait())
                ],
                timeout=wait_seconds,
                return_when=asyncio.FIRST_COMPLETED
            )
            for task in pending: task.cancel()
            if done:
                continue
            else:
                await next_event_func()
        except Exception as e:
            print(f"⚠️ Ошибка в фоне: {e}")

# ===================================================
# 9. ЗАПУСК
# ===================================================
async def main():
    print("🚀 Бот запущен!")
    
    # Никакого прокси, просто чистый запуск
    bot = Bot(token=BOT_TOKEN)
    
    await bot.delete_webhook(drop_pending_updates=True)
    asyncio.create_task(background_tasks())
    await dp.start_polling(bot)
    
if __name__ == "__main__":
    asyncio.run(main())
