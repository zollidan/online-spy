import os
import asyncio
import json
from datetime import datetime, timedelta
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

DATABASE_USER = os.getenv("DATABASE_USER")
DATABASE_PASSWORD = os.getenv("DATABASE_PASSWORD")
DATABASE_NAME = os.getenv("DATABASE_NAME")

DATABASE_URL = f"postgresql+asyncpg://{DATABASE_USER}:{DATABASE_PASSWORD}@localhost/{DATABASE_NAME}"

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
                print(f"Запись для {username} успешно сохранена")

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
                            print(f"[+] {username} онлайн: сессия начата в {now}")
                    elif isinstance(status, UserStatusOffline):
                        if username in active_sessions:
                   
                            start_time = active_sessions[username]                            
                            await save_session_record(username, start=start_time, end=now)
                            print(f"[-] {username} оффлайн: сессия с {start_time} по {now} сохранена")
                            del active_sessions[username]
            
                            
                    print(active_sessions)
                except Exception as e:
                    print(f"[!!!] Ошибка при проверке {username}: {e}")

            await asyncio.sleep(3)

async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

if __name__ == "__main__":
    asyncio.run(init_db())
    asyncio.run(monitor())
