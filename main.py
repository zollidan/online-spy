import os
import asyncio
import json
from datetime import datetime, timedelta
from collections import defaultdict

from art import tprint
from dotenv import load_dotenv
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline, UserStatusOffline

load_dotenv()

tprint("online-spy")

API_ID = int(os.getenv("API_ID"))
API_HASH = os.getenv("API_HASH")
APP_NAME = os.getenv("APP_NAME")
USERNAMES = list(map(str.strip, os.getenv("USERNAMES").split(",")))
CHECK_INTERVAL = 45

# Файлы данных
SESSIONS_FILE = "data/sessions.json"
REPORTS_DIR = "data/daily_reports"
os.makedirs(REPORTS_DIR, exist_ok=True)
os.makedirs("data", exist_ok=True)

client = TelegramClient(APP_NAME, API_ID, API_HASH)

# Загружаем сессии из файла
def load_sessions():
    if os.path.exists(SESSIONS_FILE):
        with open(SESSIONS_FILE, "r") as f:
            return json.load(f)
    return defaultdict(dict)

# Сохраняем сессии
def save_sessions(sessions):
    with open(SESSIONS_FILE, "w") as f:
        json.dump(sessions, f, indent=2)

# Расчет времени активности и генерация отчета
def generate_daily_report(sessions, date_str):
    report = []
    for username, data in sessions.items():
        daily_sessions = data.get(date_str, [])
        session_count = len(daily_sessions)
        total_online = timedelta()
        session_lines = []

        for sess in daily_sessions:
            start = datetime.fromisoformat(sess["start"])
            end = datetime.fromisoformat(sess["end"])
            duration = end - start
            total_online += duration
            session_lines.append(f"  - {start.strftime('%H:%M:%S')} → {end.strftime('%H:%M:%S')} ({str(duration)})")

        report.append(
            f"👤 {username}\n"
            f"  Сессий: {session_count}\n"
            f"  Суммарно онлайн: {str(total_online)}\n"
            + "\n".join(session_lines) + "\n"
        )

    filename = os.path.join(REPORTS_DIR, f"{date_str}.txt")
    with open(filename, "w", encoding="utf-8") as f:
        f.write("\n".join(report))
    print(f"[✔] Ежедневный отчет сохранен: {filename}")

async def monitor():
    sessions = load_sessions()
    last_statuses = {}

    async with client:
        while True:
            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            for username in USERNAMES:
                try:
                    user = await client.get_entity(username)
                    status = user.status
                    is_online = isinstance(status, UserStatusOnline)

                    # Предыдущее состояние
                    was_online = last_statuses.get(username, False)

                    # Детектируем изменение
                    if is_online and not was_online:
                        # Начало сессии
                        print(f"[{now}] {username} зашел онлайн")
                        sessions.setdefault(username, {}).setdefault(date_str, []).append({
                            "start": now.isoformat(),
                            "end": now.isoformat()  # временно, обновится позже
                        })

                    elif not is_online and was_online:
                        print(f"[{now}] {username} вышел")
                        # Завершаем последнюю сессию
                        if sessions.get(username, {}).get(date_str):
                            sessions[username][date_str][-1]["end"] = now.isoformat()

                    last_statuses[username] = is_online

                except Exception as e:
                    print(f"[!] Ошибка при проверке {username}: {e}")

            # Сохраняем прогресс
            save_sessions(sessions)

            # Генерация отчета в полночь
            if now.hour == 0 and now.minute < (CHECK_INTERVAL // 60):
                generate_daily_report(sessions, (now - timedelta(days=1)).strftime("%Y-%m-%d"))

            await asyncio.sleep(CHECK_INTERVAL)

if __name__ == "__main__":

    asyncio.run(monitor())
