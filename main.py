import os
import asyncio
import json
from datetime import datetime, timedelta
import platform
import random
import traceback

from art import tprint
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline, UserStatusOffline

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker, declarative_base
from sqlalchemy import Column, Integer, String, Date, DateTime

load_dotenv(override=True)

tprint("online-spy")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
APP_NAME = os.getenv("APP_NAME")
USERNAMES = list(map(str.strip, os.getenv("USERNAMES").split(",")))

DATABASE_HOST = os.getenv("DATABASE_HOST")
DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_NAME = os.getenv("DATABASE_NAME")
DAILY_REPORT_CHANNEL_ID = int(os.getenv("DAILY_REPORT_CHANNEL_ID"))

CHAT_ID = os.getenv("CHAT_ID")

DATABASE_URL = f"postgresql+asyncpg://{DATABASE_USER}:{DATABASE_PASSWORD}@localhost/{DATABASE_NAME}"
DEBUG = True

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

client = TelegramClient(APP_NAME, API_ID, API_HASH)

db_lock = asyncio.Lock()

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

active_sessions = {}

async def monitor():
    async with client:
        while True:
            now = datetime.now()
            for username in USERNAMES:
                try:
                    user = await client.get_entity(username)
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

            await asyncio.sleep(5) # random.randint(30, 60)
            
from sqlalchemy import select, func
from collections import defaultdict

async def generate_daily_report():
    async with AsyncSessionLocal() as session:
        async with session.begin():
            today = datetime.now().date()
            since = datetime.combine(today, datetime.min.time())

            # 👇 Выбираем формат дня для даты отчёта
            day_format = '%-d' if platform.system() != 'Windows' else '%#d'
            date_str = today.strftime(f'{day_format} %B').lower()

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
                total_time_str = f"{total_hours} час" + ("" if total_hours == 1 else "а") + f", {total_minutes} мин"

                report = (
                    f"👤 {username} • {date_str}\n\n"
                    f"Зашел/вышел:\n{time_ranges}\n\n"
                    f"Всего сессий: {total_sessions}.\n"
                    f"Общее время онлайн: {total_time_str}."
                )
                reports.append(report)

            return reports

            
async def report_scheduler():
    await client.connect()
    while True:
        try:
            reports = await generate_daily_report()
            if DEBUG:
                print("Отправка отчётов по пользователям...")

            for report in reports:
                await client.send_message(DAILY_REPORT_CHANNEL_ID, report) 
                await asyncio.sleep(1) 

        except Exception as e:
            print(f"Ошибка при создании или отправке отчета: {e}")
            traceback.print_exc()

        wait_time = 120 if DEBUG else 86400  # 2 минуты или сутки
        await asyncio.sleep(wait_time)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

async def main():
    try:
        await init_db()
        await client.start()
        await asyncio.gather(
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
