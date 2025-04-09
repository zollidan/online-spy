import os
from dotenv import load_dotenv
from telethon.sync import TelegramClient

api_id = int(input("API ID: "))
api_hash = input("API Hash: ")
phone = input("Phone: ")

load_dotenv(override=True)

APP_NAME = os.getenv("APP_NAME")

with TelegramClient(f"sessions/{APP_NAME}", api_id, api_hash) as client:
    client.start(phone=phone)
    print("Сессия успешно создана.")
