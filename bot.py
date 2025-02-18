#!/usr/bin/env python3
# Cortensor Node Monitoring Bot (PTB v13.5 Compatible) – Button Version

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
ADD_ADDRESS, NODESTATS_ADDRESS, ANNOUNCE = range(1, 4)

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
        [InlineKeyboardButton("Node Stats", callback_data="nodestats_prompt")],
        [InlineKeyboardButton("Auto Update", callback_data="auto"),
         InlineKeyboardButton("Enable Alerts", callback_data="alert")],
        [InlineKeyboardButton("Stop", callback_data="stop"),
         InlineKeyboardButton("Help", callback_data="help")]
    ]
    if is_admin:
        keyboard.append([InlineKeyboardButton("Announce", callback_data="announce"),
                         InlineKeyboardButton("Clear", callback_data="clear")])
    return InlineKeyboardMarkup(keyboard)

def show_main_menu(update, context):
    is_admin = update.effective_user.id in ADMIN_IDS
    keyboard = get_main_menu_keyboard(is_admin)
    welcome_text = (
        "👋 Selamat datang di *Cortensor Node Monitoring Bot!* \n\n"
        "Saya di sini untuk membantu Anda memantau status node dengan mudah dan menyenangkan. "
        "Pilih opsi di bawah untuk memulai, dan jangan ragu untuk mengeksplorasi semua fitur yang tersedia!\n\n"
        "💡 *Tip*: Gunakan fitur auto-update untuk mendapatkan pembaruan otomatis setiap 5 menit.\n\n"
        "Semoga Anda menikmati pengalaman menggunakan bot ini! 🚀"
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

# ==================== COMMAND HANDLERS (Conversation) ====================
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

def nodestats_prompt(update, context):
    query = update.callback_query
    query.answer()
    query.edit_message_text(text="Please send me the wallet address to view node stats:", parse_mode="Markdown")
    return NODESTATS_ADDRESS

def nodestats_receive(update, context):
    address = update.message.text.strip().lower()
    stats = fetch_node_stats(address)
    if not stats:
        update.message.reply_text("❌ No data found for this address!")
    else:
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
    show_main_menu(update, context)
    return ConversationHandler.END

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

# ==================== REMOVE ADDRESS (Inline) ====================
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
        "1. *Add Address*: Gunakan tombol **Add Address** untuk menambahkan alamat dompet.\n"
        "2. *Remove Address*: Gunakan tombol **Remove Address** untuk menghapus alamat yang telah ditambahkan.\n"
        "3. *Check Status*: Gunakan tombol **Check Status** untuk mengecek status node, saldo, dan aktivitas terbaru.\n"
        "4. *Node Health*: Gunakan tombol **Node Health** untuk memeriksa kesehatan node (berdasarkan transaksi 1 jam terakhir).\n"
        "5. *Node Stats*: Gunakan tombol **Node Stats** untuk melihat statistik node secara mendetail.\n"
        "6. *Auto Update*: Gunakan tombol **Auto Update** untuk mengaktifkan pembaruan otomatis setiap 5 menit.\n"
        "7. *Enable Alerts*: Gunakan tombol **Enable Alerts** untuk menerima notifikasi jika tidak ada transaksi selama 15 menit.\n"
        "8. *Stop*: Gunakan tombol **Stop** untuk menghentikan auto-update dan notifikasi.\n"
        "9. *Announce* (Admin only): Gunakan tombol **Announce** untuk mengirim pengumuman ke semua chat.\n"
        "10. *Clear* (Admin only): Gunakan tombol **Clear** untuk menghapus pesan terakhir di chat.\n\n"
        "💡 *Fun Fact*: Tahukah Anda, setiap transaksi di jaringan blockchain bagaikan denyut nadi digital yang menjaga sistem tetap hidup? "
        "Pantau node Anda dan jadilah pahlawan dalam dunia digital!\n\n"
        "🚀 *Happy Monitoring!*"
    )
    query.edit_message_text(text, parse_mode="Markdown")
    show_main_menu(update, context)

def clear_button(update, context):
    query = update.callback_query
    query.answer()
    chat_id = query.message.chat_id
    user_id = update.effective_user.id
    try:
        admins = [admin.user.id for admin in context.bot.get_chat_administrators(chat_id)]
    except Exception as e:
        query.edit_message_text("⚠️ Unable to check admin status.")
        show_main_menu(update, context)
        return
    if user_id not in admins:
        query.edit_message_text("❌ You must be an admin to use this command!")
        show_main_menu(update, context)
        return
    last_msg_id = query.message.message_id
    count = 0
    for msg_id in range(last_msg_id - 50, last_msg_id + 1):
        try:
            context.bot.delete_message(chat_id, msg_id)
            count += 1
        except Exception as e:
            continue
    query.edit_message_text(f"✅ Cleared {count} messages.", parse_mode="Markdown")
    show_main_menu(update, context)

# ==================== ADDITIONAL COMMAND HANDLERS ====================
def help_command(update, context):
    """Handler for /help command with full guide and fun facts."""
    text = (
        "📖 *Panduan Lengkap Penggunaan Bot Monitoring Node Cortensor!*\n\n"
        "Berikut adalah perintah dan fitur yang dapat Anda gunakan:\n\n"
        "1. *Add Address*: Tambahkan alamat dompet (gunakan tombol *Add Address*).\n"
        "   - Pastikan alamat yang Anda tambahkan adalah alamat yang valid (42 karakter dimulai dengan '0x').\n\n"
        "2. *Remove Address*: Hapus alamat yang telah ditambahkan (gunakan tombol *Remove Address*).\n\n"
        "3. *Check Status*: Cek status node, termasuk saldo dan aktivitas terbaru (gunakan tombol *Check Status*).\n\n"
        "4. *Node Health*: Periksa kesehatan node berdasarkan transaksi terakhir dalam 1 jam (gunakan tombol *Node Health*).\n\n"
        "5. *Node Stats*: Lihat statistik node secara mendetail (pilih tombol *Node Stats* dan masukkan alamat).\n\n"
        "6. *Auto Update*: Aktifkan pembaruan otomatis setiap 5 menit untuk memantau node Anda (gunakan tombol *Auto Update*).\n\n"
        "7. *Enable Alerts*: Aktifkan notifikasi jika tidak ada transaksi dalam 15 menit (gunakan tombol *Enable Alerts*).\n\n"
        "8. *Stop*: Hentikan pembaruan otomatis dan notifikasi (gunakan tombol *Stop*).\n\n"
        "9. *Announce* (Admin only): Kirim pengumuman ke semua chat (gunakan tombol *Announce*).\n\n"
        "10. *Clear* (Admin only): Hapus pesan terakhir dalam chat (gunakan tombol *Clear*).\n\n"
        "💡 *Fun Fact*: Tahukah Anda, setiap transaksi di jaringan blockchain bagaikan denyut nadi digital yang menjaga sistem tetap hidup? "
        "Pantau node Anda dan jadilah pahlawan dalam dunia digital!\n\n"
        "🚀 *Happy Monitoring!*"
    )
    update.message.reply_text(text, parse_mode="Markdown")

# ==================== MAIN FUNCTION ====================
def main():
    """Run the bot."""
    updater = Updater(TOKEN)
    dp = updater.dispatcher

    # /start command shows the main menu with a warm welcome.
    dp.add_handler(CommandHandler("start", show_main_menu))
    dp.add_handler(CommandHandler("help", help_command))

    # Conversation handlers for Add Address, Node Stats, and Announce
    add_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(add_address_entry, pattern="^add_address$")],
        states={
            ADD_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, add_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    dp.add_handler(add_conv_handler)

    nodestats_conv_handler = ConversationHandler(
        entry_points=[CallbackQueryHandler(nodestats_prompt, pattern="^nodestats_prompt$")],
        states={
            NODESTATS_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, nodestats_receive)]
        },
        fallbacks=[CommandHandler("cancel", cancel)]
    )
    dp.add_handler(nodestats_conv_handler)

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
    dp.add_handler(CallbackQueryHandler(clear_button, pattern="^clear$"))

    updater.start_polling()
    updater.idle()

if __name__ == "__main__":
    main()