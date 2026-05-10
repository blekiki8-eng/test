import os
import uuid
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    ReplyKeyboardMarkup, KeyboardButton, 
    InlineKeyboardMarkup, InlineKeyboardButton, ContentType
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("TEST_BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["test_game_bot_db"]
users_col = db["users"]
games_col = db["games"]
deposits_col = db["deposits"]

# --- СТАНИ ---
class Form(StatesGroup):
    wait_bet = State()
    wait_deposit_amount = State()
    wait_receipt = State()

# --- КЛАВІАТУРИ (Точно як на скріншоті) ---
def get_main_kb(user_id):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс"), KeyboardButton(text="🤝 Рефералка")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ЛОГІКА ЮЗЕРІВ ---
async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 0.0, "wins": 0, "losses": 0, "referals": 0}
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    u = await get_u(uid)
    
    # Обробка посилання на гру (start=game_...)
    args = m.text.split()
    if len(args) > 1 and args[1].startswith("game_"):
        gid = args[1].replace("game_", "")
        game = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if game:
            if game['creator_id'] == uid:
                return await m.answer("❌ Ви не можете грати з самим собою!")
            if u['balance'] < game['bet']:
                return await m.answer(f"❌ Недостатньо 💎. Ставка: {game['bet']}")
            
            await users_col.update_one({"_id": uid}, {"$inc": {"balance": -game['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": uid, "status": "playing", "turn": game['creator_id'],
                "c_throws": [], "o_throws": []
            }})
            await bot.send_message(game['creator_id'], "🎳 Гра почалася! Твій хід!")
            return await m.answer(f"🕹 Ви приєдналися до гри. Чекайте ходу.")

    await m.answer("👋 Ласкаво просимо!", reply_markup=get_main_kb(uid))

# --- ПРОФІЛЬ (Як на скріншоті) ---
@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **Профіль**\n"
            f"🆔 ID: `{m.from_user.id}`\n"
            f"💰 Баланс: {u['balance']} 💎")
    await m.answer(text, parse_mode="Markdown")

# --- БАЛАНС ТА ПОПОВНЕННЯ (Як на скріншоті) ---
@dp.message(F.text == "💎 Баланс")
async def balance_menu(m: types.Message):
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📥 Поповнити", callback_data="deposit")]
    ])
    u = await get_u(m.from_user.id)
    await m.answer(f"💎 Ваш баланс: {u['balance']} 💎", reply_markup=kb)

@dp.callback_query(F.data == "deposit")
async def dep_amount(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Введіть кількість 💎, яку хочете придбати:")
    await state.set_state(Form.wait_deposit_amount)

@dp.message(Form.wait_deposit_amount)
async def dep_invoice(m: types.Message, state: FSMContext):
    if not m.text.isdigit(): return await m.answer("Введіть число!")
    amount = int(m.text)
    price = amount * 46.73 # Ціна як на скріншоті
    
    text = (f"📥 **ЗАЯВКА НА ПОПОВНЕННЯ**\n\n"
            f"💎 Отримаєте: {float(amount)} 💎\n"
            f"💳 До сплати: {price} грн\n\n"
            f"📌 Карта Mono: `5355 2800 2890 2177`\n\n"
            f"Після оплати надішліть скріншот квитанції (фото або файл):")
    
    await m.answer(text, parse_mode="Markdown")
    await state.update_data(dep_amount=amount)
    await state.set_state(Form.wait_receipt)

@dp.message(Form.wait_receipt, F.photo | F.document)
async def handle_receipt(m: types.Message, state: FSMContext):
    data = await state.get_data()
    # Відправляємо адміну на перевірку
    await bot.send_message(ADMIN_ID, f"🔔 **НОВА ЗАЯВКА**\nЮзер: `{m.from_user.id}`\nСума: {data['dep_amount']} 💎")
    await bot.forward_message(ADMIN_ID, m.chat.id, m.message_id)
    
    await m.answer("✅ Квитанцію надіслано! Очікуйте підтвердження адміністратором.")
    await state.clear()

# --- GAMES ---
@dp.message(F.text == "🎮 Games")
async def games_menu(m: types.Message):
    kb = ReplyKeyboardMarkup(keyboard=[
        [KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="❌ Скасувати")]
    ], resize_keyboard=True)
    await m.answer("Оберіть гру:", reply_markup=kb)

@dp.message(F.text == "🎳 Боулінг")
async def bowl_bet(m: types.Message, state: FSMContext):
    await m.answer("Введіть суму ставки:")
    await state.set_state(Form.wait_bet)

@dp.message(Form.wait_bet)
async def bowl_create(m: types.Message, state: FSMContext):
    bet = float(m.text)
    gid = str(uuid.uuid4())[:8]
    await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"})
    
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=game_{gid}"
    await m.answer(f"🎳 Гра створена!\n{link}", reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

@dp.message(F.text == "❌ Скасувати")
async def cancel(m: types.Message, state: FSMContext):
    await state.clear()
    await m.answer("Дію скасовано.", reply_markup=get_main_kb(m.from_user.id))

# --- ЗАПУСК ---
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
