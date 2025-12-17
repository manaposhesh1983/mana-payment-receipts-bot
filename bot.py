import os
import re
import json
import jdatetime
import logging
from datetime import datetime
from telegram import Update
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import gspread
from google.oauth2.service_account import Credentials
from flask import Flask, render_template
import threading
import requests

# ================= CONFIG =================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8219133063:AAEHYeKF4J-V2PTgJaOyuufBl2gFIGz9wBE')

# Google Sheets setup
GOOGLE_SHEETS_CREDENTIALS = os.environ.get('GOOGLE_SHEETS_CREDENTIALS', '{}')
GOOGLE_SHEET_ID = os.environ.get('GOOGLE_SHEET_ID', '')
GOOGLE_SHEET_NAME = os.environ.get('GOOGLE_SHEET_NAME', 'Receipts')

# Setup logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Initialize Flask app for web server
app = Flask(__name__)

@app.route('/')
def home():
    return render_template('index.html') if os.path.exists('templates/index.html') else "🤖 Receipts Bot is running!"

@app.route('/health')
def health():
    return "✅ Bot is healthy", 200

@app.route('/ping')
def ping():
    return "🏓 Pong!", 200

# Add this to ping yourself every 5 minutes (to prevent sleep)
def keep_alive():
    """Ping the bot itself every 5 minutes"""
    import time
    while True:
        try:
            # Get Replit URL
            repl_url = os.environ.get('REPLIT_DB_URL', '').replace('kv.replit.com', '')
            if repl_url:
                full_url = f"https://{repl_url.split('/')[2]}.id.repl.co/ping"
                requests.get(full_url, timeout=10)
                logger.info("Self-ping to prevent sleep")
        except:
            pass
        time.sleep(300)  # 5 minutes

# ================= GOOGLE SHEETS FUNCTIONS =================
def init_google_sheets():
    try:
        creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive']
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME)
        
        columns = ['row_number', 'gregorian_date', 'jalali_date', 'member_name', 
                  'user_id', 'project_name', 'fee', 'reason', 'source_type', 
                  'status', 'chat_id', 'message_id', 'is_deleted', 'telegram_file_id']
        
        existing_headers = worksheet.row_values(1)
        if not existing_headers or existing_headers[0] != 'row_number':
            worksheet.clear()
            worksheet.append_row(columns)
            logger.info("Created Google Sheet headers")
        
        return worksheet
    except Exception as e:
        logger.error(f"Google Sheets error: {e}")
        return None

worksheet = init_google_sheets()

# ================= SIMPLIFIED BOT FUNCTIONS =================
def convert_persian_number(text):
    persian_nums = {'۰':'0','۱':'1','۲':'2','۳':'3','۴':'4','۵':'5','۶':'6','۷':'7','۸':'8','۹':'9','٠':'0','١':'1','٢':'2','٣':'3','٤':'4','٥':'5','٦':'6','٧':'7','٨':'8','٩':'9'}
    for persian_num, western_num in persian_nums.items():
        text = text.replace(persian_num, western_num)
    return text

def parse_fee(fee_text):
    if not fee_text: return ('', '')
    original = fee_text
    fee_text = convert_persian_number(fee_text).replace(',', '').replace('،', '').strip()
    
    multiplier = 1
    if 'هزار' in fee_text:
        multiplier = 1000
        fee_text = fee_text.replace('هزار', '').strip()
    elif 'میلیون' in fee_text or 'ملیون' in fee_text:
        multiplier = 1000000
        fee_text = fee_text.replace('میلیون', '').replace('ملیون', '').strip()
    
    match = re.search(r'[\d\.]+', fee_text)
    if not match: return ('', original)
    
    try:
        fee_value = float(match.group()) if '.' in match.group() else int(match.group())
        fee_value = fee_value * multiplier
        if 'تومن' in original or 'تومان' in original:
            fee_value = fee_value * 10
        return (str(int(fee_value)), original)
    except:
        return ('', original)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "🤖 **ربات مدیریت رسیدها**\n\n"
        "📸 **ارسال عکس رسید**\n"
        "💬 **ارسال متن به فرمت:**\n"
        "پروژه: نام پروژه\nمبلغ: ۱۰۰۰۰۰ تومان\nتوضیحات: ...\n\n"
        "📊 **دستورات:**\n"
        "/start - راهنما\n"
        "/status - وضعیت\n"
        "/list - آخرین رکوردها"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.effective_message
        user = msg.from_user
        chat = msg.chat
        
        row_num = 1
        if worksheet:
            try:
                last_rows = worksheet.col_values(1)
                if len(last_rows) > 1:
                    row_num = int(last_rows[-1]) + 1
            except:
                pass
        
        greg_dt = msg.date
        jalali_dt = jdatetime.datetime.fromgregorian(datetime=greg_dt)
        
        parsed_data = {'project_name': '', 'fee': '', 'reason': ''}
        if msg.caption:
            # Simple parsing
            if 'پروژه' in msg.caption:
                parsed_data['project_name'] = msg.caption.split('پروژه:')[-1].split('\n')[0].strip()[:50]
            if 'مبلغ' in msg.caption:
                fee_match = re.search(r'مبلغ[:\s]*([\d\s,\.]+)', msg.caption)
                if fee_match:
                    parsed_data['fee'], _ = parse_fee(fee_match.group(1))
            parsed_data['reason'] = msg.caption[:100]
        
        row_data = [
            str(row_num),
            greg_dt.strftime('%Y-%m-%d %H:%M:%S'),
            jalali_dt.strftime('%Y/%m/%d %H:%M:%S'),
            user.full_name or str(user.id),
            str(user.id),
            parsed_data['project_name'],
            parsed_data['fee'],
            parsed_data['reason'] or (msg.caption[:100] if msg.caption else 'عکس'),
            'photo',
            'completed' if parsed_data['project_name'] or parsed_data['fee'] else 'pending',
            str(chat.id),
            str(msg.message_id),
            'False',
            msg.photo[-1].file_id if msg.photo else ''
        ]
        
        if worksheet:
            worksheet.append_row(row_data)
        
        await msg.reply_text(f"✅ عکس ذخیره شد\n📁 شماره: {row_num}\n📅 {jalali_dt.strftime('%Y/%m/%d')}")
        
    except Exception as e:
        logger.error(f"Photo error: {e}")
        await update.effective_message.reply_text("❌ خطا در ذخیره عکس")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        msg = update.effective_message
        user = msg.from_user
        chat = msg.chat
        text = msg.text.strip()
        
        if text.startswith('/'):
            return
        
        row_num = 1
        if worksheet:
            try:
                last_rows = worksheet.col_values(1)
                if len(last_rows) > 1:
                    row_num = int(last_rows[-1]) + 1
            except:
                pass
        
        greg_dt = msg.date
        jalali_dt = jdatetime.datetime.fromgregorian(datetime=greg_dt)
        
        # Simple parsing
        project = ''
        fee = ''
        reason = ''
        
        if 'پروژه:' in text:
            project = text.split('پروژه:')[-1].split('\n')[0].strip()[:50]
        if 'مبلغ:' in text:
            fee_match = re.search(r'مبلغ[:\s]*([\d\s,\.]+)', text)
            if fee_match:
                fee, _ = parse_fee(fee_match.group(1))
        if 'توضیحات:' in text:
            reason = text.split('توضیحات:')[-1].strip()[:200]
        else:
            reason = text[:200]
        
        row_data = [
            str(row_num),
            greg_dt.strftime('%Y-%m-%d %H:%M:%S'),
            jalali_dt.strftime('%Y/%m/%d %H:%M:%S'),
            user.full_name or str(user.id),
            str(user.id),
            project,
            fee,
            reason,
            'text',
            'completed',
            str(chat.id),
            str(msg.message_id),
            'False',
            ''
        ]
        
        if worksheet:
            worksheet.append_row(row_data)
        
        await msg.reply_text(f"✅ متن ذخیره شد\n📁 شماره: {row_num}\n📅 {jalali_dt.strftime('%Y/%m/%d')}")
        
    except Exception as e:
        logger.error(f"Text error: {e}")
        await update.effective_message.reply_text("❌ خطا در ذخیره متن")

async def list_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        response = "📋 **آخرین رکوردها:**\n\n"
        
        if worksheet:
            data = worksheet.get_all_values()
            if len(data) > 1:
                recent = data[-10:] if len(data) > 10 else data[1:]
                for row in recent[-5:]:  # Show last 5
                    if len(row) >= 8:
                        response += f"#{row[0]} | {row[5][:15]} | {row[6]} ریال | {row[2][:10]}\n"
                response += f"\n📊 کل: {len(data)-1} رکورد"
            else:
                response = "📭 هیچ رکوردی وجود ندارد."
        else:
            response = "❌ اتصال به Google Sheets برقرار نیست."
        
        await update.message.reply_text(response, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text("❌ خطا در نمایش لیست")

# ================= RUN BOT =================
def run_flask():
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def run_bot():
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("list", list_records))
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        
        logger.info("Starting Telegram bot...")
        application.run_polling()
    except Exception as e:
        logger.error(f"Bot error: {e}")

def main():
    print("🤖 Receipts Bot Starting...")
    
    # Start keep-alive thread
    keep_alive_thread = threading.Thread(target=keep_alive, daemon=True)
    keep_alive_thread.start()
    
    # Start Flask in separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    
    # Run bot in main thread
    run_bot()

if __name__ == '__main__':
    main()
