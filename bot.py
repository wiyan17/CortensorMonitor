#!/usr/bin/env python3
# Arbitrum Account Monitor Pro

import logging
import requests
from datetime import datetime, timedelta, timezone
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes, CallbackContext

# ==================== CONFIGURATION ====================
TOKEN = "7572745359:AAFZp9src6sUJHE_L5tPlS7g6-9O846BdGs"
API_KEY = "AJGGWESPKP9GSWKHQDP4UNZP7SM67FSWWR"
UPDATE_INTERVAL = 120  # 2 minutes in seconds
CORTENSOR_API = "https://dashboard-devnet3.cortensor.network"

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
    """Return relative time (in secs/mins/hours/days) from a timestamp (in English)."""
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

def fetch_balance(address: str) -> float:
    try:
        params = {
            "module": "account",
            "action": "balance",
            "address": ADDRESSES[address],
            "tag": "latest",
            "apikey": API_KEY
        }
        response = requests.get(BASE_URL, params=params, timeout=10)
        return int(response.json()['result']) / 10**18
    except Exception as e:
        logger.error(f"Balance error: {str(e)}")
        return 0.0

def fetch_recent_tx(address: str) -> dict:
    try:
        params = {
            "module": "account",
            "action": "txlist",
            "address": ADDRESSES[address],
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

def fetch_node_stats(address: str) -> dict:
    """Fetch node statistics from Cortensor API."""
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error: {str(e)}")
        return {}

def format_node_stats(stats: dict) -> str:
    """Format node statistics data into a table."""
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
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Arbitrum Account Monitor\n\n"
        "Commands:\n"
        "/start - Show this message\n"
        "/ping - Last 2 transactions\n"
        "/auto - Enable auto updates\n"
        "/nodestats <address> - Node statistics\n"
        "/help - Command help"
    )

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Command Help:\n"
        "/ping - Last 2 transactions\n"
        "/auto - Auto updates every 2 mins\n"
        "/nodestats <address> - Node performance stats\n"
        "/help - Show this message"
    )

async def ping(update: Update, context: ContextTypes.DEFAULT_TYPE):
    transactions = []
    for address in ADDRESSES:
        tx = fetch_recent_tx(address)
        if tx:
            method = tx.get('functionName', 'Transfer')[:12]
            time_str = get_age(int(tx['timeStamp']))
            status = "🟢 Online" if tx.get('isError', '1') == '0' else "🔴 Offline"
        else:
            method = 'N/A'
            time_str = 'N/A'
            status = 'N/A'
        transactions.append((address, method, time_str, status))
    
    response = (
        "```\n"
        "Cortensor Monitor BOT\n"
        f"Updated: {format_time(get_wib_time())}\n\n"
        "| Address    | Method       | Time           | Status       |\n"
        "|------------|--------------|----------------|--------------|\n"
        + "\n".join([f"| {a:<10} | {m:<12} | {t:<14} | {s:<12} |" for a, m, t, s in transactions])
        + "\n```"
    )
    await update.message.reply_text(response, parse_mode="Markdown")

async def nodestats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /nodestats <node_address>")
        return
    
    address = context.args[0]
    stats = fetch_node_stats(address)
    response = (
        f"📊 NODE STATISTICS\n"
        f"Address: {address}\n"
        f"Updated: {format_time(get_wib_time())}\n\n"
        f"{format_node_stats(stats)}"
    )
    await update.message.reply_text(response, parse_mode="Markdown")

async def auto_update(context: CallbackContext):
    report = []
    update_time = get_wib_time()
    
    for address in ADDRESSES:
        balance = fetch_balance(address)
        tx = fetch_recent_tx(address)
        method = tx.get('functionName', 'Transfer')[:12] if tx else 'N/A'
        time_str = get_age(int(tx['timeStamp'])) if tx else 'N/A'
        status = "🟢 Online" if (tx and tx.get('isError', '1') == '0') else ("🔴 Offline" if tx else "N/A")
        row = {
            'address': address,
            'balance': f"{balance:.4f} ETH",
            'method': method,
            'time': time_str,
            'status': status
        }
        report.append(row)
    
    header = "🔄 Cortensor Monitor BOT\n"
    body = (
        "```\n"
        "| Address    | Balance     | Method       | Time           | Status       |\n"
        "|------------|-------------|--------------|----------------|--------------|\n"
        + "\n".join([
            f"| {r['address']:<10} | {r['balance']:<11} | {r['method']:<12} | {r['time']:<14} | {r['status']:<12} |"
            for r in report
        ])
        + "\n```"
    )
    footer = f"\nLast update: {format_time(update_time)}"
    
    await context.bot.send_message(
        chat_id=context.job.data,
        text=header + body + footer,
        parse_mode="Markdown"
    )

async def enable_auto(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.effective_chat.id
    context.application.job_queue.run_repeating(
        auto_update,
        interval=UPDATE_INTERVAL,
        first=10,
        context=chat_id
    )
    await update.message.reply_text("✅ Automatic updates activated (2 minute interval)")

# ==================== MAIN ====================
def main():
    application = ApplicationBuilder().token(TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping))
    application.add_handler(CommandHandler("auto", enable_auto))
    application.add_handler(CommandHandler("nodestats", nodestats))

    application.run_polling()

if __name__ == "__main__":
    main()