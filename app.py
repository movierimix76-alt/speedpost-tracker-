"""
Telegram AWB Matcher — MongoDB + Excel-File-Reading Version (v6)
=========================================================
नया फीचर (v6):
- अब दूसरे ग्रुप (GROUP2) को भी सुना जाता है — वहां जो भी .xlsx फाइलें
  भेजी जाएंगी (पुरानी और नई दोनों), बॉट उन्हें अपने-आप डाउनलोड करके
  उनके column A से हर AWB नंबर पढ़ेगा (कोई मिस नहीं होगा) और मास्टर
  लिस्ट (MongoDB) से मिलान करके column B में "Done ✅" मार्क करेगा।
- पहले वाला ग्रुप (GROUP) टेक्स्ट मैसेज से AWB पढ़ना जारी रखेगा,
  मिलने पर "मिल गया" मार्क करेगा (जैसा पहले था)।
"""

import os
import re
import io
import asyncio
import logging
import threading
from datetime import datetime, timezone
from openpyxl import load_workbook, Workbook
from flask import Flask, send_file, request
from telethon import TelegramClient, events
from telethon.sessions import StringSession
from pymongo import MongoClient
from pymongo.errors import PyMongoError

# ========================= Environment Variables से CONFIG =========================
API_ID = int(os.environ["API_ID"])
API_HASH = os.environ["API_HASH"]
SESSION_STRING = os.environ["SESSION_STRING"]
GROUP = os.environ["GROUP"]                      # टेक्स्ट मैसेज वाला ग्रुप
GROUP2 = os.environ.get("GROUP2")                 # xlsx फाइलें भेजने वाला ग्रुप (नया)
MONGODB_URI = os.environ["MONGODB_URI"]
DB_NAME = os.environ.get("DB_NAME", "awb_tracker")
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "awbs")
FOUND_TEXT = os.environ.get("FOUND_TEXT", "मिल गया")          # टेक्स्ट मैसेज से मिलान पर
FILE_DONE_TEXT = os.environ.get("FILE_DONE_TEXT", "Done ✅")   # xlsx फाइल से मिलान पर
OLD_MESSAGES_LIMIT = int(os.environ.get("OLD_MESSAGES_LIMIT", "100000"))
PORT = int(os.environ.get("PORT", "10000"))
# =====================================================================================

AWB_PATTERN = re.compile(r"\b[A-Za-z]{2}\d{9}[Ii][Nn]\b")

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
log = logging.getLogger(__name__)

# ---------------------------- MongoDB ----------------------------
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[DB_NAME]
awbs_col = db[COLLECTION_NAME]
awbs_col.create_index("awb", unique=True)

app = Flask(__name__)

telegram_loop = None
telegram_target = None       # ग्रुप 1 (टेक्स्ट)
telegram_target2 = None      # ग्रुप 2 (xlsx फाइलें)
telegram_ready = threading.Event()


def extract_awbs_from_text(text: str):
    return {m.upper() for m in AWB_PATTERN.findall(text or "")}


def extract_awbs_from_xlsx_bytes(file_bytes: bytes):
    """xlsx फाइल से AWB नंबर पढ़ता है — पहले 'AWB' / 'article_number' जैसे
    नाम वाला कॉलम ढूंढकर सिर्फ उसी कॉलम से पढ़ता है (तेज़ और सटीक, हज़ारों
    फाइलों के लिए ज़रूरी)। अगर ऐसा कोई कॉलम नाम न मिले, तो सुरक्षा के लिए
    पूरी शीट के हर सेल में regex से AWB ढूंढा जाता है (ताकि कोई मिस न हो)।"""
    awbs = set()
    try:
        wb = load_workbook(io.BytesIO(file_bytes), data_only=True)
    except Exception as e:
        log.error(f"xlsx फाइल पढ़ने में गलती: {e}")
        return awbs

    # AWB/article_number जैसे कॉलम को पहचानने के लिए keywords
    header_keywords = [
        "awb", "article_number", "article number", "articlenumber",
        "article no", "article_no", "tracking number", "tracking_number",
        "waybill", "consignment number", "consignment_number",
    ]

    for ws in wb.worksheets:
        rows = list(ws.iter_rows(values_only=True))
        if not rows:
            continue

        header_row = rows[0]
        target_col_idx = None
        for idx, cell_val in enumerate(header_row):
            if cell_val is None:
                continue
            header_text = str(cell_val).strip().lower()
            if any(kw in header_text for kw in header_keywords):
                target_col_idx = idx
                break

        if target_col_idx is not None:
            # सही कॉलम मिल गया — सिर्फ उसी कॉलम से पढ़ना (तेज़, सटीक)
            found_in_column = False
            for row in rows[1:]:
                if target_col_idx < len(row) and row[target_col_idx] is not None:
                    extracted = extract_awbs_from_text(str(row[target_col_idx]))
                    if extracted:
                        awbs.update(extracted)
                        found_in_column = True
            if found_in_column:
                continue  # इस शीट के लिए काम हो गया, बाकी सेल्स स्कैन करने की ज़रूरत नहीं

        # कॉलम नाम से नहीं मिला, या मिलने पर भी कोई valid AWB नहीं निकला —
        # सुरक्षा के लिए पूरी शीट के हर सेल में ढूंढो (कोई मिस न हो)
        for row in rows:
            for cell_val in row:
                if cell_val is None:
                    continue
                awbs.update(extract_awbs_from_text(str(cell_val)))

    return awbs


# ---------------------------- Web रूट्स ----------------------------
@app.route("/")
def health():
    total = awbs_col.count_documents({})
    found = awbs_col.count_documents({"status": FOUND_TEXT})
    done = awbs_col.count_documents({"status": FILE_DONE_TEXT})
    return f"""
    <h2>AWB बॉट चल रहा है ✅</h2>
    <p>कुल AWB: {total} | मिल गया: {found} | Done ✅: {done}</p>
    <p><a href="/upload">यहां से अपनी असली AWB Excel फाइल अपलोड करें (मास्टर लिस्ट)</a></p>
    <p><a href="/download">लेटेस्ट Excel फाइल डाउनलोड करें</a></p>
    """


@app.route("/download")
def download():
    wb = Workbook()
    ws = wb.active
    ws.append(["AWB", "Status"])
    for doc in awbs_col.find({}, {"_id": 0, "awb": 1, "status": 1}).sort("awb", 1):
        ws.append([doc["awb"], doc.get("status") or ""])

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return send_file(
        buf,
        as_attachment=True,
        download_name="awb.xlsx",
        mimetype="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    )


@app.route("/upload", methods=["GET", "POST"])
def upload():
    if request.method == "POST":
        f = request.files.get("file")
        if not f or not f.filename.endswith(".xlsx"):
            return "कृपया .xlsx फाइल चुनें। <a href='/upload'>वापस जाएं</a>"

        wb = load_workbook(f, data_only=True)
        ws = wb.active

        awbs_col.delete_many({})
        log.info("पुराना डेटा साफ किया गया, नई मास्टर शीट डाली जा रही है...")

        added = 0
        for row in ws.iter_rows(min_row=2, values_only=True):
            val = row[0]
            if val is None or str(val).strip() == "":
                continue
            awb = str(val).strip().upper()
            awbs_col.update_one(
                {"awb": awb},
                {"$setOnInsert": {"awb": awb, "status": None,
                                   "created_at": datetime.now(timezone.utc)}},
                upsert=True,
            )
            added += 1
        log.info(f"नई मास्टर Excel अपलोड — {added} AWB MongoDB में डाले गए। Rescan शुरू हो रहा है...")

        if telegram_ready.is_set() and telegram_loop:
            if telegram_target:
                asyncio.run_coroutine_threadsafe(
                    scan_old_text_messages(telegram_target), telegram_loop
                )
            if telegram_target2:
                asyncio.run_coroutine_threadsafe(
                    scan_old_files(telegram_target2), telegram_loop
                )
            return (
                f"मास्टर फाइल अपलोड हो गई ✅ ({added} AWB डाले गए) — दोनों ग्रुप के पुराने डेटा से "
                "दोबारा मिलान हो रहा है। <a href='/'>होम पर जाएं</a>"
            )
        return f"फाइल अपलोड हो गई ({added} AWB), लेकिन बॉट अभी तैयार नहीं है। <a href='/'>होम पर जाएं</a>"

    return """
    <h3>अपनी मास्टर AWB Excel फाइल अपलोड करें</h3>
    <p>ध्यान रखें: column A में AWB नंबर होने चाहिए (header row 1 में)</p>
    <form method="post" enctype="multipart/form-data">
      <input type="file" name="file" accept=".xlsx">
      <button type="submit">अपलोड करें</button>
    </form>
    """


# ---------------------------- MongoDB हेल्पर ----------------------------
def mark_found(awb_set, status_text):
    """awb_set में मौजूद AWB को MongoDB में ढूंढकर status सेट करता है
    (सिर्फ वही जो मास्टर लिस्ट में पहले से मौजूद हैं)। लौटाता है कितने मार्क हुए।"""
    if not awb_set:
        return 0
    result = awbs_col.update_many(
        {"awb": {"$in": list(awb_set)}, "status": {"$ne": status_text}},
        {"$set": {"status": status_text, "found_at": datetime.now(timezone.utc)}},
    )
    return result.modified_count


# ---------------------------- Telegram लॉजिक ----------------------------
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)


async def resolve_entity(identifier):
    if not identifier:
        return None
    try:
        return await client.get_entity(identifier)
    except Exception:
        log.info(f"'{identifier}' के लिए सीधे entity नहीं मिली, dialogs sync किए जा रहे हैं...")
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if (
                str(getattr(entity, "username", "")) == str(identifier).lstrip("@")
                or str(entity.id) == str(identifier)
                or f"-100{entity.id}" == str(identifier)
            ):
                return entity
        raise ValueError(
            f"'{identifier}' नहीं मिला — पक्का करें कि आपका अकाउंट उसमें join है।"
        )


# ---- ग्रुप 1: टेक्स्ट मैसेज से AWB ----
async def scan_old_text_messages(target):
    log.info(f"[ग्रुप 1] पुराने {OLD_MESSAGES_LIMIT} मैसेज पढ़े जा रहे हैं...")
    all_awbs = set()
    count = 0
    async for msg in client.iter_messages(target, limit=OLD_MESSAGES_LIMIT):
        count += 1
        if msg.text:
            all_awbs.update(extract_awbs_from_text(msg.text))
        if count % 5000 == 0:
            log.info(f"[ग्रुप 1] ...{count} मैसेज पढ़ लिए, अब तक {len(all_awbs)} अलग AWB मिले")

    log.info(f"[ग्रुप 1] कुल {count} मैसेज पढ़े, {len(all_awbs)} अलग AWB मिले। मिलान हो रहा है...")
    matched = mark_found(all_awbs, FOUND_TEXT)
    log.info(f"[ग्रुप 1] स्कैन पूरा — {matched} नए AWB 'मिल गया' मार्क हुए।")


def register_text_handler(target_id):
    @client.on(events.NewMessage(chats=target_id))
    async def on_new_text(event):
        found = extract_awbs_from_text(event.raw_text)
        if not found:
            return
        matched = mark_found(found, FOUND_TEXT)
        if matched:
            log.info(f"[ग्रुप 1] नया मैसेज — {matched} AWB 'मिल गया' मार्क हुए।")


# ---- ग्रुप 2: xlsx फाइलों से AWB (कोई मिस नहीं) ----
def _is_xlsx_message(msg):
    if not msg.document:
        return False
    filename = ""
    for attr in msg.document.attributes:
        if hasattr(attr, "file_name") and attr.file_name:
            filename = attr.file_name
    return filename.lower().endswith(".xlsx")


async def scan_old_files(target):
    log.info(f"[ग्रुप 2] पुराने {OLD_MESSAGES_LIMIT} मैसेज में xlsx फाइलें ढूंढी जा रही हैं...")
    all_awbs = set()
    count = 0
    files_found = 0
    async for msg in client.iter_messages(target, limit=OLD_MESSAGES_LIMIT):
        count += 1
        if _is_xlsx_message(msg):
            try:
                file_bytes = await client.download_media(msg, file=bytes)
                awbs = extract_awbs_from_xlsx_bytes(file_bytes)
                all_awbs.update(awbs)
                files_found += 1
                log.info(f"[ग्रुप 2] फाइल #{files_found} पढ़ी गई — {len(awbs)} AWB मिले (कुल अब तक {len(all_awbs)})")
            except Exception as e:
                log.error(f"[ग्रुप 2] फाइल डाउनलोड/पढ़ने में गलती: {e}")
        if count % 2000 == 0:
            log.info(f"[ग्रुप 2] ...{count} मैसेज चेक किए, {files_found} xlsx फाइलें मिलीं")

    log.info(f"[ग्रुप 2] कुल {count} मैसेज चेक किए, {files_found} xlsx फाइलें मिलीं, {len(all_awbs)} अलग AWB मिले। मिलान हो रहा है...")
    matched = mark_found(all_awbs, FILE_DONE_TEXT)
    log.info(f"[ग्रुप 2] स्कैन पूरा — {matched} नए AWB 'Done ✅' मार्क हुए।")


def register_file_handler(target_id):
    @client.on(events.NewMessage(chats=target_id))
    async def on_new_file(event):
        if not _is_xlsx_message(event.message):
            return
        try:
            file_bytes = await client.download_media(event.message, file=bytes)
            awbs = extract_awbs_from_xlsx_bytes(file_bytes)
            matched = mark_found(awbs, FILE_DONE_TEXT)
            log.info(f"[ग्रुप 2] नई फाइल — {len(awbs)} AWB मिले, {matched} 'Done ✅' मार्क हुए।")
        except Exception as e:
            log.error(f"[ग्रुप 2] नई फाइल प्रोसेस करने में गलती: {e}")


# ---- मुख्य शुरुआत ----
async def telegram_main():
    global telegram_loop, telegram_target, telegram_target2
    await client.start()
    log.info("Telegram से कनेक्ट हो गया।")
    telegram_loop = asyncio.get_event_loop()

    target = await resolve_entity(GROUP)
    telegram_target = target
    log.info(f"[ग्रुप 1] मिल गया: {getattr(target, 'title', target.id)}")
    register_text_handler(target.id)

    if GROUP2:
        target2 = await resolve_entity(GROUP2)
        telegram_target2 = target2
        log.info(f"[ग्रुप 2] मिल गया: {getattr(target2, 'title', target2.id)}")
        register_file_handler(target2.id)

    telegram_ready.set()

    await scan_old_text_messages(target)
    if telegram_target2:
        await scan_old_files(telegram_target2)

    log.info("अब दोनों ग्रुप के नए मैसेज लाइव सुने जा रहे हैं...")
    await client.run_until_disconnected()


def run_telegram_in_thread():
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(telegram_main())
    except Exception as e:
        log.error(f"Telegram thread में गलती: {e}")


if __name__ == "__main__":
    try:
        mongo_client.admin.command("ping")
        log.info("MongoDB से कनेक्ट हो गया।")
    except PyMongoError as e:
        log.error(f"MongoDB कनेक्ट नहीं हो पाया: {e}")

    t = threading.Thread(target=run_telegram_in_thread, daemon=True)
    t.start()
    app.run(host="0.0.0.0", port=PORT)
