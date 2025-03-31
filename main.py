import asyncio
import json
import os
from datetime import datetime, timedelta, date
from telethon import TelegramClient
from telethon.tl.types import UserStatusOnline, UserStatusOffline
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from dotenv import load_dotenv

load_dotenv()

API_ID = int(os.getenv('API_ID'))
API_HASH = os.getenv('API_HASH')
PHONE_NUMBER = os.getenv('PHONE_NUMBER')
USER_IDS = list(map(int, os.getenv('USER_IDS').split(',')))
SESSION_NAME = os.getenv('SESSION_NAME', 'session')
DATA_DIR = '/app/data'
REPORTS_DIR = '/app/reports'

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(REPORTS_DIR, exist_ok=True)

client = TelegramClient(SESSION_NAME, API_ID, API_HASH)
user_data = {}

def load_user_data():
    global user_data
    try:
        with open(os.path.join(DATA_DIR, 'user_data.json'), 'r') as f:
            data = json.load(f)
            for uid, udata in data.items():
                user_data[uid] = {
                    'current_session_start': datetime.fromisoformat(udata['current_session_start']) if udata['current_session_start'] else None,
                    'sessions': [{
                        'start': datetime.fromisoformat(s['start']),
                        'end': datetime.fromisoformat(s['end']),
                        'duration': s['duration']
                    } for s in udata['sessions']],
                    'total_time': udata['total_time']
                }
    except FileNotFoundError:
        pass

def save_user_data():
    data_to_save = {}
    for uid, udata in user_data.items():
        data_to_save[uid] = {
            'current_session_start': udata['current_session_start'].isoformat() if udata['current_session_start'] else None,
            'sessions': [{
                'start': s['start'].isoformat(),
                'end': s['end'].isoformat(),
                'duration': s['duration']
            } for s in udata['sessions']],
            'total_time': udata['total_time']
        }
    with open(os.path.join(DATA_DIR, 'user_data.json'), 'w') as f:
        json.dump(data_to_save, f)

async def check_statuses():
    for user_id in USER_IDS:
        try:
            user = await client.get_entity(user_id)
            user_id_str = str(user.id)
            current_status = user.status
            
            if user_id_str not in user_data:
                user_data[user_id_str] = {
                    'current_session_start': None,
                    'sessions': [],
                    'total_time': 0
                }
            
            now = datetime.now()
            udata = user_data[user_id_str]
            
            if isinstance(current_status, UserStatusOnline):
                if not udata['current_session_start']:
                    udata['current_session_start'] = now
            else:
                if udata['current_session_start']:
                    start_time = udata['current_session_start']
                    duration = (now - start_time).total_seconds()
                    udata['sessions'].append({
                        'start': start_time,
                        'end': now,
                        'duration': duration
                    })
                    udata['total_time'] += duration
                    udata['current_session_start'] = None
                    save_user_data()
        except Exception as e:
            print(f'Error checking user {user_id}: {str(e)}')

async def generate_daily_report():
    yesterday = date.today() - timedelta(days=1)
    report = f"Отчет за {yesterday.strftime('%Y-%m-%d')}\n\n"
    
    for user_id in USER_IDS:
        user_id_str = str(user_id)
        if user_id_str not in user_data:
            continue
        
        udata = user_data[user_id_str]
        sessions = [s for s in udata['sessions'] if s['start'].date() == yesterday]
        total_duration = sum(s['duration'] for s in sessions)
        
        report += f"Пользователь: {user_id}\n"
        report += f"Сессий: {len(sessions)}\n"
        report += f"Общее время: {timedelta(seconds=int(total_duration))}\n"
        report += "Детализация сессий:\n"
        for i, session in enumerate(sessions, 1):
            start = session['start'].strftime('%H:%M:%S')
            end = session['end'].strftime('%H:%M:%S')
            report += f"{i}. {start} - {end} ({timedelta(seconds=int(session['duration']))})\n"
        report += "\n"
    
    filename = os.path.join(REPORTS_DIR, f"{yesterday.strftime('%Y-%m-%d')}.txt")
    with open(filename, 'w') as f:
        f.write(report)
    print(f"Сгенерирован отчет за {yesterday.strftime('%Y-%m-%d')}")

async def main():
    await client.start(PHONE_NUMBER)
    load_user_data()
    
    scheduler = AsyncIOScheduler()
    scheduler.add_job(generate_daily_report, 'cron', hour=0, minute=5)
    scheduler.start()
    
    while True:
        await check_statuses()
        await asyncio.sleep(30)

async def shutdown():
    for user_id in USER_IDS:
        user_id_str = str(user_id)
        if user_id_str in user_data and user_data[user_id_str]['current_session_start']:
            now = datetime.now()
            udata = user_data[user_id_str]
            duration = (now - udata['current_session_start']).total_seconds()
            udata['sessions'].append({
                'start': udata['current_session_start'],
                'end': now,
                'duration': duration
            })
            udata['total_time'] += duration
            udata['current_session_start'] = None
    save_user_data()
    await client.disconnect()

if __name__ == '__main__':
    try:
        client.loop.run_until_complete(main())
    except KeyboardInterrupt:
        client.loop.run_until_complete(shutdown())