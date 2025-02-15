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

# File untuk menyimpan alamat secara persisten per chat
DATA_FILE = "data.json"

# ==================== INITIALIZATION ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
# Timezone WIB (UTC+7)
WIB = timezone(timedelta(hours=7))

# ==================== DATA STORAGE FUNCTIONS ====================

def load_data() -> dict:
    """Muat data dari file JSON."""
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
        except Exception as e:
            logger.error(f"Error loading data: {e}")
    return {}

def save_data(data: dict):
    """Simpan data ke file JSON."""
    try:
        with open(DATA_FILE, "w") as f:
            json.dump(data, f)
    except Exception as e:
        logger.error(f"Error saving data: {e}")

def get_addresses_for_chat(chat_id: int) -> list:
    """Dapatkan daftar alamat untuk chat tertentu."""
    data = load_data()
    return data.get(str(chat_id), [])

def update_addresses_for_chat(chat_id: int, addresses: list):
    """Update daftar alamat untuk chat tertentu."""
    data = load_data()
    data[str(chat_id)] = addresses
    save_data(data)

# ==================== UTILITY FUNCTIONS ====================

def shorten_address(address: str) -> str:
    """Persingkat alamat Ethereum."""
    return address[:6] + "..." + address[-4:] if len(address) > 10 else address

def get_wib_time() -> datetime:
    return datetime.now(WIB)

def format_time(time: datetime) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S WIB')

def get_age(timestamp: int) -> str:
    diff = datetime.now(WIB) - datetime.fromtimestamp(timestamp, WIB)
    seconds = int(diff.total_seconds())
    if seconds < 60:
        return f"{seconds} detik lalu"
    minutes = seconds // 60
    return f"{minutes} menit lalu" if minutes < 60 else f"{minutes//60} jam lalu"

# ==================== API FUNCTIONS ====================

def fetch_balance(address: str) -> float:
    """Dapatkan balance dari Arbiscan API."""
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
    """Dapatkan riwayat transaksi."""
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
    """Dapatkan statistik node dari Cortensor API."""
    try:
        url = f"{CORTENSOR_API}/nodestats/{address}"
        response = requests.get(url, timeout=15)
        return response.json()
    except Exception as e:
        logger.error(f"Node stats error: {str(e)}")
        return {}

# ==================== COMMAND HANDLERS ====================

def start(update, context):
    """Handler untuk command /start."""
    update.message.reply_text(
        "🤖 *Cortensor Node Monitoring Bot*\n\n"
        "Perintah tersedia:\n"
        "/add <address> - Tambahkan alamat\n"
        "/remove <address> - Hapus alamat\n"
        "/ping - Cek status node\n"
        "/auto - Update otomatis tiap 2 menit\n"
        "/nodestats <address> - Statistik node\n"
        "/help - Panduan penggunaan",
        parse_mode="Markdown"
    )

def help_command(update, context):
    """Handler untuk command /help."""
    update.message.reply_text(
        "📖 *Panduan Penggunaan:*\n\n"
        "1. Tambah alamat dengan /add\n"
        "2. Hapus dengan /remove\n"
        "3. /ping untuk cek status\n"
        "4. /auto untuk update otomatis\n"
        "5. Maksimal 5 alamat per chat",
        parse_mode="Markdown"
    )

def add(update, context):
    """Handler untuk command /add."""
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Contoh: /add 0x123...")
        return
    
    address = context.args[0].lower()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("⛔ Format alamat tidak valid!")
        return
    
    addresses = get_addresses_for_chat(chat_id)
    if address in addresses:
        update.message.reply_text("⚠️ Alamat sudah terdaftar")
        return
    
    if len(addresses) >= 5:
        update.message.reply_text("❌ Maksimal 5 alamat!")
        return
    
    addresses.append(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Alamat {shorten_address(address)} ditambahkan")

def remove(update, context):
    """Handler untuk command /remove."""
    chat_id = update.message.chat_id
    if not context.args:
        update.message.reply_text("Contoh: /remove 0x123...")
        return
    
    address = context.args[0].lower()
    addresses = get_addresses_for_chat(chat_id)
    
    if address not in addresses:
        update.message.reply_text("❌ Alamat tidak ditemukan")
        return
    
    addresses.remove(address)
    update_addresses_for_chat(chat_id, addresses)
    update.message.reply_text(f"✅ Alamat {shorten_address(address)} dihapus")

def ping(update, context):
    """Handler untuk command /ping."""
    chat_id = update.message.chat_id
    addresses = context.args or get_addresses_for_chat(chat_id)
    
    if not addresses:
        update.message.reply_text("ℹ️ Tambahkan alamat dulu dengan /add")
        return
    
    responses = []
    for addr in addresses[:5]:  # Batasi maksimal 5 alamat
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)[:6]  # 6 transaksi terakhir
        status = "🟢 Online" if any(tx['isError'] == '0' for tx in txs) else "🔴 Offline"
        
        responses.append(
            f"🔹 *{shorten_address(addr)}*\n"
            f"💵 Balance: {balance:.4f} ETH\n"
            f"📊 Status: {status}\n"
            f"⏳ Aktivitas: {get_age(int(txs[0]['timeStamp'])) if txs else 'N/A'}"
        )
    
    update.message.reply_text("\n\n".join(responses), parse_mode="Markdown")

def auto_update(context: CallbackContext):
    """Job untuk update otomatis."""
    job = context.job
    data = job.context
    addresses = data['addresses']
    chat_id = data['chat_id']
    
    for addr in addresses[:5]:
        balance = fetch_balance(addr)
        txs = fetch_transactions(addr)[:6]
        status = "🟢" if any(tx['isError'] == '0' for tx in txs) else "🔴"
        
        context.bot.send_message(
            chat_id=chat_id,
            text=f"🔄 Update Otomatis\n\n"
                 f"🔹 {shorten_address(addr)}\n"
                 f"💵 {balance:.4f} ETH\n"
                 f"📊 Status: {status}",
            parse_mode="Markdown"
        )

def enable_auto(update, context):
    """Handler untuk command /auto."""
    chat_id = update.message.chat_id
    addresses = context.args or get_addresses_for_chat(chat_id)
    
    if not addresses:
        update.message.reply_text("ℹ️ Tambahkan alamat dulu dengan /add")
        return
    
    # Schedule job dengan context kombinasi
    context.job_queue.run_repeating(
        auto_update,
        interval=UPDATE_INTERVAL,
        context={'chat_id': chat_id, 'addresses': addresses[:5]},  # Maks 5 alamat
    )
    
    update.message.reply_text("✅ Update otomatis diaktifkan tiap 2 menit")

def nodestats(update, context):
    """Handler untuk command /nodestats."""
    if not context.args:
        update.message.reply_text("Contoh: /nodestats 0x123...")
        return
    
    address = context.args[0]
    stats = fetch_node_stats(address)
    
    if not stats:
        update.message.reply_text("❌ Tidak ada data")
        return
    
    update.message.reply_text(
        f"📈 *Statistik Node*\n"
        f"Alamat: {shorten_address(address)}\n"
        f"Uptime: {stats.get('uptime', 'N/A')}\n"
        f"Transaksi: {stats.get('total_tx', 0)}",
        parse_mode="Markdown"
    )

def main():
    """Jalankan bot."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher
    
    # Daftarkan handler
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