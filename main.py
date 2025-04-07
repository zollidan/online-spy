import os
import asyncio
import json
from datetime import datetime, timedelta
import platform
import random
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
from sqlalchemy import Column, Integer, String, Date, DateTime
from sqlalchemy import select, func
from collections import defaultdict

load_dotenv(override=True)

tprint("online-spy")

BOT_TOKEN = os.getenv("BOT_TOKEN")
API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
APP_NAME = os.getenv("APP_NAME")

DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_NAME = os.getenv("DATABASE_NAME")
DAILY_REPORT_CHANNEL_ID = int(os.getenv("DAILY_REPORT_CHANNEL_ID"))

CHAT_ID = os.getenv("CHAT_ID")

DATABASE_URL = f"postgresql+asyncpg://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_HOST}/{DATABASE_NAME}"
DEBUG = False
if DEBUG:
    print(f"DEBUG: {DEBUG}")
    print(f"DATABASE_URL: {DATABASE_URL}")

active_sessions = {}

Base = declarative_base()
engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)

class SessionRecord(Base):
    __tablename__ = "sessions"

    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False)
    session_date = Column(Date, nullable=False)
    start_time = Column(DateTime, nullable=False)
    end_time = Column(DateTime, nullable=False)

class TrackedUser(Base):
    __tablename__ = "tracked_users"
    id = Column(Integer, primary_key=True)
    username = Column(String, nullable=False, unique=True)
    chat_id = Column(String, nullable=False)
    topic_id = Column(String, nullable=False)
    
client = TelegramClient(APP_NAME, API_ID, API_HASH)
bot = Bot(token=BOT_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
dp = Dispatcher(storage=MemoryStorage())

db_lock = asyncio.Lock()

@dp.message(F.text.startswith('/on'))
async def add_user(message: Message):
    
    username = message.text.split(' ')[1]
    chat_id = message.chat.id
    topic_id = message.message_thread_id
    
    await add_tracked_user(username=username, chat_id=str(chat_id) ,topic_id=str(topic_id))
    
    await message.answer(text=f"Пользователь {username} теперь отслеживается.")
    
@dp.message(F.text.startswith('/off'))
async def add_user(message: Message):
    
    username = message.text.split(' ')[1]
    
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
                        print(f"Запись для {username} успешно сохранена")
    except Exception as e:
        print(f"Ошибка при сохранении записи для {username}: {e}")
        traceback.print_exc()

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
        while True:
            now = datetime.now()
            usernames = await get_tracked_usernames()
            for username in usernames:
                try:
                    user = await client.get_entity(int(username))
                    status = user.status
                    
                    if isinstance(status, UserStatusOnline):
                        if username not in active_sessions:
                            active_sessions[username] = now
                            if DEBUG:
                                print(f"[+] {username} онлайн: сессия начата в {now}")
                    elif isinstance(status, UserStatusOffline):
                        if username in active_sessions:
                   
                            start_time = active_sessions[username]                            
                            await save_session_record(username, start=start_time, end=now)
                            if DEBUG:
                                print(f"[-] {username} оффлайн: сессия с {start_time} по {now} сохранена")
                            del active_sessions[username]
            
                    if DEBUG:        
                        print(active_sessions)
                except Exception as e:
                    print(f"[!!!] Ошибка при проверке {username}: {e}")

            if datetime.now().strftime("%H:%M") == "23:59":
                await report_scheduler()
            
            await asyncio.sleep(30)
        
async def generate_daily_report():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            today = datetime.now().date()
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
                reports.append(report)
            
            return reports

async def report_scheduler():
    try:
        reports = await generate_daily_report()
        if DEBUG:
            print("отправка отчета")
        
        for report in reports:
            # Extract username from the report text
            # Assuming the report format is "👤 username • date"
            username = report.split(' ')[1]
            
            async with AsyncSessionLocal() as session:
                
                stmt = select(TrackedUser).where(TrackedUser.username == username)
                result = await session.execute(stmt)
                user = result.scalars().first()  # Get the first result
                                    
                if user and user.topic_id:

                    await bot.send_message(chat_id=int(user.chat_id), text=report, message_thread_id=int(user.topic_id))
                    if DEBUG:
                        print(f"Отправлен отчет для {username} в чат {user.topic_id}")
                else:
                    if DEBUG:
                        print(f"Не удалось найти topic_id для пользователя {username}")
            
            await asyncio.sleep(1)

    except Exception as e:
        print(f"Ошибка при создании или отправке отчета: {e}")
        traceback.print_exc()

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def main():
    try:
        await init_db()

        await asyncio.gather(
            dp.start_polling(bot),
            client.start(),
            monitor(),
            report_scheduler()
        )
       
    finally:
        now = datetime.now()
        for username, start_time in active_sessions.items():
            await save_session_record(username, start=start_time, end=now)
            if DEBUG:
                print(f"[-] Программа завершается: сессия {username} с {start_time} по {now} сохранена")

if __name__ == "__main__":
    asyncio.run(main())
