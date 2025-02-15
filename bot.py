#!/usr/bin/env python3
# Arbitrum Account Monitor Pro

import logging
import requests
import json
import os
from datetime import datetime, timedelta, timezone
from telegram.ext import Updater, CommandHandler, CallbackContext
from dotenv import load_dotenv

# Load environment variables dari file .env
load_dotenv()

# ==================== CONFIGURATION ====================
TOKEN = os.getenv("TOKEN")
API_KEY = os.getenv("API_KEY")
UPDATE_INTERVAL = 120  # 2 minutes in seconds
CORTENSOR_API = "https://dashboard-devnet3.cortensor.network"

# File untuk menyimpan alamat yang ditambahkan oleh user
ADDRESSES_FILE = "addresses.json"

# ==================== INITIALIZATION ====================
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))

# ==================== UTILITY FUNCTIONS ====================

def load_addresses() -> list:
    """Membaca alamat dari file penyimpanan."""
    if os.path.exists(ADDRESSES_FILE):
        try:
            with open(ADDRESSES_FILE, "r") as f:
                addresses = json.load(f)
                if isinstance(addresses, list):
                    return addresses
        except Exception as e:
            logger.error(f"Error loading addresses: {e}")
    return []

def save_addresses(addresses: list):
    """Menyimpan alamat ke file penyimpanan."""
    try:
        with open(ADDRESSES_FILE, "w") as f:
            json.dump(addresses, f)
    except Exception as e:
        logger.error(f"Error saving addresses: {e}")

def shorten_address(address: str) -> str:
    """Mengembalikan alamat dalam format singkat."""
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

# ==================== HELPER FOR COMMANDS ====================

def get_addresses_from_args(args: list) -> list:
    """
    Jika args disediakan, gunakan args (maks. 5 alamat);
    jika tidak, gunakan alamat yang tersimpan.
    """
    if args:
        if len(args) > 5:
            return None
        return args
    else:
        stored = load_addresses()
        if not stored:
            return None
        return stored

# ==================== COMMAND HANDLERS ====================

def start(update, context: CallbackContext):
    text = (
        "Selamat datang di *Arbitrum Account Monitor Bot*\n\n"
        "Gunakan perintah berikut:\n"
        "• `/add <address>` - Tambah alamat (maks. 5 alamat akan disimpan secara permanen)\n"
        "• `/ping [address1 address2 ...]` - Cek status & ping untuk maksimal 5 alamat.\n"
        "    - Jika tidak diberikan, maka akan menggunakan alamat yang tersimpan.\n"
        "• `/auto [address1 address2 ...]` - Auto update setiap 2 menit dengan info yang sama seperti /ping.\n"
        "• `/nodestats <address>` - Tampilkan statistik node untuk alamat tertentu.\n"
        "• `/help` - Tampilkan pesan bantuan ini.\n\n"
        "Pastikan alamat yang dimasukkan adalah alamat Ethereum yang valid (dimulai dengan `0x`)."
    )
    update.message.reply_text(text, parse_mode="Markdown")

def help_command(update, context: CallbackContext):
    text = (
        "*Cara Penggunaan:*\n\n"
        "1. Tambah alamat ke daftar dengan:\n"
        "   `/add 0x1234567890abcdef1234567890abcdef12345678`\n\n"
        "2. Cek info alamat dengan:\n"
        "   `/ping` (menggunakan daftar alamat yang tersimpan) atau\n"
        "   `/ping 0x123... abcd...` (maks. 5 alamat, dipisahkan spasi)\n\n"
        "3. Aktifkan auto update dengan:\n"
        "   `/auto` atau `/auto <address1> <address2> ...`\n\n"
        "4. Dapatkan statistik node dengan:\n"
        "   `/nodestats 0x1234567890abcdef1234567890abcdef12345678`\n\n"
        "5. Untuk keamanan, API key dan token disimpan di file *.env*.\n"
    )
    update.message.reply_text(text, parse_mode="Markdown")

def add(update, context: CallbackContext):
    if not context.args:
        update.message.reply_text("Usage: `/add <address>`", parse_mode="Markdown")
        return
    address = context.args[0].strip()
    if not address.startswith("0x") or len(address) != 42:
        update.message.reply_text("Alamat tidak valid. Pastikan alamat Ethereum (42 karakter, dimulai dengan 0x).")
        return
    stored = load_addresses()
    if address in stored:
        update.message.reply_text("Alamat sudah ada dalam daftar.")
        return
    if len(stored) >= 5:
        update.message.reply_text("Maksimum 5 alamat sudah tersimpan. Hapus salah satunya jika ingin menambah alamat baru.")
        return
    stored.append(address)
    save_addresses(stored)
    update.message.reply_text(f"Alamat `{address}` berhasil ditambahkan.", parse_mode="Markdown")

def ping(update, context: CallbackContext):
    addresses = get_addresses_from_args(context.args)
    if addresses is None:
        update.message.reply_text("Tidak ada alamat yang diberikan atau tersimpan. Gunakan `/add` untuk menambah alamat atau berikan argumen.")
        return
    if len(addresses) > 5:
        update.message.reply_text("Maksimal 5 alamat yang diperbolehkan.")
        return

    current_time = datetime.now(WIB)
    threshold_status = current_time - timedelta(minutes=5)  # Status berdasarkan 5 menit terakhir
    threshold_ping = current_time - timedelta(hours=1)        # Ping berdasarkan 1 jam terakhir

    messages = []
    for addr in addresses:
        tx_list = fetch_transactions(addr)
        # Tentukan Status
        recent_status = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_status]
        status = "Online" if recent_status and recent_status[0].get('isError', '1') == '0' else "Offline" if recent_status else "N/A"
        # Balance
        balance = fetch_balance(addr)
        # Tentukan Ping: dari transaksi dalam 1 jam, kelompokkan per 6 tx, maksimal 5 grup
        recent_ping = [tx for tx in tx_list if datetime.fromtimestamp(int(tx['timeStamp']), WIB) >= threshold_ping]
        ping_groups = []
        for i in range(0, len(recent_ping), 6):
            group = recent_ping[i:i+6]
            if not group:
                continue
            if all(tx.get('isError', '1') == '0' for tx in group):
                ping_groups.append("🟢")
            else:
                ping_groups.append("🔴")
        ping_groups = ping_groups[:5]
        ping_result = " ".join(ping_groups) if ping_groups else "No tx in last 1h"
        messages.append(
            f"🔹 *Address:* {shorten_address(addr)}\n"
            f"💰 *Balance:* {balance:.4f} ETH\n"
            f"🟢 *Status (5 mins):* {status}\n"
            f"📡 *Ping (1h, 6 tx/group):* {ping_result}\n"
        )
    # Buat blok hyperlink (2 link) di atas last update
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
    # Gunakan alamat dari argumen job (disimpan sebagai list JSON string)
    addresses = context.job.data  # job.data di sini di-set sebagai list alamat (jika ada) atau jika tidak, maka load dari file
    if not addresses:
        addresses = load_addresses()
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
            if not group:
                continue
            if all(tx.get('isError', '1') == '0' for tx in group):
                ping_groups.append("🟢")
            else:
                ping_groups.append("🔴")
        ping_groups = ping_groups[:5]
        ping_result = " ".join(ping_groups) if ping_groups else "No tx in last 1h"
        messages.append(
            f"🔹 *Address:* {shorten_address(addr)}\n"
            f"💰 *Balance:* {balance:.4f} ETH\n"
            f"🟢 *Status (5 mins):* {status}\n"
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
    final_message = "🔄 *Cortensor Monitor BOT Auto Update*\n\n" + "\n".join(messages) + "\n\n" + links_block + "\n\n" + last_update
    context.bot.send_message(chat_id=chat_id, text=final_message, parse_mode="Markdown")

def enable_auto(update, context: CallbackContext):
    # Ambil alamat dari argumen, jika tidak ada maka load dari file
    addresses = context.args if context.args else load_addresses()
    if not addresses:
        update.message.reply_text("Tidak ada alamat yang diberikan atau tersimpan. Gunakan `/add` untuk menambah alamat atau berikan argumen.")
        return
    if len(addresses) > 5:
        update.message.reply_text("Maksimal 5 alamat yang diperbolehkan.")
        return
    # Simpan alamat yang akan digunakan pada job (dalam job.data)
    context.job_queue.run_repeating(auto_update, interval=UPDATE_INTERVAL, first=10, context=update.message.chat_id, data=addresses)
    update.message.reply_text("✅ Automatic updates activated (every 2 minutes)")

def main():
    updater = Updater(TOKEN, use_context=True)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", start))
    dp.add_handler(CommandHandler("help", help_command))
    dp.add_handler(CommandHandler("add", add))
    dp.add_handler(CommandHandler("ping", ping))
    dp.add_handler(CommandHandler("auto", enable_auto))
    dp.add_handler(CommandHandler("nodestats", nodestats))
    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()