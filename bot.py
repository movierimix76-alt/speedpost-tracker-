import os
import time
import telebot
from google import genai
from google.genai import types
from flask import Flask
from threading import Thread

# --- कॉन्फ़िगरेशन (Render के Environment Variables से ऑटोमैटिक लोड होगा) ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "यहाँ_अपना_बॉट_टोकन_डालें_अगर_लोकल_टेस्ट_कर_रहे_हैं")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "यहाँ_अपनी_GEMINI_API_KEY_डालें")

# बॉट और न्यू गूगल क्लाइंट को सेटअप करें
bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

# यूजर्स का डेटा सेव रखने के लिए एक टेम्परेरी डिक्शनरी
user_sessions = {}

# --- Render Port Fix (यह Render को 'No open ports' वाला एरर देने से रोकेगा) ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive and running!"

def run_port():
    # Render खुद 'PORT' एनवायरनमेंट वेरिएबल देता है, डिफ़ॉल्ट 10000 रखें
    port = int(os.environ.get("PORT", 10000))
    app.run(host='0.0.0.0', port=port)

# --- टेलीग्राम बॉट लॉजिक ---

# /start कमांड का जवाब
@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "नमस्ते! ज्वेलरी वीडियो एडिटिंग बॉट में आपका स्वागत है। ✨\n\nकृपया वह वीडियो भेजें जिसे आप एडिट करना चाहते हैं।")

# जब यूजर वीडियो भेजता है
@bot.message_handler(content_types=['video'])
def handle_video(message):
    chat_id = message.chat.id
    bot.reply_to(message, "⏳ वीडियो मिल रहा है, कृपया प्रतीक्षा करें...")
    
    try:
        # टेलीग्राम से वीडियो फाइल की जानकारी निकालना
        file_info = bot.get_file(message.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        # वीडियो को Render सर्वर पर अस्थाई रूप से सेव करना
        local_filename = f"video_{chat_id}_{int(time.time())}.mp4"
        with open(local_filename, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        # यूजर के सेशन में वीडियो का नाम सुरक्षित करना
        user_sessions[chat_id] = {'video_path': local_filename}
        
        # यूजर से प्रॉम्ट मांगना
        bot.send_message(chat_id, "✅ वीडियो सफलतापूर्वक अपलोड हो गया है!\n\nअब आप जो बदलाव करना चाहते हैं, उसे प्रॉम्ट (कमांड) के रूप में टाइप करके भेजें।\n(उदाहरण: 'इसका बैकग्राउंड बदलकर एक रॉयल शोरूम जैसा कर दो और लाइट बढ़ा दो')")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ वीडियो प्राप्त करने में एरर आया: {str(e)}")

# 当 यूजर प्रॉम्ट (टेक्स्ट मैसेज) भेजता है
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_prompt(message):
    chat_id = message.chat.id
    prompt_text = message.text
    
    # चेक करना कि यूजर ने पहले वीडियो भेजा है या नहीं
    if chat_id not in user_sessions or 'video_path' not in user_sessions[chat_id]:
        bot.reply_to(message, "⚠️ कृपया प्रॉम्ट लिखने से पहले एक वीडियो भेजें।")
        return
        
    local_video_path = user_sessions[chat_id]['video_path']
    status_msg = bot.reply_to(message, "🚀 Omni Flash AI आपका वीडियो प्रोसेस कर रहा है... इसमें थोड़ा समय लग सकता है।")
    
    try:
        # 1. वीडियो को Google File API पर अपलोड करना
        print(f"गूगल सर्वर पर अपलोड हो रहा है: {local_video_path}")
        google_video_file = client.files.upload(file=local_video_path)
        
        # 2. वीडियो प्रोसेसिंग पूरी होने का इंतजार करना
        while google_video_file.state.name == "PROCESSING":
            time.sleep(4)
            google_video_file = client.files.get(name=google_video_file.name)
            
        if google_video_file.state.name == "FAILED":
            raise ValueError("गूगल सर्वर पर वीडियो प्रोसेस नहीं हो पाया।")
            
        # 3. Omni Flash मॉडल को कॉल करना
        response = client.models.generate_content(
            model='gemini-omni-flash-preview',
            contents=[google_video_file, prompt_text]
        )
        
        output_filename = f"edited_{chat_id}.mp4"
        
        # 4. आउटपुट प्राप्त करना और उसे सुरक्षित रूप से सेव करना
        # नए क्लाइंट में सीधा जनरेटेड डेटा डाउनलोड करने का तरीका
        if hasattr(response, 'generated_bytes') and response.generated_bytes:
            with open(output_filename, "wb") as f:
                f.write(response.generated_bytes)
                
            # यूजर को एडिटेड वीडियो वापस भेजना
            with open(output_filename, 'rb') as video_to_send:
                bot.send_video(chat_id, video_to_send, caption="✨ Omni Flash द्वारा एडिट किया गया वीडियो!")
                
            # आउटपुट फाइल डिलीट करना
            if os.path.exists(output_filename):
                os.remove(output_filename)
        else:
            # अगर मॉडल सीधा वीडियो न देकर कोई टेक्स्ट सुझाव या एरर दे
            bot.send_message(chat_id, f"🤖 मॉडल का जवाब:\n{response.text}")

    except Exception as e:
        bot.send_message(chat_id, f"❌ प्रोसेसिंग के दौरान त्रुटि आई: {str(e)}")
        
    finally:
        # --- सर्वर स्पेस मैनेजमेंट ---
        # काम खत्म होने के बाद Render सर्वर से ओरिजिनल वीडियो तुरंत डिलीट कर देना
        if os.path.exists(local_video_path):
            os.remove(local_video_path)
        # सेशन क्लियर करना ताकि नया वीडियो भेजा जा सके
        if chat_id in user_sessions:
            del user_sessions[chat_id]
        # अस्थाई स्टेटस मैसेज डिलीट करना
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except:
            pass

# मुख्य फ़ंक्शन जो पोर्ट और बॉट दोनों को एक साथ चालू रखेगा
if __name__ == "__main__":
    # पोर्ट सर्विस को अलग थ्रेड में चालू करें ताकि बॉट का पोलिंग सिस्टम न रुके
    t = Thread(target=run_port)
    t.start()
    
    print("बॉट सफलता पूर्वक चालू हो गया है और पोर्ट एक्टिव है...")
    bot.infinity_polling()
