"""
Telegram AWB Matcher — Render FREE Web Service Version
=========================================================
Render के free plan पर चलाने के लिए। यह एक छोटा Flask वेब सर्वर चलाता है
(ताकि Render की "port bind" शर्त पूरी हो — free plan सिर्फ Web Service
टाइप ही मुफ्त देता है), और साथ ही Telegram बॉट को background thread में
चलाता है।

ज़रूरी बात: free plan पर persistent disk नहीं मिलती, इसलिए हर restart/deploy
पर Excel फाइल रीसेट हो सकती है (खाली या repo वाली base फाइल से दोबारा शुरू)।
इसलिए यहां एक /download रूट भी है जहां से आप कभी भी लेटेस्ट Excel डाउनलोड
कर सकते हैं — उसे अपने फोन/कंप्यूटर में बैकअप रखते रहें।

Sleep से बचने के लिए: Render का free web service 15 मिनट बिना ट्रैफिक के
सो जाता है। इसे जगाए रखने के लिए UptimeRobot (मुफ्त) से हर 5 मिनट पर
इसके URL को ping करवाना होगा — देखें SETUP_INSTRUCTIONS_RENDER_FREE.md
"""

import os
import re
import asyncio
import logging
import threading
from openpyxl import load_workbook, Workbook
from flask import Flask, send_file
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ========================= Environment Variables से CONFIG =========================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
GROUP = os.environ["GROUP"]
EXCEL_FILE = os.environ.get("EXCEL_FILE", "awb.xlsx")   # free plan पर local disk (ephemeral)
SHEET_NAME = os.environ.get("SHEET_NAME") or None
FOUND_TEXT = os.environ.get("FOUND_TEXT", "मिल गया")
OLD_MESSAGES_LIMIT = int(os.environ.get("OLD_MESSAGES_LIMIT", "100000"))
PORT = int(os.environ.get("PORT", "10000"))
# =====================================================================================

AWB_PATTERN = re.compile(r"\b[A-Z]{2}\d{9}IN\b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# ---------------------------- Flask (सिर्फ port bind + status/download के लिए) ----------------------------
app = Flask(__name__)


@app.route("/")
def health():
    return "AWB बॉट चल रहा है ✅"


@app.route("/download")
def download():
    if not os.path.exists(EXCEL_FILE):
        return "अभी तक कोई फाइल नहीं बनी।", 404
    return send_file(EXCEL_FILE, as_attachment=True)


# ---------------------------- Telegram लॉजिक ----------------------------
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


def ensure_excel_exists():
    if not os.path.exists(EXCEL_FILE):
        log.info("Excel फाइल नहीं मिली, नई खाली फाइल बनाई जा रही है...")
        wb = Workbook()
        ws = wb.active
        ws.append(["AWB", "Status"])
        wb.save(EXCEL_FILE)


def mark_found_in_excel(awb_set):
    if not awb_set:
        return []
    wb = load_workbook(EXCEL_FILE)
    ws = wb[SHEET_NAME] if SHEET_NAME else wb.active

    matched = []
    for row in ws.iter_rows(min_row=2):
        cell_a, cell_b = row[0], row[1]
        if cell_a.value is None:
            continue
        val = str(cell_a.value).strip()
        if val in awb_set and (cell_b.value or "").strip() != FOUND_TEXT:
            cell_b.value = FOUND_TEXT
            matched.append(val)

    if matched:
        wb.save(EXCEL_FILE)
    return matched


async def scan_old_messages():
    log.info(f"पुराने {OLD_MESSAGES_LIMIT} मैसेज पढ़े जा रहे हैं...")
    all_awbs = set()
    count = 0
    async for msg in client.iter_messages(GROUP, limit=OLD_MESSAGES_LIMIT):
        count += 1
        if msg.text:
            all_awbs.update(AWB_PATTERN.findall(msg.text))
        if count % 5000 == 0:
            log.info(f"...{count} मैसेज पढ़ लिए, अब तक {len(all_awbs)} अलग AWB मिले")

    log.info(f"कुल {count} मैसेज पढ़े, {len(all_awbs)} अलग-अलग AWB मिले। मिलान किया जा रहा है...")
    matched = mark_found_in_excel(all_awbs)
    log.info(f"पुराने मैसेज से {len(matched)} AWB मार्क हुए।")


@client.on(events.NewMessage())
async def on_new_message(event):
    chat = await event.get_chat()
    chat_ok = (
        str(getattr(chat, "username", "")) == GROUP.lstrip("@")
        or str(getattr(chat, "id", "")) == GROUP
        or f"-100{getattr(chat, 'id', '')}" == GROUP
    )
    if not chat_ok:
        return

    text = event.raw_text or ""
    found = set(AWB_PATTERN.findall(text))
    if not found:
        return
    matched = mark_found_in_excel(found)
    if matched:
        log.info(f"नया मैसेज — मार्क हुआ: {matched}")


async def telegram_main():
    ensure_excel_exists()
    await client.start()
    log.info("Telegram से कनेक्ट हो गया।")
    await scan_old_messages()
    log.info("अब नए मैसेज लाइव सुने जा रहे हैं...")
    await client.run_until_disconnected()


def run_telegram_in_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    loop.run_until_complete(telegram_main())


if __name__ == "__main__":
    t = threading.Thread(target=run_telegram_in_thread, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
