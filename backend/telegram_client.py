import os
import requests
from dotenv import load_dotenv

load_dotenv()

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")

def send_telegram_message(chat_id: int, text: str):
    if not TOKEN or TOKEN == "your_telegram_token_here":
        try:
            print(f"MOCK TELEGRAM SEND (No token) to {chat_id}: {text}")
        except UnicodeEncodeError:
            print(f"MOCK TELEGRAM SEND (No token) to {chat_id}: {text.encode('ascii', errors='replace').decode('ascii')}")
        return
        
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    payload = {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown"
    }
    try:
        res = requests.post(url, json=payload, timeout=8)
        if res.status_code != 200:
            # If Markdown parsing failed (400 Bad Request), retry as plain text!
            payload.pop("parse_mode", None)
            res2 = requests.post(url, json=payload, timeout=8)
            print(f"Sent Telegram alert (fallback plain text) to {chat_id}: status={res2.status_code}")
        else:
            print(f"Sent Telegram alert to {chat_id}: status={res.status_code}")
    except Exception as e:
        print(f"Failed to send telegram message: {e}")

