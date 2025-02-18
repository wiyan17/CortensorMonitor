#!/usr/bin/env python3
# Cortensor Node Monitoring Bot (PTB v13.5 Compatible) – Button Version (English)

import logging
import requests
import json
import os
from datetime import datetime, timedelta, timezone
from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Updater,
    CommandHandler,
    CallbackQueryHandler,
    CallbackContext,
    MessageHandler,
    Filters,
    ConversationHandler,
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
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# WIB timezone (UTC+7)
WIB = timezone(timedelta(hours=7))

# ==================== CONVERSATION STATES ====================
ADD_ADDRESS, ANNOUNCE = range(1, 3)

# ==================== DATA STORAGE FUNCTIONS ====================
def load_data() -> dict:
    """Load data from JSON file."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    return {}

def save_data(data: dict):
    """Save data to JSON file."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_addresses_for_chat(chat_id: int) -> list:
    """Get addresses for a specific chat."""
    data = load_data()
    return data.get(str(chat_id), [])

def update_addresses_for_chat(chat_id: int, addresses: list):
    """Update addresses for a specific chat."""
    data = load_data()
    data[str(chat_id)] = addresses
    save_data(data)

# ==================== UTILITY FUNCTIONS ====================
def shorten_address(address: str) -> str:
    """Shorten Ethereum address."""
    return address[:6] + "..." + address[-4:] if len(address) > 10 else address

def get_wib_time() -> datetime:
    return datetime.now(WIB)

def format_time(time: datetime) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S WIB')

def get_age(timestamp: int) -> str:
    diff = datetime.now(WIB) - datetime.fromtimestamp(timestamp, WIB)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds} secs ago"
    minutes = seconds // 60
    return f"{minutes} mins ago" if minutes < 60 else f"{minutes//60} hours ago"

# ==================== API FUNCTIONS ====================
def fetch_balance(address: str) -> float:
    """Fetch balance from Arbiscan API."""
    try:
        params = {
            "module": "account",
            "action": "balance",
            "address": address,
            "tag": "latest",
            "apikey": API_KEY
        }
        response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
        return int(response.json()['result']) / 10**18
    except Exception as e:
        logger.error(f"Balance error: {str(e)}")
        return 0.0

def fetch_transactions(address: str) -> list:
    """Fetch transaction history."""
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
        return response.json().get('result', [])
    except Exception as e:
        logger.error(f"Tx error: {str(e)}")
        return []

def fetch_node_stats(address: str) -> dict:
    """Fetch node stats from Cortensor API."""
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error: {str(e)}")
        return {}

# ==================== JOB FUNCTIONS ====================
def auto_update(context: CallbackContext):
    """Job for auto-update; always fetches the latest data from storage."""
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:5]
    if not addresses:
        context.bot.send_message(
            chat_id=chat_id,
            text="ℹ️ No addresses found! Please use the Add Address button.",
            parse_mode="Markdown"
        )
        return

    responses = []
    for addr in addresses:
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)[:6]
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
        responses.append(
            f"🔹 *{shorten_address(addr)}*\n"
            f"💵 Balance: `{balance:.4f} ETH`\n"
            f"📊 Status: {status}\n"
            f"⏳ Last activity: {last_activity}\n"
            f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
            f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )

    context.bot.send_message(
        chat_id=chat_id,
        text="🔄 *Auto Update*\n\n" + "\n\n".join(responses) +
             f"\n\n⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def alert_check(context: CallbackContext):
    """Job to check for inactivity and send alerts."""
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:5]
    for addr in addresses:
        txs = fetch_transactions(addr)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            if time_diff > timedelta(minutes=15):
                context.bot.send_message(
                    chat_id=chat_id,
                    text=f"🚨 *Inactivity Alert!*\n\n"
                         f"🔹 Address: `{shorten_address(addr)}`\n"
                         f"⏳ No transactions in the last 15 minutes!\n\n"
                         f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
                         f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})",
                    parse_mode="Markdown",
                    disable_web_page_preview=True
                )
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 *Inactivity Alert!*\n\n"
                     f"🔹 Address: `{shorten_address(addr)}`\n"
                     f"⏳ No transactions found!\n\n"
                     f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
                     f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})",
                parse_mode="Markdown",
                disable_web_page_preview=True
            )

# ==================== MAIN MENU KEYBOARD ====================
def get_main_menu_keyboard(is_admin: bool) -> InlineKeyboardMarkup:
    keyboard = [
        [InlineKeyboardButton("Add Address", callback_data="add_address"),
         InlineKeyboardButton("Remove Address", callback_data="remove_address")],
        [InlineKeyboardButton("Check Status", callback_data="ping"),
         InlineKeyboardButton("Node Health", callback_data="health")],
        [InlineKeyboardButton("Auto Update", callback_data="auto"),
         InlineKeyboardButton("Enable Alerts", callback_data="alert")],
        [InlineKeyboardButton("Stop", callback_data="stop"),
         InlineKeyboardButton("Help", callback_data="help")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("Announce", callback_data="announce")])
    return InlineKeyboardMarkup(keyboard)

def show_main_menu(update, context):
    is_admin = update.effective_user.id in ADMIN_IDS
    keyboard = get_main_menu_keyboard(is_admin)
    welcome_text = (
        "👋 Welcome to *Cortensor Node Monitoring Bot!* \n\n"
        "I am here to help you monitor your node status easily and efficiently. "
        "Choose an option below to get started, and feel free to explore all available features!\n\n"
        "💡 *Tip*: Use the Auto Update feature to receive updates every 5 minutes.\n\n"
        "Enjoy your monitoring experience! 🚀"
    )
    if update.callback_query:
        update.callback_query.edit_message_text(
            text=welcome_text,
            reply_markup=keyboard,
            parse_mode="Markdown",
            disable_web_page_preview=True
        )
    else:
        update.message.reply_text(text=welcome_text, reply_markup=keyboard, parse_mode="Markdown")

# ==================== CONVERSATION HANDLERS ====================
def add_address_entry(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="Please send me the wallet address to add:", parse_mode="Markdown")
    return ADD_ADDRESS

def add_address_receive(update, context):
    chat_id = update.effective_chat.id
    address = update.message.text.strip().lower()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("❌ Invalid address! It must start with `0x` and be 42 characters long.\nSend a valid address or /cancel to abort.", parse_mode="Markdown")
        return ADD_ADDRESS
    addresses = get_addresses_for_chat(chat_id)
    if address in addresses:
        update.message.reply_text("⚠️ Address already added!")
        show_main_menu(update, context)
        return ConversationHandler.END
    if len(addresses) >= 5:
        update.message.reply_text("❌ Maximum 5 addresses per chat!")
        show_main_menu(update, context)
        return ConversationHandler.END
    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Added `{shorten_address(address)}` to your list!", parse_mode="Markdown")
    show_main_menu(update, context)
    return ConversationHandler.END

def remove_address_entry(update, context):
    query = update.callback_query
    query.answer()
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        query.edit_message_text("ℹ️ No addresses found to remove.")
        show_main_menu(update, context)
        return
    keyboard = []
    for addr in addresses:
        keyboard.append([InlineKeyboardButton(shorten_address(addr), callback_data=f"remove_{addr}")])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="cancel_remove")])
    query.edit_message_text("Select the address to remove:", reply_markup=InlineKeyboardMarkup(keyboard), parse_mode="Markdown")

def remove_address_selection(update, context):
    query = update.callback_query
    query.answer()
    data = query.data
    if data == "cancel_remove":
        show_main_menu(update, context)
        return
    address = data.replace("remove_", "")
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if address not in addresses:
        query.edit_message_text("❌ Address not found.")
        show_main_menu(update, context)
        return
    addresses.remove(address)
    update_addresses_for_chat(chat_id, addresses)
    query.edit_message_text(f"✅ Removed `{shorten_address(address)}` from your list!", parse_mode="Markdown")
    show_main_menu(update, context)

def announce_entry(update, context):
    query = update.callback_query
    query.answer()
    if update.effective_user.id not in ADMIN_IDS:
        query.edit_message_text("❌ You are not authorized to use this command.")
        show_main_menu(update, context)
        return ConversationHandler.END
    query.edit_message_text("Please send the announcement message:")
    return ANNOUNCE

def announce_receive(update, context):
    message = update.message.text
    data = load_data()
    if not data:
        update.message.reply_text("No chats found to announce to.")
        show_main_menu(update, context)
        return ConversationHandler.END
    count = 0
    for chat in data.keys():
        try:
            context.bot.send_message(chat_id=int(chat), text=message)
            count += 1
        except Exception as e:
            logger.error(f"Error sending announcement to chat {chat}: {e}")
    update.message.reply_text(f"Announcement sent to {count} chats.", parse_mode="Markdown")
    show_main_menu(update, context)
    return ConversationHandler.END

def cancel(update, context):
    update.message.reply_text("Operation cancelled.")
    show_main_menu(update, context)
    return ConversationHandler.END

# ==================== BUTTON CALLBACK HANDLERS ====================
def ping_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        query.edit_message_text("ℹ️ No addresses found! Please add one using the Add Address button.")
        show_main_menu(update, context)
        return
    responses = []
    for addr in addresses[:5]:
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)[:6]
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            status = "🟢 Online" if time_diff <= timedelta(minutes=5) else "🔴 Offline"
            last_activity = get_age(last_tx_time)
        else:
            status = "🔴 Offline"
            last_activity = "N/A"
        responses.append(
            f"🔹 *{shorten_address(addr)}*\n"
            f"💵 Balance: `{balance:.4f} ETH`\n"
            f"📊 Status: {status}\n"
            f"⏳ Last activity: {last_activity}\n"
            f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
            f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    query.edit_message_text(
        text="📊 *Node Status*\n\n" + "\n\n".join(responses) +
             f"\n\n⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    show_main_menu(update, context)

def health_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    now = datetime.now(WIB)
    one_hour_ago = now - timedelta(hours=1)
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        query.edit_message_text("ℹ️ No addresses found! Please add one using the Add Address button.")
        show_main_menu(update, context)
        return
    responses = []
    for addr in addresses[:5]:
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)
        recent_txs = [tx for tx in txs if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= one_hour_ago]
        if recent_txs:
            last_tx_time = int(recent_txs[0]['timeStamp'])
            last_activity = get_age(last_tx_time)
            groups = [recent_txs[i:i+6] for i in range(0, len(recent_txs), 6)]
            group_statuses = []
            for group in groups:
                if any(tx.get('isError') != '0' for tx in group):
                    group_statuses.append("🟥")
                else:
                    group_statuses.append("🟩")
            health_status = " ".join(group_statuses)
        else:
            last_activity = "N/A"
            health_status = "No transactions in the last hour"
        responses.append(
            f"🔹 *{shorten_address(addr)}*\n"
            f"💵 Balance: `{balance:.4f} ETH`\n"
            f"⏳ Last activity: {last_activity}\n"
            f"🩺 Health: {health_status}\n"
            f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | "
            f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{addr})"
        )
    query.edit_message_text(
        text="🩺 *Node Health*\n\n" + "\n\n".join(responses) +
             f"\n\n⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )
    show_main_menu(update, context)

def auto_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    if not get_addresses_for_chat(chat_id):
        query.edit_message_text("ℹ️ No addresses found! Please add one using the Add Address button.")
        show_main_menu(update, context)
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"auto_update_{chat_id}")
    if current_jobs:
        query.edit_message_text("ℹ️ Auto-update is already active!")
        show_main_menu(update, context)
        return
    context.job_queue.run_repeating(
        auto_update,
        interval=UPDATE_INTERVAL,
        context={'chat_id': chat_id},
        name=f"auto_update_{chat_id}"
    )
    query.edit_message_text("✅ *Auto-updates enabled!*\n\nI will send updates every 5 minutes with the latest data.", parse_mode="Markdown")
    show_main_menu(update, context)

def alert_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    if not get_addresses_for_chat(chat_id):
        query.edit_message_text("ℹ️ No addresses found! Please add one using the Add Address button.")
        show_main_menu(update, context)
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"alert_{chat_id}")
    if current_jobs:
        query.edit_message_text("ℹ️ Alerts are already active!")
        show_main_menu(update, context)
        return
    context.job_queue.run_repeating(
        alert_check,
        interval=900,  # 15 minutes
        context={'chat_id': chat_id},
        name=f"alert_{chat_id}"
    )
    query.edit_message_text("✅ *Alerts enabled!*\n\nI will notify you if there are no transactions in the last 15 minutes.", parse_mode="Markdown")
    show_main_menu(update, context)

def stop_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    removed_jobs = 0
    for job_name in (f"auto_update_{chat_id}", f"alert_{chat_id}"):
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()
            removed_jobs += 1
    if removed_jobs:
        query.edit_message_text("✅ *Auto-update and alerts have been stopped!*", parse_mode="Markdown")
    else:
        query.edit_message_text("ℹ️ No active jobs found.")
    show_main_menu(update, context)

def help_button(update, context):
    query = update.callback_query
    query.answer()
    text = (
        "📖 *Help Menu*\n\n"
        "1. *Add Address*: Use the **Add Address** button to add a wallet address.\n"
        "2. *Remove Address*: Use the **Remove Address** button to remove an address from your list.\n"
        "3. *Check Status*: Use the **Check Status** button to view node status, balance, and recent activity.\n"
        "4. *Node Health*: Use the **Node Health** button to check node health based on the last hour's transactions.\n"
        "5. *Auto Update*: Use the **Auto Update** button to enable automatic updates every 5 minutes.\n"
        "6. *Enable Alerts*: Use the **Enable Alerts** button to receive notifications if there are no transactions for 15 minutes.\n"
        "7. *Stop*: Use the **Stop** button to disable auto-updates and alerts.\n"
        "8. *Announce* (Admin only): Use the **Announce** button to send a message to all chats.\n\n"
        "💡 *Fun Fact*: Every blockchain transaction is like a digital heartbeat that keeps the system alive. Monitor your node and be a digital hero!\n\n"
        "🚀 *Happy Monitoring!*"
    )
    query.edit_message_text(text, parse_mode="Markdown")
    show_main_menu(update, context)

# ==================== ADDITIONAL COMMAND HANDLERS ====================
def help_command(update, context):
    """Handler for /help command with full guide and fun facts."""
    text = (
        "📖 *Complete Guide for Cortensor Node Monitoring Bot!*\n\n"
        "Below are the available commands and features:\n\n"
        "1. *Add Address*: Add a wallet address using the **Add Address** button.\n"
        "   - Ensure the address is valid (42 characters starting with '0x').\n\n"
        "2. *Remove Address*: Remove an address from your list using the **Remove Address** button.\n\n"
        "3. *Check Status*: View node status, including balance and recent activity using the **Check Status** button.\n\n"
        "4. *Node Health*: Check the health of your node based on transactions in the last hour using the **Node Health** button.\n\n"
        "5. *Auto Update*: Enable automatic updates every 5 minutes using the **Auto Update** button.\n\n"
        "6. *Enable Alerts*: Receive notifications if there are no transactions in the last 15 minutes using the **Enable Alerts** button.\n\n"
        "7. *Stop*: Disable auto-updates and alerts using the **Stop** button.\n\n"
        "8. *Announce* (Admin only): Send an announcement to all chats using the **Announce** button.\n\n"
        "💡 *Fun Fact*: Every blockchain transaction is like a digital heartbeat that keeps the system alive. Monitor your node and be a digital hero!\n\n"
        "🚀 *Happy Monitoring!*"
    )
    update.message.reply_text(text, parse_mode="Markdown")

# ==================== MAIN FUNCTION ====================
def main():
    """Run the bot."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    # Log when the bot is starting
    logger.info("Bot is starting...")

    # /start command shows the main menu with a warm welcome.
    dp.add_handler(CommandHandler("start", show_main_menu))
    dp.add_handler(CommandHandler("help", help_command))

    # Conversation handlers for Add Address and Announce
    add_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_address_entry, pattern="^add_address$")],
        states={
            ADD_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, add_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    dp.add_handler(add_conv_handler)

    announce_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(announce_entry, pattern="^announce$")],
        states={
            ANNOUNCE: [MessageHandler(Filters.text & ~Filters.command, announce_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    dp.add_handler(announce_conv_handler)

    # Callback query handlers for remove address flow
    dp.add_handler(CallbackQueryHandler(remove_address_entry, pattern="^remove_address$"))
    dp.add_handler(CallbackQueryHandler(remove_address_selection, pattern="^remove_"))

    # Callback query handlers for other button actions
    dp.add_handler(CallbackQueryHandler(ping_button, pattern="^ping$"))
    dp.add_handler(CallbackQueryHandler(health_button, pattern="^health$"))
    dp.add_handler(CallbackQueryHandler(auto_button, pattern="^auto$"))
    dp.add_handler(CallbackQueryHandler(alert_button, pattern="^alert$"))
    dp.add_handler(CallbackQueryHandler(stop_button, pattern="^stop$"))
    dp.add_handler(CallbackQueryHandler(help_button, pattern="^help$"))

    updater.start_polling()
    logger.info("Bot is running...")
    updater.idle()

if __name__ == "__main__":
    main()