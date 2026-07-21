"""
Telegram AWB Matcher — Render FREE Web Service Version (v2)
=========================================================
बदलाव (v2):
1) चैनल की entity पहले से sync की जाती है (get_dialogs) ताकि
   "Cannot find any entity corresponding to..." वाली गलती न आए
2) /upload पेज जोड़ा गया — यहां से अपनी असली AWB Excel फाइल browser से
   सीधे अपलोड कर सकते हैं, वही आगे मास्टर फाइल के तौर पर इस्तेमाल होगी
"""

import os
import re
import asyncio
import logging
import threading
from openpyxl import load_workbook, Workbook
from flask import Flask, send_file, request
from telethon import TelegramClient, events
from telethon.sessions import StringSession

# ========================= Environment Variables से CONFIG =========================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
GROUP = os.environ["GROUP"]
EXCEL_FILE = os.environ.get("EXCEL_FILE", "awb.xlsx")
SHEET_NAME = os.environ.get("SHEET_NAME") or None
FOUND_TEXT = os.environ.get("FOUND_TEXT", "मिल गया")
OLD_MESSAGES_LIMIT = int(os.environ.get("OLD_MESSAGES_LIMIT", "100000"))
PORT = int(os.environ.get("PORT", "10000"))
# =====================================================================================

AWB_PATTERN = re.compile(r"\b[A-Z]{2}\d{9}IN\b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)


@app.route("/")
def health():
    return """
    <h2>AWB बॉट चल रहा है ✅</h2>
    <p><a href="/upload">यहां से अपनी असली AWB Excel फाइल अपलोड करें</a></p>
    <p><a href="/download">लेटेस्ट Excel फाइल डाउनलोड करें</a></p>
    """


@app.route("/download")
def download():
    if not os.path.exists(EXCEL_FILE):
        return "अभी तक कोई फाइल नहीं बनी।", 404
    return send_file(EXCEL_FILE, as_attachment=True)


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.endswith(".xlsx"):
            return "कृपया .xlsx फाइल चुनें। <a href='/upload'>वापस जाएं</a>"
        f.save(EXCEL_FILE)
        return "फाइल अपलोड हो गई ✅ <a href='/'>होम पर जाएं</a>"
    return """
    <h3>अपनी AWB Excel फाइल अपलोड करें</h3>
    <p>ध्यान रखें: column A में AWB नंबर होने चाहिए (header row 1 में)</p>
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".xlsx">
      <button type="submit">अपलोड करें</button>
    </form>
    """


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


async def get_target_entity():
    """चैनल/ग्रुप की entity को पहले dialogs sync करके ढूंढता है ताकि
    'Cannot find any entity' वाली गलती न आए।"""
    try:
        return await client.get_entity(GROUP)
    except Exception:
        log.info("सीधे entity नहीं मिली, dialogs sync किए जा रहे हैं...")
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if (
                str(getattr(entity, "username", "")) == str(GROUP).lstrip("@")
                or str(entity.id) == str(GROUP)
                or f"-100{entity.id}" == str(GROUP)
            ):
                return entity
        raise ValueError(
            f"चैनल/ग्रुप '{GROUP}' नहीं मिला — पक्का करें कि आपका अकाउंट उसमें join है।"
        )


async def scan_old_messages(target):
    log.info(f"पुराने {OLD_MESSAGES_LIMIT} मैसेज पढ़े जा रहे हैं...")
    all_awbs = set()
    count = 0
    async for msg in client.iter_messages(target, limit=OLD_MESSAGES_LIMIT):
        count += 1
        if msg.text:
            all_awbs.update(AWB_PATTERN.findall(msg.text))
        if count % 5000 == 0:
            log.info(f"...{count} मैसेज पढ़ लिए, अब तक {len(all_awbs)} अलग AWB मिले")

    log.info(f"कुल {count} मैसेज पढ़े, {len(all_awbs)} अलग-अलग AWB मिले। मिलान किया जा रहा है...")
    matched = mark_found_in_excel(all_awbs)
    log.info(f"पुराने मैसेज से {len(matched)} AWB मार्क हुए।")


def register_new_message_handler(target_id):
    @client.on(events.NewMessage(chats=target_id))
    async def on_new_message(event):
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

    target = await get_target_entity()
    log.info(f"टारगेट चैनल/ग्रुप मिल गया: {getattr(target, 'title', target.id)}")

    register_new_message_handler(target.id)
    await scan_old_messages(target)

    log.info("अब नए मैसेज लाइव सुने जा रहे हैं...")
    await client.run_until_disconnected()


def run_telegram_in_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(telegram_main())
    except Exception as e:
        log.error(f"Telegram thread में गलती: {e}")


if __name__ == "__main__":
    t = threading.Thread(target=run_telegram_in_thread, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
