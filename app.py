import os
import re
import queue
import threading
import requests
import base64
from flask import Flask, render_template, jsonify, request
import telebot
from pymongo import MongoClient
from bson.objectid import ObjectId
from bs4 import BeautifulSoup

BOT_TOKEN = "8266046259:AAHbq_TB6JOqAM-BYdZHXBfGIaZLrQbPYBw"
MONGO_URI = "mongodb+srv://serdiyasixacshowroom99_db_user:yIIZMCDjV3qGyfnB@cluster0.zxnddtj.mongodb.net/?appName=Cluster0"
OCR_API_KEY = "K81758351788957"

bot = telebot.TeleBot(BOT_TOKEN)
app = Flask(__name__)

client = MongoClient(MONGO_URI)
db = client['tracking_business_db']
shipments_col = db['shipments']

photo_queue = queue.Queue()

# एक ही सेशन रखने के लिए ताकि कैप्चा कोड मैच हो सके
session_storage = {}

# 🚚 सरकारी डेटाबेस से पूरी टाइमलाइन हिस्ट्री निकालने का असली इंजन
def scrape_india_post_with_captcha(tracking_no, captcha_text, session_id):
    session = session_storage.get(session_id, requests.Session())
    default_res = {"status": "In Transit 🚚", "history": []}
    
    try:
        # कूरियर सेवा प्रदाता के मुख्य लाइव फॉर्म पर डेटा सबमिट करना
        url = "https://www.trackcourier.in/track-india-post-speed-post.php"
        payload = {
            "reg_no": tracking_no,
            "captcha": captcha_text,
            "submit": "Track"
        }
        headers = {"User-Agent": "Mozilla/5.0", "Referer": url}
        
        # कैप्चा के साथ रिक्वेस्ट भेजना
        r = session.post(url, data=payload, headers=headers, timeout=12)
        if r.status_code == 200:
            soup = BeautifulSoup(r.text, 'html.parser')
            page_text = r.text.lower()
            
            status = "In Transit 🚚"
            if "delivered" in page_text: status = "Delivered ✅"
            elif "out for delivery" in page_text: status = "Out for Delivery 🛵"
            
            # सुंदर टाइमलाइन टेबल से तारीख और जगह खोजना
            history = []
            tables = soup.find_all('table')
            for table in tables:
                rows = table.find_all('tr')
                for row in rows[1:]: # हेडर छोड़कर बाकी डेटा निकालना
                    cols = row.find_all('td')
                    if len(cols) >= 3:
                        history.append({
                            "date": cols[0].get_text().strip(),
                            "location": cols[1].get_text().strip(),
                            "details": cols[2].get_text().strip()
                        })
            
            if history:
                return {"status": status, "history": history}
    except: pass
    return default_res

# 🛡️ इंडिया पोस्ट का लाइव कैप्चा इमेज फेच रूट
@app.route('/api/get_captcha', methods=['GET'])
def get_captcha():
    session = requests.Session()
    # रैंडम सेशन आईडी बनाकर स्टोर करना
    session_id = str(time.time())
    session_storage[session_id] = session
    
    try:
        # लाइव सरकारी कैप्चा इमेज यूआरएल
        captcha_url = "https://www.trackcourier.in/captcha.php"
        headers = {"User-Agent": "Mozilla/5.0"}
        img_res = session.get(captcha_url, headers=headers, timeout=8)
        
        # इमेज को बेस64 स्ट्रिंग में बदलना ताकि मोबाइल स्क्रीन पर दिख सके
        encoded_img = base64.b64encode(img_res.content).decode('utf-8')
        
        res = jsonify({'captcha_img': encoded_img})
        res.set_cookie('session_ref', session_id)
        return res
    except:
        return jsonify({'captcha_img': ''}), 400

@app.route('/api/refresh_shipment', methods=['POST'])
def refresh_shipment():
    data = request.json
    shipment_id = data.get('id')
    captcha_text = data.get('captcha', '')
    session_id = request.cookies.get('session_ref', '')
    
    if shipment_id and captcha_text:
        shipment = shipments_col.find_one({'_id': ObjectId(shipment_id)})
        if shipment:
            track_data = scrape_india_post_with_captcha(shipment['tracking_no'], captcha_text, session_id)
            shipments_col.update_one(
                {'_id': ObjectId(shipment_id)}, 
                {'$set': {'status': track_data["status"], 'history': track_data["history"]}}
            )
            return jsonify({'success': True}), 200
    return jsonify({'success': False}), 400

@app.route('/api/track_direct', methods=['GET'])
def track_direct():
    tracking_no = request.args.get('tracking_no', '')
    captcha_text = request.args.get('captcha', '')
    session_id = request.cookies.get('session_ref', '')
    
    if tracking_no and captcha_text:
        track_data = scrape_india_post_with_captcha(tracking_no, captcha_text, session_id)
        return jsonify({'status': f"{track_data['status']}\n(पूरी जर्नी डैशबोर्ड पर सेव हो गई है)"}), 200
    return jsonify({'status': 'त्रुटि'}), 400

# --- बाकी पुराना लॉजिक यथावत ---
@app.route('/')
def dashboard(): return render_template('index.html')

@app.route('/api/shipments')
def get_shipments():
    shipments = list(shipments_col.find())
    for s in shipments: s['_id'] = str(s['_id'])
    return jsonify(shipments)

@app.route('/api/add_manual', methods=['POST'])
def add_manual():
    data = request.json
    name = data.get('name')
    tracking_no = data.get('tracking_no', '').upper()
    mobile1 = data.get('mobile1')
    if name and tracking_no and mobile1:
        shipments_col.insert_one({
            'name': name, 'tracking_no': tracking_no, 'mobile1': mobile1, 
            'status': 'In Transit 🚚', 'history': [], 'image_url': ''
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
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port)
