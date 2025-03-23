#!/usr/bin/env python3
"""
Cortensor Node Monitoring Bot – Telegram Reply Keyboard Version

Features:
• Add Address (with optional label, format: <wallet_address>,<label>)
• Remove Address
• Check Status
• Auto Update
• Enable Alerts
• Set Delay (custom auto update interval per chat)
• Stop
• Help
• Announce (admin only)

Maximum nodes per chat: 15
"""

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
    CallbackContext
)
from dotenv import load_dotenv

load_dotenv()

# -------------------- CONFIGURATION --------------------
TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
DEFAULT_UPDATE_INTERVAL = 300  # Default auto update interval: 5 minutes
CORTENSOR_API = os.getenv("CORTENSOR_API", "https://dashboard-devnet3.cortensor.network")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATA_FILE = "data.json"
MIN_AUTO_UPDATE_INTERVAL = 60  # Minimum allowed auto update interval in seconds

# -------------------- INITIALIZATION --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))  # WIB (UTC+7)

# -------------------- CONVERSATION STATES --------------------
ADD_ADDRESS, REMOVE_ADDRESS, ANNOUNCE, SET_INTERVAL = range(1, 5)

# -------------------- DATA STORAGE FUNCTIONS --------------------
def load_data() -> dict:
    """
    Load data from DATA_FILE. If the file's content is not a dict,
    log a warning, reset the file to an empty dict, and return an empty dict.
    """
    if os.path.exists(DATA_FILE):
        try:
            data = json.load(open(DATA_FILE, "r"))
            if not isinstance(data, dict):
                logger.warning("Data file is not in the expected format. Resetting data.")
                save_data({})
                return {}
            return data
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    return {}

def save_data(data: dict):
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_chat_data(chat_id: int) -> dict:
    data = load_data()
    return data.get(str(chat_id), {"addresses": [], "auto_update_interval": DEFAULT_UPDATE_INTERVAL})

def update_chat_data(chat_id: int, chat_data: dict):
    data = load_data()
    data[str(chat_id)] = chat_data
    save_data(data)

def get_addresses_for_chat(chat_id: int) -> list:
    return get_chat_data(chat_id).get("addresses", [])

def update_addresses_for_chat(chat_id: int, addresses: list):
    chat_data = get_chat_data(chat_id)
    chat_data["addresses"] = addresses
    update_chat_data(chat_id, chat_data)

def get_auto_update_interval(chat_id: int) -> float:
    return get_chat_data(chat_id).get("auto_update_interval", DEFAULT_UPDATE_INTERVAL)

def update_auto_update_interval(chat_id: int, interval: float):
    chat_data = get_chat_data(chat_id)
    chat_data["auto_update_interval"] = interval
    update_chat_data(chat_id, chat_data)

# -------------------- HELPER FUNCTION --------------------
def parse_address_item(item):
    """
    Return the wallet and label as a tuple.
    If the stored item is a dictionary, extract the values;
    if it's a plain string, return it with an empty label.
    """
    if isinstance(item, dict):
        return item.get("address"), item.get("label", "")
    return item, ""

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
        ["Enable Alerts", "Set Delay"],
        ["Stop", "Help"]
    ]
    if user_id in ADMIN_IDS:
        keyboard.append(["Announce"])
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=False)

# -------------------- API FUNCTIONS --------------------
def safe_fetch_balance(address: str, delay: float) -> float:
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
    try:
        url = f"{CORTENSOR_API}/stats/node/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {e}")
        return {}

# -------------------- JOB FUNCTIONS --------------------
def auto_update(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:15]
    if not addresses:
        context.bot.send_message(chat_id=chat_id, text="ℹ️ No addresses found! Please add one using 'Add Address'.")
        return
    output_lines = []
    for item in addresses:
        wallet, label = parse_address_item(item)
        addr_display = f"🔑 {shorten_address(wallet)}" + (f" ({label})" if label else "")
        balance = safe_fetch_balance(wallet, delay=2.0)
        txs = safe_fetch_transactions(wallet, delay=2.0)
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
            f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{wallet}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{wallet})"
        )
    final_output = "*Auto Update*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    context.bot.send_message(chat_id=chat_id, text=final_output, parse_mode="Markdown")

def alert_check(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:15]
    for item in addresses:
        wallet, label = parse_address_item(item)
        txs = safe_fetch_transactions(wallet, delay=2.0)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            latest_25 = txs[:25]
            stall_condition = len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25)
            if time_diff > timedelta(minutes=15) or stall_condition:
                msg_lines = [f"🚨 *Alert for {shorten_address(wallet)}" + (f" ({label})" if label else "") + "*:"]
                if time_diff > timedelta(minutes=15):
                    msg_lines.append("⏱️ No transactions in the last 15 minutes.")
                if stall_condition:
                    msg_lines.append("⚠️ Node stall detected (only PING transactions in the last 25).")
                msg_lines.append(f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{wallet}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{wallet})")
                context.bot.send_message(chat_id=chat_id, text="\n".join(msg_lines), parse_mode="Markdown")
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 *Alert for {shorten_address(wallet)}" + (f" ({label})" if label else "") + "*:\n- No transactions found!\n[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{wallet}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{wallet})",
                parse_mode="Markdown"
            )

# -------------------- CONVERSATION HANDLER FUNCTIONS --------------------
def add_address_start(update, context):
    update.effective_message.reply_text(
        "Please send the wallet address (with format `<wallet_address>,<label>` if you want to add a label).\nExample: 0xABC123...7890,My Node\n(Send /cancel to abort)",
        reply_markup=ReplyKeyboardRemove()
    )
    return ADD_ADDRESS

def add_address_receive(update, context):
    chat_id = update.effective_chat.id
    text = update.effective_message.text.strip()
    parts = [x.strip() for x in text.split(",")]
    wallet = parts[0].lower()
    label = parts[1] if len(parts) > 1 else ""
    if not wallet.startswith("0x") or len(wallet) != 42:
        update.effective_message.reply_text("❌ Invalid wallet address! It must start with '0x' and be 42 characters long.\nTry again or send /cancel to abort.")
        return ADD_ADDRESS
    addresses = get_addresses_for_chat(chat_id)
    if any((item.get("address") if isinstance(item, dict) else item) == wallet for item in addresses):
        update.effective_message.reply_text("⚠️ Address already exists! Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if len(addresses) >= 15:
        update.effective_message.reply_text("❌ Maximum of 15 nodes per chat reached! Returning to main menu.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.append({"address": wallet, "label": label})
    update_addresses_for_chat(chat_id, addresses)
    update.effective_message.reply_text(f"✅ Added: {shorten_address(wallet)}" + (f" ({label})" if label else ""), reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def remove_address_start(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.effective_message.reply_text("No addresses found to remove.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    keyboard = []
    for item in addresses:
        wallet, label = parse_address_item(item)
        display = f"{wallet}" + (f" ({label})" if label else "")
        keyboard.append([display])
    keyboard.append(["Cancel"])
    update.effective_message.reply_text("Select the address you want to remove:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return REMOVE_ADDRESS

def remove_address_receive(update, context):
    chat_id = update.effective_chat.id
    choice = update.effective_message.text.strip()
    if choice.lower() == "cancel":
        update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses = get_addresses_for_chat(chat_id)
    new_addresses = []
    found = False
    for item in addresses:
        wallet, label = parse_address_item(item)
        display = f"{wallet}" + (f" ({label})" if label else "")
        if display == choice:
            found = True
            continue
        new_addresses.append(item)
    if not found:
        update.effective_message.reply_text("❌ Address not found.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    update_addresses_for_chat(chat_id, new_addresses)
    update.effective_message.reply_text("✅ Address removed.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def set_interval_start(update, context):
    update.effective_message.reply_text(
        "Please enter the desired auto update interval (in seconds).\nExample: 300\nMinimum is 60 seconds.\n(Send /cancel to abort)",
        reply_markup=ReplyKeyboardRemove()
    )
    return SET_INTERVAL

def set_interval_receive(update, context):
    chat_id = update.effective_chat.id
    try:
        interval_val = float(update.effective_message.text.strip())
        if interval_val < MIN_AUTO_UPDATE_INTERVAL:
            update.effective_message.reply_text(f"Interval cannot be less than {MIN_AUTO_UPDATE_INTERVAL} seconds. Try again or send /cancel.")
            return SET_INTERVAL
        update_auto_update_interval(chat_id, interval_val)
        update.effective_message.reply_text(f"✅ Auto update interval set to {interval_val} seconds.", reply_markup=main_menu_keyboard(update.effective_user.id))
    except ValueError:
        update.effective_message.reply_text("❌ Invalid input. Please enter a number (e.g., 300).")
        return SET_INTERVAL
    return ConversationHandler.END

def announce_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.effective_message.reply_text("❌ You are not authorized to use this command.", reply_markup=main_menu_keyboard(user_id))
        return ConversationHandler.END
    update.effective_message.reply_text("Please send the announcement message:", reply_markup=ReplyKeyboardRemove())
    return ANNOUNCE

def announce_receive(update, context):
    message = update.effective_message.text
    data = load_data()
    if not data:
        update.effective_message.reply_text("No chats found to send the announcement.", reply_markup=main_menu_keyboard(update.effective_user.id))
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

# -------------------- COMMAND FUNCTIONS --------------------
def menu_check_status(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.effective_message.reply_text("No addresses registered! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    output_lines = []
    for item in addresses[:15]:
        wallet, label = parse_address_item(item)
        addr_display = f"🔑 {shorten_address(wallet)}" + (f" ({label})" if label else "")
        balance = safe_fetch_balance(wallet, delay=2.0)
        txs = safe_fetch_transactions(wallet, delay=2.0)
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
            f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{wallet}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{wallet})"
        )
    final_output = "*Check Status*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    update.effective_message.reply_text(final_output, parse_mode="Markdown", reply_markup=main_menu_keyboard(update.effective_user.id))

def menu_auto_update(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.effective_message.reply_text("No addresses registered! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    interval = get_auto_update_interval(chat_id)
    current_jobs = context.job_queue.get_jobs_by_name(f"auto_update_{chat_id}")
    if current_jobs:
        update.effective_message.reply_text("Auto update is already active.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(auto_update, interval=interval, context={'chat_id': chat_id}, name=f"auto_update_{chat_id}")
    update.effective_message.reply_text(
        f"✅ Auto update started. (Interval: {interval} seconds)\n\nThe bot will send node updates automatically.",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )

def menu_enable_alerts(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.effective_message.reply_text("No addresses registered! Please add one using 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"alert_{chat_id}")
    if current_jobs:
        update.effective_message.reply_text("Alerts are already active.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(alert_check, interval=900, context={'chat_id': chat_id}, name=f"alert_{chat_id}")
    update.effective_message.reply_text(
        "✅ Alerts enabled.\n\nThe bot will monitor your node and send alerts if no transactions occur for 15 minutes or if a node stall is detected.",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )

def menu_stop(update, context):
    chat_id = update.effective_chat.id
    removed_jobs = 0
    for job_name in (f"auto_update_{chat_id}", f"alert_{chat_id}"):
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()
            removed_jobs += 1
    if removed_jobs:
        update.effective_message.reply_text("✅ All jobs (auto update and alerts) have been stopped.", reply_markup=main_menu_keyboard(update.effective_user.id))
    else:
        update.effective_message.reply_text("No active jobs found.", reply_markup=main_menu_keyboard(update.effective_user.id))

def help_command(update, context):
    help_text = (
        "📖 *Cortensor Node Monitoring Bot - Help Guide*\n\n"
        "Below is a list of all available commands and their functions:\n\n"
        "• *Add Address*\n"
        "  - *Usage*: Send your wallet address using the format `<wallet_address>,<label>` (the label is optional).\n"
        "  - *Example*: `0xABC123...7890,My Node`\n"
        "  - *Description*: Adds the specified wallet address to your monitoring list. You can monitor up to 15 nodes per chat.\n\n"
        "• *Remove Address*\n"
        "  - *Usage*: Select an address from your list to remove.\n"
        "  - *Description*: Removes a wallet address from your monitoring list.\n\n"
        "• *Check Status*\n"
        "  - *Usage*: Simply send the command.\n"
        "  - *Description*: Provides a detailed update on each node, including balance, online status, last activity, health metrics, and stall status.\n\n"
        "• *Auto Update*\n"
        "  - *Usage*: Activate by sending the command.\n"
        "  - *Description*: Starts periodic auto updates (default every 5 minutes or your custom interval) to deliver real-time node status.\n\n"
        "• *Enable Alerts*\n"
        "  - *Usage*: Activate by sending the command.\n"
        "  - *Description*: Monitors your nodes continuously and sends alerts if no transactions are detected within 15 minutes or if a node stall is observed.\n\n"
        "• *Set Delay*\n"
        "  - *Usage*: After sending the command, enter your desired auto update interval (in seconds, minimum 60 seconds).\n"
        "  - *Description*: Allows you to customize the interval for auto updates.\n\n"
        "• *Stop*\n"
        "  - *Usage*: Simply send the command.\n"
        "  - *Description*: Stops all active auto update and alert jobs.\n\n"
        "• *Announce* (Admin only)\n"
        "  - *Usage*: Accessible only to administrators. Send the command followed by your announcement message.\n"
        "  - *Description*: Broadcasts an announcement to all registered chats.\n\n"
        "• *Help*\n"
        "  - *Usage*: Simply send the command.\n"
        "  - *Description*: Displays this help guide with detailed information on all commands.\n\n"
        "💡 *Note*: Maximum nodes per chat: 15\n"
        "🚀 *Happy Monitoring!*"
    )
    update.effective_message.reply_text(help_text, parse_mode="Markdown", disable_web_page_preview=True)

def start_command(update, context):
    user_id = update.effective_user.id
    update.effective_message.reply_text(
        "👋 Welcome to the Cortensor Node Monitoring Bot!\nSelect an option from the menu below:",
        reply_markup=main_menu_keyboard(user_id)
    )

# -------------------- ERROR HANDLER --------------------
def error_handler(update, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    error_text = f"⚠️ An error occurred: {context.error}"
    for admin_id in ADMIN_IDS:
        context.bot.send_message(chat_id=admin_id, text=error_text)

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

    conv_interval = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Set Delay$"), set_interval_start)],
        states={
            SET_INTERVAL: [MessageHandler(Filters.text & ~Filters.command, set_interval_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_interval)

    conv_announce = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Announce$"), announce_start)],
        states={
            ANNOUNCE: [MessageHandler(Filters.text & ~Filters.command, announce_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operation cancelled.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_announce)

    updater.start_polling()
    logger.info("Bot is running... 🚀")
    updater.idle()

if __name__ == "__main__":
    main()