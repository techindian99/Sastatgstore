import os
import asyncio
import logging
import asyncpg
import urllib.parse
from aiohttp import web
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION ---
BOT_TOKEN = os.getenv("BOT_TOKEN")
DB_URL = os.getenv("DB_URL")
OWNER_ID = int(os.getenv("OWNER_ID"))

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# --- STATES ---
class UserStates(StatesGroup):
    dep_amount = State()
    dep_utr = State()
    dep_screenshot = State()
    dep_sender = State()
    support_msg = State()

class AdminStates(StatesGroup):
    add_country = State()
    add_price = State()
    add_phone = State()
    rem_acc_id = State()
    reply_user = State()

class OwnerStates(StatesGroup):
    add_admin = State()
    remove_admin = State()
    set_upi = State()

# --- DATABASE SETUP ---
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DB_URL)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, username TEXT, balance NUMERIC DEFAULT 0.0, 
                    referrer_id BIGINT, has_purchased BOOLEAN DEFAULT FALSE, is_banned BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id SERIAL PRIMARY KEY, admin_id BIGINT, action TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY, country TEXT, price NUMERIC, 
                    phone TEXT, is_sold BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY, user_id BIGINT, account_id INT, price NUMERIC, status TEXT DEFAULT 'PENDING', admin_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS deposits (
                    id SERIAL PRIMARY KEY, user_id BIGINT, utr TEXT UNIQUE, amount NUMERIC, screenshot_id TEXT, sender_name TEXT, status TEXT DEFAULT 'PENDING', admin_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            ''')
            await conn.execute("INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING", OWNER_ID)
            await conn.execute("INSERT INTO settings (key, value) VALUES ('upi_id', 'sastatgstore@fam') ON CONFLICT DO NOTHING")
            await conn.execute("INSERT INTO settings (key, value) VALUES ('upi_name', 'SASTA TG STORE') ON CONFLICT DO NOTHING")
        logger.info("✅ Database initialized successfully.")
    except Exception as e:
        logger.error(f"❌ DB Init Error: {e}")

async def get_setting(key):
    async with db_pool.acquire() as conn:
        return await conn.fetchval("SELECT value FROM settings WHERE key = $1", key)

async def set_setting(key, value):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO settings (key, value) VALUES ($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value", key, value)

async def log_admin(admin_id, action):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO admin_logs (admin_id, action) VALUES ($1, $2)", admin_id, action)

async def is_admin(user_id):
    if user_id == OWNER_ID: return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM admins WHERE user_id = $1", user_id)
        return bool(row)

# --- PREMIUM KEYBOARDS ---
def main_menu(is_admin=False):
    builder = InlineKeyboardBuilder()
    builder.row(InlineKeyboardButton(text="🛒 PREMIUM STORE", callback_data="u_store"))
    builder.row(
        InlineKeyboardButton(text="💰 WALLET", callback_data="u_wallet"),
        InlineKeyboardButton(text="👤 PROFILE", callback_data="u_profile")
    )
    builder.row(
        InlineKeyboardButton(text="🎁 REFER & EARN", callback_data="u_refer"),
        InlineKeyboardButton(text="🏆 LEADERBOARD", callback_data="u_leaderboard")
    )
    builder.row(InlineKeyboardButton(text="🎧 SECRET SUPPORT", callback_data="u_support"))
    
    if is_admin:
        builder.row(InlineKeyboardButton(text="🛡️ ADMIN PANEL", callback_data="admin_panel"))
        if is_admin == 2:
            builder.row(InlineKeyboardButton(text="👑 OWNER CONTROL", callback_data="owner_panel"))
    return builder.as_markup()

def admin_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Add Account", callback_data="a_add_acc"),
        InlineKeyboardButton(text="🗑 Remove Account", callback_data="a_rem_acc")
    )
    builder.row(
        InlineKeyboardButton(text="📦 Pending Orders", callback_data="a_pending_orders"),
        InlineKeyboardButton(text="📊 Store Stats", callback_data="a_stats")
    )
    builder.row(InlineKeyboardButton(text="🔙 Back to Menu", callback_data="u_main"))
    return builder.as_markup()

def owner_menu():
    builder = InlineKeyboardBuilder()
    builder.row(
        InlineKeyboardButton(text="➕ Add Admin", callback_data="o_add_admin"),
        InlineKeyboardButton(text="➖ Remove Admin", callback_data="o_rem_admin")
    )
    builder.row(InlineKeyboardButton(text="⚙️ Change UPI Settings", callback_data="o_settings"))
    builder.row(InlineKeyboardButton(text="📜 Admin Activity Logs", callback_data="o_logs"))
    builder.row(InlineKeyboardButton(text="🔙 Back to Menu", callback_data="u_main"))
    return builder.as_markup()

# --- BEAUTIFUL TEXT TEMPLATES ---
WELCOME_TEXT = """
<b>👑 WELCOME TO SASTA TG STORE 👑</b>
━━━━━━━━━━━━━━━━━━━━
India's Most Trusted & Premium Telegram Account Market.
⚡️ <b>Rate:</b> 1 INR = 1 Coin
🔒 <b>Security:</b> 100% Safe & Instant Delivery
━━━━━━━━━━━━━━━━━━━━
<i>Select an option from the menu below to explore our VIP services.</i>
"""

# --- USER HANDLERS ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if user and user['is_banned']:
            return await message.answer("🚫 You are banned from using this bot.")
            
        if not user:
            referrer_id = None
            args = message.text.split()
            if len(args) > 1 and args[1].startswith("ref_"):
                try:
                    referrer_id = int(args[1].split("_")[1])
                    if referrer_id == user_id: referrer_id = None
                except: pass
            
            await conn.execute("INSERT INTO users (user_id, username, referrer_id) VALUES ($1, $2, $3)", user_id, message.from_user.username, referrer_id)
            if referrer_id:
                try: await bot.send_message(referrer_id, "🎉 <b>New User Joined!</b>\nSomeone joined using your link. You will earn 10% commission when they make their first purchase.")
                except: pass
            
    admin_status = 2 if user_id == OWNER_ID else (1 if await is_admin(user_id) else 0)
    await message.answer(WELCOME_TEXT, reply_markup=main_menu(admin_status), parse_mode="HTML")

@dp.callback_query(F.data == "u_main")
async def u_main(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.clear()
    admin_status = 2 if call.from_user.id == OWNER_ID else (1 if await is_admin(call.from_user.id) else 0)
    await call.message.edit_text(WELCOME_TEXT, reply_markup=main_menu(admin_status), parse_mode="HTML")

@dp.callback_query(F.data == "u_profile")
async def u_profile(call: types.CallbackQuery):
    await call.answer()
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", call.from_user.id)
        total_orders = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1 AND status = 'COMPLETED'", call.from_user.id)
        
    text = f"""
<b>👤 VIP PROFILE</b>
━━━━━━━━━━━━━━━━━━━━
🆔 <b>User ID:</b> <code>{call.from_user.id}</code>
💰 <b>Wallet Balance:</b> ₹{user['balance']:.2f}
📦 <b>Total Purchases:</b> {total_orders}
━━━━━━━━━━━━━━━━━━━━
<i>Top up your wallet to buy premium accounts instantly.</i>
"""
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]]), parse_mode="HTML")

@dp.callback_query(F.data == "u_refer")
async def u_refer(call: types.CallbackQuery):
    await call.answer()
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=ref_{call.from_user.id}"
    text = f"""
<b>🎁 REFER & EARN PROGRAM</b>
━━━━━━━━━━━━━━━━━━━━
Invite your friends and earn <b>10% Commission</b> on their first purchase!

🔗 <b>Your Referral Link:</b>
<code>{link}</code>

<i>Share this link. When they buy their first account, you get paid instantly!</i>
"""
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]]), parse_mode="HTML")

@dp.callback_query(F.data == "u_leaderboard")
async def u_leaderboard(call: types.CallbackQuery):
    await call.answer()
    async with db_pool.acquire() as conn:
        top_users = await conn.fetch("SELECT username, user_id, balance FROM users ORDER BY balance DESC LIMIT 5")
    
    text = "<b>🏆 TOP DEPOSITORS LEADERBOARD</b>\n━━━━━━━━━━━━━━━━━━━━\n\n"
    for i, u in enumerate(top_users, 1):
        name = u['username'] if u['username'] else f"User_{u['user_id']}"
        text += f"{i}. <b>{name}</b> - ₹{u['balance']:.2f}\n"
    text += "\n━━━━━━━━━━━━━━━━━━━━\n<i>Compete to be the richest!</i>"
    
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]]), parse_mode="HTML")

# --- SECRET SUPPORT (FIXED) ---
@dp.callback_query(F.data == "u_support")
async def u_support(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(UserStates.support_msg)
    text = """
<b>🎧 SECRET SUPPORT CHAT</b>
━━━━━━━━━━━━━━━━━━━━
<i>Type your message below. It will be forwarded directly to the Admin/Owner. Your identity will remain hidden.</i>
"""
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Cancel", callback_data="u_main")]]), parse_mode="HTML")

@dp.message(UserStates.support_msg)
async def process_support_msg(message: types.Message, state: FSMContext):
    await message.answer("✅ <b>Message Sent!</b>\nAdmin will reply to you here shortly.", parse_mode="HTML")
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Reply to User", callback_data=f"chat_user_{message.from_user.id}")]])
    admin_text = f"🎧 <b>NEW SUPPORT MESSAGE</b>\n━━━━━━━━━━━━━━━━━━━━\n👤 User: <code>{message.from_user.id}</code>\n\n{message.text}"
    
    admin_ids = [OWNER_ID]
    async with db_pool.acquire() as conn:
        for r in await conn.fetch("SELECT user_id FROM admins"):
            if r['user_id'] not in admin_ids: admin_ids.append(r['user_id'])
            
    for adm in admin_ids:
        try: await bot.send_message(adm, admin_text, reply_markup=kb, parse_mode="HTML")
        except: pass

# --- DYNAMIC QR DEPOSIT FLOW ---
@dp.callback_query(F.data == "u_wallet")
async def wallet(call: types.CallbackQuery):
    await call.answer()
    async with db_pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1", call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Deposit Now", callback_data="dep_start")], [InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]])
    text = f"""
<b>💰 YOUR WALLET</b>
━━━━━━━━━━━━━━━━━━━━
💵 <b>Current Balance:</b> ₹{bal:.2f}
⚡️ <b>Exchange Rate:</b> 1 INR = 1 Coin
━━━━━━━━━━━━━━━━━━━━
"""
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "dep_start")
async def dep_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await state.set_state(UserStates.dep_amount)
    await call.message.edit_text("💵 <b>Enter the amount (in ₹) you want to deposit:</b>", parse_mode="HTML")

@dp.message(UserStates.dep_amount)
async def dep_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        if amt <= 0: raise ValueError
        
        upi_id = await get_setting("upi_id")
        upi_name = await get_setting("upi_name")
        
        upi_uri = f"upi://pay?pa={upi_id}&pn={urllib.parse.quote(upi_name)}&am={amt}&cu=INR"
        qr_url = f"https://api.qrserver.com/v1/create-qr-code/?size=400x400&data={urllib.parse.quote(upi_uri)}"
        
        await state.update_data(amount=amt)
        await state.set_state(UserStates.dep_utr)
        
        caption = f"""
<b>💳 SCAN & PAY ₹{amt}</b>
━━━━━━━━━━━━━━━━━━━━
📱 <b>UPI ID:</b> <code>{upi_id}</code>
👤 <b>Name:</b> {upi_name}
━━━━━━━━━━━━━━━━━━━━
1️⃣ Scan the QR code and pay exactly <b>₹{amt}</b>.
2️⃣ After paying, enter the <b>12-digit UTR/Transaction ID</b> below.
"""
        await message.answer_photo(qr_url, caption=caption, parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Invalid amount. Send numbers only.")

@dp.message(UserStates.dep_utr)
async def dep_utr(message: types.Message, state: FSMContext):
    utr = message.text.strip()
    if len(utr) < 8:
        return await message.answer("❌ Invalid UTR. Please send the correct transaction ID.")
        
    async with db_pool.acquire() as conn:
        if await conn.fetchval("SELECT 1 FROM deposits WHERE utr = $1", utr):
            return await message.answer("⚠️ This UTR has already been used.")
            
    await state.update_data(utr=utr)
    await state.set_state(UserStates.dep_screenshot)
    await message.answer("📸 <b>Now send the payment screenshot:</b>", parse_mode="HTML")

@dp.message(UserStates.dep_screenshot, F.photo)
async def dep_screenshot(message: types.Message, state: FSMContext):
    await state.update_data(screenshot=message.photo[-1].file_id)
    await state.set_state(UserStates.dep_sender)
    await message.answer("👤 <b>Enter the name on the bank account from which you sent the payment:</b>", parse_mode="HTML")

@dp.message(UserStates.dep_sender)
async def dep_sender(message: types.Message, state: FSMContext):
    data = await state.get_data()
    sender = message.text
    user_id = message.from_user.id
    
    async with db_pool.acquire() as conn:
        dep_id = await conn.fetchval(
            "INSERT INTO deposits (user_id, amount, utr, screenshot_id, sender_name) VALUES ($1, $2, $3, $4, $5) RETURNING id", 
            user_id, data['amount'], data['utr'], data['screenshot'], sender
        )
    
    await message.answer("✅ <b>Deposit Request Sent!</b>\n\nAdmin will verify and add funds shortly.", reply_markup=main_menu())
    await state.clear()
    
    admin_kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_app_{dep_id}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"dep_rej_{dep_id}")]])
    admin_text = f"🚨 <b>NEW DEPOSIT REQUEST</b>\n━━━━━━━━━━━━━━━━━━━━\n👤 User: <code>{user_id}</code>\n💰 Amount: ₹{data['amount']}\n🔢 UTR: <code>{data['utr']}</code>\n👤 Sender: {sender}"
    
    admin_ids = [OWNER_ID]
    async with db_pool.acquire() as conn:
        for r in await conn.fetch("SELECT user_id FROM admins"):
            if r['user_id'] not in admin_ids: admin_ids.append(r['user_id'])
            
    for adm in admin_ids:
        try: await bot.send_photo(adm, data['screenshot'], caption=admin_text, reply_markup=admin_kb, parse_mode="HTML")
        except: pass

@dp.callback_query(F.data.startswith("dep_app_"))
async def dep_approve(call: types.CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    dep_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id = $1", dep_id)
        if dep['status'] != 'PENDING': return await call.answer("Already processed!", show_alert=True)
        
        await conn.execute("UPDATE deposits SET status = 'APPROVED', admin_id = $1 WHERE id = $2", call.from_user.id, dep_id)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", dep['amount'], dep['user_id'])
    
    await call.message.edit_caption(caption=f"✅ Deposit Approved: ₹{dep['amount']} for User {dep['user_id']}")
    await bot.send_message(dep['user_id'], f"✅ <b>Deposit Approved!</b>\n\n₹{dep['amount']} has been added to your wallet.", parse_mode="HTML")
    await log_admin(call.from_user.id, f"Approved Deposit #{dep_id} (₹{dep['amount']})")

# --- STORE & MANUAL ORDER FLOW ---
@dp.callback_query(F.data == "u_store")
async def browse(call: types.CallbackQuery):
    await call.answer()
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT country, COUNT(*), MIN(price) FROM accounts WHERE is_sold = FALSE GROUP BY country")
    
    if not rows: return await call.answer("Store is empty! Check back later.", show_alert=True)
    
    builder = InlineKeyboardBuilder()
    for r in rows:
        builder.button(text=f"🌍 {r['country']} | Stock: {r['count']} | Min: ₹{r['min']}", callback_data=f"c_{r['country']}")
    builder.button(text="🔙 Back", callback_data="u_main")
    builder.adjust(1)
    
    text = "<b>🛒 PREMIUM STORE</b>\n━━━━━━━━━━━━━━━━━━━━\nSelect a country to view available accounts."
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("c_"))
async def country_accs(call: types.CallbackQuery):
    await call.answer()
    country = call.data.split("_")[1]
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, price FROM accounts WHERE country = $1 AND is_sold = FALSE", country)
    
    builder = InlineKeyboardBuilder()
    for r in rows:
        builder.button(text=f"📱 {country} Account | ₹{r['price']}", callback_data=f"b_{r['id']}")
    builder.button(text="🔙 Back to Countries", callback_data="u_store")
    builder.adjust(1)
    
    text = f"<b>🌍 Available Accounts in {country}</b>\n━━━━━━━━━━━━━━━━━━━━\nClick to buy."
    await call.message.edit_text(text, reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("b_"))
async def buy_acc(call: types.CallbackQuery):
    await call.answer("Processing...", show_alert=False) # SPINNER FIX: Turant loading hatao
    acc_id = int(call.data.split("_")[1])
    uid = call.from_user.id
    
    try:
        async with db_pool.acquire() as conn:
            acc = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1 AND is_sold = FALSE", acc_id)
            if not acc: return await call.answer("Account just sold out!", show_alert=True)
            
            user = await conn.fetchrow("SELECT balance, has_purchased, referrer_id FROM users WHERE user_id = $1", uid)
            if user['balance'] < acc['price']:
                return await call.answer(f"❌ Insufficient Balance! You need ₹{acc['price'] - user['balance']} more.", show_alert=True)
            
            await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", acc['price'], uid)
            await conn.execute("UPDATE accounts SET is_sold = TRUE WHERE id = $1", acc_id)
            order_id = await conn.fetchval("INSERT INTO orders (user_id, account_id, price, status) VALUES ($1, $2, $3, 'PENDING') RETURNING id", uid, acc_id, acc['price'])
            
            if not user['has_purchased'] and user['referrer_id']:
                comm = acc['price'] * 0.10
                await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", comm, user['referrer_id'])
                await conn.execute("UPDATE users SET has_purchased = TRUE WHERE user_id = $1", uid)
        
        await call.message.edit_text(f"🎉 <b>Order Placed!</b>\n\nOrder ID: <code>#{order_id}</code>\n\n⏳ <i>Admin will contact you shortly via Secret Chat for login process.</i>", parse_mode="HTML")
        
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Chat with User", callback_data=f"chat_user_{uid}")]])
        admin_msg = f"🚨 <b>NEW ORDER</b>\n━━━━━━━━━━━━━━━━━━━━\nOrder: <code>#{order_id}</code>\nUser: <code>{uid}</code>\nCountry: {acc['country']}\nPrice: ₹{acc['price']}\n\n<i>Click below to chat and get login details.</i>"
        
        admin_ids = [OWNER_ID]
        async with db_pool.acquire() as conn:
            for r in await conn.fetch("SELECT user_id FROM admins"):
                if r['user_id'] not in admin_ids: admin_ids.append(r['user_id'])
                
        for adm in admin_ids:
            try: await bot.send_message(adm, admin_msg, reply_markup=kb, parse_mode="HTML")
            except: pass
    except Exception as e:
        logger.error(f"Buy Error: {e}")
        await call.message.answer("⚠️ Something went wrong. Please contact support.")

# --- ADMIN CHAT SYSTEM (TICKETS) ---
@dp.callback_query(F.data.startswith("chat_user_"))
async def admin_chat_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    target_uid = int(call.data.split("_")[2])
    await state.update_data(target_uid=target_uid)
    await state.set_state(AdminStates.reply_user)
    await call.message.reply(f"💬 <b>Chat Mode Active</b>\nTalking to User: <code>{target_uid}</code>\n\n<i>Type your message below. The user will receive it in Secret Chat.</i>")

@dp.message(AdminStates.reply_user)
async def admin_chat_send(message: types.Message, state: FSMContext):
    data = await state.get_data()
    target_uid = data['target_uid']
    
    try:
        kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💬 Reply to Admin", callback_data=f"reply_admin_{message.from_user.id}")]])
        await bot.send_message(target_uid, f"🎧 <b>Admin Message:</b>\n\n{message.text}", reply_markup=kb, parse_mode="HTML")
        await message.answer("✅ Message delivered.")
    except Exception as e:
        await message.answer("❌ User may have blocked the bot.")
    await state.clear()

# User replies to Admin
@dp.callback_query(F.data.startswith("reply_admin_"))
async def user_reply_start(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    admin_id = int(call.data.split("_")[2])
    await state.update_data(admin_id=admin_id)
    await state.set_state(UserStates.support_msg) # Reuse support state
    await call.message.reply("💬 <b>Reply to Admin:</b>\n\n<i>Type your message below.</i>")

# --- ADMIN PANEL ---
@dp.callback_query(F.data == "admin_panel")
async def open_admin_panel(call: types.CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    await call.message.edit_text("<b>🛡️ ADMIN PANEL</b>\n━━━━━━━━━━━━━━━━━━━━", reply_markup=admin_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "a_stats")
async def a_stats(call: types.CallbackQuery):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        sold = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'COMPLETED'")
        revenue = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status = 'APPROVED'")
    
    text = f"<b>📊 STORE STATISTICS</b>\n━━━━━━━━━━━━━━━━━━━━\n👥 <b>Users:</b> {users}\n📦 <b>Sold:</b> {sold}\n💰 <b>Revenue:</b> ₹{revenue}"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]), parse_mode="HTML")

# --- ADD ACCOUNT (No Password needed now) ---
@dp.callback_query(F.data == "a_add_acc")
async def a_add_acc(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    await state.set_state(AdminStates.add_country)
    await call.message.edit_text("🌍 <b>Enter Country Name (e.g., India):</b>", parse_mode="HTML")

@dp.message(AdminStates.add_country)
async def process_add_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    await state.set_state(AdminStates.add_price)
    await message.answer("💰 <b>Enter Price (₹):</b>")

@dp.message(AdminStates.add_price)
async def process_add_price(message: types.Message, state: FSMContext):
    try: price = float(message.text)
    except: return await message.answer("❌ Invalid price.")
    await state.update_data(price=price)
    await state.set_state(AdminStates.add_phone)
    await message.answer("📱 <b>Enter Phone Number (Optional):</b>\n\n<i>Just press skip if you want to add it later during login.</i>", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="Skip", callback_data="skip_phone")]]))

@dp.callback_query(F.data == "skip_phone")
async def skip_phone(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    await process_add_phone(call.message, state, "N/A")

@dp.message(AdminStates.add_phone)
async def process_add_phone(message: types.Message, state: FSMContext, phone="N/A"):
    if isinstance(message, types.Message): phone = message.text
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO accounts (country, price, phone) VALUES ($1, $2, $3)", data['country'], data['price'], phone)
                           
    await message.answer(f"✅ Account Added: {data['country']} | ₹{data['price']}", reply_markup=admin_menu())
    await state.clear()

# --- REMOVE ACCOUNT ---
@dp.callback_query(F.data == "a_rem_acc")
async def a_rem_acc(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if not await is_admin(call.from_user.id): return
    async with db_pool.acquire() as conn:
        accounts = await conn.fetch("SELECT id, country, price FROM accounts WHERE is_sold = FALSE LIMIT 20")
    
    if not accounts: return await call.message.edit_text("No accounts in stock.", reply_markup=admin_menu())
    
    builder = InlineKeyboardBuilder()
    for acc in accounts:
        builder.button(text=f"❌ {acc['country']} | ₹{acc['price']} (ID: {acc['id']})", callback_data=f"rem_acc_{acc['id']}")
    builder.button(text="🔙 Back", callback_data="admin_panel")
    builder.adjust(1)
    await call.message.edit_text("Select account to remove:", reply_markup=builder.as_markup())

@dp.callback_query(F.data.startswith("rem_acc_"))
async def process_rem_acc(call: types.CallbackQuery):
    await call.answer()
    acc_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM accounts WHERE id = $1", acc_id)
    await call.message.edit_text("✅ Account Removed.", reply_markup=admin_menu())

# --- OWNER FEATURES ---
@dp.callback_query(F.data == "owner_panel")
async def open_owner_panel(call: types.CallbackQuery):
    await call.answer()
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("<b>👑 OWNER PANEL</b>\n━━━━━━━━━━━━━━━━━━━━", reply_markup=owner_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "o_settings")
async def o_settings(call: types.CallbackQuery):
    await call.answer()
    if call.from_user.id != OWNER_ID: return
    upi = await get_setting("upi_id")
    text = f"⚙️ <b>UPI SETTINGS</b>\n\n📱 UPI ID: <code>{upi}</code>"
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Change UPI ID", callback_data="o_set_upi")], [InlineKeyboardButton(text="🔙 Back", callback_data="owner_panel")]])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "o_set_upi")
async def o_set_upi(call: types.CallbackQuery, state: FSMContext):
    await call.answer()
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.set_upi)
    await call.message.edit_text("💳 Enter new UPI ID:")

@dp.message(OwnerStates.set_upi)
async def process_set_upi(message: types.Message, state: FSMContext):
    await set_setting("upi_id", message.text.strip())
    await message.answer("✅ UPI ID Updated!", reply_markup=owner_menu())
    await state.clear()

# --- DUMMY WEB SERVER FOR RENDER ---
async def handle(request): return web.Response(text="SASTA TG STORE BOT IS ALIVE!")
async def start_web_server():
    app = web.Application()
    app.router.add_get('/', handle)
    runner = web.AppRunner(app)
    await runner.setup()
    port = int(os.getenv("PORT", 8080))
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()

# --- MAIN ---
async def main():
    await init_db()
    asyncio.create_task(start_web_server())
    print("🚀 SASTA TG STORE is LIVE")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
