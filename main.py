import json
import logging
from datetime import datetime, timedelta, time as dtime
from zoneinfo import ZoneInfo

from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

from days import *

# --- Настройки ---
import os
TOKEN = os.environ.get('TOKEN')

if not TOKEN:
    print("❌ ERROR: TOKEN environment variable is not set!")
    print("Please set the TOKEN variable on Railway")
    exit(1)
ADMIN_ID = os.environ.get('ADMIN_ID')

if not ADMIN_ID:
    print("⚠️ WARNING: ADMIN_ID not set, admin commands will be disabled")
    ADMIN_ID = None
else:
    try:
        ADMIN_ID = int(ADMIN_ID)
        print(f"✅ Admin ID set to: {ADMIN_ID}")
    except ValueError:
        print("❌ ERROR: ADMIN_ID must be a number")
        ADMIN_ID = None
BASE_DIR = os.getcwd()
DATA_FILE = os.path.join(BASE_DIR, "user_data.json")
MEDIA_DIR = os.path.join(BASE_DIR, "user_media")
TZ = ZoneInfo("Europe/Moscow")
REMINDER_INTERVAL = 3600

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

YES_NO_KEYBOARD = ReplyKeyboardMarkup([["Да", "Нет"]], one_time_keyboard=True, resize_keyboard=True)


def load_data():
    if not os.path.exists(DATA_FILE):
        return {}
    try:
        with open(DATA_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.exception("Ошибка при загрузке данных: %s", e)
        return {}


def save_data(data):
    try:
        with open(DATA_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.exception("Ошибка при сохранении данных: %s", e)


USER_DATA = load_data()
os.makedirs(MEDIA_DIR, exist_ok=True)


def now_in_tz():
    return datetime.now(TZ)


def today_date_str():
    return now_in_tz().date().isoformat()


async def send_reminder(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет напоминание пользователю"""
    job = context.job
    chat_id = job.chat_id
    uid = str(chat_id)

    logger.info(f"Отправка напоминания для пользователя {chat_id}")

    global USER_DATA
    USER_DATA = load_data()

    u = USER_DATA.get(uid)
    if not u:
        logger.error(f"Пользователь {chat_id} не найден")
        return

    today = today_date_str()
    if u.get("answered_today") and u.get("last_response_date") == today:
        logger.info(f"Пользователь {chat_id} уже ответил, удаляем напоминание")
        current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{chat_id}")
        for job in current_jobs:
            job.schedule_removal()
        return

    user_name = u.get("user_info", {}).get("first_name", "")

    REMINDER_TEXT = f"""</b>{user_name}, думаю о нашем исследовании и твоем опыте!</b> 😊\
    Не забыл(а) ли ты сделать сегодня небольшую пометку в дневнике? Не обязательно писать много — поделись\
    одним ярким моментом, мыслью или даже небольшой трудностью, связанной с одеждой. """

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=REMINDER_TEXT,
            parse_mode="HTML",
        )
        logger.info(f"Напоминание отправлено пользователю {chat_id}")

        context.job_queue.run_once(
            send_reminder,
            when=REMINDER_INTERVAL,
            chat_id=chat_id,
            name=f"reminder_{chat_id}"
        )

    except Exception as e:
        logger.error(f"Ошибка при отправке напоминания пользователю {chat_id}: {e}")


def schedule_reminders(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Планирует напоминания для пользователя"""
    current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        send_reminder,
        when=REMINDER_INTERVAL,
        chat_id=chat_id,
        name=f"reminder_{chat_id}"
    )
    logger.info(f"Запланировано напоминание для {chat_id} через 1 час")


def cancel_reminders(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Отменяет все напоминания для пользователя"""
    current_jobs = context.job_queue.get_jobs_by_name(f"reminder_{chat_id}")
    for job in current_jobs:
        job.schedule_removal()
    logger.info(f"Напоминания отменены для пользователя {chat_id}")


async def send_day_message(context: ContextTypes.DEFAULT_TYPE):
    """Отправляет сообщение следующего дня"""
    job = context.job
    chat_id = job.chat_id
    uid = str(chat_id)

    logger.info(f"Отправка сообщения дня для пользователя {chat_id}")

    global USER_DATA
    USER_DATA = load_data()

    u = USER_DATA.get(uid)
    if not u:
        logger.error(f"Пользователь {chat_id} не найден")
        return

    day = u.get("day", 1)

    u["answered_today"] = False
    u["care_question_answered"] = False
    u["waiting_for_care_response"] = False
    save_data(USER_DATA)

    try:
        await context.bot.send_message(
            chat_id=chat_id,
            text=DAY_GREETING_TEXT.format(day=day),
            parse_mode="HTML"
        )

        await context.bot.send_message(
            chat_id=chat_id,
            text=CARE_QUESTION_TEXT,
            reply_markup=YES_NO_KEYBOARD,
            parse_mode="HTML"
        )

        schedule_reminders(context, chat_id)

        logger.info(f"Сообщение дня {day} отправлено пользователю {chat_id}")

    except Exception as e:
        logger.error(f"Ошибка при отправке сообщения пользователю {chat_id}: {e}")


def schedule_next_day(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Планирует отправку следующего дня на следующий день после последнего ответа"""
    global USER_DATA
    USER_DATA = load_data()

    uid = str(chat_id)
    u = USER_DATA.get(uid)

    if not u or not u.get("next_day_time"):
        logger.warning(f"Нет времени для планирования у пользователя {chat_id}")
        return

    try:
        next_day_time = u["next_day_time"]
        hour, minute = map(int, next_day_time.split(":"))

        last_response_date = u.get("last_response_date")
        if last_response_date:
            last_date = datetime.fromisoformat(last_response_date).date()
            send_date = last_date + timedelta(days=1)
        else:
            send_date = now_in_tz().date() + timedelta(days=1)

        send_time = datetime.combine(send_date, dtime(hour, minute)).replace(tzinfo=TZ)

        now = now_in_tz()
        delay = (send_time - now).total_seconds()

        if delay < 0:
            delay = 10
            logger.info(f"Время уже прошло, отправляем через {delay} секунд")

        logger.info(f"Планируем отправку для {chat_id} на {send_time} (через {delay:.0f} секунд)")

        current_jobs = context.job_queue.get_jobs_by_name(f"nextday_{chat_id}")
        for job in current_jobs:
            job.schedule_removal()

        context.job_queue.run_once(
            send_day_message,
            when=delay,
            chat_id=chat_id,
            name=f"nextday_{chat_id}"
        )
        logger.info(f"Создано задание для {chat_id} на время {send_time}")

    except Exception as e:
        logger.error(f"Ошибка планирования для {chat_id}: {e}")


async def check_missed_day(context: ContextTypes.DEFAULT_TYPE):
    """Проверяет пользователей, которые не ответили за предыдущий день, и отправляет сообщение 'нам очень жаль'"""
    logger.info("=== ПРОВЕРКА ПРОПУЩЕННЫХ ДНЕЙ ===")

    global USER_DATA
    USER_DATA = load_data()

    today = today_date_str()
    yesterday = (now_in_tz().date() - timedelta(days=1)).isoformat()

    processed_count = 0

    for uid, u in USER_DATA.items():
        try:
            chat_id = int(uid)
        except Exception:
            continue

        last_response_date = u.get("last_response_date")
        answered_today = u.get("answered_today", False)

        if last_response_date != yesterday and not answered_today:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=SORRY_TEXT,
                    parse_mode="HTML"
                )
                logger.info(f"Отправлено сообщение 'нам очень жаль' пользователю {chat_id}")

                current_day = u.get("day", 1)
                if current_day < 7:
                    u["day"] = current_day + 1
                    logger.info(f"День пользователя {chat_id} обновлен с {current_day} на {u['day']}")

                u["answered_today"] = False
                u["care_question_answered"] = False
                u["waiting_for_care_response"] = False

                save_data(USER_DATA)
                processed_count += 1

            except Exception as e:
                logger.error(f"Ошибка при обработке пропущенного дня для {chat_id}: {e}")

    logger.info(f"=== ОБРАБОТАНО {processed_count} ПОЛЬЗОВАТЕЛЕЙ С ПРОПУЩЕННЫМИ ДНЯМИ ===")


def schedule_daily_check(context: ContextTypes.DEFAULT_TYPE):
    """Планирует ежедневную проверку пропущенных дней в 00:01"""
    now = now_in_tz()

    check_time = datetime.combine(now.date(), dtime(0, 1)).replace(tzinfo=TZ)
    if now >= check_time:
        check_time += timedelta(days=1)

    delay = (check_time - now).total_seconds()

    logger.info(f"Планируем ежедневную проверку на {check_time} (через {delay:.0f} секунд)")

    current_jobs = context.job_queue.get_jobs_by_name("daily_check")
    for job in current_jobs:
        job.schedule_removal()

    context.job_queue.run_once(
        schedule_daily_check_callback,
        when=delay,
        name="daily_check"
    )


async def schedule_daily_check_callback(context: ContextTypes.DEFAULT_TYPE):
    """Колбэк для планирования ежедневной проверки"""
    await check_missed_day(context)

    schedule_daily_check(context)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = str(chat_id)

    user = update.effective_user

    u = USER_DATA.get(uid)
    if u is None:
        USER_DATA[uid] = {
            "day": 1,
            "answered_today": False,
            "care_question_answered": False,
            "waiting_for_care_response": False,
            "last_response_date": None,
            "responses": {},
            "next_day_time": None,
            "user_info": {
                "first_name": user.first_name,
                "username": user.username,
                "user_id": user.id
            }
        }
        u = USER_DATA[uid]
        save_data(USER_DATA)
    else:
        u["user_info"] = {
            "first_name": user.first_name,
            "username": user.username,
            "user_id": user.id
        }
        save_data(USER_DATA)

    today = today_date_str()
    day = u.get("day", 1)

    if u.get("answered_today") and u.get("last_response_date") == today:
        await update.message.reply_text(
            f"Ты уже ответил(а) на сегодняшний вопрос! Сегодня у нас был день {day - 1}. Жду тебя завтра для следующего задания. 🙂"
        )
    else:
        if u.get("last_response_date") is None:
            await update.message.reply_text(WELCOME_TEXT, parse_mode="HTML")

        await update.message.reply_text(DAY_GREETING_TEXT.format(day=day), parse_mode="HTML")

        await update.message.reply_text(
            CARE_QUESTION_TEXT,
            reply_markup=YES_NO_KEYBOARD,
            parse_mode="HTML"
        )

        schedule_reminders(context, chat_id)


async def handle_care_question(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = str(chat_id)
    text = update.message.text.strip().lower()

    global USER_DATA
    USER_DATA = load_data()
    u = USER_DATA.get(uid)

    if not u:
        return

    if text == "да":
        await update.message.reply_text(
            CARE_TIPS_TEXT,
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        u["waiting_for_care_response"] = True
        u["care_question_answered"] = True
        save_data(USER_DATA)

    else:
        await update.message.reply_text(
            "Хорошо!",
            reply_markup=ReplyKeyboardRemove(),
            parse_mode="HTML"
        )
        u["care_question_answered"] = True
        save_data(USER_DATA)

        day = u.get("day", 1)
        await update.message.reply_text(
            DAY_TEXTS.get(day, "Спасибо! Неделя завершена. 🎉"),
            parse_mode="HTML"
        )

        if day < 7:
            await update.message.reply_text(
                "После ответа отправь время для следующего дня в формате ЧЧ:ММ (например, 09:30)",
                parse_mode="HTML"
            )


async def handle_media_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await process_user_response(update, context)


async def handle_text_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text.strip()

    if text.lower() in ["да", "нет"]:
        await handle_care_question(update, context)
        return

    if text and any(char.isdigit() for char in text) and ":" in text:
        try:
            hour, minute = map(int, text.split(":"))
            if (0 <= hour < 24 and 0 <= minute < 60):
                await handle_time(update, context)
                return
        except ValueError:
            pass

    await process_user_response(update, context)


async def process_user_response(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    uid = str(chat_id)

    user = update.effective_user

    u = USER_DATA.get(uid)

    if u and not u.get("care_question_answered", False):
        await handle_care_question(update, context)
        return

    if u and u.get("waiting_for_care_response", False):
        saved_text = update.message.text or update.message.caption or "<медиа-сообщение>"

        today = today_date_str()
        if "care_responses" not in u:
            u["care_responses"] = {}

        day_care_responses = u["care_responses"].get(today, [])
        day_care_responses.append(saved_text)
        u["care_responses"][today] = day_care_responses

        u["waiting_for_care_response"] = False
        save_data(USER_DATA)

        await update.message.reply_text(NEXT_TO_QUESTIONS_TEXT, parse_mode="HTML")

        day = u.get("day", 1)
        await update.message.reply_text(
            DAY_TEXTS.get(day, "Спасибо! Неделя завершена. 🎉"),
            parse_mode="HTML"  # ← ДОБАВИТЬ ЭТУ СТРОЧКУ
        )

        if day < 7:
            await update.message.reply_text(
                "После ответа отправь время для следующего дня в формате ЧЧ:ММ (например, 09:30)",
                parse_mode="HTML"
            )
        return

    if u is None:
        USER_DATA[uid] = {
            "day": 1,
            "answered_today": False,
            "care_question_answered": False,
            "waiting_for_care_response": False,
            "last_response_date": None,
            "responses": {},
            "next_day_time": None,
            "user_info": {
                "first_name": user.first_name,
                "username": user.username,
                "user_id": user.id
            }
        }
        u = USER_DATA[uid]
    else:
        u["user_info"] = {
            "first_name": user.first_name,
            "username": user.username,
            "user_id": user.id
        }

    today = today_date_str()

    if u.get("answered_today") and u.get("last_response_date") == today:
        await update.message.reply_text(
            f"Ты уже ответил(а) на сегодняшний вопрос. Сегодня у нас день {u['day'] - 1}. Жду тебя завтра для следующего задания. 🙂",
            parse_mode="HTML",
        )
        return

    saved_text = update.message.text or update.message.caption or "<медиа-сообщение>"

    # --- Создаем папку для пользователя ---
    user_dir = os.path.join(MEDIA_DIR, uid)
    os.makedirs(user_dir, exist_ok=True)

    # --- Сохранение медиа ---
    if update.message.photo:
        file = await context.bot.get_file(update.message.photo[-1].file_id)
        file_path = os.path.join(user_dir, f"{today}_photo_{datetime.now().strftime('%H%M%S')}.jpg")
        await file.download_to_drive(file_path)
        saved_text += f" [прикреплено фото: {file_path}]"

    elif update.message.video:
        file = await context.bot.get_file(update.message.video.file_id)
        file_path = os.path.join(user_dir, f"{today}_video_{datetime.now().strftime('%H%M%S')}.mp4")
        await file.download_to_drive(file_path)
        saved_text += f" [прикреплено видео: {file_path}]"

    # --- Сохраняем ответ в JSON ---
    if "responses" not in u:
        u["responses"] = {}

    day_responses = u["responses"].get(today, [])
    if isinstance(day_responses, str):
        day_responses = [day_responses]

    day_responses.append(saved_text)
    u["responses"][today] = day_responses

    u["answered_today"] = True
    u["last_response_date"] = today

    current_day = u.get("day", 1)
    if current_day < 7:
        u["day"] = current_day + 1

    save_data(USER_DATA)

    cancel_reminders(context, chat_id)

    if current_day < 7:
        await update.message.reply_text(
            "Спасибо! ✅ Твоя заметка сохранена. Теперь отправь время для следующего дня в формате ЧЧ:ММ, например 09:30",
            parse_mode="HTML"
        )
    else:

        await update.message.reply_text(
            "Спасибо! ✅ Твоя заметка сохранена. Неделя исследований завершена! 🎉",
            parse_mode="HTML"
        )

        await update.message.reply_text(
            THANK_YOU_TEXT,
            parse_mode="HTML"
        )


async def handle_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    global USER_DATA
    chat_id = update.effective_chat.id
    uid = str(chat_id)
    u = USER_DATA.get(uid)
    if u is None:
        await update.message.reply_text("Сначала отправьте ответ на сегодняшний день.")
        return

    text = update.message.text.strip()
    try:
        hour, minute = map(int, text.split(":"))
        if not (0 <= hour < 24 and 0 <= minute < 60):
            raise ValueError
    except ValueError:
        await update.message.reply_text("Неверный формат. Пожалуйста, отправь время в формате ЧЧ:ММ, например 09:30")
        return

    u["next_day_time"] = f"{hour:02d}:{minute:02d}"
    save_data(USER_DATA)

    logger.info(f"Пользователь {chat_id} установил время: {u['next_day_time']}")

    await update.message.reply_text(
        f"Отлично! ✅ Я отправлю следующий день в {u['next_day_time']} по твоему времени."
    )

    schedule_next_day(context, chat_id)

## ADMIN PANEL
async def stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Статистика бота (доступна всем)"""
    chat_id = update.effective_chat.id

    global USER_DATA
    USER_DATA = load_data()

    total_users = len(USER_DATA)
    active_today = 0
    total_responses = 0

    today = today_date_str()
    for uid, user_data in USER_DATA.items():
        if user_data.get("last_response_date") == today:
            active_today += 1
        total_responses += len(user_data.get("responses", {}))

    stats_text = f"""
📊 <b>Статистика бота</b>

👥 Всего пользователей: {total_users}
✅ Активных сегодня: {active_today}
💬 Всего ответов: {total_responses}

📅 Данные обновлены: {today}
"""
    await update.message.reply_text(stats_text, parse_mode="HTML")

async def export_data(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт данных (только для админа)"""
    if not ADMIN_ID:
        await update.message.reply_text("❌ Admin commands are disabled")
        return

    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return

    global USER_DATA
    USER_DATA = load_data()

    import tempfile
    with tempfile.NamedTemporaryFile(mode='w', suffix='.json', delete=False, encoding='utf-8') as f:
        json.dump(USER_DATA, f, ensure_ascii=False, indent=2)
        temp_path = f.name

    with open(temp_path, 'rb') as f:
        await update.message.reply_document(
            document=f,
            filename=f"bot_data_{today_date_str()}.json",
            caption="Данные бота"
        )

    os.unlink(temp_path)


async def admin_check(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Проверка админского доступа"""

    chat_id = update.effective_chat.id

    if not ADMIN_ID:
        await update.message.reply_text("❌ Admin commands are disabled (ADMIN_ID not set)")
        return

    if chat_id == ADMIN_ID:
        await update.message.reply_text(f"✅ Вы администратор! Ваш ID: {chat_id}")
    else:
        await update.message.reply_text(f"❌ Вы не администратор. Ваш ID: {chat_id}\nАдмин ID: {ADMIN_ID}")


async def get_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Получить медиа файлы пользователя (только для админа)"""

    if not ADMIN_ID:
        await update.message.reply_text("❌ Admin commands are disabled")
        return

    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return

    if not context.args:
        await update.message.reply_text(
            "❌ Укажите ID пользователя:\n"
            "Пример: `/get_media 123456789`",
            parse_mode="Markdown"
        )
        return

    try:
        user_id = context.args[0]
        user_media_dir = os.path.join(MEDIA_DIR, user_id)

        if not os.path.exists(user_media_dir):
            await update.message.reply_text(f"❌ Папка пользователя {user_id} не найдена")
            return

        media_files = []
        for file in os.listdir(user_media_dir):
            if file.endswith(('.jpg', '.jpeg', '.png', '.mp4', '.mov')):
                media_files.append(file)

        if not media_files:
            await update.message.reply_text(f"❌ У пользователя {user_id} нет медиа файлов")
            return

        media_files.sort()

        await update.message.reply_text(
            f"📁 Медиа файлы пользователя {user_id}:\n"
            f"Всего файлов: {len(media_files)}\n\n"
            "Отправляю файлы..."
        )

        sent_count = 0
        for media_file in media_files[:10]:
            file_path = os.path.join(user_media_dir, media_file)

            try:
                if media_file.endswith(('.jpg', '.jpeg', '.png')):
                    with open(file_path, 'rb') as f:
                        await update.message.reply_photo(
                            photo=f,
                            caption=f"📸 {media_file}\nUser: {user_id}"
                        )
                elif media_file.endswith(('.mp4', '.mov')):
                    with open(file_path, 'rb') as f:
                        await update.message.reply_video(
                            video=f,
                            caption=f"🎥 {media_file}\nUser: {user_id}"
                        )

                sent_count += 1

                import asyncio
                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Ошибка отправки файла {media_file}: {e}")
                await update.message.reply_text(f"❌ Ошибка отправки {media_file}")

        if len(media_files) > 10:
            await update.message.reply_text(
                f"📋 Показано первых 10 файлов из {len(media_files)}\n"
                f"Используйте `/get_media {user_id} 10` для следующих файлов",
                parse_mode="Markdown"
            )

    except Exception as e:
        logger.error(f"Ошибка в get_media: {e}")
        await update.message.reply_text("❌ Ошибка при получении медиа файлов")


async def list_users_with_media(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Список всех пользователей у которых есть медиа файлы"""

    if not ADMIN_ID:
        await update.message.reply_text("❌ Admin commands are disabled")
        return

    if update.effective_chat.id != ADMIN_ID:
        await update.message.reply_text("❌ Эта команда только для администратора")
        return

    try:
        users_with_media = []

        if not os.path.exists(MEDIA_DIR):
            await update.message.reply_text("❌ Папка user_media не существует")
            return

        for user_id in os.listdir(MEDIA_DIR):
            user_dir = os.path.join(MEDIA_DIR, user_id)
            if os.path.isdir(user_dir):
                media_files = [f for f in os.listdir(user_dir) if f.endswith(('.jpg', '.jpeg', '.png', '.mp4', '.mov'))]
                if media_files:
                    users_with_media.append((user_id, len(media_files)))

        if not users_with_media:
            await update.message.reply_text("❌ Нет пользователей с медиа файлами")
            return

        global USER_DATA
        USER_DATA = load_data()

        message = "👥 <b>Пользователи с медиа файлами:</b>\n\n"

        for user_id, file_count in sorted(users_with_media, key=lambda x: x[1], reverse=True):
            user_data = USER_DATA.get(user_id, {})
            user_info = user_data.get("user_info", {})
            user_name = user_info.get('first_name', 'Unknown')
            username = user_info.get('username', 'No username')

            message += f"👤 <b>{user_name}</b> (@{username})\n"
            message += f"   🆔: {user_id}\n"
            message += f"   📁 Файлов: {file_count}\n"
            message += f"   📥 Команда: <code>/get_media {user_id}</code>\n\n"

        await update.message.reply_text(message, parse_mode="HTML")

    except Exception as e:
        logger.error(f"Ошибка в list_users_with_media: {e}")
        await update.message.reply_text("❌ Ошибка при получении списка пользователей")

# --- Main ---
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    # Обработчики (ВАЖНО: правильный порядок!)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stats", stats))
    application.add_handler(CommandHandler("export", export_data))
    application.add_handler(CommandHandler("get_media", get_media))
    application.add_handler(CommandHandler("media_users", list_users_with_media))
    application.add_handler(MessageHandler(filters.Regex(r"^(Да|Нет)$"), handle_care_question))
    application.add_handler(MessageHandler(filters.Regex(r"^\d{1,2}:\d{2}$"), handle_time))
    application.add_handler(MessageHandler(filters.PHOTO | filters.VIDEO | filters.Document.ALL, handle_media_message))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text_message))

    async def post_init(application):
        """Восстанавливаем расписание при запуске"""
        logger.info("=== ВОССТАНОВЛЕНИЕ РАСПИСАНИЯ ===")

        restored_count = 0
        reminder_count = 0
        for uid, u in USER_DATA.items():
            try:
                chat_id = int(uid)
            except Exception:
                continue

            if u.get("next_day_time"):
                logger.info(f"Восстанавливаем для {chat_id}: время {u['next_day_time']}, день {u.get('day', 1)}")

                class DummyContext:
                    def __init__(self, app):
                        self.job_queue = app.job_queue

                dummy_context = DummyContext(application)
                schedule_next_day(dummy_context, chat_id)
                restored_count += 1

            today = today_date_str()
            if not u.get("answered_today") or u.get("last_response_date") != today:
                class DummyContext:
                    def __init__(self, app):
                        self.job_queue = app.job_queue

                dummy_context = DummyContext(application)
                schedule_reminders(dummy_context, chat_id)
                reminder_count += 1

        schedule_daily_check(application)

        logger.info(f"=== ВОССТАНОВЛЕНО {restored_count} ЗАДАНИЙ И {reminder_count} НАПОМИНАНИЙ ===")

    application.post_init = post_init

    logger.info("=== БОТ ЗАПУЩЕН ===")
    application.run_polling()


if __name__ == "__main__":
    main()