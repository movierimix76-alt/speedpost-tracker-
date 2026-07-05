import os
import time
import telebot
# यहाँ पर हम बिल्कुल नए क्लाइंट का उपयोग कर रहे हैं
from google import genai
from google.genai import types

# --- कॉन्फ़िगरेशन ---
BOT_TOKEN = os.environ.get("BOT_TOKEN", "यहाँ_अपना_बॉट_टोकन_डालें")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "यहाँ_अपनी_GEMINI_API_KEY_डालें")

# बॉट और न्यू गूगल क्लाइंट को सेटअप करें
bot = telebot.TeleBot(BOT_TOKEN)
client = genai.Client(api_key=GEMINI_API_KEY)

user_sessions = {}

@bot.message_handler(commands=['start'])
def send_welcome(message):
    bot.reply_to(message, "नमस्ते! ज्वेलरी वीडियो एडिटिंग बॉट में आपका स्वागत है। ✨\n\nकृपया वह वीडियो भेजें जिसे आप एडिट करना चाहते हैं।")

@bot.message_handler(content_types=['video'])
def handle_video(message):
    chat_id = message.chat.id
    bot.reply_to(message, "⏳ वीडियो मिल रहा है, कृपया प्रतीक्षा करें...")
    
    try:
        file_info = bot.get_file(message.video.file_id)
        downloaded_file = bot.download_file(file_info.file_path)
        
        local_filename = f"video_{chat_id}_{int(time.time())}.mp4"
        with open(local_filename, 'wb') as new_file:
            new_file.write(downloaded_file)
            
        user_sessions[chat_id] = {'video_path': local_filename}
        
        bot.send_message(chat_id, "✅ वीडियो सफलतापूर्वक अपलोड हो गया है!\n\nअब आप जो बदलाव करना चाहते हैं, उसे प्रॉम्ट (कमांड) के रूप में टाइप करके भेजें।")
        
    except Exception as e:
        bot.send_message(chat_id, f"❌ वीडियो प्राप्त करने में एरर आया: {str(e)}")

@bot.message_handler(func=lambda message: True, content_types=['text'])
def handle_prompt(message):
    chat_id = message.chat.id
    prompt_text = message.text
    
    if chat_id not in user_sessions or 'video_path' not in user_sessions[chat_id]:
        bot.reply_to(message, "⚠️ कृपया प्रॉम्ट लिखने से पहले एक वीडियो भेजें।")
        return
        
    local_video_path = user_sessions[chat_id]['video_path']
    status_msg = bot.reply_to(message, "🚀 Omni Flash AI आपका वीडियो प्रोसेस कर रहा है... इसमें थोड़ा समय लग सकता है।")
    
    try:
        # नए तरीके से वीडियो को गूगल सर्वर पर अपलोड करना
        print(f"गूगल सर्वर पर अपलोड हो रहा है: {local_video_path}")
        google_video_file = client.files.upload(file=local_video_path)
        
        # प्रोसेसिंग का इंतजार करना
        while google_video_file.state.name == "PROCESSING":
            time.sleep(4)
            google_video_file = client.files.get(name=google_video_file.name)
            
        if google_video_file.state.name == "FAILED":
            raise ValueError("गूगल सर्ver पर वीडियो प्रोसेस नहीं हो पाया।")
            
        # नए सिंटैक्स के साथ Omni Flash मॉडल को कॉल करना
        response = client.models.generate_content(
            model='gemini-omni-flash-preview',
            contents=[google_video_file, prompt_text]
        )
        
        output_filename = f"edited_{chat_id}.mp4"
        
        # नए क्लाइंट में सीधा जनरेटेड डेटा डाउनलोड करने का तरीका
        if response.generated_bytes:
            with open(output_filename, "wb") as f:
                f.write(response.generated_bytes)
                
            with open(output_filename, 'rb') as video_to_send:
                bot.send_video(chat_id, video_to_send, caption="✨ Omni Flash द्वारा एडिट किया गया वीडियो!")
                
            if os.path.exists(output_filename):
                os.remove(output_filename)
        else:
            bot.send_message(chat_id, f"🤖 मॉडल का जवाब:\n{response.text}")

    except Exception as e:
        bot.send_message(chat_id, f"❌ प्रोसेसिंग के दौरान त्रुटि आई: {str(e)}")
        
    finally:
        if os.path.exists(local_video_path):
            os.remove(local_video_path)
        if chat_id in user_sessions:
            del user_sessions[chat_id]
        try:
            bot.delete_message(chat_id, status_msg.message_id)
        except:
            pass

print("बॉट सफलता पूर्वक चालू हो गया है...")
bot.infinity_polling()
