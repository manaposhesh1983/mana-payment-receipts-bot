import asyncio
import os
import re
import json
import pandas as pd
import jdatetime
import logging
from datetime import datetime, timedelta
from telegram import Update, Message, ReplyParameters
from telegram.ext import Application, MessageHandler, filters, ContextTypes, CommandHandler
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from google.oauth2.service_account import Credentials
import requests
from flask import Flask
import threading

# ================= CONFIG =================
BOT_TOKEN = os.environ.get('BOT_TOKEN', '8219133063:AAEHYeKF4J-V2PTgJaOyuufBl2gFIGz9wBE')
RENDER_APP_URL = os.environ.get('RENDER_APP_URL', '')  # Will be set on Render

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

# Initialize Flask app for web server (for Render)
app = Flask(__name__)

@app.route('/')
def home():
    return "🤖 Receipts Bot is running!"

@app.route('/health')
def health():
    return "✅ Bot is healthy", 200

@app.route('/ping')
def ping():
    return "🏓 Pong!", 200

# Define allowed users (optional)
ALLOWED_USERS = []

def check_user_access(user_id):
    """Check if user is allowed to use the bot"""
    if not ALLOWED_USERS:  # If list is empty, allow all users
        return True
    return user_id in ALLOWED_USERS

# ================= GOOGLE SHEETS FUNCTIONS =================

def init_google_sheets():
    """Initialize Google Sheets connection"""
    try:
        # Parse credentials from environment variable
        creds_dict = json.loads(GOOGLE_SHEETS_CREDENTIALS)
        
        # Use service account credentials
        scope = ['https://spreadsheets.google.com/feeds',
                'https://www.googleapis.com/auth/drive']
        
        creds = Credentials.from_service_account_info(creds_dict, scopes=scope)
        client = gspread.authorize(creds)
        
        # Open the spreadsheet
        sheet = client.open_by_key(GOOGLE_SHEET_ID)
        worksheet = sheet.worksheet(GOOGLE_SHEET_NAME)
        
        # Define all columns
        columns = [
            'row_number', 'gregorian_date', 'jalali_date',
            'member_name', 'user_id', 'project_name', 'fee', 'reason',
            'source_type', 'file_name', 'status', 'chat_id',
            'message_id', 'related_message_id', 'is_deleted',
            'processed_date', 'offline_saved', 'transcribed_text',
            'photo_message_id', 'text_message_id', 'voice_message_id',
            'voice_file_name',
            'is_combined', 'highlight', 'pending_age', 'requires_attention',
            'last_updated', 'reply_count', 'reply_message_ids',
            'telegram_file_id', 'voice_file_id'  # Store Telegram file IDs instead of local files
        ]
        
        # Check if headers exist, if not create them
        existing_headers = worksheet.row_values(1)
        if not existing_headers or existing_headers[0] != 'row_number':
            worksheet.clear()
            worksheet.append_row(columns)
            logger.info(f"Created Google Sheet with {len(columns)} columns")
        else:
            logger.info(f"Google Sheet exists with {len(existing_headers)} columns")
        
        return worksheet, client
        
    except Exception as e:
        logger.error(f"Error initializing Google Sheets: {e}")
        # Try to create a local backup if Google Sheets fails
        logger.info("Google Sheets failed, using local storage as backup")
        return None, None

# Initialize Google Sheets
worksheet, gs_client = init_google_sheets()

def get_next_row_number():
    """Get next available row number from Google Sheet"""
    try:
        if worksheet:
            # Get all values in column A (row numbers)
            row_numbers = worksheet.col_values(1)
            if len(row_numbers) <= 1:  # Only header
                return 1
            
            # Find max row number (skip header)
            max_num = 0
            for i in range(1, len(row_numbers)):
                try:
                    num = int(row_numbers[i])
                    if num > max_num:
                        max_num = num
                except:
                    continue
            
            return max_num + 1
        else:
            # Fallback: use a simple counter file
            counter_file = 'row_counter.txt'
            if os.path.exists(counter_file):
                with open(counter_file, 'r') as f:
                    counter = int(f.read().strip())
            else:
                counter = 0
            
            counter += 1
            with open(counter_file, 'w') as f:
                f.write(str(counter))
            return counter
            
    except Exception as e:
        logger.error(f"Error getting next row number: {e}")
        return 1

def save_to_sheet(row_data):
    """Save row data to Google Sheet"""
    try:
        if worksheet:
            # Convert row_data dict to list in correct column order
            row_list = []
            
            # Get headers
            headers = worksheet.row_values(1)
            
            # Create row in correct order
            for header in headers:
                row_list.append(row_data.get(header, ''))
            
            # Append to sheet
            worksheet.append_row(row_list)
            logger.info(f"Saved row {row_data.get('row_number', 'N/A')} to Google Sheet")
            return True
        else:
            # Fallback to local CSV
            csv_file = 'receipts_backup.csv'
            df = pd.DataFrame([row_data])
            
            if os.path.exists(csv_file):
                existing_df = pd.read_csv(csv_file)
                df = pd.concat([existing_df, df], ignore_index=True)
            
            df.to_csv(csv_file, index=False)
            logger.info(f"Saved row {row_data.get('row_number', 'N/A')} to backup CSV")
            return True
            
    except Exception as e:
        logger.error(f"Error saving to sheet: {e}")
        return False

def update_sheet_row(row_num, update_data):
    """Update existing row in Google Sheet"""
    try:
        if worksheet:
            # Find the row
            row_numbers = worksheet.col_values(1)
            
            for i, cell_value in enumerate(row_numbers, start=1):
                if str(cell_value) == str(row_num):
                    row_idx = i
                    # Get current row values
                    row_values = worksheet.row_values(row_idx)
                    
                    # Update with new values
                    headers = worksheet.row_values(1)
                    
                    for key, value in update_data.items():
                        if key in headers:
                            col_idx = headers.index(key) + 1  # 1-indexed
                            worksheet.update_cell(row_idx, col_idx, value)
                    
                    logger.info(f"Updated row {row_num} in Google Sheet")
                    return True
            
            logger.warning(f"Row {row_num} not found in Google Sheet")
            return False
        else:
            # Fallback to local CSV
            csv_file = 'receipts_backup.csv'
            if os.path.exists(csv_file):
                df = pd.read_csv(csv_file)
                mask = df['row_number'].astype(str) == str(row_num)
                if mask.any():
                    idx = df[mask].index[0]
                    for key, value in update_data.items():
                        if key in df.columns:
                            df.at[idx, key] = value
                    df.to_csv(csv_file, index=False)
                    return True
            return False
            
    except Exception as e:
        logger.error(f"Error updating sheet row: {e}")
        return False

def get_row_by_message_id(chat_id, message_id):
    """Find sheet row by chat_id and message_id"""
    try:
        if worksheet:
            # Get all data
            data = worksheet.get_all_records()
            
            for row in data:
                if (str(row.get('chat_id', '')) == str(chat_id) and 
                    (str(row.get('message_id', '')) == str(message_id) or 
                     str(row.get('related_message_id', '')) == str(message_id) or
                     str(row.get('photo_message_id', '')) == str(message_id) or
                     str(row.get('text_message_id', '')) == str(message_id))):
                    return row.get('row_number'), row
            
        else:
            # Fallback to local CSV
            csv_file = 'receipts_backup.csv'
            if os.path.exists(csv_file):
                df = pd.read_csv(csv_file)
                mask = ((df['chat_id'] == str(chat_id)) & 
                       ((df['message_id'] == str(message_id)) | 
                        (df['related_message_id'] == str(message_id)) |
                        (df['photo_message_id'] == str(message_id)) |
                        (df['text_message_id'] == str(message_id))))
                
                if mask.any():
                    idx = df[mask].index[0]
                    return df.at[idx, 'row_number'], df.loc[idx].to_dict()
        
        return None, None
        
    except Exception as e:
        logger.error(f"Error getting row by message_id: {e}")
        return None, None

# ================= HELPER FUNCTIONS (Keep these) =================

def convert_persian_number(text):
    """Convert Persian/Arabic numerals to Western numerals"""
    persian_nums = {
        '۰': '0', '۱': '1', '۲': '2', '۳': '3', 
        '۴': '4', '۵': '5', '۶': '6', '۷': '7', '۸': '8', '۹': '9',
        '٠': '0', '١': '1', '٢': '2', '٣': '3', '٤': '4',
        '٥': '5', '٦': '6', '٧': '7', '٨': '8', '٩': '9'
    }
    
    for persian_num, western_num in persian_nums.items():
        text = text.replace(persian_num, western_num)
    
    return text

def parse_and_convert_fee(fee_text):
    """
    Parse fee text and convert to ریال
    Returns: tuple of (fee_in_rial, original_text)
    """
    if not fee_text:
        return ('', '')
    
    original_text = fee_text
    
    # Convert Persian numbers to Western
    fee_text = convert_persian_number(fee_text)
    
    # Remove commas and other separators
    fee_text = fee_text.replace(',', '').replace('،', '').strip()
    
    # Initialize multiplier for هزار and میلیون
    multiplier = 1
    
    # Check for هزار
    if 'هزار' in fee_text:
        multiplier = 1000
        fee_text = fee_text.replace('هزار', '').strip()
    
    # Check for میلیون - also accept ملیون
    elif 'میلیون' in fee_text or 'ملیون' in fee_text:
        multiplier = 1000000
        fee_text = fee_text.replace('میلیون', '').replace('ملیون', '').strip()
    
    # Extract numeric part
    numeric_match = re.search(r'[\d\.]+', fee_text)
    if not numeric_match:
        return ('', original_text)
    
    numeric_part = numeric_match.group()
    
    try:
        # Convert to float then to int
        if '.' in numeric_part:
            fee_value = float(numeric_part)
        else:
            fee_value = int(numeric_part)
        
        # Apply multiplier for هزار/میلیون
        fee_value = fee_value * multiplier
        
        # Check currency and convert if needed
        if 'تومن' in original_text or 'تومان' in original_text:
            # Convert تومن/تومان to ریال (multiply by 10)
            fee_value = fee_value * 10
        
        # Return as string
        return (str(int(fee_value)), original_text)
    
    except (ValueError, TypeError):
        return ('', original_text)

def parse_persian_text(text):
    """Extract project_name, fee, and reason from Persian text"""
    # [Keep the same parse_persian_text function from your original code]
    # [I'm truncating here for brevity, but use your existing function]
    # Initialize with empty values
    project_name = ''
    fee = ''
    fee_original = ''
    reason = ''
    
    if not text or not isinstance(text, str):
        return {'project_name': '', 'fee': '', 'reason': '', 'fee_original': ''}
    
    # [Your existing parse_persian_text logic here...]
    
    return {
        'project_name': project_name,
        'fee': fee,
        'reason': reason,
        'fee_original': fee_original
    }

# ================= TELEGRAM BOT HANDLERS =================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    user = update.effective_user
    
    if not check_user_access(user.id):
        await update.message.reply_text(
            "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
            parse_mode='Markdown'
        )
        return
    
    help_text = (
        "🤖 **ربات مدیریت رسیدها (نسخه ابری)**\n\n"
        "📝 **نحوه استفاده:**\n"
        "1. عکس رسید را ارسال کنید\n"
        "2. اطلاعات را به فرمت زیر ارسال نمایید:\n"
        "```\n"
        "پروژه: طراحی وب\n"
        "مبلغ: ۲۵۰۰۰۰۰ تومان\n"
        "توضیحات: خرید هاست و دامنه\n"
        "```\n\n"
        "💰 **پشتیبانی از ارزها:**\n"
        "• تومان/تومن: خودکار به ریال تبدیل می‌شود (×10)\n"
        "• ریال: مستقیم ذخیره می‌شود\n"
        "• هزار/میلیون/ملیون: پشتیبانی کامل\n\n"
        "**دستورات:**\n"
        "/start - نمایش راهنما\n"
        "/status - وضعیت سیستم\n"
        "/list - نمایش آخرین رکوردها\n"
        "/delete <شماره> - حذف رکورد\n"
    )
    
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle photo messages - Store file_id instead of downloading"""
    try:
        if update.edited_message:
            return
            
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        
        if not check_user_access(user.id):
            await update.message.reply_text(
                "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
                parse_mode='Markdown'
            )
            return
        
        logger.info(f"Photo from {user.full_name}, message_id: {msg.message_id}")
        
        # Get current date/time
        greg_dt = msg.date
        jalali_dt = jdatetime.datetime.fromgregorian(datetime=greg_dt)
        greg_str = greg_dt.strftime('%Y-%m-%d %H:%M:%S')
        jalali_str = jalali_dt.strftime('%Y/%m/%d %H:%M:%S')
        
        # Get next row number
        row_num = get_next_row_number()
        
        # Get file_id (we don't download the file)
        file_id = msg.photo[-1].file_id  # Get highest resolution photo
        
        # Parse caption if exists
        parsed_data = {'project_name': '', 'fee': '', 'reason': '', 'fee_original': ''}
        if msg.caption:
            parsed_data = parse_persian_text(msg.caption)
        
        # Determine status
        has_data = bool(parsed_data['project_name'] or parsed_data['fee'] or parsed_data['reason'])
        
        # Create new row data
        row_data = {
            'row_number': str(row_num),
            'gregorian_date': greg_str,
            'jalali_date': jalali_str,
            'member_name': user.full_name or str(user.id),
            'user_id': str(user.id),
            'project_name': parsed_data['project_name'],
            'fee': parsed_data['fee'],
            'reason': parsed_data['reason'] or (msg.caption if msg.caption else '⏳ توضیحات دریافت نشده'),
            'source_type': 'photo',
            'file_name': '',  # We don't store filename anymore
            'telegram_file_id': file_id,  # Store Telegram file ID
            'status': 'completed' if has_data else 'pending',
            'chat_id': str(chat.id),
            'message_id': str(msg.message_id),
            'is_deleted': 'False',
            'processed_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'photo_message_id': str(msg.message_id),
        }
        
        # Save to Google Sheet
        if save_to_sheet(row_data):
            confirmation = (
                f"✅ **عکس ذخیره شد**\n"
                f"📁 شماره ردیف: {row_num}\n"
                f"📅 تاریخ: {jalali_str}\n"
            )
            
            if has_data:
                if parsed_data['project_name']:
                    confirmation += f"🏢 پروژه: {parsed_data['project_name']}\n"
                if parsed_data['fee']:
                    confirmation += f"💰 مبلغ: {parsed_data['fee']} ریال\n"
                if parsed_data['reason']:
                    confirmation += f"📝 توضیحات: {parsed_data['reason'][:80]}...\n"
            else:
                confirmation += "\n📝 **لطفاً توضیحات را در پیام بعدی ارسال کنید**"
            
            await msg.reply_text(confirmation, parse_mode='Markdown')
        else:
            await msg.reply_text("❌ خطا در ذخیره اطلاعات.", parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error in handle_photo: {e}", exc_info=True)
        await update.effective_message.reply_text(f"❌ خطا در ذخیره عکس: {str(e)}")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle text messages"""
    try:
        msg = update.effective_message
        user = update.effective_user
        chat = update.effective_chat
        text = msg.text.strip()
        
        if not check_user_access(user.id):
            await update.message.reply_text(
                "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
                parse_mode='Markdown'
            )
            return
        
        # Skip if it's a command
        if text.startswith('/'):
            return
        
        logger.info(f"Text from {user.full_name}: {text[:100]}")
        
        # Parse the text
        parsed_data = parse_persian_text(text)
        
        # Check if any data was extracted
        if not parsed_data['project_name'] and not parsed_data['fee'] and not parsed_data['reason']:
            await msg.reply_text(
                "⚠️ **فرمت پیام صحیح نیست**\n\n"
                "💡 **فرمت صحیح:**\n"
                "```\n"
                "پروژه: نام پروژه\n"
                "مبلغ: مقدار [تومان/ریال]\n"
                "توضیحات: شرح هزینه\n"
                "```",
                parse_mode='Markdown'
            )
            return
        
        # Get next row number
        row_num = get_next_row_number()
        
        # Get current date/time
        greg_dt = msg.date
        jalali_dt = jdatetime.datetime.fromgregorian(datetime=greg_dt)
        greg_str = greg_dt.strftime('%Y-%m-%d %H:%M:%S')
        jalali_str = jalali_dt.strftime('%Y/%m/%d %H:%M:%S')
        
        # Create new row data
        row_data = {
            'row_number': str(row_num),
            'gregorian_date': greg_str,
            'jalali_date': jalali_str,
            'member_name': user.full_name or str(user.id),
            'user_id': str(user.id),
            'project_name': parsed_data['project_name'],
            'fee': parsed_data['fee'],
            'reason': parsed_data['reason'] or text,
            'source_type': 'text',
            'status': 'completed',
            'chat_id': str(chat.id),
            'message_id': str(msg.message_id),
            'is_deleted': 'False',
            'processed_date': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'text_message_id': str(msg.message_id),
        }
        
        # Save to Google Sheet
        if save_to_sheet(row_data):
            confirmation = (
                f"✅ **اطلاعات ذخیره شد**\n"
                f"📁 شماره ردیف: {row_num}\n"
                f"📅 تاریخ: {jalali_str}\n"
            )
            
            if parsed_data['project_name']:
                confirmation += f"🏢 پروژه: {parsed_data['project_name']}\n"
            if parsed_data['fee']:
                confirmation += f"💰 مبلغ: {parsed_data['fee']} ریال\n"
            if parsed_data['reason']:
                confirmation += f"📝 توضیحات: {parsed_data['reason'][:80]}...\n"
            
            await msg.reply_text(confirmation, parse_mode='Markdown')
        else:
            await msg.reply_text("❌ خطا در ذخیره اطلاعات.", parse_mode='Markdown')
            
    except Exception as e:
        logger.error(f"Error in handle_text: {e}", exc_info=True)
        await update.effective_message.reply_text(f"❌ خطا در ذخیره متن: {str(e)}")

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /status command"""
    try:
        user = update.effective_user
        
        if not check_user_access(user.id):
            await update.message.reply_text(
                "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
                parse_mode='Markdown'
            )
            return
        
        status_text = (
            f"🤖 **وضعیت ربات (نسخه ابری)**\n\n"
            f"🔧 **ذخیره‌سازی:**\n"
            f"• Google Sheets: {'✅ متصل' if worksheet else '❌ قطع'}\n"
            f"• آپتایم ربات: {'✅ فعال' if RENDER_APP_URL else '❌ غیرفعال'}\n\n"
            f"📊 **دستورات:**\n"
            f"/start - راهنمای کامل\n"
            f"/list - نمایش آخرین رکوردها\n"
            f"/delete <شماره> - حذف رکورد\n\n"
            f"💡 **ویژگی‌ها:**\n"
            "• ذخیره‌سازی ابری با Google Sheets\n"
            "• بدون نیاز به سرور اختصاصی\n"
            "• رایگان و همیشه در دسترس\n"
            "• پشتیبانی از عکس و متن\n"
            "• تبدیل خودکار تومان به ریال\n"
        )
        
        await update.message.reply_text(status_text, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ خطا در بررسی وضعیت: {str(e)}")

async def list_records(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List recent records"""
    try:
        user = update.effective_user
        
        if not check_user_access(user.id):
            await update.message.reply_text(
                "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
                parse_mode='Markdown'
            )
            return
        
        response = "📋 **آخرین رکوردها:**\n\n"
        
        if worksheet:
            # Get last 10 records from Google Sheet
            data = worksheet.get_all_records()
            if len(data) > 10:
                recent_data = data[-10:]
            else:
                recent_data = data
            
            for row in recent_data:
                row_num = row.get('row_number', 'N/A')
                project = row.get('project_name', 'بدون نام')[:20]
                fee = row.get('fee', '0')
                date = row.get('jalali_date', 'N/A')[:10] if row.get('jalali_date') else 'N/A'
                source = row.get('source_type', 'N/A')
                
                response += f"#{row_num} | {project} | {fee} ریال | {date} | {source}\n"
            
            response += f"\n📊 **کل رکوردها:** {len(data)}"
        else:
            response += "❌ اتصال به Google Sheets برقرار نیست."
        
        await update.message.reply_text(response, parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in list_records: {e}")
        await update.message.reply_text(f"❌ خطا در نمایش لیست: {str(e)}")

async def delete_record(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Delete a record by row number"""
    try:
        user = update.effective_user
        
        if not check_user_access(user.id):
            await update.message.reply_text(
                "⛔ **دسترسی محدود**\nشما مجاز به استفاده از این ربات نیستید.",
                parse_mode='Markdown'
            )
            return
        
        args = context.args
        
        if not args or len(args) != 1:
            await update.message.reply_text(
                "❌ **فرمت صحیح:**\n`/delete <شماره ردیف>`\nمثال: `/delete 5`",
                parse_mode='Markdown'
            )
            return
        
        row_num = args[0]
        
        if update_sheet_row(row_num, {'is_deleted': 'True'}):
            await update.message.reply_text(f"✅ **ردیف {row_num} حذف شد.**", parse_mode='Markdown')
        else:
            await update.message.reply_text(f"❌ ردیف {row_num} یافت نشد.", parse_mode='Markdown')
        
    except Exception as e:
        logger.error(f"Error in delete_record: {e}")
        await update.message.reply_text(f"❌ خطا در حذف رکورد: {str(e)}")

# ================= WEB SERVER FUNCTIONS =================

def run_flask():
    """Run Flask web server"""
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port)

def start_bot():
    """Start Telegram bot"""
    try:
        # Create application
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Register command handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("status", status))
        application.add_handler(CommandHandler("list", list_records))
        application.add_handler(CommandHandler("delete", delete_record))
        
        # Register message handlers
        application.add_handler(MessageHandler(filters.PHOTO, handle_photo))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
        
        logger.info("Starting Telegram bot...")
        
        # Run the bot
        application.run_polling(drop_pending_updates=True)
        
    except Exception as e:
        logger.error(f"Fatal error in bot: {e}", exc_info=True)

# ================= MAIN FUNCTION =================

def main():
    """Main function to start both Flask server and Telegram bot"""
    print("\n" + "="*60)
    print("🤖 RECEIPTS MANAGEMENT BOT (CLOUD VERSION)")
    print("="*60)
    print("📁 Storage: Google Sheets")
    print("🌐 Hosting: Render (Free)")
    print("⏰ Uptime: UptimeRobot pings every 5 minutes")
    print("="*60 + "\n")
    
    # Start Flask server in a separate thread
    flask_thread = threading.Thread(target=run_flask, daemon=True)
    flask_thread.start()
    logger.info("Flask server started on port 8080")
    
    # Start Telegram bot in the main thread
    start_bot()

if __name__ == '__main__':
    main()
