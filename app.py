import os
import re
import queue
import threading
import urllib.parse
import requests
import time
from bs4 import BeautifulSoup
from flask import Flask, render_template, jsonify, request
import telebot
from pymongo import MongoClient
from bson.objectid import ObjectId

# ⚙️ कॉन्फ़िगरेशन
BOT_TOKEN = "8266046259:AAHbq_TB6JOqAM-BYdZHXBfGIaZLrQbPYBw"
MONGO_URI = "mongodb+srv://serdiyasixacshowroom99_db_user:yIIZMCDjV3qGyfnB@cluster0.zxnddtj.mongodb.net/?appName=Cluster0"
OCR_API_KEY = "K81758351788957"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

client = MongoClient(MONGO_URI)
db = client['tracking_business_db']
shipments_col = db['shipments']

photo_queue = queue.Queue()

def fetch_india_post_status(tracking_no):
    try:
        url = f"https://speedposttrack.io/track/{tracking_no}"
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
        response = requests.get(url, headers=headers, timeout=10)
        if response.status_code == 200:
            soup = BeautifulSoup(response.text, 'html.parser')
            page_text = soup.get_text().lower()
            if "delivered" in page_text: return "Delivered ✅"
            elif "out for delivery" in page_text: return "Out for Delivery 🛵"
            elif "dispatched" in page_text or "item received" in page_text: return "In Transit 🚚"
        return "Pending / Just Booked 📦"
    except:
        return "Pending / Just Booked 📦"

def ocr_space_scan(img_path):
    try:
        payload = {'isOverlayRequired': False, 'apikey': OCR_API_KEY, 'language': 'eng'}
        with open(img_path, 'rb') as f:
            r = requests.post('https://api.ocr.space/parse/image', files={'image': f}, data=payload, timeout=15)
        result = r.json()
        if result and "ParsedResults" in result and len(result["ParsedResults"]) > 0:
            return result["ParsedResults"][0]["ParsedText"].split('\n')
    except: pass
    return []

def photo_processor_worker():
    while True:
        task = photo_queue.get()
        if task is None: break
        message, img_path, msg_id, file_id = task
        try:
            result = ocr_space_scan(img_path)
            if not result:
                bot.edit_message_text("❌ फोटो से डेटा साफ़ नहीं पढ़ा जा सका। दोबारा साफ फोटो भेजें।", chat_id=message.chat.id, message_id=msg_id)
                continue
                
            full_text = "\n".join(result)
            tracking_match = re.search(r'[A-Z]{2}\d{9}[A-Z]{2}', full_text.upper())
            phone_numbers = re.findall(r'\b\d{10}\b', full_text)
            
            if not tracking_match:
                bot.edit_message_text("❌ ट्रैकिंग नंबर (AWB) नहीं मिल पाया। कृपया साफ फोटो भेजें।", chat_id=message.chat.id, message_id=msg_id)
                continue

            tracking_no = tracking_match.group(0)
            customer_name = "Unknown Customer"
            mobile1 = phone_numbers[0] if len(phone_numbers) > 0 else "0000000000"
            
            if len(phone_numbers) > 0:
                for i, text_line in enumerate(result):
                    if mobile1 in text_line and i > 0:
                        customer_name = result[i-1].strip()
                        break

            status = fetch_india_post_status(tracking_no)
            file_info = bot.get_file(file_id)
            image_cloud_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_info.file_path}"
            
            shipments_col.insert_one({
                'telegram_user_id': message.from_user.id,
                'tracking_no': tracking_no,
                'name': customer_name,
                'mobile1': mobile1,
                'status': status,
                'image_url': image_cloud_url
            })
            
            bot.edit_message_text(f"✅ **पार्सल ऐड हो गया!**\n\n🆔 AWB: `{tracking_no}`\n👤 नाम: {customer_name}\n📱 मोबाइल: {mobile1}\n⚡ स्टेटस: {status}", chat_id=message.chat.id, message_id=msg_id, parse_mode="Markdown")
        except Exception as e:
            bot.edit_message_text(f"❌ त्रुटि: {str(e)}", chat_id=message.chat.id, message_id=msg_id)
        finally:
            if os.path.exists(img_path): os.remove(img_path)
            photo_queue.task_done()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "👋 स्वागत है! पार्सल की फोटो भेजें या नीचे दिए गए डैशबोर्ड बटन का उपयोग करें।")

@bot.message_handler(content_types=['photo'])
def handle_receipt_photo(message):
    msg = bot.reply_to(message, "⏳ फोटो मिल गई है, स्कैन किया जा रहा है...")
    file_id = message.photo[-1].file_id
    file_info = bot.get_file(file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    img_path = f"receipt_{message.message_id}.jpg"
    with open(img_path, 'wb') as f:
        f.write(downloaded_file)
    photo_queue.put((message, img_path, msg.message_id, file_id))

# --- Web App Routes ---
@app.route('/')
def dashboard(): return render_template('index.html')

@app.route('/api/shipments')
def get_shipments():
    shipments = list(shipments_col.find())
    for s in shipments: s['_id'] = str(s['_id'])
    return jsonify(shipments)

# 🔍 बिना सेव किए सीधे अलग से ट्रैक करने की API
@app.route('/api/track_direct', methods=['GET'])
def track_direct():
    tracking_no = request.args.get('tracking_no', '')
    if tracking_no:
        status = fetch_india_post_status(tracking_no)
        return jsonify({'status': status}), 200
    return jsonify({'status': 'Invalid Number'}), 400

@app.route('/api/add_manual', methods=['POST'])
def add_manual():
    data = request.json
    name = data.get('name')
    tracking_no = data.get('tracking_no', '').upper()
    mobile1 = data.get('mobile1')
    if name and tracking_no and mobile1:
        status = fetch_india_post_status(tracking_no)
        shipments_col.insert_one({
            'name': name, 'tracking_no': tracking_no, 'mobile1': mobile1, 'status': status, 'image_url': ''
        })
        return jsonify({'success': True}), 200
    return jsonify({'success': False}), 400

@app.route('/api/delete_shipment', methods=['POST'])
def delete_shipment():
    data = request.json
    shipment_id = data.get('id')
    if shipment_id:
        shipments_col.delete_one({'_id': ObjectId(shipment_id)})
        return jsonify({'success': True}), 200
    return jsonify({'success': False}), 400

if __name__ == "__main__":
    threading.Thread(target=photo_processor_worker, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
