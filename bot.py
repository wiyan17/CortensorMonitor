#!/usr/bin/env python3
# Cortensor Node Monitoring Bot (PTB v13.5 Compatible) - Reply Keyboard Version (English)

import logging
import requests
import json
import os
import time
from datetime import datetime, timedelta, timezone
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    Updater,
    CommandHandler,
    MessageHandler,
    Filters,
    ConversationHandler,
    CallbackContext,
)
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
UPDATE_INTERVAL = 300  # 5 minutes
CORTENSOR_API = "https://dashboard-devnet3.cortensor.network"

# ADMIN_IDS should be a comma-separated list of Telegram user IDs (e.g., "12345678,87654321")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]

# File to persistently store addresses per chat (also used for tracking chat IDs)
DATA_FILE = "data.json"

# ==================== INITIALIZATION ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))

# ==================== CONVERSATION STATES ====================
ADD_ADDRESS, REMOVE_ADDRESS, ANNOUNCE = range(1, 4)

# ==================== DATA STORAGE FUNCTIONS ====================
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    return {}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_addresses_for_chat(chat_id: int) -> list:
    data = load_data()
    return data.get(str(chat_id), [])

def update_addresses_for_chat(chat_id: int, addresses: list):
    data = load_data()
    data[str(chat_id)] = addresses
    save_data(data)

# ==================== UTILITY FUNCTIONS ====================
def shorten_address(address: str) -> str:
    return address[:6] + "..." + address[-4:] if len(address) > 10 else address

def get_wib_time() -> datetime:
    return datetime.now(WIB)

def format_time(time_obj: datetime) -> str:
    return time_obj.strftime('%Y-%m-%d %H:%M:%S WIB')

def get_age(timestamp: int) -> str:
    diff = datetime.now(WIB) - datetime.fromtimestamp(timestamp, WIB)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds} secs ago"
    minutes = seconds // 60
    return f"{minutes} mins ago" if minutes < 60 else f"{minutes//60} hours ago"

# ==================== DYNAMIC RATE LIMIT HELPER ====================
def get_dynamic_delay(num_addresses: int) -> float:
    """
    Menghitung delay dinamis per panggilan API agar total API call
    tidak melebihi 5 per detik.
    Asumsi: setiap address memerlukan 2 panggilan API (balance & txlist).
    Jika 2 * num_addresses <= 5, delay bisa 0.
    Jika lebih, delay = (total_calls / 5) / (total_calls - 1)
    """
    total_calls = 2 * num_addresses
    if total_calls <= 5:
        return 0.0
    required_total_time = total_calls / 5.0  # dalam detik
    intervals = total_calls - 1  # jeda antar panggilan
    return required_total_time / intervals

def safe_fetch_balance(address: str, delay: float) -> float:
    try:
        params = {
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
            "apikey": API_KEY
        }
        response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
        result = int(response.json()['result']) / 10**18
    except Exception as e:
        logger.error(f"Balance error for {address}: {e}")
        result = 0.0
    time.sleep(delay)
    return result

def safe_fetch_transactions(address: str, delay: float) -> list:
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "sort": "desc",
            "page": 1,
            "offset": 100,
            "apikey": API_KEY
        }
        response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
        result = response.json().get('result', [])
        if isinstance(result, list) and result and isinstance(result[0], dict):
            tx_list = result
        else:
            logger.error(f"Unexpected transactions format for address {address}: {result}")
            tx_list = []
    except Exception as e:
        logger.error(f"Tx error for {address}: {e}")
        tx_list = []
    time.sleep(delay)
    return tx_list

# ==================== API FUNCTIONS (tanpa delay internal) ====================
def fetch_node_stats(address: str) -> dict:
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {e}")
        return {}

# ==================== JOB FUNCTIONS ====================
def auto_update(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please use 'Add Address'.")
        return

    # Hitung delay dinamis berdasarkan jumlah address
    dynamic_delay = get_dynamic_delay(len(addresses))

    responses = []
    for addr in addresses:
        balance = safe_fetch_balance(addr, dynamic_delay)
        txs = safe_fetch_transactions(addr, dynamic_delay)[:6]
        if txs:
   
