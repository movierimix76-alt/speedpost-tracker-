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
import easyocr

# ⚙️ फाइनल कॉन्फ़िगरेशन सेटिंग्स
BOT_TOKEN = "8266046259:AAHbq_TB6JOqAM-BYdZHXBfGIaZLrQbPYBw"
MONGO_URI = "mongodb+srv://serdiyasixacshowroom99_db_user:yIIZMCDjV3qGyfnB@cluster0.zxnddtj.mongodb.net/?appName=Cluster0"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

# क्लाउड डेटाबेस कनेक्शन
client = MongoClient(MONGO_URI)
db = client['tracking_business_db']
shipments_col = db['shipments']

# भारी लोड और 50 रीसेलर्स को संभालने के लिए क्यू (Queue) सिस्टम
photo_queue = queue.Queue()

# EasyOCR लोड करना
print("🤖 OCR इंजन लोड हो रहा है, कृपया प्रतीक्षा करें...")
reader = easyocr.Reader(['en'])
print("✅ OCR इंजन सफलतापूर्वक लोड हो गया!")

# बिना कैप्चा ट्रैकिंग लॉजिक
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

# बैकग्राउंड वर्कर जो एक बार में केवल 1 फोटो प्रोसेस करेगा (512MB RAM के लिए सेफ)
def photo_processor_worker():
    while True:
        task = photo_queue.get()
        if task is None: break
        
        message, img_path, msg_id = task
        try:
            # इमेज से टेक्स्ट पढ़ना
            result = reader.readtext(img_path, detail=0)
            full_text = "\n".join(result)
            
            # रेगुलर एक्सप्रेशन पैटर्न्स (ट्रैकिंग, मोबाइल और COD अमाउंट)
            tracking_match = re.search(r'[A-Z]{2}\d{9}[A-Z]{2}', full_text.upper())
            phone_numbers = re.findall(r'\b\d{10}\b', full_text)
            cod_match = re.search(r'(?:COD|cod|CASH|cash|₹)\s*[:\-\s]*(\d+)', full_text)
            
            if not tracking_match:
                bot.edit_message_text("❌ फोटो से ट्रैकिंग नंबर साफ नहीं पढ़ा जा सका। कृपया दोबारा साफ़ फोटो भेजें।", chat_id=message.chat.id, message_id=msg_id)
                continue

            tracking_no = tracking_match.group(0)
            cod_amount = cod_match.group(1) if cod_match else "0"
            
            # मोबाइल और उसके ठीक ऊपर का नाम ढूंढना
            customer_name = "Unknown Customer"
            mobile1 = phone_numbers[0] if len(phone_numbers) > 0 else "0000000000"
            mobile2 = phone_numbers[1] if len(phone_numbers) > 1 else ""
            
            if len(phone_numbers) > 0:
                for i, text_line in enumerate(result):
                    if mobile1 in text_line and i > 0:
                        customer_name = result[i-1].strip()
                        break

            status = fetch_india_post_status(tracking_no)
            
            # क्लाउड में सुरक्षित सेव करना
            shipments_col.insert_one({
                'telegram_user_id': message.from_user.id,
                'tracking_no': tracking_no,
                'name': customer_name,
                'mobile1': mobile1,
                'mobile2': mobile2,
                'cod': cod_amount,
                'status': status
            })
            
            response_text = f"✅ **शिपमेंट ऑटो-ऐड हो गया!**\n\n🆔 नंबर: `{tracking_no}`\n👤 नाम: {customer_name}\n📱 Mob 1: {mobile1}\n"
            if mobile2: response_text += f"📱 Mob 2: {mobile2}\n"
            if cod_amount != "0": response_text += f"💵 COD Amount: ₹{cod_amount}\n"
            response_text += f"⚡ स्टेटस: {status}"
            
            bot.edit_message_text(response_text, chat_id=message.chat.id, message_id=msg_id, parse_mode="Markdown")
            
        except Exception as e:
            bot.edit_message_text(f"❌ त्रुटि: {str(e)}", chat_id=message.chat.id, message_id=msg_id)
        finally:
            if os.path.exists(img_path): os.remove(img_path)  # 🚨 रेंडर की रैम तुरंत खाली करना
            photo_queue.task_done()

@bot.message_handler(commands=['start', 'help'])
def send_welcome(message):
    bot.reply_to(message, "👋 नमस्ते! बस पार्सल के लेबल या रसीद की फोटो यहाँ भेजें, शिपमेंट ऑटोमैटिक ऐड हो जाएगा।\n\nनीचे दिए गए बटन से अपना आफ्टरशिप डैशबोर्ड खोलें।")

@bot.message_handler(content_types=['photo'])
def handle_receipt_photo(message):
    msg = bot.reply_to(message, "⏳ फोटो मिल गई है! इसे प्रोसेसिंग लाइन (Queue) में लगा दिया गया है। कृपया प्रतीक्षा करें...")
    
    file_info = bot.get_file(message.photo[-1].file_id)
    downloaded_file = bot.download_file(file_info.file_path)
    img_path = f"receipt_{message.message_id}.jpg"
    
    with open(img_path, 'wb') as f:
        f.write(downloaded_file)
        
    photo_queue.put((message, img_path, msg.message_id))

# --- Web App Routes ---
@app.route('/')
def dashboard(): return render_template('index.html')

@app.route('/health')
def health(): return "24/7 Active", 200

@app.route('/api/shipments')
def get_shipments():
    shipments = list(shipments_col.find())
    for s in shipments: s['_id'] = str(s['_id'])
    return jsonify(shipments)

@app.route('/api/delete_shipment', methods=['POST'])
def delete_shipment():
    data = request.json
    shipment_id = data.get('id')
    if shipment_id:
        shipments_col.delete_one({'_id': ObjectId(shipment_id)})
        return jsonify({'success': True}), 200
    return jsonify({'success': False}), 400

# ऑटोमैटिक स्टेटस ट्रैकिंग सिंक (हर 1 घंटे में बैकग्राउंड में चलेगा)
def auto_track_sync():
    while True:
        time.sleep(3600)  # 1 घंटा
        try:
            shipments = shipments_col.find()
            for s in shipments:
                if "Delivered" not in s['status']:
                    new_status = fetch_india_post_status(s['tracking_no'])
                    if new_status != s['status']:
                        shipments_col.update_one({'_id': s['_id']}, {'$set': {'status': new_status}})
        except: pass

if __name__ == "__main__":
    threading.Thread(target=photo_processor_worker, daemon=True).start()
    threading.Thread(target=auto_track_sync, daemon=True).start()
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
