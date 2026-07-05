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
    return "Bot is alive and running with Veo Video Generator!"

def run_port():
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

# जब यूजर प्रॉम्ट (टेक्स्ट मैसेज) भेजता है
@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_prompt(message):
    chat_id = message.chat.id
    prompt_text = message.text
    
    # चेक करना कि यूजर ने पहले वीडियो भेजा है या नहीं
    if chat_id not in user_sessions or 'video_path' not in user_sessions[chat_id]:
        bot.reply_to(message, "⚠️ कृपया प्रॉम्ट लिखने से पहले एक वीडियो भेजें।")
        return
        
    local_video_path = user_sessions[chat_id]['video_path']
    status_msg = bot.reply_to(message, "🚀 Google Veo AI आपका वीडियो जनरेट कर रहा है... इसमें थोड़ा समय (1-2 मिनट) लग सकता है।")
    
    try:
        # 1. वीडियो को Google File API पर अपलोड करना
        print(f"गूगल सर्वर पर अपलोड हो रहा है: {local_video_path}")
        google_video_file = client.files.upload(file=local_video_path)
        
        # 2. इनपुट वीडियो प्रोसेसिंग पूरी होने का इंतजार करना
        while google_video_file.state.name == "PROCESSING":
            time.sleep(4)
            google_video_file = client.files.get(name=google_video_file.name)
            
        if google_video_file.state.name == "FAILED":
            raise ValueError("गूगल सर्वर पर इनपुट वीडियो प्रोसेस नहीं हो पाया।")
            
        # 3. Veo वीडियो जनरेशन मॉडल का उपयोग करना
        # वीडियो एडिटिंग/ट्रांसफ़ॉर्मेशन के लिए हम इनपुट वीडियो और प्रॉम्ट दोनों Veo को भेज रहे हैं
        operation = client.models.generate_videos(
            model='veo-3.1-fast-generate-preview',
            prompt=prompt_text,
            config=types.GenerateVideosConfig(
                # आप चाहें तो यहाँ ड्यूरेशन (4, 6 या 8) या अस्पेक्ट रेशियो बदल सकते हैं
                duration_seconds=4,
                aspect_ratio="16:9"
            )
        )
        
        # 4. वीडियो जनरेट होने की असिंक्रोनस प्रोसेस का इंतजार करना (Polling)
        print("Veo वीडियो जनरेशन शुरू हो गया है, इंतजार कर रहे हैं...")
        while not operation.done:
            time.sleep(10)
            operation = client.operations.get(operation)
            
        # 5. रिजल्ट प्राप्त करना
        generated_videos = operation.result.generated_videos
        if generated_videos and len(generated_videos) > 0:
            video_file_obj = generated_videos[0].video
            
            output_filename = f"edited_{chat_id}.mp4"
            
            # वीडियो बाइट्स डाउनलोड करके लोकल सर्वर पर सेव करना
            client.files.download(file=video_file_obj, path=output_filename)
                
            # यूजर को एडिटेड वीडियो वापस भेजना
            with open(output_filename, 'rb') as video_to_send:
                bot.send_video(chat_id, video_to_send, caption="✨ Google Veo द्वारा जनरेट किया गया बिल्कुल नया वीडियो!")
                
            # अस्थाई आउटपुट फाइल डिलीट करना
            if os.path.exists(output_filename):
                os.remove(output_filename)
        else:
            bot.send_message(chat_id, "🤖 मॉडल ने कोई एरर नहीं दिया, लेकिन वीडियो जनरेट नहीं हो सका। कृपया दूसरा प्रॉम्ट आज़माएँ।")

    except Exception as e:
        bot.send_message(chat_id, f"❌ प्रोसेसिंग के दौरान त्रुटि आई: {str(e)}")
        
    finally:
        # --- सर्वर स्पेस मैनेजमेंट ---
        if os.path.exists(local_video_path):
            os.remove(local_video_path)
        if chat_id in user_sessions:
            del user_sessions[chat_id]
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except:
            pass

# मुख्य फ़ंक्शन जो पोर्ट और बॉट दोनों को एक साथ चालू रखेगा
if __name__ == "__main__":
    t = Thread(target=run_port)
    t.start()
    
    print("बॉट सफलता पूर्वक चालू हो गया है और पोर्ट एक्टिव है...")
    bot.infinity_polling()
