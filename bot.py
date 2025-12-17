import os
import json
import logging
import jdatetime
from flask import Flask
from threading import Thread
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes
import gspread
from oauth2client.service_account import ServiceAccountCredentials

# 1. --- KEEP ALIVE SERVER FOR UPTIMEROBOT ---
app = Flask('')

@app.route('/')
def home():
    return "Bot is alive!"

def run():
    app.run(host='0.0.0.0', port=8080)

def keep_alive():
    t = Thread(target=run)
    t.start()

# 2. --- GOOGLE SHEETS SETUP ---
def get_gsheet():
    scope = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
    # We pull the JSON from Replit Secrets (Environment Variables)
    creds_dict = json.loads(os.environ['GCP_SERVICE_ACCOUNT'])
    creds = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(creds)
    # Uses the Sheet ID from your secret
    return client.open_by_key(os.environ['SHEET_ID']).get_worksheet(0)

# 3. --- CORE BOT LOGIC ---
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    user = update.effective_user
    
    # Simple logic to save data (you can keep your regex logic from your original file)
    try:
        sheet = get_gsheet()
        now = jdatetime.datetime.now()
        
        row_data = [
            str(now),                 # Date
            user.full_name,          # Member Name
            user.id,                 # User ID
            user_text                # The Message Content
        ]
        
        sheet.append_row(row_data)
        await update.message.reply_text("✅ Data saved to Google Sheets!")
    except Exception as e:
        logging.error(f"Error: {e}")
        await update.message.reply_text("❌ Failed to save data.")

# 4. --- MAIN EXECUTION ---
if __name__ == '__main__':
    logging.basicConfig(level=logging.INFO)
    
    # Start the web server for UptimeRobot
    keep_alive()
    
    # Initialize Bot using Secret Token
    TOKEN = os.environ['BOT_TOKEN']
    application = Application.builder().token(TOKEN).build()
    
    # Add handler for text messages
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    print("Bot is running and web server is active...")
    application.run_polling()
