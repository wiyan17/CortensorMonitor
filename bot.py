#!/usr/bin/env python3
"""
Cortensor Node Monitoring Bot – Telegram Reply Keyboard Version
This bot sends node status updates, alerts, and periodic checks via Telegram.
It logs errors and reports them to admin users, and displays status with emojis and hyperlinks.
"""

import logging
import requests
import json
import os
import time
from datetime import datetime, timedelta, timezone
from telegram import ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import Updater, CommandHandler, MessageHandler, Filters, ConversationHandler, CallbackContext
from dotenv import load_dotenv

load_dotenv()

# -------------------- CONFIGURATION --------------------
TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
UPDATE_INTERVAL = 300  # 5 minutes update interval
CORTENSOR_API = "https://dashboard-devnet3.cortensor.network"
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATA_FILE = "data.json"

# -------------------- INITIALIZATION --------------------
logging.basicConfig(format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO)
logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))  # WIB (UTC+7)

# -------------------- CONVERSATION STATES --------------------
ADD_ADDRESS, REMOVE_ADDRESS, ANNOUNCE = range(1, 4)

# -------------------- DATA STORAGE FUNCTIONS --------------------
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

# -------------------- UTILITY FUNCTIONS --------------------
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

# -------------------- DYNAMIC RATE LIMIT HELPER --------------------
def get_dynamic_delay(num_addresses: int) -> float:
    """
    Calculate a dynamic delay per API call so that total calls do not exceed 5 per second.
    Enforces a minimum delay of 0.2 seconds.
    """
    base_delay = 0.2  # Minimum delay (max 5 calls per second)
    total_calls = 2 * num_addresses  # 2 API calls per address: balance & txlist
    if total_calls <= 5:
        return base_delay
    required_total_time = total_calls / 5.0
    intervals = total_calls - 1
    dynamic_delay = required_total_time / intervals
    return max(dynamic_delay, base_delay)

# -------------------- API FUNCTIONS --------------------
def safe_fetch_balance(address: str, delay: float) -> float:
    """
    Safely fetch the balance of the address using Arbiscans API.
    """
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
    """
    Safely fetch the list of transactions for the address using Arbiscans API.
    """
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
            logger.error(f"Unexpected transactions format for {address}: {result}")
            tx_list = []
    except Exception as e:
        logger.error(f"Tx error for {address}: {e}")
        tx_list = []
    time.sleep(delay)
    return tx_list

def fetch_node_stats(address: str) -> dict:
    """
    Fetch node statistics from the dashboard API.
    """
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {e}")
        return {}

# -------------------- JOB FUNCTIONS --------------------
def auto_update(context: CallbackContext):
    """
    Send an auto-update message with combined node status, health, and stall info.
    """
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please use 'Add Address'.")
        return
    delay = get_dynamic_delay(len(addresses))
    output_lines = []
    for addr in addresses:
        balance = safe_fetch_balance(addr, delay)
        txs = safe_fetch_transactions(addr, delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
            latest_25 = txs[:25]
            groups = [latest_25[i*5:(i+1)*5] for i in range(5)]
            health_list = []
            for group in groups:
                if group:
                    health_list.append("🟩" if all(tx.get('isError') == '0' for tx in group) else "🟥")
                else:
                    health_list.append("⬜")
            health_status = " ".join(health_list)
            stall_status = "🚨 Node Stall" if len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25) else "✅ Normal"
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
            health_status = "No transactions"
            stall_status = "No transactions"
        output_lines.append(
            f"*{shorten_address(addr)}*\n"
            f"💰 Balance: `{balance:.4f} ETH` | Status: {status}\n"
            f"⏱️ Last Activity: `{last_activity}`\n"
            f"🩺 Health: {health_status} | Stall: {stall_status}\n"
            f"[Arbiscan]({CORTENSOR_API.replace('dashboard-devnet3','sepolia.arbiscan.io/address')}/{addr}) | "
            f"[Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    final_output = "*Auto Update*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    context.bot.send_message(chat_id=chat_id, text=final_output, parse_mode="Markdown")

def alert_check(context: CallbackContext):
    """
    Check for alerts and send a message if no transactions in the last 15 minutes or if a node stall is detected.
    """
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    delay = get_dynamic_delay(len(addresses))
    for addr in addresses:
        txs = safe_fetch_transactions(addr, delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            latest_25 = txs[:25]
            stall_condition = len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25)
            if time_diff > timedelta(minutes=15) or stall_condition:
                msg_lines = [f"🚨 *Alert for {shorten_address(addr)}*:"]
                if time_diff > timedelta(minutes=15):
                    msg_lines.append("⏱️ No transactions in the last 15 minutes.")
                if stall_condition:
                    msg_lines.append("⚠️ Node stall detected (only PING transactions in the last 25).")
                msg_lines.append(f"[Arbiscan]({CORTENSOR_API.replace('dashboard-devnet3','sepolia.arbiscan.io/address')}/{addr}) | "
                                 f"[Dashboard]({CORTENSOR_API}/nodestats/{addr})")
                context.bot.send_message(chat_id=chat_id, text="\n".join(msg_lines), parse_mode="Markdown")
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 *Alert for {shorten_address(addr)}*:\n- No transactions found!\n"
                     f"[Arbiscan]({CORTENSOR_API.replace('dashboard-devnet3','sepolia.arbiscan.io/address')}/{addr}) | "
                     f"[Dashboard]({CORTENSOR_API}/nodestats/{addr})",
                parse_mode="Markdown"
            )

def auto_node_stall(context: CallbackContext):
    """
    Send a concise update for node stall status.
    """
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please use 'Add Address'.")
        return
    delay = get_dynamic_delay(len(addresses))
    output_lines = []
    for addr in addresses:
        balance = safe_fetch_balance(addr, delay)
        txs = safe_fetch_transactions(addr, delay)
        if txs:
            latest_25 = txs[:25]
            stall_status = "🚨 Node Stall" if len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25) else "✅ Normal"
            last_tx_time = int(txs[0]['timeStamp'])
            last_activity = get_age(last_tx_time)
        else:
            last_activity = "N/A"
            stall_status = "No transactions"
        output_lines.append(
            f"*{shorten_address(addr)}*\n"
            f"💰 Balance: `{balance:.4f} ETH`\n"
            f"⏱️ Last Activity: `{last_activity}`\n"
            f"⚠️ Stall: {stall_status}\n"
            f"[Arbiscan]({CORTENSOR_API.replace('dashboard-devnet3','sepolia.arbiscan.io/address')}/{addr}) | "
            f"[Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    final_output = "*Auto Node Stall Check*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    context.bot.send_message(chat_id=chat_id, text=final_output, parse_mode="Markdown")

# -------------------- MENU KEYBOARDS --------------------
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        ["Add Address", "Remove Address"],
        ["Check Status", "Auto Update"],
        ["Enable Alerts", "Auto Node Stall"],
        ["Stop", "Help"]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append(["Announce"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# -------------------- ERROR HANDLER --------------------
def error_handler(update, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    error_text = f"⚠️ An error occurred: {context.error}"
    for admin_id in ADMIN_IDS:
        context.bot.send_message(chat_id=admin_id, text=error_text)

# -------------------- COMMAND HANDLERS --------------------
def start_command(update, context):
    user_id = update.effective_user.id
    update.message.reply_text(
        "👋 Welcome to Cortensor Node Monitoring Bot!\n\nI am here to help you monitor your node status easily. Choose an option from the menu below.",
        reply_markup=main_menu_keyboard(user_id)
    )

def help_command(update, context):
    update.message.reply_text(
        "📖 *Cortensor Node Monitoring Bot Guide*\n\n"
        "• *Add Address*: ➕ Add a wallet address.\n"
        "• *Remove Address*: ➖ Remove a wallet address from your list.\n"
        "• *Check Status*: 📊 View combined node status, health, & stall info.\n"
        "• *Auto Update*: 🔄 Enable automatic updates every 5 minutes with combined info.\n"
        "• *Enable Alerts*: 🔔 Receive notifications if no transactions in 15 minutes or if a node stall is detected.\n"
        "• *Auto Node Stall*: ⏱️ Periodically check for node stall only.\n"
        "• *Stop*: ⛔ Disable auto-updates and alerts.\n"
        "• *Announce* (Admin only): 📣 Send an announcement to all chats.\n\n"
        "💡 *Fun Fact*: Every blockchain transaction is like a digital heartbeat. Monitor your node and be a digital hero! 🦸‍♂️\n\n"
        "🚀 *Happy Monitoring!*",
        reply_markup=main_menu_keyboard(update.effective_user.id),
        parse_mode="Markdown"
    )

# -------------------- CONVERSATION HANDLERS --------------------
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
        update.message.reply_text("⚠️ Address already added!\nReturning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if len(addresses) >= 10:
        update.message.reply_text("❌ Maximum 10 addresses per chat!\nReturning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Added {shorten_address(address)} to your list!", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

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
    update.message.reply_text(f"📣 Announcement sent to {count} chats.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# -------------------- MAIN FUNCTION --------------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    logger.info("Bot is starting...")

    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("auto_update", menu_auto_update))
    dp.add_handler(CommandHandler("auto_node_stall", menu_auto_node_stall))
    dp.add_handler(CommandHandler("enable_alerts", menu_enable_alerts))
    dp.add_handler(CommandHandler("stop", menu_stop))
    dp.add_handler(CommandHandler("check_status", menu_check_status))
    dp.add_handler(CommandHandler("announce", announce_start))
    dp.add_error_handler(error_handler)

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

    updater.start_polling()
    logger.info("Bot is running... 🚀")
    updater.idle()

def start_command(update, context):
    user_id = update.effective_user.id
    update.message.reply_text(
        "👋 Welcome to Cortensor Node Monitoring Bot!\n\nI am here to help you monitor your node status easily. Choose an option from the menu below.",
        reply_markup=main_menu_keyboard(user_id)
    )

if __name__ == "__main__":
    main()
