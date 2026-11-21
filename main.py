import os
import asyncio
import logging
import asyncpg
import aiohttp
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

FAMPAY_QR_URL = "https://fampay.anujbots.xyz/qr.php"
FAMPAY_VERIFY_URL = "https://fampay.anujbots.xyz/verify.php"

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# --- STATES ---
class UserStates(StatesGroup):
    dep_amount = State()

class AdminStates(StatesGroup):
    add_country = State()
    add_price = State()
    add_creds = State()
    send_creds = State()
    broadcast_msg = State()

class OwnerStates(StatesGroup):
    add_admin = State()
    remove_admin = State()
    set_fampay_upi = State()
    set_fampay_key = State()

# --- DATABASE SETUP ---
async def init_db():
    global db_pool
    try:
        db_pool = await asyncpg.create_pool(DB_URL)
        async with db_pool.acquire() as conn:
            await conn.execute('''
                CREATE TABLE IF NOT EXISTS users (
                    user_id BIGINT PRIMARY KEY, username TEXT, balance NUMERIC DEFAULT 0.0, 
                    is_banned BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS admins (user_id BIGINT PRIMARY KEY);
                CREATE TABLE IF NOT EXISTS admin_logs (
                    id SERIAL PRIMARY KEY, admin_id BIGINT, action TEXT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS accounts (
                    id SERIAL PRIMARY KEY, country TEXT, price NUMERIC, 
                    credentials TEXT, is_sold BOOLEAN DEFAULT FALSE
                );
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY, user_id BIGINT, account_id INT, price NUMERIC, status TEXT DEFAULT 'PENDING', admin_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS deposits (
                    id SERIAL PRIMARY KEY, user_id BIGINT, utr TEXT UNIQUE, amount NUMERIC, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
            ''')
            await conn.execute("INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING", OWNER_ID)
            await conn.execute("INSERT INTO settings (key, value) VALUES ('fampay_upi', 'yourupi@fam') ON CONFLICT DO NOTHING")
            await conn.execute("INSERT INTO settings (key, value) VALUES ('fampay_api_key', 'YOUR_API_KEY') ON CONFLICT DO NOTHING")
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

# --- KEYBOARDS ---
def main_menu(is_admin=False):
    kb = [
        [InlineKeyboardButton(text="🛒 Store", callback_data="u_store"), InlineKeyboardButton(text="💰 Wallet", callback_data="u_wallet")],
        [InlineKeyboardButton(text="📞 Support (Secret Chat)", callback_data="u_support")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="🛡️ Admin Panel", callback_data="admin_panel")])
        if is_admin == 2:
            kb.append([InlineKeyboardButton(text="👑 Owner Control", callback_data="owner_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Account", callback_data="a_add_acc"), InlineKeyboardButton(text="📊 Stats", callback_data="a_stats")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="u_main")]
    ])

def owner_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Admin", callback_data="o_add_admin"), InlineKeyboardButton(text="➖ Remove Admin", callback_data="o_rem_admin")],
        [InlineKeyboardButton(text="📜 Admin Logs", callback_data="o_logs")],
        [InlineKeyboardButton(text="⚙️ Bot Settings (FamPay)", callback_data="o_settings")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="u_main")]
    ])

# --- USER HANDLERS ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if not user:
            await conn.execute("INSERT INTO users (user_id, username) VALUES ($1, $2)", user_id, message.from_user.username)
            
    admin_status = 2 if user_id == OWNER_ID else (1 if await is_admin(user_id) else 0)
    await message.answer("👑 <b>SASTA TG STORE</b>\n\nPremium Telegram accounts. 1 INR = 1 Coin.", reply_markup=main_menu(admin_status), parse_mode="HTML")

@dp.callback_query(F.data == "u_main")
async def u_main(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    admin_status = 2 if call.from_user.id == OWNER_ID else (1 if await is_admin(call.from_user.id) else 0)
    await call.message.edit_text("👑 <b>SASTA TG STORE</b>\n\nSelect an option:", reply_markup=main_menu(admin_status), parse_mode="HTML")

# --- WALLET & FAMPAY DEPOSIT ---
@dp.callback_query(F.data == "u_wallet")
async def wallet(call: types.CallbackQuery):
    async with db_pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1", call.from_user.id)
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="💳 Deposit Now", callback_data="dep_start")], [InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]])
    await call.message.edit_text(f"💰 <b>Your Wallet</b>\n\nBalance: <b>₹{bal:.2f}</b>\n(1 INR = 1 Coin)", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "dep_start")
async def dep_start(call: types.CallbackQuery, state: FSMContext):
    await state.set_state(UserStates.dep_amount)
    await call.message.edit_text("💵 <b>Auto Deposit</b>\n\nEnter the amount (in ₹) you want to add:", parse_mode="HTML")

@dp.message(UserStates.dep_amount)
async def dep_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        if amt <= 0: raise ValueError
        
        fampay_upi = await get_setting("fampay_upi")
        loading = await message.answer("⏳ Generating QR...")
        
        async with aiohttp.ClientSession() as session:
            async with session.get(f"{FAMPAY_QR_URL}?upi={fampay_upi}&amount={amt}") as resp:
                data = await resp.json()
                
        await loading.delete()
        
        if data.get("status") != "success":
            return await message.answer("❌ QR generation failed. Try again.")
            
        order_id = data["data"]["order_id"]
        qr_url = data["data"]["qr_url"]
        expires = data["data"]["expires_at_ist"]
        
        await state.update_data(amount=amt, order_id=order_id)
        
        await bot.send_photo(
            message.chat.id, qr_url,
            caption=f"💳 <b>FAMPAY DEPOSIT</b>\n\n💰 Amount: <code>₹{amt}</code>\n⏳ Expires: <code>{expires}</code>\n\nScan QR or pay to: <code>{fampay_upi}</code>\n\nTap <b>Verify Payment</b> after paying.",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Verify Payment", callback_data=f"fampay_verify_{order_id}_{amt}")]]),
            parse_mode="HTML"
        )
    except ValueError:
        await message.answer("❌ Invalid amount.")

@dp.callback_query(F.data.startswith("fampay_verify_"))
async def fampay_verify(call: types.CallbackQuery):
    _, _, order_id, amount = call.data.split("_")
    user_id = call.from_user.id
    
    async with db_pool.acquire() as conn:
        used = await conn.fetchrow("SELECT 1 FROM deposits WHERE utr = $1", order_id)
        if used: return await call.answer("⚠️ Already credited", alert=True)
        
    fampay_api_key = await get_setting("fampay_api_key")
    verify_url = f"{FAMPAY_VERIFY_URL}?order_id={order_id}&api_key={fampay_api_key}"
    
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(verify_url) as resp:
                res = await resp.json()
                
        if res.get("status") != "success":
            return await call.answer("❌ Payment not received yet", alert=True)
            
        utr = res["data"]["utr"]
        actual_amt = float(res["data"]["amount"])
        
        async with db_pool.acquire() as conn:
            await conn.execute("INSERT INTO deposits (user_id, utr, amount) VALUES ($1, $2, $3)", user_id, utr, actual_amt)
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", actual_amt, user_id)
            
        await call.message.edit_text(f"✅ <b>DEPOSIT SUCCESS</b>\n\nAmount: <code>₹{actual_amt}</code>\nUTR: <code>{utr}</code>\nFunds added.", parse_mode="HTML")
    except Exception as e:
        await call.answer(f"⚠️ Error: {str(e)[:30]}", alert=True)

# --- ADMIN: ADD ACCOUNT ---
@dp.callback_query(F.data == "a_add_acc")
async def a_add_acc(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id): return
    await state.set_state(AdminStates.add_country)
    await call.message.edit_text("🌍 <b>Add Account</b>\n\nEnter Country Name (e.g., India):", parse_mode="HTML")

@dp.message(AdminStates.add_country)
async def process_add_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    await state.set_state(AdminStates.add_price)
    await message.answer("💰 Enter Price (₹):")

@dp.message(AdminStates.add_price)
async def process_add_price(message: types.Message, state: FSMContext):
    try: price = float(message.text)
    except: return await message.answer("❌ Invalid price.")
    await state.update_data(price=price)
    await state.set_state(AdminStates.add_creds)
    await message.answer("🔑 Enter Account Details (Format: Phone | Password | 2FA):\n\nExample:\n<code>+919876543210 | mypass123 | 12345</code>", parse_mode="HTML")

@dp.message(AdminStates.add_creds)
async def process_add_creds(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO accounts (country, price, credentials) VALUES ($1, $2, $3)",
                           data['country'], data['price'], message.text)
                           
    await message.answer(f"✅ Account Added Successfully!", reply_markup=admin_menu())
    await log_admin(message.from_user.id, f"Added Account {data['country']} - ₹{data['price']}")
    await state.clear()

# --- BUY FLOW & SECRET CHAT ---
@dp.callback_query(F.data == "u_store")
async def browse(call: types.CallbackQuery):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT country, COUNT(*), MIN(price) FROM accounts WHERE is_sold = FALSE GROUP BY country")
    
    if not rows: return await call.answer("Out of stock!", show_alert=True)
    
    builder = InlineKeyboardBuilder()
    for r in rows:
        builder.button(text=f"🌍 {r['country']} | {r['count']} left | Min: ₹{r['min']}", callback_data=f"c_{r['country']}")
    builder.button(text="🔙 Back", callback_data="u_main")
    builder.adjust(1)
    await call.message.edit_text("🛒 <b>Select Country:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("c_"))
async def country_accs(call: types.CallbackQuery):
    country = call.data.split("_")[1]
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, price FROM accounts WHERE country = $1 AND is_sold = FALSE", country)
    
    builder = InlineKeyboardBuilder()
    for r in rows:
        builder.button(text=f"📱 Account | ₹{r['price']}", callback_data=f"b_{r['id']}")
    builder.button(text="🔙 Back", callback_data="u_store")
    builder.adjust(1)
    await call.message.edit_text(f"🛒 <b>Available in {country}:</b>", reply_markup=builder.as_markup(), parse_mode="HTML")

@dp.callback_query(F.data.startswith("b_"))
async def buy_acc(call: types.CallbackQuery):
    acc_id = int(call.data.split("_")[1])
    uid = call.from_user.id
    
    async with db_pool.acquire() as conn:
        acc = await conn.fetchrow("SELECT * FROM accounts WHERE id = $1 AND is_sold = FALSE", acc_id)
        if not acc: return await call.answer("Already sold!", show_alert=True)
        
        user = await conn.fetchrow("SELECT balance FROM users WHERE user_id = $1", uid)
        if user['balance'] < acc['price']:
            return await call.answer(f"❌ Need ₹{acc['price'] - user['balance']} more.", show_alert=True)
        
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", acc['price'], uid)
        await conn.execute("UPDATE accounts SET is_sold = TRUE WHERE id = $1", acc_id)
        order_id = await conn.fetchval("INSERT INTO orders (user_id, account_id, price, status) VALUES ($1, $2, $3, 'PENDING') RETURNING id", uid, acc_id, acc['price'])
        
    await call.message.edit_text(f"🎉 <b>Purchase Request Placed!</b>\n\nOrder ID: <code>#{order_id}</code>\n\n⏳ Admin will verify and send your credentials shortly via this bot. Please wait...", parse_mode="HTML")
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Send Credentials", callback_data=f"send_creds_{order_id}")]])
    admin_msg = f"🚨 <b>NEW ORDER</b>\n\nOrder: <code>#{order_id}</code>\nUser: <code>{uid}</code>\nCountry: {acc['country']}\nPrice: ₹{acc['price']}"
    
    admin_ids = [OWNER_ID]
    async with db_pool.acquire() as conn:
        for r in await conn.fetch("SELECT user_id FROM admins"):
            if r['user_id'] not in admin_ids: admin_ids.append(r['user_id'])
            
    for adm in admin_ids:
        try: await bot.send_message(adm, admin_msg, reply_markup=kb, parse_mode="HTML")
        except: pass

# --- ADMIN: SEND CREDS ---
@dp.callback_query(F.data.startswith("send_creds_"))
async def admin_send_creds(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id): return
    order_id = int(call.data.split("_")[2])
    await state.update_data(order_id=order_id)
    await state.set_state(AdminStates.send_creds)
    await call.message.reply("🔑 Send the credentials for this order (Bot will forward it to the user):")

@dp.message(AdminStates.send_creds)
async def process_send_creds(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data['order_id']
    
    async with db_pool.acquire() as conn:
        order = await conn.fetchrow("SELECT user_id, account_id FROM orders WHERE id = $1", order_id)
        await conn.execute("UPDATE orders SET status = 'COMPLETED', admin_id = $1 WHERE id = $2", message.from_user.id, order_id)
        
    user_id = order['user_id']
    
    await bot.send_message(
        user_id, 
        f"🎉 <b>Order #{order_id} Accepted!</b>\n\nHere are your account details:\n\n<code>{message.text}</code>\n\n⚠️ Change password immediately!\n\nIf you need help, simply <b>reply</b> to this message. The owner will assist you.", 
        parse_mode="HTML"
    )
    await message.answer(f"✅ Credentials sent to user for Order #{order_id}.")
    await log_admin(message.from_user.id, f"Completed Order #{order_id}")
    await state.clear()

# --- RELAY CHAT (SECRET CHAT) ---
@dp.message(F.reply_to_message)
async def relay_system(message: types.Message):
    if message.reply_to_message.from_user.id == bot.id and "Order #" in message.reply_to_message.text:
        try:
            order_id = int(message.reply_to_message.text.split("Order #")[1].split(" ")[0])
            async with db_pool.acquire() as conn:
                order = await conn.fetchrow("SELECT user_id, admin_id FROM orders WHERE id = $1", order_id)
                
            if message.from_user.id == order['user_id']:
                if order['admin_id']:
                    await bot.send_message(order['admin_id'], f"💬 <b>User Message (Order #{order_id})</b>\nUser: <code>{order['user_id']}</code>\n\n{message.text}")
                    await message.answer("✅ Your message has been sent to the Admin.")
            elif message.from_user.id == order['admin_id'] or message.from_user.id == OWNER_ID:
                await bot.send_message(order['user_id'], f"💬 <b>Admin Reply (Order #{order_id})</b>\n\n{message.text}")
                await message.answer("✅ Reply sent to user.")
        except Exception as e:
            logging.error(f"Relay Error: {e}")

@dp.callback_query(F.data == "u_support")
async def u_support(call: types.CallbackQuery):
    await call.message.edit_text("📞 <b>Secret Support</b>\n\nSend your message here. It will be forwarded directly to the Owner. Your identity will remain hidden.", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]]), parse_mode="HTML")

# --- ADMIN & OWNER PANELS ---
@dp.callback_query(F.data == "admin_panel")
async def open_admin_panel(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    await call.message.edit_text("🛡️ <b>Admin Dashboard</b>", reply_markup=admin_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "owner_panel")
async def open_owner_panel(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("👑 <b>Owner Dashboard</b>", reply_markup=owner_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "a_stats")
async def a_stats(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        orders = await conn.fetchval("SELECT COUNT(*) FROM orders")
        deps = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deposits")
    await call.message.edit_text(f"📊 <b>Stats</b>\n\nUsers: {users}\nSold: {orders}\nRevenue: ₹{deps}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]), parse_mode="HTML")

@dp.callback_query(F.data == "o_add_admin")
async def o_add_admin(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.add_admin)
    await call.message.edit_text("👤 Enter User ID to make Admin:")

@dp.message(OwnerStates.add_admin)
async def o_add_admin_id(message: types.Message, state: FSMContext):
    try: uid = int(message.text)
    except: return await message.answer("❌ Invalid ID.")
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING", uid)
    await message.answer(f"✅ {uid} is now an Admin.", reply_markup=owner_menu())
    await log_admin(OWNER_ID, f"Promoted {uid} to Admin")
    await state.clear()

@dp.callback_query(F.data == "o_rem_admin")
async def o_rem_admin(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.remove_admin)
    await call.message.edit_text("👤 Enter User ID to demote from Admin:")

@dp.message(OwnerStates.remove_admin)
async def o_rem_admin_id(message: types.Message, state: FSMContext):
    try: uid = int(message.text)
    except: return await message.answer("❌ Invalid ID.")
    async with db_pool.acquire() as conn:
        await conn.execute("DELETE FROM admins WHERE user_id = $1", uid)
    await message.answer(f"✅ {uid} removed from Admins.", reply_markup=owner_menu())
    await log_admin(OWNER_ID, f"Removed {uid} from Admin")
    await state.clear()

@dp.callback_query(F.data == "o_logs")
async def o_logs(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with db_pool.acquire() as conn:
        logs = await conn.fetch("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 10")
    text = "📜 <b>Recent Logs</b>\n\n"
    for log in logs:
        text += f"[{log['created_at'].strftime('%m-%d %H:%M')}] <code>{log['admin_id']}</code>: {log['action']}\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_panel")]]), parse_mode="HTML")

# --- OWNER SETTINGS ---
@dp.callback_query(F.data == "o_settings")
async def o_settings(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    upi = await get_setting("fampay_upi")
    key = await get_setting("fampay_api_key")
    text = f"⚙️ <b>Bot Settings</b>\n\nCurrent UPI: <code>{upi}</code>\nAPI Key: <code>{key[:10]}...</code>\n\nSelect what to update:"
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Change FamPay UPI", callback_data="o_set_upi")],
        [InlineKeyboardButton(text="🔑 Change API Key", callback_data="o_set_key")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="owner_panel")]
    ])
    await call.message.edit_text(text, reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "o_set_upi")
async def o_set_upi(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.set_fampay_upi)
    await call.message.edit_text("💳 Enter new FamPay UPI ID:")

@dp.message(OwnerStates.set_fampay_upi)
async def process_set_upi(message: types.Message, state: FSMContext):
    await set_setting("fampay_upi", message.text.strip())
    await message.answer("✅ FamPay UPI Updated successfully!", reply_markup=owner_menu())
    await log_admin(OWNER_ID, "Updated FamPay UPI")
    await state.clear()

@dp.callback_query(F.data == "o_set_key")
async def o_set_key(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.set_fampay_key)
    await call.message.edit_text("🔑 Enter new FamPay API Key:")

@dp.message(OwnerStates.set_fampay_key)
async def process_set_key(message: types.Message, state: FSMContext):
    await set_setting("fampay_api_key", message.text.strip())
    await message.answer("✅ API Key Updated successfully!", reply_markup=owner_menu())
    await log_admin(OWNER_ID, "Updated FamPay API Key")
    await state.clear()

# --- MAIN ---
async def main():
    await init_db()
    print("🚀 SASTA TG STORE is LIVE (Manual Mode)")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
  
