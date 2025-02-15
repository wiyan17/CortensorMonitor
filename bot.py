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

# File to persistently store addresses per chat
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

# ==================== COMMAND HANDLERS ====================

def start(update, context):
    """Handler for /start command."""
    update.message.reply_text(
        "👋 *Welcome to Cortensor Node Monitoring Bot!*\n\n"
        "Here's what I can do:\n"
        "✅ `/add <address>` - Add a wallet address\n"
        "❌ `/remove <address>` - Remove a wallet address\n"
        "📊 `/ping` - Check node status\n"
        "🔄 `/auto` - Enable auto-updates every 2 mins\n"
        "🚫 `/stop` - Stop auto-updates and alerts\n"
        "📈 `/nodestats <address>` - View node stats\n"
        "🚨 `/alert` - Get notified if no transactions in 15 mins\n"
        "❓ `/help` - Show help menu\n\n"
        "Let's get started! Add your first address with `/add`.",
        parse_mode="Markdown"
    )

def help_command(update, context):
    """Handler for /help command."""
    update.message.reply_text(
        "📖 *Help Menu*\n\n"
        "1. Add an address: `/add 0x123...`\n"
        "2. Remove an address: `/remove 0x123...`\n"
        "3. Check status: `/ping`\n"
        "4. Enable auto-updates: `/auto`\n"
        "5. Stop auto-updates/alerts: `/stop`\n"
        "6. Set alerts: `/alert`\n"
        "7. Maximum 5 addresses per chat.\n\n"
        "Need more help? Just ask!",
        parse_mode="Markdown"
    )

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
    for addr in addresses[:5]:  # Limit to 5 addresses
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)[:6]  # Last 6 transactions
        status = "🟢 Online" if any(tx['isError'] == '0' for tx in txs) else "🔴 Offline"
        
        responses.append(
            f"🔹 *{shorten_address(addr)}*\n"
            f"💵 Balance: `{balance:.4f} ETH`\n"
            f"📊 Status: {status}\n"
            f"⏳ Last activity: {get_age(int(txs[0]['timeStamp'])) if txs else 'N/A'}\n"
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

# ==================== AUTO UPDATE & ALERT JOBS ====================

def auto_update(context: CallbackContext):
    """Job for auto-update; always fetches the latest data from storage."""
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:5]  # Limit to 5 addresses
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
        txs = fetch_transactions(addr)[:6]  # Last 6 transactions
        status = "🟢 Online" if any(tx['isError'] == '0' for tx in txs) else "🔴 Offline"
        last_activity = get_age(int(txs[0]['timeStamp'])) if txs else 'N/A'
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

def enable_auto(update, context):
    """Handler for /auto command."""
    chat_id = update.message.chat_id
    if not get_addresses_for_chat(chat_id):
        update.message.reply_text("ℹ️ No addresses found! Add one with `/add`.")
        return

    # Check if an auto-update job is already running for this chat
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
    
    update.message.reply_text(
        "✅ *Auto-updates enabled!*\n\n"
        "I will send updates every 2 minutes with the latest data.",
        parse_mode="Markdown"
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
            time_since_last_tx = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            if time_since_last_tx > timedelta(minutes=15):
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
            # If no transactions found at all, send alert immediately
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

    # Check if an alert job is already running for this chat
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
    
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()