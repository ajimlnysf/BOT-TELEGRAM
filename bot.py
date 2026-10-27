import os
import json
import asyncio
from pathlib import Path

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
)
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BASE = Path(__file__).parent
DATA_FILE = BASE / "data.json"
QRIS_FILE = BASE / "qris.jpg"

TOKEN = os.getenv("BOT_TOKEN", "").strip()
OWNER_ID = int(os.getenv("OWNER_ID", "0"))
PRICE = 1000


def load_data():
    if DATA_FILE.exists():
        try:
            return json.loads(DATA_FILE.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {"stock": 0, "orders": {}, "next_id": 1}


def save_data(data):
    DATA_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def rupiah(amount):
    return "Rp" + f"{amount:,}".replace(",", ".")


def is_owner(user_id):
    return user_id == OWNER_ID and OWNER_ID != 0


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("🍿 Beli Netflix — Rp1.000",
                              callback_data="buy")],
    ]
    if is_owner(update.effective_user.id):
        keyboard.append([
            InlineKeyboardButton("🛠 Panel Owner",
                                 callback_data="owner")
        ])

    await update.effective_message.reply_text(
        "🍿 TOKO NETFLIX\n\n"
        f"Harga: {rupiah(PRICE)}\n"
        "Pembayaran melalui QRIS manual.\n"
        "Pesanan diproses setelah pembayaran diverifikasi.",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def show_owner(update, context):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer("Khusus owner.", show_alert=True)
        return

    data = load_data()
    await query.answer()
    await query.message.reply_text(
        "🛠 PANEL OWNER\n\n"
        f"Stok tersedia: {data['stock']}\n\n"
        "Perintah owner:\n"
        "/addstock JUMLAH — tambah stok\n"
        "/stock — cek stok\n"
        "/orders — lihat pesanan\n"
        "/helpowner — bantuan"
    )


async def buy(update, context):
    query = update.callback_query
    await query.answer()

    data = load_data()
    if data["stock"] < 1:
        await query.message.reply_text(
            "Maaf, stok sedang habis. Silakan coba lagi nanti."
        )
        return

    order_id = str(data["next_id"])
    data["next_id"] += 1
    data["orders"][order_id] = {
        "user_id": query.from_user.id,
        "status": "awaiting_payment",
    }
    save_data(data)

    if QRIS_FILE.exists():
        with QRIS_FILE.open("rb") as photo:
            await context.bot.send_photo(
                chat_id=query.from_user.id,
                photo=photo,
                caption=(
                    f"Pesanan #{order_id}\n"
                    f"Total: {rupiah(PRICE)}\n\n"
                    "Silakan bayar melalui QRIS ini, lalu kirim "
                    "foto bukti pembayaran di chat ini.\n"
                    "Jangan membayar lebih dari satu kali."
                ),
            )
    else:
        await query.message.reply_text(
            "File QRIS belum tersedia. Hubungi owner."
        )
        return


async def receive_proof(update, context):
    user_id = update.effective_user.id
    if not update.message or not update.message.photo:
        return

    data = load_data()
    candidates = [
        (oid, order)
        for oid, order in data["orders"].items()
        if order["user_id"] == user_id
        and order["status"] == "awaiting_payment"
    ]

    if not candidates:
        await update.message.reply_text(
            "Tidak ada pesanan yang menunggu bukti pembayaran."
        )
        return

    order_id, order = candidates[-1]
    order["status"] = "pending_review"
    order["proof_file_id"] = update.message.photo[-1].file_id
    save_data(data)

    keyboard = InlineKeyboardMarkup([
        [
            InlineKeyboardButton(
                "✅ Setujui",
                callback_data=f"approve:{order_id}",
            ),
            InlineKeyboardButton(
                "❌ Tolak",
                callback_data=f"reject:{order_id}",
            ),
        ]
    ])

    await context.bot.send_photo(
        chat_id=OWNER_ID,
        photo=order["proof_file_id"],
        caption=(
            f"Bukti pembayaran pesanan #{order_id}\n"
            f"Pembeli: {user_id}\n"
            f"Total: {rupiah(PRICE)}\n\n"
            "Periksa mutasi pembayaran sebelum menyetujui."
        ),
        reply_markup=keyboard,
    )

    await update.message.reply_text(
        "Bukti pembayaran diterima dan sedang diperiksa owner. "
        "Pesanan belum disetujui."
    )


async def review_order(update, context):
    query = update.callback_query
    if not is_owner(query.from_user.id):
        await query.answer("Khusus owner.", show_alert=True)
        return

    action, order_id = query.data.split(":", 1)
    data = load_data()
    order = data["orders"].get(order_id)

    if not order or order["status"] != "pending_review":
        await query.answer("Pesanan sudah diproses atau tidak ditemukan.",
                           show_alert=True)
        return

    if action == "approve":
        if data["stock"] < 1:
            await query.answer("Stok habis.", show_alert=True)
            return
        data["stock"] -= 1
        order["status"] = "approved"
        message = (
            f"Pembayaran pesanan #{order_id} telah disetujui.\n"
            "Silakan hubungi owner untuk menerima pesanan."
        )
    else:
        order["status"] = "rejected"
        message = (
            f"Bukti pembayaran pesanan #{order_id} ditolak. "
            "Silakan hubungi owner."
        )

    save_data(data)
    await query.answer("Pesanan diperbarui.")
    await query.edit_message_caption(
        caption=f"Pesanan #{order_id}: {order['status']}"
    )
    await context.bot.send_message(
        chat_id=order["user_id"],
        text=message,
    )


async def add_stock(update, context):
    if not is_owner(update.effective_user.id):
        return

    if len(context.args) != 1 or not context.args[0].isdigit():
        await update.message.reply_text("Gunakan: /addstock JUMLAH")
        return

    amount = int(context.args[0])
    if amount < 1 or amount > 10000:
        await update.message.reply_text("Jumlah harus 1 sampai 10000.")
        return

    data = load_data()
    data["stock"] += amount
    save_data(data)
    await update.message.reply_text(
        f"Stok ditambahkan {amount}. Stok sekarang: {data['stock']}"
    )


async def stock(update, context):
    if not is_owner(update.effective_user.id):
        return
    data = load_data()
    await update.message.reply_text(f"Stok tersedia: {data['stock']}")


async def orders(update, context):
    if not is_owner(update.effective_user.id):
        return
    data = load_data()
    if not data["orders"]:
        await update.message.reply_text("Belum ada pesanan.")
        return

    lines = []
    for oid, order in list(data["orders"].items())[-20:]:
        lines.append(
            f"#{oid} | {order['user_id']} | {order['status']}"
        )
    await update.message.reply_text("\n".join(lines))


async def help_owner(update, context):
    if not is_owner(update.effective_user.id):
        return
    await update.message.reply_text(
        "/addstock JUMLAH — tambah stok\n"
        "/stock — lihat stok\n"
        "/orders — lihat pesanan"
    )


def main():
    if not TOKEN or OWNER_ID == 0:
        raise RuntimeError("BOT_TOKEN atau OWNER_ID belum diatur.")

    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(buy, pattern="^buy$"))
    app.add_handler(CallbackQueryHandler(show_owner, pattern="^owner$"))
    app.add_handler(
        CallbackQueryHandler(review_order, pattern="^(approve|reject):")
    )
    app.add_handler(CommandHandler("addstock", add_stock))
    app.add_handler(CommandHandler("stock", stock))
    app.add_handler(CommandHandler("orders", orders))
    app.add_handler(CommandHandler("helpowner", help_owner))
    app.add_handler(
        MessageHandler(filters.PHOTO, receive_proof)
    )

    print("Bot berjalan.")
    app.run_polling()


if __name__ == "__main__":
    main()

