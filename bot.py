#!/usr/bin/env python3
# Cortensor Node Monitoring Bot

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
# We use WIB (UTC+7) as our timezone
WIB = timezone(timedelta(hours=7))

# ==================== DATA STORAGE FUNCTIONS ====================

def load_data() -> dict:
    """Load persistent data from DATA_FILE and return as a dictionary."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    return {}

def save_data(data: dict):
    """Save data to DATA_FILE."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_addresses_for_chat(chat_id: int) -> list:
    """Return the list of addresses for the given chat."""
    data = load_data()
    return data.get(str(chat_id), [])

def update_addresses_for_chat(chat_id: int, addresses: list):
    """Update the list of addresses for the given chat."""
    data = load_data()
    data[str(chat_id)] = addresses
    save_data(data)

# ==================== UTILITY FUNCTIONS ====================

def shorten_address(address: str) -> str:
    """Return a shortened version of the address (e.g., 0x1234...abcd)."""
    if len(address) > 10:
        return address[:6] + "..." + address[-4:]
    return address

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
    if minutes < 60:
        return f"{minutes} mins ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours} hours ago"
    days = hours // 24
    return f"{days} days ago"

# ==================== API FUNCTIONS ====================

def fetch_balance(address: str) -> float:
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
        logger.error(f"Balance error for {address}: {str(e)}")
        return 0.0

def fetch_transactions(address: str) -> list:
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
        results = response.json().get('result', [])
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.error(f"Transactions error for {address}: {str(e)}")
        return []

def fetch_recent_tx(address: str) -> dict:
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": address,
            "sort": "desc",
            "offset": 1,
            "apikey": API_KEY
        }
        response = requests.get("https://api-sepolia.arbiscan.io/api", params=params, timeout=10)
        results = response.json().get('result', [])
        return results[0] if results else {}
    except Exception as e:
        logger.error(f"Recent TX error for {address}: {str(e)}")
        return {}

def fetch_node_stats(address: str) -> dict:
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error for {address}: {str(e)}")
        return {}

# ==================== COMMAND HANDLERS ====================

def start(update, context: CallbackContext):
    text = (
        "Welcome to the *Cortensor Node Monitoring Bot*.\n\n"
        "Available commands:\n"
        "• `/add <address>` - Add a valid Ethereum address to your list (up to 5 addresses per chat).\n"
        "• `/remove <address>` - Remove an address from your list.\n"
        "• `/ping [address1 address2 ...]` - Retrieve status and ping information for up to 5 addresses.\n"
        "   If no addresses are provided, the bot uses the addresses saved for this chat.\n"
        "• `/auto [address1 address2 ...]` - Enable automatic updates every 2 minutes with the same info as `/ping`.\n"
        "• `/nodestats <address>` - Display detailed node statistics for a given address.\n"
        "• `/help` - Show usage instructions.\n\n"
        "Please ensure that any address entered is a valid Ethereum address (starting with `0x`)."
    )
    update.message.reply_text(text, parse_mode="Markdown")

def help_command(update, context: CallbackContext):
    text = (
        "*Usage Instructions:*\n\n"
        "1. Add an address using:\n"
        "   `/add 0x1234567890abcdef1234567890abcdef12345678`\n\n"
        "2. Remove an address using:\n"
        "   `/remove 0x1234567890abcdef1234567890abcdef12345678`\n\n"
        "3. Retrieve information with:\n"
        "   `/ping` (to use your saved addresses) or\n"
        "   `/ping 0x123... 0xabcd...` (up to 5 addresses, separated by spaces)\n\n"
        "4. Enable auto-updates with:\n"
        "   `/auto` or `/auto <address1> <address2> ...`\n\n"
        "5. Display node statistics with:\n"
        "   `/nodestats 0x1234567890abcdef1234567890abcdef12345678`\n\n"
        "Each chat stores its own list of addresses, ensuring data privacy per user.\n"
        "For security, the API key and token are loaded from a *.env* file."
    )
    update.message.reply_text(text, parse_mode="Markdown")

def add(update, context: CallbackContext):
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Usage: `/add <address>`", parse_mode="Markdown")
        return
    address = context.args[0].strip()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("Invalid address. Please ensure it is a valid Ethereum address (42 characters, starting with 0x).")
        return
    user_addresses = get_addresses_for_chat(chat_id)
    if address in user_addresses:
        update.message.reply_text("This address is already in your list.")
        return
    if len(user_addresses) >= 5:
        update.message.reply_text("You have reached the maximum of 5 addresses. Please remove one before adding another.")
        return
    user_addresses.append(address)
    update_addresses_for_chat(chat_id, user_addresses)
    update.message.reply_text(f"Address `{address}` has been added successfully.", parse_mode="Markdown")

def remove(update, context: CallbackContext):
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Usage: `/remove <address>`", parse_mode="Markdown")
        return
    address = context.args[0].strip()
    user_addresses = get_addresses_for_chat(chat_id)
    if address not in user_addresses:
        update.message.reply_text("The address was not found in your list.")
        return
    user_addresses.remove(address)
    update_addresses_for_chat(chat_id, user_addresses)
    update.message.reply_text(f"Address `{address}` has been removed.", parse_mode="Markdown")

def ping(update, context: CallbackContext):
    chat_id = update.message.chat_id
    # Use addresses provided as arguments if available; otherwise, use saved addresses.
    if context.args:
        addresses = context.args[:5]
    else:
        addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.message.reply_text("No addresses provided or saved. Please add an address using `/add`.")
        return

    current_time = datetime.now(WIB)
    threshold_status = current_time - timedelta(minutes=5)  # Based on the last 5 minutes
    threshold_ping = current_time - timedelta(hours=1)        # Based on the last 1 hour

    messages = []
    for addr in addresses:
        tx_list = fetch_transactions(addr)
        recent_status = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_status]
        if recent_status:
            latest_tx = recent_status[0]
            status = "Online" if latest_tx.get('isError', '1') == '0' else "Offline"
        else:
            status = "N/A"
        balance = fetch_balance(addr)
        recent_ping = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_ping]
        ping_groups = []
        for i in range(0, len(recent_ping), 6):
            group = recent_ping[i:i+6]
            if group:
                if all(tx.get('isError', '1') == '0' for tx in group):
                    ping_groups.append("🟢")
                else:
                    ping_groups.append("🔴")
        ping_groups = ping_groups[:5]  # Maximum 5 groups
        ping_result = " ".join(ping_groups) if ping_groups else "No transactions in the last 1 hour"
        messages.append(
            f"🔹 *Address:* {shorten_address(addr)}\n"
            f"💰 *Balance:* {balance:.4f} ETH\n"
            f"🔵 *Status (last 5 mins):* {status}\n"
            f"📡 *Ping (1h, 6 tx/group):* {ping_result}\n"
        )
    arbiscan_links = "🔗 *Arbiscan Links:*\n" + "\n".join(
        [f"{shorten_address(addr)}: [Link](https://sepolia.arbiscan.io/address/{addr})" for addr in addresses]
    )
    dashboard_links = "📊 *Dashboard Links:*\n" + "\n".join(
        [f"{shorten_address(addr)}: [Link](https://dashboard-devnet3.cortensor.network/nodestats/{addr})" for addr in addresses]
    )
    links_block = arbiscan_links + "\n\n" + dashboard_links
    last_update = f"*Last update:* {format_time(get_wib_time())}"
    final_message = "\n".join(messages) + "\n\n" + links_block + "\n\n" + last_update
    update.message.reply_text(final_message, parse_mode="Markdown")

def nodestats(update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: `/nodestats <address>`", parse_mode="Markdown")
        return
    addr = context.args[0].strip()
    stats = fetch_node_stats(addr)
    response = (
        f"📊 *NODE STATISTICS*\n"
        f"*Address:* {shorten_address(addr)}\n"
        f"*Updated:* {format_time(get_wib_time())}\n\n"
        f"{stats if stats else 'No data available.'}"
    )
    update.message.reply_text(response, parse_mode="Markdown")

def auto_update(context: CallbackContext):
    chat_id = context.job.context
    addresses = context.job.data  # Addresses provided when /auto was invoked
    if not addresses:
        addresses = get_addresses_for_chat(chat_id)
    current_time = datetime.now(WIB)
    threshold_status = current_time - timedelta(minutes=5)
    threshold_ping = current_time - timedelta(hours=1)
    messages = []
    for addr in addresses:
        tx_list = fetch_transactions(addr)
        recent_status = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_status]
        status = "Online" if recent_status and recent_status[0].get('isError', '1') == '0' else "Offline" if recent_status else "N/A"
        balance = fetch_balance(addr)
        recent_ping = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_ping]
        ping_groups = []
        for i in range(0, len(recent_ping), 6):
            group = recent_ping[i:i+6]
            if group:
                if all(tx.get('isError', '1') == '0' for tx in group):
                    ping_groups.append("🟢")
                else:
                    ping_groups.append("🔴")
        ping_groups = ping_groups[:5]
        ping_result = " ".join(ping_groups) if ping_groups else "No transactions in the last 1 hour"
        messages.append(
            f"🔹 *Address:* {shorten_address(addr)}\n"
            f"💰 *Balance:* {balance:.4f} ETH\n"
            f"🔵 *Status (last 5 mins):* {status}\n"
            f"📡 *Ping (1h, 6 tx/group):* {ping_result}\n"
        )
    arbiscan_links = "🔗 *Arbiscan Links:*\n" + "\n".join(
        [f"{shorten_address(addr)}: [Link](https://sepolia.arbiscan.io/address/{addr})" for addr in addresses]
    )
    dashboard_links = "📊 *Dashboard Links:*\n" + "\n".join(
        [f"{shorten_address(addr)}: [Link](https://dashboard-devnet3.cortensor.network/nodestats/{addr})" for addr in addresses]
    )
    links_block = arbiscan_links + "\n\n" + dashboard_links
    last_update = f"*Last update:* {format_time(get_wib_time())}"
    final_message = ("🔄 *Cortensor Node Monitoring Bot Auto Update*\n\n" +
                     "\n".join(messages) + "\n\n" + links_block + "\n\n" + last_update)
    context.bot.send_message(chat_id=chat_id, text=final_message, parse_mode="Markdown")

def enable_auto(update, context: CallbackContext):
    chat_id = update.message.chat_id
    addresses = context.args if context.args else get_addresses_for_chat(chat_id)
    if not addresses:
        update.message.reply_text("No addresses provided or saved. Please add an address using `/add` or supply them as arguments.")
        return
    if len(addresses) > 5:
        update.message.reply_text("A maximum of 5 addresses is allowed.")
        return
    context.job_queue.run_repeating(auto_update, interval=UPDATE_INTERVAL, first=10, context=chat_id, data=addresses)
    update.message.reply_text("✅ Automatic updates activated (every 2 minutes)")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("add", add))
    dp.add_handler(CommandHandler("remove", remove))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("auto", enable_auto))
    dp.add_handler(CommandHandler("nodestats", nodestats))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()