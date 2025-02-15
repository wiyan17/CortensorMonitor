#!/usr/bin/env python3
# Cortensor Node Monitoring Bot (PTB v13.5 Compatible)

import logging
import requests
import json
import os
from datetime import datetime, timedelta, timezone
from telegram.ext import Updater, CommandHandler, CallbackContext
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
UPDATE_INTERVAL = 120  # 2 minutes
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

# ==================== JOB FUNCTION ====================
def auto_update(context: CallbackContext):
    """Job for auto-update; always fetches the latest data from storage."""
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:5]
    if not addresses:
        context.bot.send_message(
            chat_id=chat_id,
            text="ℹ️ No addresses found! Add one with `/add`.",
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

# ==================== COMMAND HANDLERS ====================
def start(update, context):
    """Handler for /start command."""
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS
    text = (
        "👋 *Welcome to Cortensor Node Monitoring Bot!*\n\n"
        "Here's what I can do:\n"
        "✅ `/add <address>` - Add a wallet address\n"
        "❌ `/remove <address>` - Remove a wallet address\n"
        "📊 `/ping` - Check node status\n"
        "🩺 `/health` - Check node health (last 1 hour transactions)\n"
        "🔄 `/auto` - Enable auto-updates every 2 mins\n"
        "🚫 `/stop` - Stop auto-updates and alerts\n"
        "📈 `/nodestats <address>` - View node stats\n"
        "🚨 `/alert` - Get notified if no transactions in 15 mins\n"
    )
    if is_admin:
        text += "📢 `/announce <message>` - Send an announcement to all chats\n"
    text += "❓ `/help` - Show help menu\n\nLet's get started! Add your first address with `/add`."
    update.message.reply_text(text, parse_mode="Markdown")

def help_command(update, context):
    """Handler for /help command."""
    user_id = update.message.from_user.id
    is_admin = user_id in ADMIN_IDS
    text = (
        "📖 *Help Menu*\n\n"
        "1. Add an address: `/add 0x123...`\n"
        "2. Remove an address: `/remove 0x123...`\n"
        "3. Check status: `/ping`\n"
        "4. Check health: `/health`\n"
        "5. Enable auto-updates: `/auto`\n"
        "6. Stop auto-updates/alerts: `/stop`\n"
        "7. Set alerts: `/alert`\n"
    )
    if is_admin:
        text += "8. Announce a message: `/announce <message>`\n"
    text += "9. Clear recent messages: `/clear`\n"
    text += "Maximum 5 addresses per chat.\n\nNeed more help? Just ask!"
    update.message.reply_text(text, parse_mode="Markdown")

def add(update, context):
    """Handler for /add command."""
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Usage: `/add 0x123...`", parse_mode="Markdown")
        return

    address = context.args[0].lower()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("❌ Invalid address! It must start with `0x` and be 42 characters long.")
        return

    addresses = get_addresses_for_chat(chat_id)
    if address in addresses:
        update.message.reply_text("⚠️ Address already added!")
        return

    if len(addresses) >= 5:
        update.message.reply_text("❌ Maximum 5 addresses per chat!")
        return

    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Added `{shorten_address(address)}` to your list!", parse_mode="Markdown")

def remove(update, context):
    """Handler for /remove command."""
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Usage: `/remove 0x123...`", parse_mode="Markdown")
        return

    address = context.args[0].lower()
    addresses = get_addresses_for_chat(chat_id)

    if address not in addresses:
        update.message.reply_text("❌ Address not found in your list!")
        return

    addresses.remove(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Removed `{shorten_address(address)}` from your list!", parse_mode="Markdown")

def ping(update, context):
    """Handler for /ping command."""
    chat_id = update.message.chat_id
    addresses = context.args if context.args else get_addresses_for_chat(chat_id)
    if not addresses:
        update.message.reply_text("ℹ️ No addresses found! Add one with `/add`.")
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

    update.message.reply_text(
        "📊 *Node Status*\n\n" + "\n\n".join(responses) +
        f"\n\n⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def nodestats(update, context):
    """Handler for /nodestats command."""
    if not context.args:
        update.message.reply_text("Usage: `/nodestats 0x123...`", parse_mode="Markdown")
        return

    address = context.args[0]
    stats = fetch_node_stats(address)
    if not stats:
        update.message.reply_text("❌ No data found for this address!")
        return

    update.message.reply_text(
        f"📈 *Node Stats for {shorten_address(address)}*\n\n"
        f"• Uptime: `{stats.get('uptime', 'N/A')}`\n"
        f"• Total TXs: `{stats.get('total_tx', 0)}`\n"
        f"• Last activity: `{get_age(stats.get('last_tx', 0))}`\n\n"
        f"🔗 [Arbiscan](https://sepolia.arbiscan.io/address/{address}) | "
        f"📈 [Dashboard]({CORTENSOR_API}/nodestats/{address})\n\n"
        f"⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def health(update, context):
    """Handler for /health command to check node health based on the last 1 hour of transactions."""
    chat_id = update.message.chat_id
    addresses = context.args if context.args else get_addresses_for_chat(chat_id)
    now = datetime.now(WIB)
    one_hour_ago = now - timedelta(hours=1)
    if not addresses:
        update.message.reply_text("ℹ️ No addresses found! Add one with `/add`.")
        return

    responses = []
    for addr in addresses[:5]:
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)
        # Filter transactions from the last 1 hour
        recent_txs = [tx for tx in txs if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= one_hour_ago]
        if recent_txs:
            last_tx_time = int(recent_txs[0]['timeStamp'])
            last_activity = get_age(last_tx_time)
            # Group transactions into chunks of 6
            groups = [recent_txs[i:i+6] for i in range(0, len(recent_txs), 6)]
            group_statuses = []
            for group in groups:
                # Mark group green (🟩) if all transactions are successful (isError == "0"),
                # otherwise mark red (🟥)
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

    update.message.reply_text(
        "🩺 *Node Health*\n\n" + "\n\n".join(responses) +
        f"\n\n⏰ *Last update:* {format_time(get_wib_time())}",
        parse_mode="Markdown",
        disable_web_page_preview=True
    )

def enable_auto(update, context):
    """Handler for /auto command to enable auto-updates."""
    chat_id = update.message.chat_id
    if not get_addresses_for_chat(chat_id):
        update.message.reply_text("ℹ️ No addresses found! Add one with `/add`.")
        return
    # Check if an auto-update job already exists for this chat
    current_jobs = context.job_queue.get_jobs_by_name(f"auto_update_{chat_id}")
    if current_jobs:
        update.message.reply_text("ℹ️ Auto-update is already active!")
        return
    context.job_queue.run_repeating(
        auto_update,
        interval=UPDATE_INTERVAL,
        context={'chat_id': chat_id},
        name=f"auto_update_{chat_id}"
    )
    update.message.reply_text("✅ *Auto-updates enabled!*\n\nI will send updates every 2 minutes with the latest data.", parse_mode="Markdown")

def clear(update, context):
    """Admin command to clear recent messages in the chat."""
    chat_id = update.message.chat_id
    user_id = update.message.from_user.id
    try:
        admins = [admin.user.id for admin in context.bot.get_chat_administrators(chat_id)]
    except Exception as e:
        update.message.reply_text("⚠️ Unable to check admin status.")
        return
    if user_id not in admins:
        update.message.reply_text("❌ You must be an admin to use this command!")
        return
    last_msg_id = update.message.message_id
    count = 0
    for msg_id in range(last_msg_id - 50, last_msg_id + 1):
        try:
            context.bot.delete_message(chat_id, msg_id)
            count += 1
        except Exception as e:
            continue
    update.message.reply_text(f"✅ Cleared {count} messages.", parse_mode="Markdown")

def announce(update, context):
    """Handler for /announce command to send a message to all chats (admin only)."""
    user_id = update.message.from_user.id
    if user_id not in ADMIN_IDS:
        update.message.reply_text("❌ You are not authorized to use this command.")
        return
    if not context.args:
        update.message.reply_text("Usage: `/announce <message>`", parse_mode="Markdown")
        return
    message = " ".join(context.args)
    data = load_data()
    if not data:
        update.message.reply_text("No chats found to announce to.")
        return
    count = 0
    for chat in data.keys():
        try:
            context.bot.send_message(chat_id=int(chat), text=message)
            count += 1
        except Exception as e:
            logger.error(f"Error sending announcement to chat {chat}: {e}")
    update.message.reply_text(f"Announcement sent to {count} chats.", parse_mode="Markdown")

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

def enable_alert(update, context):
    """Handler for /alert command."""
    chat_id = update.message.chat_id
    if not get_addresses_for_chat(chat_id):
        update.message.reply_text("ℹ️ No addresses found! Add one with `/add`.")
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"alert_{chat_id}")
    if current_jobs:
        update.message.reply_text("ℹ️ Alerts are already active!")
        return
    context.job_queue.run_repeating(
        alert_check,
        interval=900,  # 15 minutes
        context={'chat_id': chat_id},
        name=f"alert_{chat_id}"
    )
    update.message.reply_text(
        "✅ *Alerts enabled!*\n\n"
        "I will notify you if there are no transactions in the last 15 minutes.",
        parse_mode="Markdown"
    )

def stop(update, context):
    """Handler for /stop command to stop auto-update and alert jobs."""
    chat_id = update.message.chat_id
    removed_jobs = 0
    for job_name in (f"auto_update_{chat_id}", f"alert_{chat_id}"):
        jobs = context.job_queue.get_jobs_by_name(job_name)
        for job in jobs:
            job.schedule_removal()
            removed_jobs += 1
    if removed_jobs:
        update.message.reply_text("✅ *Auto-update and alerts have been stopped!*", parse_mode="Markdown")
    else:
        update.message.reply_text("ℹ️ No active jobs found.")

# ==================== MAIN FUNCTION ====================
def main():
    """Run the bot."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    # Register handlers
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("add", add))
    dp.add_handler(CommandHandler("remove", remove))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("auto", enable_auto))
    dp.add_handler(CommandHandler("nodestats", nodestats))
    dp.add_handler(CommandHandler("alert", enable_alert))
    dp.add_handler(CommandHandler("stop", stop))
    dp.add_handler(CommandHandler("health", health))
    dp.add_handler(CommandHandler("announce", announce))
    dp.add_handler(CommandHandler("clear", clear))

    updater.start_polling()
    updater.idle()

if __name__