#!/usr/bin/env python3
"""
Cortensor Node Monitoring Bot – Telegram Reply Keyboard Version

This bot provides real-time node monitoring via Telegram.
It supports commands for adding/removing wallet addresses, checking status,
enabling auto updates, alerts, and admin announcements.
All outputs are in English with detailed explanations and innovative emoji decorations.
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
# Base URL for the dashboard; we'll use the new endpoint for node stats.
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

# -------------------- MENU KEYBOARD --------------------
def main_menu_keyboard(user_id: int) -> ReplyKeyboardMarkup:
    keyboard = [
        ["Add Address", "Remove Address"],
        ["Check Status", "Auto Update"],
        ["Enable Alerts", "Stop", "Help"]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append(["Announce"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# -------------------- DYNAMIC RATE LIMIT HELPER --------------------
def get_dynamic_delay(num_addresses: int) -> float:
    """
    Calculate a dynamic delay per API call so that total calls do not exceed 0.5 API calls per second.
    This enforces a minimum delay of 2.0 seconds between calls.
    """
    base_delay = 2.0  # 1 call every 2 seconds
    total_calls = 2 * num_addresses  # 2 API calls per address: balance & txlist
    if total_calls <= 0.5:
        return base_delay
    required_total_time = total_calls / 0.5  # in seconds
    intervals = total_calls - 1
    dynamic_delay = required_total_time / intervals
    return max(dynamic_delay, base_delay)

# -------------------- API FUNCTIONS --------------------
def safe_fetch_balance(address: str, delay: float) -> float:
    """
    Safely fetch the balance of the address using Arbiscans API.
    Retries with exponential backoff if a rate limit error is encountered.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            params = {"module": "account", "action": "balance", "address": address, "tag": "latest", "apikey": API_KEY}
            response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
            json_resp = response.json()
            result_str = json_resp.get("result", "")
            try:
                balance_int = int(result_str)
                return balance_int / 10**18
            except ValueError:
                if "Max calls per sec rate limit" in result_str:
                    logger.error(f"Rate limit reached for {address}. Retrying (attempt {attempt+1})...")
                    time.sleep(delay * (attempt+1) * 2)
                    continue
                else:
                    logger.error(f"Balance error for {address}: {result_str}")
                    return 0.0
        except Exception as e:
            logger.error(f"Exception fetching balance for {address}: {e}")
        time.sleep(delay * (attempt+1))
    return 0.0

def safe_fetch_transactions(address: str, delay: float) -> list:
    """
    Safely fetch the list of transactions for the address using Arbiscans API.
    Retries with exponential backoff if a rate limit error is encountered.
    """
    max_retries = 3
    for attempt in range(max_retries):
        try:
            params = {"module": "account", "action": "txlist", "address": address, "sort": "desc", "page": 1, "offset": 100, "apikey": API_KEY}
            response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
            json_resp = response.json()
            result = json_resp.get("result", [])
            if isinstance(result, list) and result and isinstance(result[0], dict):
                return result
            else:
                if isinstance(result, str) and "Max calls per sec rate limit" in result:
                    logger.error(f"Rate limit reached for transactions of {address}. Retrying (attempt {attempt+1})...")
                    time.sleep(delay * (attempt+1) * 2)
                    continue
                else:
                    logger.error(f"Unexpected transactions format for {address}: {result}")
                    return []
        except Exception as e:
            logger.error(f"Exception fetching transactions for {address}: {e}")
        time.sleep(delay * (attempt+1))
    return []

def fetch_node_stats(address: str) -> dict:
    """
    Fetch node statistics from the dashboard API.
    (Not used in current outputs but available for future use.)
    """
    try:
        url = f"{CORTENSOR_API}/stats/node/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {e}")
        return {}

# -------------------- JOB FUNCTIONS --------------------
def auto_update(context: CallbackContext):
    """
    Job function: send an auto-update message with combined node status, health, and node stall info.
    """
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:10]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please add one using 'Add Address'.")
        return
    delay = get_dynamic_delay(len(addresses))
    output_lines = []
    for addr in addresses:
        addr_display = f"🔑 {shorten_address(addr)}"
        balance = safe_fetch_balance(addr, delay)
        txs = safe_fetch_transactions(addr, delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
            latest_25 = txs[:25]
            groups = [latest_25[i*5:(i+1)*5] for i in range(5)]
            health_list = [("🟩" if all(tx.get('isError') == '0' for tx in group) else "🟥") if group else "⬜" for group in groups]
            health_status = " ".join(health_list)
            stall_status = "🚨 Node Stall" if len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25) else "✅ Normal"
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
            health_status = "No transactions"
            stall_status = "N/A"
        output_lines.append(
            f"*{addr_display}*\n"
            f"💰 Balance: `{balance:.4f} ETH` | Status: {status}\n"
            f"⏱️ Last Activity: `{last_activity}`\n"
            f"🩺 Health: {health_status}\n"
            f"⚠️ Stall: {stall_status}\n"
            f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})"
        )
    final_output = "*Auto Update*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    context.bot.send_message(chat_id=chat_id, text=final_output, parse_mode="Markdown")

def alert_check(context: CallbackContext):
    """
    Job function: check for alerts and send an alert message if conditions are met.
    Alerts if no transactions occur in the last 15 minutes or if a node stall is detected.
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
                msg_lines.append(f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})")
                context.bot.send_message(chat_id=chat_id, text="\n".join(msg_lines), parse_mode="Markdown")
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 *Alert for {shorten_address(addr)}*:\n- No transactions found!\n[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})",
                parse_mode="Markdown"
            )

# -------------------- CONVERSATION HANDLER FUNCTIONS --------------------
def add_address_start(update, context):
    """Initiate adding a wallet address."""
    update.effective_message.reply_text("Please send me the wallet address to add:", reply_markup=ReplyKeyboardRemove())
    return ADD_ADDRESS

def add_address_receive(update, context):
    """Receive and validate the wallet address to add."""
    chat_id = update.effective_chat.id
    address = update.effective_message.text.strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        update.effective_message.reply_text("❌ Invalid address! It must start with '0x' and be 42 characters long. Please send a valid address or type /cancel to abort.")
        return ADD_ADDRESS
    addresses = get_addresses_for_chat(chat_id)
    if address in addresses:
        update.effective_message.reply_text("⚠️ Address already added! Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if len(addresses) >= 10:
        update.effective_message.reply_text("❌ Maximum 10 addresses per chat! Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.effective_message.reply_text(f"✅ Added {shorten_address(address)} to your list!", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def remove_address_start(update, context):
    """Initiate removing a wallet address."""
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.effective_message.reply_text("No addresses found to remove.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    keyboard = [[addr] for addr in addresses]
    keyboard.append(["Cancel"])
    update.effective_message.reply_text("Select the address to remove:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return REMOVE_ADDRESS

def remove_address_receive(update, context):
    """Receive the wallet address to remove and update the list."""
    chat_id = update.effective_chat.id
    choice = update.effective_message.text.strip()
    if choice == "Cancel":
        update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses = get_addresses_for_chat(chat_id)
    if choice not in addresses:
        update.effective_message.reply_text("❌ Address not found.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.remove(choice)
    update_addresses_for_chat(chat_id, addresses)
    update.effective_message.reply_text(f"✅ Removed {shorten_address(choice)} from your list!", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def announce_start(update, context):
    """Initiate the announcement process (admin only)."""
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.effective_message.reply_text("❌ You are not authorized to use this command.", reply_markup=main_menu_keyboard(user_id))
        return ConversationHandler.END
    update.effective_message.reply_text("Please send the announcement message:", reply_markup=ReplyKeyboardRemove())
    return ANNOUNCE

def announce_receive(update, context):
    """Receive the announcement message and broadcast it."""
    message = update.effective_message.text
    data = load_data()
    if not data:
        update.effective_message.reply_text("No chats found to announce to.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    count = 0
    for chat in data.keys():
        try:
            context.bot.send_message(chat_id=int(chat), text=message)
            count += 1
        except Exception as e:
            logger.error(f"Error sending announcement to chat {chat}: {e}")
    update.effective_message.reply_text(f"📣 Announcement sent to {count} chats.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# -------------------- ERROR HANDLER --------------------
def error_handler(update, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    error_text = f"⚠️ An error occurred: {context.error}"
    for admin_id in ADMIN_IDS:
        context.bot.send_message(chat_id=admin_id, text=error_text)

# -------------------- COMMAND HANDLERS --------------------
def start_command(update, context):
    """Send a welcome message and show the main menu."""
    user_id = update.effective_user.id
    update.effective_message.reply_text(
        "👋 Welcome to Cortensor Node Monitoring Bot!\n\nI am here to help you monitor your node status easily. Choose an option from the menu below.",
        reply_markup=main_menu_keyboard(user_id)
    )

def help_command(update, context):
    """Send a detailed help message with explanations for each command."""
    update.effective_message.reply_text(
        "📖 *Cortensor Node Monitoring Bot Guide*\n\n"
        "• *Add Address*: ➕ Add a wallet address.\n"
        "   - Type your wallet address when prompted. It must start with '0x' and be 42 characters long.\n"
        "• *Remove Address*: ➖ Remove a wallet address from your list.\n"
        "   - Select the address you want to remove.\n"
        "• *Check Status*: 📊 Get a consolidated update of your node's balance, status, recent activity, health, and stall info.\n"
        "• *Auto Update*: 🔄 Enable automatic updates every 5 minutes with combined info.\n"
        "   - Automatically sends regular updates about your node(s).\n"
        "• *Enable Alerts*: 🔔 Monitor your node and receive alerts if no transactions occur for 15 minutes or if a node stall is detected.\n"
        "• *Stop*: ⛔ Disable all auto-update and alert jobs.\n"
        "• *Announce* (Admin only): 📣 Broadcast an announcement to all registered chats.\n\n"
        "💡 *Fun Fact*: Every blockchain transaction is like a digital heartbeat. Monitor your node and be a digital hero! 🦸‍♂️\n\n"
        "🚀 *Happy Monitoring!*",
        reply_markup=main_menu_keyboard(update.effective_user.id),
        parse_mode="Markdown"
    )

# -------------------- MAIN FUNCTION --------------------
def main():
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    logger.info("Bot is starting...")

    dp.add_handler(CommandHandler("start", start_command))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("auto_update", menu_auto_update))
    dp.add_handler(MessageHandler(Filters.regex("^Auto Update$"), menu_auto_update))
    dp.add_handler(CommandHandler("enable_alerts", menu_enable_alerts))
    dp.add_handler(MessageHandler(Filters.regex("^Enable Alerts$"), menu_enable_alerts))
    dp.add_handler(CommandHandler("stop", menu_stop))
    dp.add_handler(MessageHandler(Filters.regex("^Stop$"), menu_stop))
    dp.add_handler(CommandHandler("check_status", menu_check_status))
    dp.add_handler(MessageHandler(Filters.regex("^Check Status$"), menu_check_status))
    dp.add_handler(CommandHandler("announce", announce_start))
    dp.add_error_handler(error_handler)

    conv_add = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Add Address$"), add_address_start)],
        states={
            ADD_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, add_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_add)

    conv_remove = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Remove Address$"), remove_address_start)],
        states={
            REMOVE_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, remove_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_remove)

    conv_announce = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Announce$"), announce_start)],
        states={
            ANNOUNCE: [MessageHandler(Filters.text & ~Filters.command, announce_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_announce)

    dp.add_handler(MessageHandler(Filters.regex("^Check Status$"), menu_check_status))

    updater.start_polling()
    logger.info("Bot is running... 🚀")
    updater.idle()

# Ensure start_command is defined for fallback.
def start_command(update, context):
    user_id = update.effective_user.id
    update.effective_message.reply_text(
        "👋 Welcome to Cortensor Node Monitoring Bot!\n\nI am here to help you monitor your node status easily. Choose an option from the menu below.",
        reply_markup=main_menu_keyboard(user_id)
    )

if __name__ == "__main__":
    main()