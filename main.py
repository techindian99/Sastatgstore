import asyncio
import logging
import asyncpg
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton

# --- CONFIGURATION ---
BOT_TOKEN = "YOUR_BOT_TOKEN_HERE" # BotFather se naya token dale
DB_URL = "postgresql://neondb_owner:npg_emAy3oL7auBU@ep-fancy-bird-bduwnbau-pooler.c-8.us-east-2.aws.neon.tech/neondb?sslmode=require&channel_binding=require"
OWNER_ID = 8494356106

logging.basicConfig(level=logging.INFO)
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
db_pool = None

# --- STATES ---
class UserStates(StatesGroup):
    dep_amount = State()
    dep_screenshot = State()
    dep_sender_name = State()
    support_msg = State()

class AdminStates(StatesGroup):
    add_country = State()
    add_price = State()
    add_creds = State()
    broadcast_msg = State()
    accept_order_creds = State()
    reply_user = State()
    
class OwnerStates(StatesGroup):
    add_admin = State()
    remove_admin = State()

# --- DATABASE SETUP ---
async def init_db():
    global db_pool
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
                id SERIAL PRIMARY KEY, country TEXT, price NUMERIC, credentials TEXT, is_sold BOOLEAN DEFAULT FALSE
            );
            CREATE TABLE IF NOT EXISTS orders (
                id SERIAL PRIMARY KEY, user_id BIGINT, account_id INT, price NUMERIC, status TEXT DEFAULT 'PENDING', admin_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS deposits (
                id SERIAL PRIMARY KEY, user_id BIGINT, amount NUMERIC, sender_name TEXT, screenshot_id TEXT, status TEXT DEFAULT 'PENDING', admin_id BIGINT, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT);
        ''')
        await conn.execute("INSERT INTO admins (user_id) VALUES ($1) ON CONFLICT DO NOTHING", OWNER_ID)
        await conn.execute("INSERT INTO settings (key, value) VALUES ('upi_id', 'yourupi@okaxis') ON CONFLICT DO NOTHING")

async def log_admin(admin_id, action):
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO admin_logs (admin_id, action) VALUES ($1, $2)", admin_id, action)

# --- ACCESS CHECK ---
async def is_admin(user_id):
    if user_id == OWNER_ID: return True
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow("SELECT user_id FROM admins WHERE user_id = $1", user_id)
        return bool(row)

# --- KEYBOARDS ---
def main_menu(is_admin=False):
    kb = [
        [InlineKeyboardButton(text="🛒 Store", callback_data="u_store"), InlineKeyboardButton(text="💰 Wallet", callback_data="u_wallet")],
        [InlineKeyboardButton(text="🎁 Refer & Earn", callback_data="u_refer"), InlineKeyboardButton(text="📦 My Orders", callback_data="u_orders")],
        [InlineKeyboardButton(text="📞 Support", callback_data="u_support")]
    ]
    if is_admin:
        kb.append([InlineKeyboardButton(text="🛡️ Admin Panel", callback_data="admin_panel")])
        if is_admin == 2: # Owner
            kb.append([InlineKeyboardButton(text="👑 Owner Control", callback_data="owner_panel")])
    return InlineKeyboardMarkup(inline_keyboard=kb)

def admin_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Stats", callback_data="a_stats"), InlineKeyboardButton(text="📥 Deposits", callback_data="a_deposits")],
        [InlineKeyboardButton(text="📦 Pending Orders", callback_data="a_orders"), InlineKeyboardButton(text="➕ Add Account", callback_data="a_add_acc")],
        [InlineKeyboardButton(text="📢 Broadcast", callback_data="a_broadcast")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="u_main")]
    ])

def owner_menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="➕ Add Admin", callback_data="o_add_admin"), InlineKeyboardButton(text="➖ Remove Admin", callback_data="o_rem_admin")],
        [InlineKeyboardButton(text="📜 Admin Logs", callback_data="o_logs"), InlineKeyboardButton(text="👥 Admin List", callback_data="o_list")],
        [InlineKeyboardButton(text="🔙 Main Menu", callback_data="u_main")]
    ])

# --- USER HANDLERS ---
@dp.message(CommandStart())
async def start_cmd(message: types.Message, state: FSMContext):
    await state.clear()
    user_id = message.from_user.id
    username = message.from_user.username or "NoUsername"
    
    async with db_pool.acquire() as conn:
        user = await conn.fetchrow("SELECT * FROM users WHERE user_id = $1", user_id)
        if user and user['is_banned']:
            return await message.answer("🚫 You are banned from using SASTA TG STORE.")
            
        if not user:
            referrer_id = None
            args = message.text.split()
            if len(args) > 1 and args[1].startswith("ref_"):
                try:
                    referrer_id = int(args[1].split("_")[1])
                    if referrer_id == user_id: referrer_id = None
                except: pass
            await conn.execute("INSERT INTO users (user_id, username, referrer_id) VALUES ($1, $2, $3)", user_id, username, referrer_id)
            if referrer_id:
                try: await bot.send_message(referrer_id, "🎉 New user joined via your link! 10% commission milega unki pehli purchase par.")
                except: pass

    admin_status = 2 if user_id == OWNER_ID else (1 if await is_admin(user_id) else 0)
    
    text = (
        "👑 <b>Welcome to SASTA TG STORE</b> 👑\n\n"
        "India's cheapest & most trusted Telegram account market.\n"
        "⚡️ <b>1 INR = 1 Coin</b>\n\n"
        "Select an option below:"
    )
    await message.answer(text, reply_markup=main_menu(admin_status), parse_mode="HTML")

@dp.callback_query(F.data == "u_main")
async def u_main(call: types.CallbackQuery, state: FSMContext):
    await state.clear()
    admin_status = 2 if call.from_user.id == OWNER_ID else (1 if await is_admin(call.from_user.id) else 0)
    await call.message.edit_text("👑 <b>SASTA TG STORE</b>\n\nSelect an option:", reply_markup=main_menu(admin_status), parse_mode="HTML")

# --- WALLET & DEPOSIT FLOW ---
@dp.callback_query(F.data == "u_wallet")
async def wallet(call: types.CallbackQuery):
    async with db_pool.acquire() as conn:
        bal = await conn.fetchval("SELECT balance FROM users WHERE user_id = $1", call.from_user.id)
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Deposit Now", callback_data="dep_start")],
        [InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]
    ])
    await call.message.edit_text(f"💰 <b>Your Wallet</b>\n\nBalance: <b>₹{bal:.2f}</b>\n(1 INR = 1 Coin)", reply_markup=kb, parse_mode="HTML")

@dp.callback_query(F.data == "dep_start")
async def dep_start(call: types.CallbackQuery, state: FSMContext):
    async with db_pool.acquire() as conn:
        upi_id = await conn.fetchval("SELECT value FROM settings WHERE key = 'upi_id'")
    await state.set_state(UserStates.dep_amount)
    await call.message.edit_text(f"💵 <b>Manual Deposit</b>\n\nEnter the amount (in ₹) you want to add:", parse_mode="HTML")

@dp.message(UserStates.dep_amount)
async def dep_amount(message: types.Message, state: FSMContext):
    try:
        amt = float(message.text)
        if amt <= 0: raise ValueError
        await state.update_data(amount=amt)
        await state.set_state(UserStates.dep_screenshot)
        qr_link = "https://api.qrserver.com/v1/create-qr-code/?size=300x300&data=upi://pay?pa=yourupi@okaxis&pn=SASTA%20TG%20STORE"
        await message.answer_photo(qr_link, caption=f"✅ Amount: ₹{amt}\n\nScan QR to pay. After paying, send the <b>Screenshot</b> here.", parse_mode="HTML")
    except ValueError:
        await message.answer("❌ Invalid amount. Send numbers only (e.g., 100).")

@dp.message(UserStates.dep_screenshot, F.photo)
async def dep_screenshot(message: types.Message, state: FSMContext):
    await state.update_data(screenshot=message.photo[-1].file_id)
    await state.set_state(UserStates.dep_sender_name)
    await message.answer("📝 Enter the <b>Sender Name</b> or UTR exactly as shown in your bank app:")

@dp.message(UserStates.dep_sender_name)
async def dep_sender_name(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        dep_id = await conn.fetchval("INSERT INTO deposits (user_id, amount, sender_name, screenshot_id) VALUES ($1, $2, $3, $4) RETURNING id", 
                                     message.from_user.id, data['amount'], message.text, data['screenshot'])
    
    await message.answer("✅ Deposit request sent! Admin will verify and add balance.", reply_markup=main_menu())
    await state.clear()
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Approve", callback_data=f"dep_app_{dep_id}"), InlineKeyboardButton(text="❌ Reject", callback_data=f"dep_rej_{dep_id}")]])
    admins = [OWNER_ID]
    async with db_pool.acquire() as conn:
        for r in await conn.fetch("SELECT user_id FROM admins"):
            if r['user_id'] not in admins: admins.append(r['user_id'])
            
    for adm in admins:
        try: await bot.send_photo(adm, data['screenshot'], caption=f"🚨 <b>NEW DEPOSIT</b>\nUser: <code>{message.from_user.id}</code>\nAmount: ₹{data['amount']}\nSender: {message.text}", reply_markup=kb, parse_mode="HTML")
        except: pass

# --- STORE & BUY FLOW ---
@dp.callback_query(F.data == "u_store")
async def browse(call: types.CallbackQuery):
    async with db_pool.acquire() as conn:
        rows = await conn.fetch("SELECT country, COUNT(*), MIN(price) FROM accounts WHERE is_sold = FALSE GROUP BY country")
    
    if not rows: return await call.answer("Abhi koi account available nahi hai.", show_alert=True)
    
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
        if not acc: return await call.answer("Account already sold!", show_alert=True)
        
        user = await conn.fetchrow("SELECT balance, has_purchased, referrer_id FROM users WHERE user_id = $1", uid)
        if user['balance'] < acc['price']:
            return await call.answer(f"❌ Insufficient balance! Need ₹{acc['price'] - user['balance']} more.", show_alert=True)
        
        await conn.execute("UPDATE users SET balance = balance - $1 WHERE user_id = $2", acc['price'], uid)
        await conn.execute("UPDATE accounts SET is_sold = TRUE WHERE id = $1", acc_id)
        order_id = await conn.fetchval("INSERT INTO orders (user_id, account_id, price) VALUES ($1, $2, $3) RETURNING id", uid, acc_id, acc['price'])
        
        if not user['has_purchased'] and user['referrer_id']:
            comm = acc['price'] * 0.10
            await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", comm, user['referrer_id'])
            await conn.execute("UPDATE users SET has_purchased = TRUE WHERE user_id = $1", uid)
            try: await bot.send_message(user['referrer_id'], f"🎉 You earned ₹{comm} (10%) from your referral's first purchase!")
            except: pass
    
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="✅ Send Creds", callback_data=f"ord_ok_{order_id}")]])
    admins = [OWNER_ID]
    async with db_pool.acquire() as conn:
        for r in await conn.fetch("SELECT user_id FROM admins"):
            if r['user_id'] not in admins: admins.append(r['user_id'])
            
    for adm in admins:
        try: await bot.send_message(adm, f"🚨 <b>NEW ORDER</b>\nUser: <code>{uid}</code>\nAccount: {acc['country']}\nPrice: ₹{acc['price']}\nOrder: #{order_id}", reply_markup=kb, parse_mode="HTML")
        except: pass
    
    await call.message.edit_text("✅ Order placed! Admin will send credentials shortly.")

# --- ADMIN PANEL HANDLERS ---
@dp.callback_query(F.data == "admin_panel")
async def open_admin_panel(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    await call.message.edit_text("🛡️ <b>Admin Dashboard</b>\n\nManage SASTA TG STORE:", reply_markup=admin_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "a_stats")
async def a_stats(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    async with db_pool.acquire() as conn:
        users = await conn.fetchval("SELECT COUNT(*) FROM users")
        orders = await conn.fetchval("SELECT COUNT(*) FROM orders WHERE status = 'COMPLETED'")
        deps = await conn.fetchval("SELECT COALESCE(SUM(amount), 0) FROM deposits WHERE status = 'APPROVED'")
    await call.message.edit_text(f"📊 <b>Bot Stats</b>\n\n👥 Total Users: {users}\n📦 Accounts Sold: {orders}\n💵 Total Deposits: ₹{deps}", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="admin_panel")]]), parse_mode="HTML")

@dp.callback_query(F.data == "a_add_acc")
async def a_add_acc(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id): return
    await state.set_state(AdminStates.add_country)
    await call.message.edit_text("🌍 Enter Country Name (e.g., India):")

@dp.message(AdminStates.add_country)
async def a_add_country(message: types.Message, state: FSMContext):
    await state.update_data(country=message.text)
    await state.set_state(AdminStates.add_price)
    await message.answer("💰 Enter Price (₹):")

@dp.message(AdminStates.add_price)
async def a_add_price(message: types.Message, state: FSMContext):
    try: price = float(message.text)
    except: return await message.answer("❌ Invalid price.")
    await state.update_data(price=price)
    await state.set_state(AdminStates.add_creds)
    await message.answer("🔑 Send Session String / Credentials:")

@dp.message(AdminStates.add_creds)
async def a_add_creds(message: types.Message, state: FSMContext):
    data = await state.get_data()
    async with db_pool.acquire() as conn:
        await conn.execute("INSERT INTO accounts (country, price, credentials) VALUES ($1, $2, $3)", data['country'], data['price'], message.text)
    await message.answer("✅ Account added!", reply_markup=admin_menu())
    await log_admin(message.from_user.id, f"Added account {data['country']} - ₹{data['price']}")
    await state.clear()

@dp.callback_query(F.data.startswith("dep_app_"))
async def dep_approve(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    dep_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id, amount, status FROM deposits WHERE id = $1", dep_id)
        if dep['status'] != 'PENDING': return await call.answer("Already processed!", show_alert=True)
        await conn.execute("UPDATE deposits SET status = 'APPROVED', admin_id = $1 WHERE id = $2", call.from_user.id, dep_id)
        await conn.execute("UPDATE users SET balance = balance + $1 WHERE user_id = $2", dep['amount'], dep['user_id'])
    
    await call.message.edit_caption(caption=f"✅ Deposit Approved: ₹{dep['amount']} for {dep['user_id']}")
    await bot.send_message(dep['user_id'], f"✅ Deposit of ₹{dep['amount']} approved by Admin!")
    await log_admin(call.from_user.id, f"Approved Deposit #{dep_id} (₹{dep['amount']})")

@dp.callback_query(F.data.startswith("dep_rej_"))
async def dep_reject(call: types.CallbackQuery):
    if not await is_admin(call.from_user.id): return
    dep_id = int(call.data.split("_")[2])
    async with db_pool.acquire() as conn:
        dep = await conn.fetchrow("SELECT user_id FROM deposits WHERE id = $1", dep_id)
        await conn.execute("UPDATE deposits SET status = 'REJECTED', admin_id = $1 WHERE id = $2", call.from_user.id, dep_id)
    await call.message.edit_caption(caption=f"❌ Deposit #{dep_id} Rejected")
    await bot.send_message(dep['user_id'], "❌ Your deposit was rejected. Contact support.")
    await log_admin(call.from_user.id, f"Rejected Deposit #{dep_id}")

@dp.callback_query(F.data.startswith("ord_ok_"))
async def acc_accept(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id): return
    order_id = int(call.data.split("_")[2])
    await state.update_data(order_id=order_id)
    await state.set_state(AdminStates.accept_order_creds)
    await call.message.answer("🔑 Send the Credentials for this order:")

@dp.message(AdminStates.accept_order_creds)
async def acc_send_creds(message: types.Message, state: FSMContext):
    data = await state.get_data()
    order_id = data['order_id']
    creds = message.text
    async with db_pool.acquire() as conn:
        order = await conn.fetchrow("SELECT user_id, account_id FROM orders WHERE id = $1", order_id)
        await conn.execute("UPDATE orders SET status = 'COMPLETED', admin_id = $1 WHERE id = $2", message.from_user.id, order_id)
        await conn.execute("UPDATE accounts SET credentials = $1 WHERE id = $2", creds, order['account_id'])
    
    await bot.send_message(order['user_id'], f"🎉 <b>Order #{order_id} Accepted!</b>\n\n🔑 Details:\n<code>{creds}</code>\n\n⚠️ Change password immediately. Reply here for support.", parse_mode="HTML")
    await message.answer(f"✅ Creds sent for Order #{order_id}.")
    await log_admin(message.from_user.id, f"Completed Order #{order_id}")
    await state.clear()

# --- OWNER PANEL HANDLERS ---
@dp.callback_query(F.data == "owner_panel")
async def open_owner_panel(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    await call.message.edit_text("👑 <b>Owner Dashboard</b>\n\nFull control over admins and logs:", reply_markup=owner_menu(), parse_mode="HTML")

@dp.callback_query(F.data == "o_add_admin")
async def o_add_admin(call: types.CallbackQuery, state: FSMContext):
    if call.from_user.id != OWNER_ID: return
    await state.set_state(OwnerStates.add_admin)
    await call.message.edit_text("👤 Enter User ID to promote to Admin:")

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
        logs = await conn.fetch("SELECT * FROM admin_logs ORDER BY id DESC LIMIT 15")
    text = "📜 <b>Recent Admin Logs</b>\n\n"
    for log in logs:
        text += f"[{log['created_at'].strftime('%m-%d %H:%M')}] <code>{log['admin_id']}</code>: {log['action']}\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_panel")]]), parse_mode="HTML")

@dp.callback_query(F.data == "o_list")
async def o_list(call: types.CallbackQuery):
    if call.from_user.id != OWNER_ID: return
    async with db_pool.acquire() as conn:
        admins = await conn.fetch("SELECT user_id FROM admins")
    text = "👥 <b>Current Admins</b>\n\n"
    for a in admins:
        if a['user_id'] != OWNER_ID:
            text += f"• <code>{a['user_id']}</code>\n"
    await call.message.edit_text(text, reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="owner_panel")]]), parse_mode="HTML")

# --- SUPPORT & BROADCAST ---
@dp.callback_query(F.data == "u_support")
async def support(call: types.CallbackQuery):
    await call.message.edit_text("📞 <b>Support</b>\n\nContact Owner directly: @TheRuthlessLord", reply_markup=InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="🔙 Back", callback_data="u_main")]]), parse_mode="HTML")

@dp.callback_query(F.data == "a_broadcast")
async def a_broadcast(call: types.CallbackQuery, state: FSMContext):
    if not await is_admin(call.from_user.id): return
    await state.set_state(AdminStates.broadcast_msg)
    await call.message.edit_text("📢 Send the message to broadcast to all users:")

@dp.message(AdminStates.broadcast_msg)
async def a_broadcast_msg(message: types.Message, state: FSMContext):
    async with db_pool.acquire() as conn:
        users = await conn.fetch("SELECT user_id FROM users")
    success = 0
    for u in users:
        try:
            await message.copy_to(u['user_id'])
            success += 1
            await asyncio.sleep(0.05)
        except: pass
    await message.answer(f"✅ Broadcast sent to {success} users.", reply_markup=admin_menu())
    await log_admin(message.from_user.id, f"Broadcasted to {success} users")
    await state.clear()

# --- RELAY CHAT (SECRET CHAT) ---
# User replies to bot's order message -> Bot forwards to admin
@dp.message(F.reply_to_message)
async def relay_to_admin(message: types.Message):
    if message.reply_to_message.text and "Order #" in message.reply_to_message.text:
        try:
            order_id = int(message.reply_to_message.text.split("Order #")[1].split(" ")[0].split("Accepted!")[0])
            async with db_pool.acquire() as conn:
                order = await conn.fetchrow("SELECT admin_id FROM orders WHERE id = $1", order_id)
            if order and order['admin_id']:
                await bot.send_message(order['admin_id'], f"💬 <b>User Message (Order #{order_id})</b>\n\nFrom: <code>{message.from_user.id}</code>\n\n{message.text}")
                await message.answer("✅ Message sent to Admin.")
        except Exception as e:
            pass

# --- MAIN ---
async def main():
    await init_db()
    print("SASTA TG STORE Bot is running...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
  
