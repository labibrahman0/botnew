import os
import json
import logging
import requests
import threading
import time
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv
import telebot
from telebot.types import Message

# Load environment variables
load_dotenv()

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENSEA_API_KEY = os.getenv("OPENSEA_API_KEY")
ADMIN_ID = os.getenv("ADMIN_ID") # Add ADMIN_ID to .env

if not TELEGRAM_BOT_TOKEN or TELEGRAM_BOT_TOKEN == "your_telegram_bot_token_here":
    print("Please set TELEGRAM_BOT_TOKEN in .env")
    exit(1)

# Initialize bot
bot = telebot.TeleBot(TELEGRAM_BOT_TOKEN)

# Logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Data Stores
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
WALLETS_FILE = os.path.join(DATA_DIR, "wallets.json")
WATCHLIST_FILE = os.path.join(DATA_DIR, "watchlist.json")
AUTH_FILE = os.path.join(DATA_DIR, "authorized_users.json")

os.makedirs(DATA_DIR, exist_ok=True)

def load_json(file_path):
    if os.path.exists(file_path):
        try:
            with open(file_path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_json(file_path, data):
    with open(file_path, "w") as f:
        json.dump(data, f, indent=2)

def get_authorized_users():
    data = load_json(AUTH_FILE)
    if isinstance(data, list):
        return data
    return []

def save_authorized_users(users_list):
    save_json(AUTH_FILE, users_list)

def is_authorized(user_id):
    if ADMIN_ID and str(user_id) == str(ADMIN_ID):
        return True
    return str(user_id) in get_authorized_users()

def check_auth(func):
    def wrapper(message, *args, **kwargs):
        if not is_authorized(message.from_user.id):
            bot.reply_to(message, f"⛔ **Access Denied!**\n\nYou are not authorized to use this bot.\n\nYour User ID is: `{message.from_user.id}` (Click to copy)\n\nPlease contact Dev **@SK1Z0V41** to get access.", parse_mode='Markdown')
            return
        return func(message, *args, **kwargs)
    return wrapper

def get_wallet(user_id):
    return load_json(WALLETS_FILE).get(str(user_id))

def set_wallet(user_id, address):
    wallets = load_json(WALLETS_FILE)
    wallets[str(user_id)] = address.lower()
    save_json(WALLETS_FILE, wallets)

def load_watchlist():
    return load_json(WATCHLIST_FILE)

def save_watchlist(data):
    save_json(WATCHLIST_FILE, data)

# OpenSea API Calls
def get_opensea_drop(slug):
    url = f"https://api.opensea.io/api/v2/drops/{slug}"
    headers = {
        "accept": "application/json",
        "x-api-key": OPENSEA_API_KEY
    }
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json(), None
    elif response.status_code == 401:
        return None, "invalid_key"
    return None, "not_found"

def check_eligibility_api(slug, address):
    url = f"https://api.opensea.io/api/v2/drops/{slug}/mint"
    headers = {
        "accept": "application/json",
        "content-type": "application/json",
        "x-api-key": OPENSEA_API_KEY
    }
    payload = {
        "minter": address,
        "quantity": 1
    }
    response = requests.post(url, headers=headers, json=payload)
    if response.status_code == 200:
        return True
    return False

def parse_slug(input_str):
    slug = input_str
    if "opensea.io/collection/" in input_str:
        try:
            slug = input_str.split("opensea.io/collection/")[1].split("/")[0].split("?")[0]
        except:
            pass
    return slug.lower()

# Background Checker for Watchlist
def background_checker():
    while True:
        try:
            watchlist = load_watchlist()
            changed = False
            now = datetime.now(timezone.utc)
            
            for user_id_str, projects in list(watchlist.items()):
                user_id = int(user_id_str)
                for slug, project_data in list(projects.items()):
                    drop_data, error = get_opensea_drop(slug)
                    if error or not drop_data:
                        continue
                        
                    # Check sold out
                    total_supply = int(drop_data.get('total_supply', 0) or 0)
                    max_supply = int(drop_data.get('max_supply', 0) or 0)
                    
                    if max_supply > 0 and total_supply >= max_supply:
                        bot.send_message(user_id, f"🚨 *SOLD OUT!* 🚨\n\nThe project `{slug}` is completely sold out!\nTracking stopped.", parse_mode='Markdown')
                        del watchlist[user_id_str][slug]
                        changed = True
                        continue
                    
                    stages = drop_data.get('stages', [])
                    notified_stages = project_data.get('notified_stages', [])
                    
                    for stage in stages:
                        stage_name = stage.get('label') or stage.get('name') or 'Unknown'
                        if stage_name in notified_stages:
                            continue
                            
                        start_ts = stage.get('start_time')
                        if start_ts:
                            try:
                                dt = datetime.strptime(str(start_ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                                if now >= dt:
                                    # Phase just started!
                                    address = get_wallet(user_id)
                                    eligibility_str = ""
                                    if address:
                                        is_eligible = check_eligibility_api(slug, address)
                                        if is_eligible or stage.get('stage_type') == 'public_sale' or 'public' in stage_name.lower() or stage.get('type') == 'public':
                                            eligibility_str = "\n✅ **YOU ARE ELIGIBLE!**"
                                        else:
                                            eligibility_str = "\n❌ You are NOT ELIGIBLE."
                                    
                                    msg = f"🔔 **Phase Started!**\n\n🖼 Project: *{slug}*\n🔹 Phase: *{stage_name}*{eligibility_str}\n\n[Go to OpenSea](https://opensea.io/collection/{slug}/drop)"
                                    bot.send_message(user_id, msg, parse_mode='Markdown', disable_web_page_preview=True)
                                    
                                    watchlist[user_id_str][slug].setdefault('notified_stages', []).append(stage_name)
                                    changed = True
                            except Exception as e:
                                logger.error(f"Time parsing error: {e}")
                                
            if changed:
                save_watchlist(watchlist)
                
        except Exception as e:
            logger.error(f"Checker error: {e}")
            
        time.sleep(60) # check every minute

# Bot Commands
@bot.message_handler(commands=['start', 'help'])
def send_welcome(message: Message):
    text = (
        "👋 *Welcome to the OpenSea Eligibility Bot!*\n\n"
        "Here is what I can do for you:\n"
        "✅ Check your wallet eligibility for OpenSea Drops.\n"
        "✅ View complete Mint Schedules in your local time (BD/GMT+6).\n"
        "✅ Auto-track projects and notify you exactly when phases start.\n"
        "✅ Auto-alert you if a tracked project sells out completely.\n\n"
        "🛠 *Commands:*\n"
        "🔹 `/wallet <0x...>` - Set your Ethereum wallet address\n"
        "🔹 `/mywallet` - Check your saved wallet address\n"
        "🔹 `/check <link>` - Check schedule & eligibility immediately\n"
        "🔹 `/add <link>` - Add project to auto-notifier watchlist\n"
        "🔹 `/remove <link>` - Stop tracking a project\n"
        "🔹 `/list` - View your tracked projects\n\n"
        "⚠️ *Note:* You need to be authorized to use these commands. Send any command to get your User ID and contact the developer."
    )
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['approve'])
def approve_user(message: Message):
    if not ADMIN_ID or str(message.from_user.id) != str(ADMIN_ID):
        bot.reply_to(message, "❌ Only the admin can authorize users.")
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide User ID(s).\n*Usage:* `/approve id1 id2 ...`", parse_mode='Markdown')
        return
        
    new_ids = parts[1].strip().split()
    users = get_authorized_users()
    added = []
    already = []
    
    for uid in new_ids:
        if uid not in users:
            users.append(uid)
            added.append(uid)
        else:
            already.append(uid)
            
    if added:
        save_authorized_users(users)
        bot.reply_to(message, f"✅ Authorized: `{', '.join(added)}`", parse_mode='Markdown')
    if already:
        bot.reply_to(message, f"⚠️ Already authorized: `{', '.join(already)}`", parse_mode='Markdown')

@bot.message_handler(commands=['ban'])
def ban_user(message: Message):
    if not ADMIN_ID or str(message.from_user.id) != str(ADMIN_ID):
        bot.reply_to(message, "❌ Only the admin can ban users.")
        return
        
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide User ID(s).\n*Usage:* `/ban id1 id2 ...`", parse_mode='Markdown')
        return
        
    ban_ids = parts[1].strip().split()
    users = get_authorized_users()
    banned = []
    not_found = []
    
    for uid in ban_ids:
        if uid in users:
            users.remove(uid)
            banned.append(uid)
        else:
            not_found.append(uid)
            
    if banned:
        save_authorized_users(users)
        bot.reply_to(message, f"🚫 Banned: `{', '.join(banned)}`", parse_mode='Markdown')
    if not_found:
        bot.reply_to(message, f"⚠️ Not in authorized list: `{', '.join(not_found)}`", parse_mode='Markdown')

@bot.message_handler(commands=['wallet'])
@check_auth
def update_wallet(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide a wallet address.\n*Usage:* `/wallet 0x...`", parse_mode='Markdown')
        return
    
    address = parts[1].strip()
    if not address.startswith("0x") or len(address) != 42:
        bot.reply_to(message, "❌ Invalid Ethereum address format.")
        return
    
    set_wallet(message.from_user.id, address)
    bot.reply_to(message, f"✅ Wallet successfully saved:\n`{address}`", parse_mode='Markdown')

@bot.message_handler(commands=['mywallet'])
@check_auth
def my_wallet(message: Message):
    address = get_wallet(message.from_user.id)
    if address:
        bot.reply_to(message, f"💳 Your saved wallet is:\n`{address}`", parse_mode='Markdown')
    else:
        bot.reply_to(message, "⚠️ You haven't saved a wallet yet. Use `/wallet <0x...>` to set it.", parse_mode='Markdown')

@bot.message_handler(commands=['check'])
@check_auth
def check_project(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide a project slug or OpenSea link.\n*Usage:* `/check <link>`", parse_mode='Markdown')
        return
    
    slug = parse_slug(parts[1].strip())
    address = get_wallet(message.from_user.id)
    
    if not address:
        bot.reply_to(message, "⚠️ Please set your wallet address first using `/wallet <0x...>`.", parse_mode='Markdown')
        return
    
    msg = bot.reply_to(message, f"🔍 Searching for project `{slug}`...", parse_mode='Markdown')
    
    try:
        drop_data, error = get_opensea_drop(slug)
        if error == "invalid_key":
            bot.edit_message_text(f"❌ **Invalid OpenSea API Key!**", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            return
        elif not drop_data:
            bot.edit_message_text(f"❌ Could not find a drop with slug `{slug}`.", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')
            return
        
        stages = drop_data.get('stages', [])
        active_stage = drop_data.get('active_stage')
        is_minting = drop_data.get('is_minting', False)
        
        response_text = f"🖼 *Project:* [{slug}](https://opensea.io/collection/{slug}/drop)\n"
        response_text += f"💳 *Wallet:* `{address[:6]}...{address[-4:]}`\n\n"
        
        response_text += "🗓 *MINT SCHEDULE:*\n\n"
        
        if not stages:
            response_text += "No mint stages found for this drop."
        else:
            is_active_eligible = False
            if is_minting:
                is_active_eligible = check_eligibility_api(slug, address)
            
            for stage in stages:
                name = stage.get('label') or stage.get('name') or 'Unknown Phase'
                start_ts = stage.get('start_time')
                end_ts = stage.get('end_time')
                max_mints = stage.get('max_per_wallet') or stage.get('max_mints_per_wallet') or 0
                
                price_val = stage.get('price', "0")
                price = "FREE"
                if price_val and str(price_val).isdigit() and int(price_val) > 0:
                    eth_price = int(price_val) / (10 ** 18)
                    price = f"{eth_price} ETH"
                
                # Format times (BD time: UTC+6)
                bd_tz = timezone(timedelta(hours=6))
                
                start_str = "TBA"
                if start_ts:
                    try:
                        dt = datetime.strptime(str(start_ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        dt_bd = dt.astimezone(bd_tz)
                        start_str = dt_bd.strftime('%b %d at %I:%M %p GMT+6')
                    except Exception:
                        start_str = str(start_ts)
                
                end_str = ""
                if end_ts:
                    try:
                        dt = datetime.strptime(str(end_ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                        dt_bd = dt.astimezone(bd_tz)
                        end_str = f"\nEnds: {dt_bd.strftime('%b %d at %I:%M %p GMT+6')}"
                    except Exception:
                        end_str = f"\nEnds: {end_ts}"
                
                eligibility_str = "NOT ELIGIBLE" # Default
                
                is_this_active = active_stage and active_stage.get('label') == name
                
                if is_this_active and is_active_eligible:
                    eligibility_str = "✅ ELIGIBLE"
                elif stage.get('stage_type') == 'public_sale' or "public" in name.lower() or stage.get('type') == 'public':
                    eligibility_str = "✅ ELIGIBLE"
                else:
                    eligibility_str = "❌ NOT ELIGIBLE"
                
                response_text += f"🔹 *{name}*\n"
                response_text += f"Starts: {start_str}{end_str}\n"
                response_text += f"{price} | LIMIT {max_mints} PER WALLET\n"
                response_text += f"Status: {eligibility_str}\n\n"
                
        bot.edit_message_text(response_text, chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown', disable_web_page_preview=True)

    except Exception as e:
        logger.error(f"Error checking project: {e}")
        bot.edit_message_text(f"❌ An error occurred while checking `{slug}`. Please try again later.", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')

@bot.message_handler(commands=['add'])
@check_auth
def add_project(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide a project link.\n*Usage:* `/add <project_link>`", parse_mode='Markdown')
        return
        
    slug = parse_slug(parts[1].strip())
    msg = bot.reply_to(message, f"🔍 Validating `{slug}`...", parse_mode='Markdown')
    drop_data, error = get_opensea_drop(slug)
    
    if error == "invalid_key":
        bot.edit_message_text(f"❌ **Invalid OpenSea API Key!**", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')
        return
    elif not drop_data:
        bot.edit_message_text(f"❌ Could not find drop `{slug}`.", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')
        return
        
    user_id = str(message.from_user.id)
    watchlist = load_watchlist()
    
    if user_id not in watchlist:
        watchlist[user_id] = {}
        
    if slug in watchlist[user_id]:
        bot.edit_message_text(f"⚠️ `{slug}` is already in your watchlist!", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')
        return
        
    watchlist[user_id][slug] = {'notified_stages': []}
    
    # Fast forward: don't notify for stages that already passed
    now = datetime.now(timezone.utc)
    for stage in drop_data.get('stages', []):
        start_ts = stage.get('start_time')
        if start_ts:
            try:
                dt = datetime.strptime(str(start_ts), "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
                if now >= dt:
                    stage_name = stage.get('label') or stage.get('name') or 'Unknown'
                    watchlist[user_id][slug]['notified_stages'].append(stage_name)
            except: pass
            
    save_watchlist(watchlist)
    bot.edit_message_text(f"✅ Successfully added `{slug}` to your watchlist!\nI will notify you when new phases start or if it sells out.", chat_id=message.chat.id, message_id=msg.message_id, parse_mode='Markdown')

@bot.message_handler(commands=['remove'])
@check_auth
def remove_project(message: Message):
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        bot.reply_to(message, "❌ Please provide a project link.\n*Usage:* `/remove <project_link>`", parse_mode='Markdown')
        return
        
    slug = parse_slug(parts[1].strip())
    user_id = str(message.from_user.id)
    watchlist = load_watchlist()
    
    if user_id in watchlist and slug in watchlist[user_id]:
        del watchlist[user_id][slug]
        save_watchlist(watchlist)
        bot.reply_to(message, f"🗑️ Removed `{slug}` from your watchlist.", parse_mode='Markdown')
    else:
        bot.reply_to(message, f"⚠️ `{slug}` is not in your watchlist.", parse_mode='Markdown')

@bot.message_handler(commands=['list'])
@check_auth
def list_projects(message: Message):
    user_id = str(message.from_user.id)
    watchlist = load_watchlist()
    
    projects = watchlist.get(user_id, {})
    if not projects:
        bot.reply_to(message, "📝 Your watchlist is empty. Add projects using `/add <link>`.", parse_mode='Markdown')
        return
        
    text = "📝 *Your Watchlist:*\n\n"
    for slug in projects.keys():
        text += f"🔹 `{slug}`\n"
        
    bot.reply_to(message, text, parse_mode='Markdown')

if __name__ == "__main__":
    print("Bot is running...")
    
    # Start background checker thread
    checker_thread = threading.Thread(target=background_checker, daemon=True)
    checker_thread.start()
    
    bot.infinity_polling()
