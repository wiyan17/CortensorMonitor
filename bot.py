#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# Cortensor Node Monitoring Bot (PTB v13.5 Compatible) – Reply Keyboard Version (English)

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
    Calculate dynamic delay per API call so that total calls do not exceed 5 per second.
    Assumption: Each address requires 2 API calls (balance & txlist).
    """
    total_calls = 2 * num_addresses
    if total_calls <= 5:
        return 0.0
    required_total_time = total_calls / 5.0  # in seconds
    intervals = total_calls - 1
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

# ==================== API FUNCTIONS (without internal delay) ====================
def fetch_node_stats(address: str) -> dict:
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {e}")
        return {}

# ==================== JOB FUNCTIONS ====================

# Auto update combines status, health, and stall info.
def auto_update(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please use 'Add Address'.")
        return

    dynamic_delay = get_dynamic_delay(len(addresses))
    responses = []
    for addr in addresses:
        balance = safe_fetch_balance(addr, dynamic_delay)
        txs = safe_fetch_transactions(addr, dynamic_delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
            latest_25 = txs[:25]
            # Health: divide last 25 transactions into 5 groups.
            groups = [latest_25[i*5:(i+1)*5] for i in range(5)]
            group_statuses = []
            for group in groups:
                if group:
                    if any(tx.get('isError') != '0' for tx in group):
                        group_statuses.append("🟥")
                    else:
                        group_statuses.append("🟩")
                else:
                    group_statuses.append("⬜")
            health_status = " ".join(group_statuses)
            # Stall check: require at least 25 transactions and all must start with PING method.
            if len(latest_25) >= 25 and all(tx.get('input', '').lower().startswith("0x5c36b186") for tx in latest_25):
                stall_status = "🚨 Node Stall"
            else:
                stall_status = "✅ Normal"
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
            health_status = "No transactions"
            stall_status = "No transactions"
        responses.append(
            f"🔹 {shorten_address(addr)}\n"
            f"💵 Balance: {balance:.4f} ETH\n"
            f"📊 Status: {status}\n"
            f"⏳ Last activity: {last_activity}\n"
            f"🩺 Health: {health_status}\n"
            f"⚠️ Stall: {stall_status}\n"
            f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
            f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    context.bot.send_message(
        chat_id=chat_id,
        text="🔄 Auto Update\n\n" + "\n\n".join(responses) +
             f"\n\n⏰ Last update: {format_time(get_wib_time())}",
        parse_mode="Markdown"
    )

# Alert check includes both inactivity and stall conditions.
def alert_check(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    dynamic_delay = get_dynamic_delay(len(addresses))
    for addr in addresses:
        txs = safe_fetch_transactions(addr, dynamic_delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            latest_25 = txs[:25]
            stall_condition = (len(latest_25) >= 25 and
                               all(tx.get('input', '').lower().startswith("0x5c36b186") for tx in latest_25))
            if time_diff > timedelta(minutes=15) or stall_condition:
                alert_text = f"🚨 Alert for {shorten_address(addr)}:\n"
                if time_diff > timedelta(minutes=15):
                    alert_text += "No transactions in the last 15 minutes.\n"
                if stall_condition:
                    alert_text += "Node stall detected (only PING transactions in the last 25).\n"
                alert_text += f"[Arbiscan](https://sepolia.arbiscan.io/address/{addr})"
                context.bot.send_message(chat_id=chat_id, text=alert_text, parse_mode="Markdown")
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 Alert for {shorten_address(addr)}:\nNo transactions found!\n[Arbiscan](https://sepolia.arbiscan.io/address/{addr})",
                parse_mode="Markdown"
            )

# ==================== MENU KEYBOARDS ====================
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        ["Add Address", "Remove Address"],
        ["Check Status", "Auto Update"],
        ["Enable Alerts", "Stop", "Help"]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append(["Announce"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# ==================== COMMAND HANDLERS ====================
def start(update, context):
    user_id = update.effective_user.id
    update.message.reply_text(
        "👋 Welcome to Cortensor Node Monitoring Bot!\n\nI am here to help you monitor your node status easily. Choose an option from the menu below.",
        reply_markup=main_menu_keyboard(user_id)
    )

def help_command(update, context):
    update.message.reply_text(
        "📖 Complete Guide for Cortensor Node Monitoring Bot!\n\n"
        "• Add Address: Add a wallet address.\n"
        "• Remove Address: Remove a wallet address from your list.\n"
        "• Check Status: View node status, balance, health, and stall info.\n"
        "• Auto Update: Enable automatic updates every 5 minutes (combined status, health & stall).\n"
        "• Enable Alerts: Receive notifications if no transactions in 15 minutes or if a node stall is detected.\n"
        "• Stop: Disable auto-updates and alerts.\n"
        "• Announce (Admin only): Send an announcement to all chats.\n\n"
        "💡 Fun Fact: Every blockchain transaction is like a digital heartbeat that keeps the system alive. Monitor your node and be a digital hero!\n\n"
        "🚀 Happy Monitoring!",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )

# ---------- Conversation for "Add Address" ----------
def add_address_start(update, context):
    update.message.reply_text("Please send me the wallet address to add:", reply_markup=ReplyKeyboardRemove())
    return ADD_ADDRESS

def add_address_receive(update, context):
    chat_id = update.effective_chat.id
    address = update.message.text.strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("❌ Invalid address! It must start with '0x' and be 42 characters long. Please send a valid address or type /cancel to abort.")
        return ADD_ADDRESS
    addresses = get_addresses_for_chat(chat_id)
    if address in addresses:
        update.message.reply_text("⚠️ Address already added!")
        update.message.reply_text("Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if len(addresses) >= 10:
        update.message.reply_text("❌ Maximum 10 addresses per chat!")
        update.message.reply_text("Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Added {shorten_address(address)} to your list!", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# ---------- Conversation for "Remove Address" ----------
def remove_address_start(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.message.reply_text("No addresses found to remove.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    keyboard = [[addr] for addr in addresses]
    keyboard.append(["Cancel"])
    update.message.reply_text("Select the address to remove:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return REMOVE_ADDRESS

def remove_address_receive(update, context):
    chat_id = update.effective_chat.id
    choice = update.message.text.strip()
    if choice == "Cancel":
        update.message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses = get_addresses_for_chat(chat_id)
    if choice not in addresses:
        update.message.reply_text("❌ Address not found.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.remove(choice)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Removed {shorten_address(choice)} from your list!", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# ---------- Conversation for "Announce" (Admin only) ----------
def announce_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ You are not authorized to use this command.", reply_markup=main_menu_keyboard(user_id))
        return ConversationHandler.END
    update.message.reply_text("Please send the announcement message:", reply_markup=ReplyKeyboardRemove())
    return ANNOUNCE

def announce_receive(update, context):
    message = update.message.text
    data = load_data()
    if not data:
        update.message.reply_text("No chats found to announce to.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    count = 0
    for chat in data.keys():
        try:
            context.bot.send_message(chat_id=int(chat), text=message)
            count += 1
        except Exception as e:
            logger.error(f"Error sending announcement to chat {chat}: {e}")
    update.message.reply_text(f"Announcement sent to {count} chats.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# ---------- Menu Function for "Check Status" ----------
def menu_check_status(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.message.reply_text("No addresses found! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    dynamic_delay = get_dynamic_delay(len(addresses))
    responses = []
    for addr in addresses[:10]:
        balance = safe_fetch_balance(addr, dynamic_delay)
        txs = safe_fetch_transactions(addr, dynamic_delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
            latest_25 = txs[:25]
            # Health: divide last 25 txs into 5 groups.
            groups = [latest_25[i*5:(i+1)*5] for i in range(5)]
            group_statuses = []
            for group in groups:
                if group:
                    if any(tx.get('isError') != '0' for tx in group):
                        group_statuses.append("🟥")
                    else:
                        group_statuses.append("🟩")
                else:
                    group_statuses.append("⬜")
            health_status = " ".join(group_statuses)
            # Stall check:
            if len(latest_25) >= 25 and all(tx.get('input', '').lower().startswith("0x5c36b186") for tx in latest_25):
                stall_status = "🚨 Node Stall"
            else:
                stall_status = "✅ Normal"
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
            health_status = "No transactions"
            stall_status = "No transactions"
        responses.append(
            f"🔹 {shorten_address(addr)}\n"
            f"💵 Balance: {balance:.4f} ETH\n"
            f"📊 Status: {status}\n"
            f"⏳ Last activity: {last_activity}\n"
            f"🩺 Health: {health_status}\n"
            f"⚠️ Stall: {stall_status}\n"
            f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
            f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    update.message.reply_text("📊 Node Status\n\n" + "\n\n".join(responses) +
                              f"\n\n⏰ Last update: {format_time(get_wib_time())}",
                              parse_mode="Markdown", reply_markup=main_menu_keyboard(update.effective_user.id))

def menu_auto_update(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.message.reply_text("No addresses found! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"auto_update_{chat_id}")
    if current_jobs:
        update.message.reply_text("Auto-update is already active.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(auto_update, interval=UPDATE_INTERVAL, context={'chat_id': chat_id}, name=f"auto_update_{chat_id}")
    update.message.reply_text("✅ Auto-updates enabled! I will send combined status, health & stall updates every 5 minutes.",
                                reply_markup=main_menu_keyboard(update.effective_user.id))

def menu_enable_alerts(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.message.reply_text("No addresses found! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"alert_{chat_id}")
    if current_jobs:
        update.message.reply_text("Alerts are already active.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(alert_check, interval=900, context={'chat_id': chat_id}, name=f"alert_{chat_id}")
    update.message.reply_text("✅ Alerts enabled! You will be notified if no transactions in 15 minutes or if a node stall is detected.",
                                reply_markup=main_menu_keyboard(update.effective_user.id))

def menu_stop(update, context):
    chat_id = update.effective_chat.id
    removed_jobs = 0
    for job_name in (f"auto_update_{chat_id}", f"alert_{chat_id}"):
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()
            removed_jobs += 1
    if removed_jobs:
        update.message.reply_text("✅ Auto-update and alerts have been stopped.", reply_markup=main_menu_keyboard(update.effective_user.id))
    else:
        update.message.reply_text("No active jobs found.", reply_markup=main_menu_keyboard(update.effective_user.id))

# ==================== MAIN FUNCTION ====================
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    logger.info("Bot is starting...")

    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))

    conv_add = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Add Address$"), add_address_start)],
        states={
            ADD_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, add_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_add)

    conv_remove = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Remove Address$"), remove_address_start)],
        states={
            REMOVE_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, remove_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_remove)

    conv_announce = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Announce$"), announce_start)],
        states={
            ANNOUNCE: [MessageHandler(Filters.text & ~Filters.command, announce_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_announce)

    dp.add_handler(MessageHandler(Filters.regex("^Check Status$"), menu_check_status))
    dp.add_handler(MessageHandler(Filters.regex("^Auto Update$"), menu_auto_update))
    dp.add_handler(MessageHandler(Filters.regex("^Enable Alerts$"), menu_enable_alerts))
    dp.add_handler(MessageHandler(Filters.regex("^Stop$"), menu_stop))
    dp.add_handler(MessageHandler(Filters.regex("^Help$"), help_command))
    dp.add_handler(MessageHandler(Filters.regex("^Announce$"), announce_start))

    updater.start_polling()
    logger.info("Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()
