import os
import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
import traceback

from art import tprint
from dotenv import load_dotenv

from aiogram import F, Bot, Dispatcher
from aiogram.types import Message
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode

from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline, UserStatusOffline

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import TIMESTAMP, Column, Integer, String, Date, DateTime
from sqlalchemy import select
from collections import defaultdict

import logging
load_dotenv(override=True)

tprint("online-spy")

ADMINS = [int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip()]
BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
APP_NAME = os.getenv("APP_NAME")
TIMEZONE = timezone(timedelta(hours=3))

DATABASE_HOST = os.getenv("POSTGRES_HOST")
DATABASE_USER = os.getenv("POSTGRES_USER")
DATABASE_PASSWORD = os.getenv("POSTGRES_PASSWORD")
DATABASE_NAME = os.getenv("POSTGRES_DB")
TELEGRAM_MAX_MESSAGE_LENGTH = 4096
CHECK_INTERVAL_SECONDS = int(3)

CHAT_ID = os.getenv("CHAT_ID")

DATABASE_URL = f"postgresql+asyncpg://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}/{DATABASE_NAME}"
DEBUG = False

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
LOG_FILE = LOG_DIR / "app.log"

logging.basicConfig(
    level=logging.DEBUG,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler()
    ]
)

logging.getLogger("telethon").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.WARNING)

if DEBUG:
    logging.debug(f"DEBUG: {DEBUG}")
    logging.debug(f"DATABASE_URL: {DATABASE_URL}")

active_sessions = {}

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class SessionRecord(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    session_date = Column(Date, nullable=False)
    start_time = Column(TIMESTAMP(timezone=True), nullable=False)
    end_time = Column(TIMESTAMP(timezone=True), nullable=False)

class TrackedUser(Base):
    __tablename__ = "tracked_users"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True)
    chat_id = Column(String, nullable=False)
    topic_id = Column(String, nullable=False)
    
client = TelegramClient(f"sessions/{APP_NAME}", API_ID, API_HASH)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

db_lock = asyncio.Lock()

@dp.message(F.text.startswith('/on'))
async def add_user(message: Message):
    if message.from_user.id not in ADMINS:
        await message.answer(text="У вас нет прав на выполнение этой команды.")
        return
    
    try:
        username = message.text.split(' ')[1]
    except IndexError:
        await message.answer(text="Id пользователя не найден в вашем сообщении.")
        return
    chat_id = message.chat.id
    topic_id = message.message_thread_id
    
    await add_tracked_user(username=username, chat_id=str(chat_id), topic_id=str(topic_id))
    
    await message.answer(text=f"Пользователь {username} теперь отслеживается.")
    
@dp.message(F.text.startswith('/off'))
async def delete_user(message: Message):
    if message.from_user.id not in ADMINS:
        await message.answer(text="У вас нет прав на выполнение этой команды.")
        return
    
    try:
        username = message.text.split(' ')[1]
    except IndexError:
        await message.answer(text="Id пользователя не найден в вашем сообщении.")
        return
    
    await remove_tracked_user(username=username)
    
    await message.answer(text=f"Пользователь {username} больше не отслеживается.")

@dp.message(F.text == "/list")
async def list_user(message: Message):
    usernames = await get_tracked_usernames()
    answer = '\n'.join(usernames) if usernames else "Нет отслеживаемых пользователей."
    await message.answer(answer)

async def save_session_record(username: str, start: datetime, end: datetime):
    try:
        async with db_lock:
            async with AsyncSessionLocal() as session:
                async with session.begin():
                    new_record = SessionRecord(
                        username=username,
                        session_date=start.date(),
                        start_time=start,
                        end_time=end
                    )
                    session.add(new_record)
                    await session.commit()
                    if DEBUG:
                        logging.debug(f"Запись для {username} успешно сохранена")
    except Exception as e:
        logging.error(f"Ошибка при сохранении записи для {username}: {e}")
        logging.debug(traceback.print_exc())

async def remove_tracked_user(username):
    async with db_lock:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                result = await session.execute(
                    select(TrackedUser).where(TrackedUser.username == username)
                )
                user = result.scalars().first()
                if user:
                    await session.delete(user)
                    await session.commit()

async def add_tracked_user(username, chat_id, topic_id):
    async with db_lock:
        async with AsyncSessionLocal() as session:
            async with session.begin():
                user = TrackedUser(username=username, topic_id=topic_id, chat_id=chat_id)
                session.add(user)
                await session.commit()

async def get_tracked_usernames():
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(TrackedUser.username))
        usernames = result.scalars().all()
        return usernames

async def monitor():
    async with client:
        last_report_date = None
        while True:
            now = datetime.now(tz=TIMEZONE)
            usernames = await get_tracked_usernames()
            for username in usernames:
                try:
                    user = await client.get_entity(int(username))
                    status = user.status
                    
                    if isinstance(status, UserStatusOnline):
                        if username not in active_sessions:
                            active_sessions[username] = now
                            if DEBUG:
                                logging.debug(f"[+] {username} онлайн: сессия начата в {now}")
                    elif isinstance(status, UserStatusOffline):
                        if username in active_sessions:
                            start_time = active_sessions[username]                            
                            await save_session_record(username, start=start_time, end=now)
                            if DEBUG:
                                logging.debug(f"[-] {username} оффлайн: сессия с {start_time} по {now} сохранена")
                            del active_sessions[username]
            
                    if DEBUG:        
                        pass
                except Exception as e:
                    logging.error(f"[!!!] Ошибка при проверке {username}: {e}")
            
            current_date = now.date()
            current_time = now.strftime("%H:%M")
            if current_time == "23:59" and last_report_date != current_date:
                await report_scheduler()
                last_report_date = current_date
                if DEBUG:
                    logging.debug(f"Daily report triggered for {current_date}")
            
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
        
async def generate_daily_report():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            today = datetime.now(tz=TIMEZONE).date()
            since = datetime.combine(today, datetime.min.time())
            
            month_names_ru = {
                1: 'января',
                2: 'февраля',
                3: 'марта',
                4: 'апреля',
                5: 'мая',
                6: 'июня',
                7: 'июля',
                8: 'августа',
                9: 'сентября',
                10: 'октября',
                11: 'ноября',
                12: 'декабря'
            }
            
            month_num = today.month
            month_ru = month_names_ru[month_num]
            date_str = f"{today.day} {month_ru}"
            
            stmt = select(SessionRecord).where(SessionRecord.start_time >= since)
            result = await session.execute(stmt)
            records = result.scalars().all()
            user_data = defaultdict(list)
            
            for record in records:
                duration = record.end_time - record.start_time
                user_data[record.username].append((record.start_time, record.end_time, duration))
            
            reports = []
            for username, sessions in user_data.items():
                total_sessions = len(sessions)
                total_duration = sum((s[2] for s in sessions), timedelta())
                time_ranges = "\n".join(
                    f"{start.strftime('%H:%M')} – {end.strftime('%H:%M')}"
                    for start, end, _ in sessions
                )
                
                total_hours, remainder = divmod(total_duration.seconds, 3600)
                total_minutes = remainder // 60
                
                if total_hours > 0:
                    hours_word = "час" if total_hours == 1 else "часа" if 2 <= total_hours <= 4 else "часов"
                    total_time_str = f"{total_hours} {hours_word}, {total_minutes} мин"
                else:
                    total_time_str = f"{total_minutes} мин"
                
                report = (
                    f"👤 {username} • {date_str}\n\n"
                    f"<b>Зашел/вышел:</b>\n{time_ranges}\n\n"
                    f"<b>Всего сессий:</b> {total_sessions}.\n"
                    f"<b>Общее время онлайн:</b> {total_time_str}."
                )
                reports.append((username, report))
            
            return reports

async def report_scheduler():
    try:
        reports = await generate_daily_report()
        if DEBUG:
            logging.debug(f"Generating reports for {len(reports)} users")
        
        for username, report in reports:
            async with AsyncSessionLocal() as session:
                stmt = select(TrackedUser).where(TrackedUser.username == username)
                result = await session.execute(stmt)
                user = result.scalars().first()
                
                if not user or not user.topic_id:
                    if DEBUG:
                        logging.debug(f"Не удалось найти topic_id для пользователя {username}")
                    continue
                
                if len(report) <= TELEGRAM_MAX_MESSAGE_LENGTH:
                    await bot.send_message(
                        chat_id=int(user.chat_id),
                        text=report,
                        message_thread_id=int(user.topic_id)
                    )
                    if DEBUG:
                        logging.debug(f"Sent report for {username} in one message")
                else:
                    lines = report.split('\n')
                    current_part = f"👤 {username} • {lines[0].split('•')[1].strip()}\n\n<b>Зашел/вышел:</b>\n"
                    parts = []
                    time_range_start = False
                    
                    for line in lines[2:]:
                        if line.startswith('<b>Всего сессий:</b>'):
                            time_range_start = False
                            if current_part:
                                parts.append(current_part)
                            current_part = line + '\n'
                        elif line.startswith('<b>Общее время онлайн:</b>'):
                            current_part += line
                            parts.append(current_part)
                            current_part = ''
                        else:
                            if not time_range_start:
                                time_range_start = True
                            potential_part = current_part + line + '\n'
                            if len(potential_part) > TELEGRAM_MAX_MESSAGE_LENGTH - 100:
                                parts.append(current_part)
                                current_part = f"👤 {username} • (продолжение)\n\n<b>Зашел/вышел:</b>\n{line}\n"
                            else:
                                current_part = potential_part
                    
                    if current_part:
                        parts.append(current_part)
                    
                    for i, part in enumerate(parts, 1):
                        await bot.send_message(
                            chat_id=int(user.chat_id),
                            text=part,
                            message_thread_id=int(user.topic_id)
                        )
                        if DEBUG:
                            logging.debug(f"Sent part {i}/{len(parts)} of report for {username}")
                        await asyncio.sleep(0.5)
                
                await asyncio.sleep(1)
        
    except Exception as e:
        logging.error(f"Ошибка при создании или отправке отчета: {e}")
        logging.error(traceback.print_exc())

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def main():
    try:
        await init_db()
        await asyncio.gather(
            dp.start_polling(bot),
            monitor()
        )
    finally:
        now = datetime.now(tz=TIMEZONE)
        for username, start_time in active_sessions.items():
            await save_session_record(username, start=start_time, end=now)
            if DEBUG:
                logging.debug(f"[-] Программа завершается: сессия {username} с {start_time} по {now} сохранена")

if __name__ == "__main__":
    asyncio.run(main())