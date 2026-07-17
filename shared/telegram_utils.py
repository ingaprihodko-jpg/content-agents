import os

import requests
from dotenv import load_dotenv

load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")

def send_message(chat_id: str, text: str) -> dict:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    resp = requests.post(url, json={"chat_id": chat_id, "text": text})
    resp.raise_for_status()
    return resp.json()

if __name__ == "__main__":
    chat_id = os.getenv("TELEGRAM_CHAT_ID_TEST")
    result = send_message(chat_id, "Тестовое сообщение от content-agents")
    print(result)
