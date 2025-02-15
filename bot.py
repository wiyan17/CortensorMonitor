#!/usr/bin/env python3
# Arbitrum Account Monitor Pro

import logging
import requests
from datetime import datetime, timedelta, timezone
from telegram.ext import Updater, CommandHandler, CallbackContext

# ==================== CONFIGURATION ====================
TOKEN = "7572745359:AAFZp9src6sUJHE_L5tPlS7g6-9O846BdGs"
API_KEY = "AJGGWESPKP9GSWKHQDP4UNZP7SM67FSWWR"
UPDATE_INTERVAL = 120  # 2 minutes in seconds
CORTENSOR_API = "https://dashboard-devnet3.cortensor.network"

# Dictionary: key = abbreviated address, value = full address
ADDRESSES = {
    "0x93...F2E": "0x9344ed8328CF501F7A8d87231a2cB4EBd1207F2E",
    "0xb0...85b": "0xb0aBf49fDD7953A9394428aCE5dEA6fA93b8e85b",
    "0x77...01d": "0x777efBCab46DbF81F2144E093456f1c99215601d",
    "0x47...128": "0x47B2F49719f04c1B408c3c5B93ccdaE7E3477128",
    "0x28...D0a": "0x28Aa8a804a57e08cb46F983a2C988eb24bb58D0a",
}

BASE_URL = "https://api-sepolia.arbiscan.io/api"
WIB = timezone(timedelta(hours=7))

# ==================== INITIALIZATION ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# ==================== HELPER FUNCTIONS ====================
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

def fetch_balance(addr_key: str) -> float:
    try:
        params = {
            "module": "account",
            "action": "balance",
            "address": ADDRESSES[addr_key],
            "tag": "latest",
            "apikey": API_KEY
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        return int(response.json()['result']) / 10**18
    except Exception as e:
        logger.error(f"Balance error: {str(e)}")
        return 0.0

def fetch_recent_tx(addr_key: str) -> dict:
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": ADDRESSES[addr_key],
            "sort": "desc",
            "offset": 1,
            "apikey": API_KEY
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        results = response.json().get('result', [])
        return results[0] if results else {}
    except Exception as e:
        logger.error(f"TX error: {str(e)}")
        return {}

def fetch_transactions(addr_key: str) -> list:
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": ADDRESSES[addr_key],
            "sort": "desc",
            "page": 1,
            "offset": 100,
            "apikey": API_KEY
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        results = response.json().get('result', [])
        return results if isinstance(results, list) else []
    except Exception as e:
        logger.error(f"Transactions error: {str(e)}")
        return []

def fetch_node_stats(addr: str) -> dict:
    try:
        url = f"{CORTENSOR_API}/nodestats/{addr}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error: {str(e)}")
        return {}

def format_node_stats(stats: dict) -> str:
    if not stats:
        return "❌ Failed to fetch node statistics"
    
    metrics = [
        ("Request Metrics", stats.get("RequestMetrics")),
        ("Create Metrics", stats.get("CreateMetrics")),
        ("Prepare Metrics", stats.get("PrepareMetrics")),
        ("Start Metrics", stats.get("StartMetrics")),
        ("Precommit Metrics", stats.get("PrecommitMetrics")),
        ("Commit Metrics", stats.get("CommitMetrics")),
        ("End Metrics", stats.get("EndMetrics")),
        ("Correctness Metrics", stats.get("CorrectnessMetrics")),
        ("Ping Metrics", stats.get("PingMetrics")),
        ("Global Ping Metrics", stats.get("GlobalPingMetrics")),
    ]
    table = "```\n"
    table += "| METRIC TYPE         | POINT | COUNTER | SUCCESS RATE |\n"
    table += "|---------------------|-------|---------|--------------|\n"
    for name, data in metrics:
        if not data:
            continue
        table += f"| {name:<19} | {data.get('Point','N/A'):<5} | {data.get('Counter','N/A'):<7} | {data.get('SuccessRate','N/A'):<12} |\n"
    table += "```"
    return table

# ==================== COMMAND HANDLERS ====================
def start(update, context: CallbackContext):
    update.message.reply_text(
        "Arbitrum Account Monitor\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/ping - Check status and ping info (table format)\n"
        "/auto - Enable auto updates\n"
        "/nodestats <address> - Node statistics\n"
        "/help - Command help"
    )

def help_command(update, context: CallbackContext):
    update.message.reply_text(
        "Command Help:\n"
        "/ping - For each address, displays a table with:\n"
        "  • Address (with hyperlink to Arbiscan)\n"
        "  • Status (Online/Offline from last 5 mins)\n"
        "  • Balance\n"
        "  • Ping (from last 1 hour, grouped per 5 transactions: 🟢 if all succeed, 🔴 if any fails)\n"
        "/auto - Auto updates every 2 mins (includes Arbiscan links)\n"
        "/nodestats <address> - Node performance stats\n"
        "/help - Show this message"
    )

def ping(update, context: CallbackContext):
    current_time = datetime.now(WIB)
    threshold_status = current_time - timedelta(minutes=5)  # untuk Status
    threshold_ping = current_time - timedelta(hours=1)        # untuk Ping
    table_lines = []
    header = "| Address | Status (last 5 mins) | Balance (ETH) | Ping (last 1 hour, per 5 tx) |"
    separator = "|---------|----------------------|---------------|----------------------------|"
    table_lines.append(header)
    table_lines.append(separator)
    for addr_key, full_addr in ADDRESSES.items():
        tx_list = fetch_transactions(addr_key)
        # Tentukan Status dari transaksi dalam 5 menit terakhir
        recent_status_txs = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_status]
        if recent_status_txs:
            most_recent_tx = recent_status_txs[0]
            status = "Online" if most_recent_tx.get('isError', '1') == '0' else "Offline"
        else:
            status = "N/A"
        # Tentukan Ping dari transaksi dalam 1 jam terakhir, dikelompokkan per 5 transaksi
        recent_ping_txs = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_ping]
        ping_symbols = []
        for i in range(0, len(recent_ping_txs), 5):
            chunk = recent_ping_txs[i:i+5]
            if not chunk:
                continue
            if all(tx.get('isError', '1') == '0' for tx in chunk):
                ping_symbols.append("🟢")
            else:
                ping_symbols.append("🔴")
        ping_str = " ".join(ping_symbols) if ping_symbols else "No tx in last 1 hour"
        balance = fetch_balance(addr_key)
        address_link = f"[{addr_key}](https://sepolia.arbiscan.io/address/{full_addr})"
        row = f"| {address_link} | {status} | {balance:.4f} | {ping_str} |"
        table_lines.append(row)
    final_message = "```\n" + "\n".join(table_lines) + "\n```"
    update.message.reply_text(final_message, parse_mode="Markdown")

def nodestats(update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: /nodestats <node_address>")
        return
    addr = context.args[0]
    stats = fetch_node_stats(addr)
    response = (
        f"📊 NODE STATISTICS\n"
        f"Address: {addr}\n"
        f"Updated: {format_time(get_wib_time())}\n\n"
        f"{format_node_stats(stats)}"
    )
    update.message.reply_text(response, parse_mode="Markdown")

def auto_update(context: CallbackContext):
    chat_id = context.job.context
    table_lines = []
    header = "| Address | Balance (ETH) | Method       | Time           | Status |"
    separator = "|---------|---------------|--------------|----------------|--------|"
    table_lines.append(header)
    table_lines.append(separator)
    for addr_key, full_addr in ADDRESSES.items():
        balance = fetch_balance(addr_key)
        tx = fetch_recent_tx(addr_key)
        method = tx.get('functionName', 'Transfer')[:12] if tx else 'N/A'
        time_str = get_age(int(tx['timeStamp'])) if tx else 'N/A'
        status = "🟢 Online" if (tx and tx.get('isError', '1') == '0') else ("🔴 Offline" if tx else "N/A")
        address_link = f"[{addr_key}](https://sepolia.arbiscan.io/address/{full_addr})"
        row = f"| {address_link} | {balance:.4f} | {method:<12} | {time_str:<14} | {status} |"
        table_lines.append(row)
    final_message = "```\n" + "\n".join(table_lines) + "\n```"
    # Daftar hyperlink untuk pengecekan manual
    links = "\n".join([f"{addr_key}: [Arbiscan](https://sepolia.arbiscan.io/address/{full_addr})" for addr_key, full_addr in ADDRESSES.items()])
    message = "🔄 Cortensor Monitor BOT\n" + final_message + "\n\n" + links
    context.bot.send_message(chat_id=chat_id, text=message, parse_mode="Markdown")

def enable_auto(update, context: CallbackContext):
    chat_id = update.message.chat_id
    context.job_queue.run_repeating(auto_update, interval=UPDATE_INTERVAL, first=10, context=chat_id)
    update.message.reply_text("✅ Automatic updates activated (2 minute interval)")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("auto", enable_auto))
    dp.add_handler(CommandHandler("nodestats", nodestats))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()