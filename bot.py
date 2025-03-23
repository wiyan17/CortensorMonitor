#!/usr/bin/env python3
"""
Cortensor Node Monitoring Bot – Telegram Reply Keyboard Version

Fitur:
• Add Address (dengan label opsional, format: <wallet_address>,<label>)
• Remove Address
• Check Status
• Auto Update
• Enable Alerts
• Set Delay (custom delay antar API call per chat)
• Stop
• Help
• Announce (admin only)

Maksimum node per chat: 15
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
UPDATE_INTERVAL = 300  # interval auto update: 5 menit
CORTENSOR_API = os.getenv("CORTENSOR_API", "https://dashboard-devnet3.cortensor.network")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "").split(",") if x.strip()]
DATA_FILE = "data.json"
BASE_DELAY = 2.0  # delay dasar antar API call

# -------------------- INITIALIZATION --------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)
WIB = timezone(timedelta(hours=7))  # WIB (UTC+7)

# -------------------- CONVERSATION STATES --------------------
ADD_ADDRESS, REMOVE_ADDRESS, ANNOUNCE, SET_DELAY = range(1, 5)

# -------------------- DATA STORAGE FUNCTIONS --------------------
def load_data() -> dict:
    if os.path.exists(DATA_FILE):
        try:
            with open(DATA_FILE, "r") as f:
                return json.load(f)
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
    return data.get(str(chat_id), {"addresses": [], "delay": None})

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

def get_custom_delay(chat_id: int) -> float:
    return get_chat_data(chat_id).get("delay", None)

def update_custom_delay(chat_id: int, delay: float):
    chat_data = get_chat_data(chat_id)
    chat_data["delay"] = delay
    update_chat_data(chat_id, chat_data)

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

def get_chat_delay(chat_id: int, num_addresses: int) -> float:
    """Jika delay custom diset untuk chat, gunakan delay tersebut, jika tidak, hitung delay dinamis."""
    custom = get_custom_delay(chat_id)
    if custom is not None:
        return custom
    # Hitung delay dinamis: 2 API calls per address, tidak melebihi 0.5 call per detik.
    total_calls = 2 * num_addresses
    if total_calls <= 0.5:
        return BASE_DELAY
    required_total_time = total_calls / 0.5  # total waktu dalam detik
    intervals = total_calls - 1
    dynamic_delay = required_total_time / intervals
    return max(dynamic_delay, BASE_DELAY)

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
    delay = get_chat_delay(chat_id, len(addresses))
    output_lines = []
    for item in addresses:
        addr = item.get("address")
        label = item.get("label", "")
        addr_display = f"🔑 {shorten_address(addr)}" + (f" ({label})" if label else "")
        balance = safe_fetch_balance(addr, delay)
        txs = safe_fetch_transactions(addr, delay)
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
            f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})"
        )
    final_output = "*Auto Update*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    context.bot.send_message(chat_id=chat_id, text=final_output, parse_mode="Markdown")

def alert_check(context: CallbackContext):
    job = context.job
    chat_id = job.context['chat_id']
    addresses = get_addresses_for_chat(chat_id)[:15]
    delay = get_chat_delay(chat_id, len(addresses))
    for item in addresses:
        addr = item.get("address")
        label = item.get("label", "")
        txs = safe_fetch_transactions(addr, delay)
        if txs:
            last_tx_time = int(txs[0]['timeStamp'])
            time_diff = datetime.now(WIB) - datetime.fromtimestamp(last_tx_time, WIB)
            latest_25 = txs[:25]
            stall_condition = len(latest_25) >= 25 and all(tx.get('input','').lower().startswith("0x5c36b186") for tx in latest_25)
            if time_diff > timedelta(minutes=15) or stall_condition:
                msg_lines = [f"🚨 *Alert for {shorten_address(addr)}" + (f" ({label})" if label else "") + "*:"]
                if time_diff > timedelta(minutes=15):
                    msg_lines.append("⏱️ No transactions in the last 15 minutes.")
                if stall_condition:
                    msg_lines.append("⚠️ Node stall detected (only PING transactions in the last 25).")
                msg_lines.append(f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})")
                context.bot.send_message(chat_id=chat_id, text="\n".join(msg_lines), parse_mode="Markdown")
        else:
            context.bot.send_message(
                chat_id=chat_id,
                text=f"🚨 *Alert for {shorten_address(addr)}" + (f" ({label})" if label else "") + "*:\n- No transactions found!\n[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})",
                parse_mode="Markdown"
            )

# -------------------- CONVERSATION HANDLER FUNCTIONS --------------------
def add_address_start(update, context):
    update.effective_message.reply_text(
        "Silakan kirimkan alamat wallet (dengan format `<wallet_address>,<label>` jika ingin menambahkan label). "
        "Contoh: 0xABC123...7890,My Node\n(Kirim /cancel untuk membatalkan)",
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
        update.effective_message.reply_text("❌ Alamat tidak valid! Pastikan dimulai dengan '0x' dan terdiri dari 42 karakter.\nCoba lagi atau ketik /cancel untuk membatalkan.")
        return ADD_ADDRESS
    addresses = get_addresses_for_chat(chat_id)
    if any(item.get("address") == wallet for item in addresses):
        update.effective_message.reply_text("⚠️ Alamat sudah ada! Kembali ke menu utama.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    if len(addresses) >= 15:
        update.effective_message.reply_text("❌ Maksimum 15 node per chat sudah tercapai! Kembali ke menu utama.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses.append({"address": wallet, "label": label})
    update_addresses_for_chat(chat_id, addresses)
    update.effective_message.reply_text(f"✅ Ditambahkan: {shorten_address(wallet)}" + (f" ({label})" if label else ""), reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def remove_address_start(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.effective_message.reply_text("Tidak ada alamat untuk dihapus.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    # Tampilkan setiap alamat dengan label (jika ada)
    keyboard = [[f"{item.get('address')}" + (f" ({item.get('label')})" if item.get("label") else "")] for item in addresses]
    keyboard.append(["Cancel"])
    update.effective_message.reply_text("Pilih alamat yang ingin dihapus:", reply_markup=ReplyKeyboardMarkup(keyboard, resize_keyboard=True, one_time_keyboard=True))
    return REMOVE_ADDRESS

def remove_address_receive(update, context):
    chat_id = update.effective_chat.id
    choice = update.effective_message.text.strip()
    if choice.lower() == "cancel":
        update.effective_message.reply_text("Operasi dibatalkan.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    addresses = get_addresses_for_chat(chat_id)
    new_addresses = []
    found = False
    for item in addresses:
        display = f"{item.get('address')}" + (f" ({item.get('label')})" if item.get("label") else "")
        if display == choice:
            found = True
            continue
        new_addresses.append(item)
    if not found:
        update.effective_message.reply_text("❌ Alamat tidak ditemukan.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    update_addresses_for_chat(chat_id, new_addresses)
    update.effective_message.reply_text(f"✅ Alamat telah dihapus.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

def set_delay_start(update, context):
    update.effective_message.reply_text(
        "Silakan masukkan nilai delay (dalam detik) yang diinginkan antar API call.\nContoh: 3.5\n(Kirim /cancel untuk membatalkan)",
        reply_markup=ReplyKeyboardRemove()
    )
    return SET_DELAY

def set_delay_receive(update, context):
    chat_id = update.effective_chat.id
    try:
        delay_val = float(update.effective_message.text.strip())
        if delay_val < BASE_DELAY:
            update.effective_message.reply_text(f"Delay tidak boleh kurang dari {BASE_DELAY} detik. Coba lagi atau ketik /cancel.")
            return SET_DELAY
        update_custom_delay(chat_id, delay_val)
        update.effective_message.reply_text(f"✅ Delay berhasil disetel ke {delay_val} detik.", reply_markup=main_menu_keyboard(update.effective_user.id))
    except ValueError:
        update.effective_message.reply_text("❌ Input tidak valid. Masukkan angka delay dalam format desimal (contoh: 3.5).")
        return SET_DELAY
    return ConversationHandler.END

def announce_start(update, context):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        update.effective_message.reply_text("❌ Anda tidak memiliki izin untuk menggunakan perintah ini.", reply_markup=main_menu_keyboard(user_id))
        return ConversationHandler.END
    update.effective_message.reply_text("Silakan kirimkan pesan pengumuman:", reply_markup=ReplyKeyboardRemove())
    return ANNOUNCE

def announce_receive(update, context):
    message = update.effective_message.text
    data = load_data()
    if not data:
        update.effective_message.reply_text("Tidak ditemukan chat untuk dikirimkan pengumuman.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return ConversationHandler.END
    count = 0
    for chat in data.keys():
        try:
            context.bot.send_message(chat_id=int(chat), text=message)
            count += 1
        except Exception as e:
            logger.error(f"Error sending announcement to chat {chat}: {e}")
    update.effective_message.reply_text(f"📣 Pengumuman dikirim ke {count} chat.", reply_markup=main_menu_keyboard(update.effective_user.id))
    return ConversationHandler.END

# -------------------- COMMAND FUNCTIONS --------------------
def menu_check_status(update, context):
    chat_id = update.effective_chat.id
    addresses = get_addresses_for_chat(chat_id)
    if not addresses:
        update.effective_message.reply_text("Tidak ada alamat yang terdaftar! Silakan tambahkan menggunakan 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    delay = get_chat_delay(chat_id, len(addresses))
    output_lines = []
    for item in addresses[:15]:
        addr = item.get("address")
        label = item.get("label", "")
        addr_display = f"🔑 {shorten_address(addr)}" + (f" ({label})" if label else "")
        balance = safe_fetch_balance(addr, delay)
        txs = safe_fetch_transactions(addr, delay)
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
            f"[🔗 Arbiscan](https://sepolia.arbiscan.io/address/{addr}) | [📈 Dashboard]({CORTENSOR_API}/stats/node/{addr})"
        )
    final_output = "*Check Status*\n\n" + "\n\n".join(output_lines) + f"\n\n_Last update: {format_time(get_wib_time())}_"
    update.effective_message.reply_text(final_output, parse_mode="Markdown", reply_markup=main_menu_keyboard(update.effective_user.id))

def menu_auto_update(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.effective_message.reply_text("Tidak ada alamat terdaftar! Tambahkan menggunakan 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"auto_update_{chat_id}")
    if current_jobs:
        update.effective_message.reply_text("Auto update sudah aktif.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(auto_update, interval=UPDATE_INTERVAL, context={'chat_id': chat_id}, name=f"auto_update_{chat_id}")
    update.effective_message.reply_text(
        "✅ Auto update dimulai.\n\nBot akan mengirimkan update node setiap 5 menit.",
        reply_markup=main_menu_keyboard(update.effective_user.id)
    )

def menu_enable_alerts(update, context):
    chat_id = update.effective_chat.id
    if not get_addresses_for_chat(chat_id):
        update.effective_message.reply_text("Tidak ada alamat terdaftar! Tambahkan menggunakan 'Add Address'.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    current_jobs = context.job_queue.get_jobs_by_name(f"alert_{chat_id}")
    if current_jobs:
        update.effective_message.reply_text("Alerts sudah aktif.", reply_markup=main_menu_keyboard(update.effective_user.id))
        return
    context.job_queue.run_repeating(alert_check, interval=900, context={'chat_id': chat_id}, name=f"alert_{chat_id}")
    update.effective_message.reply_text(
        "✅ Alerts diaktifkan.\n\nBot akan memantau node dan mengirimkan alert jika tidak ada transaksi selama 15 menit atau terdeteksi node stall.",
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
        update.effective_message.reply_text("✅ Semua job (auto update dan alerts) telah dihentikan.", reply_markup=main_menu_keyboard(update.effective_user.id))
    else:
        update.effective_message.reply_text("Tidak ada job aktif.", reply_markup=main_menu_keyboard(update.effective_user.id))

def help_command(update, context):
    update.effective_message.reply_text(
        "📖 *Panduan Cortensor Node Monitoring Bot*\n\n"
        "• *Add Address*: ➕ Tambahkan alamat wallet dengan format `<wallet_address>,<label>` (label opsional).\n"
        "• *Remove Address*: ➖ Hapus alamat wallet yang sudah ditambahkan.\n"
        "• *Check Status*: 📊 Tampilkan status node, balance, aktivitas terbaru, health, dan stall.\n"
        "• *Auto Update*: 🔄 Mulai auto update setiap 5 menit.\n"
        "• *Enable Alerts*: 🔔 Aktifkan alert jika tidak ada transaksi selama 15 menit atau terjadi node stall.\n"
        "• *Set Delay*: ⏱️ Atur delay antar API call (minimal 2.0 detik).\n"
        "• *Stop*: ⛔ Hentikan semua auto update dan alert.\n"
        "• *Announce* (Admin only): 📣 Kirim pengumuman ke semua chat terdaftar.\n\n"
        "💡 Maksimum node per chat: 15\n\n"
        "🚀 *Happy Monitoring!*",
        reply_markup=main_menu_keyboard(update.effective_user.id),
        parse_mode="Markdown"
    )

def start_command(update, context):
    user_id = update.effective_user.id
    update.effective_message.reply_text(
        "👋 Selamat datang di Cortensor Node Monitoring Bot!\nPilih opsi dari menu di bawah:",
        reply_markup=main_menu_keyboard(user_id)
    )

# -------------------- ERROR HANDLER --------------------
def error_handler(update, context):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    error_text = f"⚠️ Terjadi error: {context.error}"
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
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operasi dibatalkan.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_add)

    conv_remove = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Remove Address$"), remove_address_start)],
        states={
            REMOVE_ADDRESS: [MessageHandler(Filters.text & ~Filters.command, remove_address_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operasi dibatalkan.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_remove)

    conv_delay = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Set Delay$"), set_delay_start)],
        states={
            SET_DELAY: [MessageHandler(Filters.text & ~Filters.command, set_delay_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operasi dibatalkan.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_delay)

    conv_announce = ConversationHandler(
        entry_points=[MessageHandler(Filters.regex("^Announce$"), announce_start)],
        states={
            ANNOUNCE: [MessageHandler(Filters.text & ~Filters.command, announce_receive)]
        },
        fallbacks=[CommandHandler("cancel", lambda update, context: update.effective_message.reply_text("Operasi dibatalkan.", reply_markup=main_menu_keyboard(update.effective_user.id)))]
    )
    dp.add_handler(conv_announce)

    updater.start_polling()
    logger.info("Bot is running... 🚀")
    updater.idle()

if __name__ == "__main__":
    main()