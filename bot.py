import os
import requests
import logging
import time
import asyncio
import nest_asyncio

# Terapkan nest_asyncio untuk mengizinkan nested event loop.
nest_asyncio.apply()

from telegram import Update
from telegram.ext import Application, CommandHandler, CallbackContext

# Ambil API key dari environment variables
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ARBISCAN_API_KEY = os.getenv("ARBISCAN_API_KEY")

if not TELEGRAM_BOT_TOKEN or not ARBISCAN_API_KEY:
    raise ValueError("Pastikan TELEGRAM_BOT_TOKEN dan ARBISCAN_API_KEY sudah diset di environment variables.")

# Konfigurasi logging
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

def format_age(ts: str) -> str:
    """
    Mengonversi timestamp (dalam detik) menjadi format relatif (misalnya "5 minutes ago").
    """
    try:
        ts_int = int(ts)
        now = int(time.time())
        diff = now - ts_int
        if diff < 60:
            return f"{diff} seconds ago"
        elif diff < 3600:
            return f"{diff // 60} minutes ago"
        elif diff < 86400:
            return f"{diff // 3600} hours ago"
        else:
            return f"{diff // 86400} days ago"
    except Exception as e:
        logger.error("Error menghitung age: %s", e)
        return "Unknown"

async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "?? *Bot Monitoring Cortensor & Arbiscan*\n"
        "Gunakan perintah berikut:\n"
        "/ping <address> - Cek transaksi terbaru\n"
        "/status <address> - Cek status node\n"
        "/nodestats <address> - Cek metric node\n"
        "/info <address> - Live update transaksi\n"
        "/help - Bantuan penggunaan",
        parse_mode="Markdown"
    )

async def help_command(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "?? *Panduan Penggunaan*\n"
        "/ping <address> - Lihat transaksi terbaru (Success/Failed, Method, Age)\n"
        "/status <address> - Cek apakah node berjalan (5 menit terakhir)\n"
        "/nodestats <address> - Cek statistik Cortensor node\n"
        "/info <address> - Live update jika tidak ada transaksi dalam 5 menit terakhir",
        parse_mode="Markdown"
    )

async def ping(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Gunakan: `/ping <address>`", parse_mode="Markdown")
        return

    address = context.args[0]
    url = (
        f"https://api-sepolia.arbiscan.io/api?module=account&action=txlist"
        f"&address={address}&startblock=0&endblock=99999999&sort=desc&apikey={ARBISCAN_API_KEY}"
    )
    
    response = requests.get(url)
    data = response.json()

    if data.get("status") == "1" and "result" in data:
        transactions = data["result"][:25]
        reply = f"?? *Transaksi Terbaru untuk {address}*\n\n"
        reply += "| Status  |  Method  |  Age  |\n"
        reply += "|---------|----------|-------|\n"
        for tx in transactions:
            status = "✅ Success" if tx.get("isError", "1") == "0" else "❌ Failed"
            method = tx.get("functionName", "Unknown")
            # Mengonversi timestamp ke format "AGE"
            age = format_age(tx.get("timeStamp", "0"))
            reply += f"| {status} | {method} | {age} |\n"
        await update.message.reply_text(f"```\n{reply}\n```", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Tidak dapat mengambil data transaksi untuk {address}.")

async def status(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Gunakan: `/status <address>`", parse_mode="Markdown")
        return

    address = context.args[0]
    url = (
        f"https://api-sepolia.arbiscan.io/api?module=account&action=txlist"
        f"&address={address}&startblock=0&endblock=99999999&sort=desc&apikey={ARBISCAN_API_KEY}"
    )
    
    response = requests.get(url)
    data = response.json()

    if data.get("status") == "1" and "result" in data and data["result"]:
        transactions = data["result"]
        latest_timestamp = int(transactions[0]["timeStamp"])
        # Dapatkan waktu UTC saat ini dari worldtimeapi
        current_timestamp = int(requests.get("http://worldtimeapi.org/api/timezone/Etc/UTC").json()["unixtime"])
        if current_timestamp - latest_timestamp <= 300:
            await update.message.reply_text(f"✅ Node untuk {address} *Berjalan*", parse_mode="Markdown")
        else:
            await update.message.reply_text(f"⚠️ Node untuk {address} *Berhenti Berjalan*", parse_mode="Markdown")
    else:
        await update.message.reply_text(f"Tidak dapat mengambil data status untuk {address}.")

async def nodestats(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Gunakan: `/nodestats <address>`", parse_mode="Markdown")
        return

    address = context.args[0]
    url = f"https://dashboard-devnet3.cortensor.network/nodestats/{address}"
    
    response = requests.get(url)
    if response.status_code == 200:
        try:
            data = response.json()
        except Exception as e:
            await update.message.reply_text("Gagal mem-parsing data JSON dari dashboard.", parse_mode="Markdown")
            return

        reply = f"?? *Cortensor Monitor untuk {address}*\n\n"
        metrics = [
            "Request", "Create", "Prepare", "Start",
            "Precommit", "Commit", "End", "Correctness",
            "Ping", "Global Ping"
        ]
        for metric in metrics:
            point = data.get(f"{metric.lower()}_point", "N/A")
            counter = data.get(f"{metric.lower()}_counter", "N/A")
            success_rate = data.get(f"{metric.lower()}_success_rate", "N/A")
            reply += (
                f"*{metric} Metrics:*\n"
                f"Point: {point}\n"
                f"Counter: {counter}\n"
                f"Success Rate: {success_rate}\n\n"
            )
        # Sertakan link manual agar bisa dicek langsung
        reply += f"[Cek Data Manual]({url})"
        await update.message.reply_text(reply, parse_mode="Markdown")
    else:
        await update.message.reply_text(f"❌ Tidak dapat mengambil nodestats untuk {address}.")

async def info(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text("?? Fitur info sedang dalam pengembangan!")

async def main() -> None:
    app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("ping", ping))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("nodestats", nodestats))
    app.add_handler(CommandHandler("info", info))

    print("Bot berjalan...")
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
