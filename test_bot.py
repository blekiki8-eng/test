import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

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

class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class GameState(StatesGroup):
    wait_mode = State()
    wait_bet = State()

def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Профіль (TEST)"), KeyboardButton(text="🎮 Games")],
        [KeyboardButton(text="💎 Баланс (FREE)"), KeyboardButton(text="🤝 Рефералка")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡 Панель адміна")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 1000.0}
        await users_col.insert_one(u)
    return u

@dp.message(Command("start"))
async def start_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    u = await get_u(m.from_user.id)
    
    args = m.text.split()[1:] if len(m.text.split()) > 1 else None
    if args and args[0].startswith("game_"):
        gid = args[0].replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            if u['balance'] < g['bet']: return await m.answer("❌ Не вистачає 💎")
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id'],
                "c_throws": [], "o_throws": []
            }})
            await bot.send_message(g['creator_id'], "🧪 Гра почалася! Твій хід 🎳")
            await m.answer("🧪 Приєдналися! Хід суперника.", reply_markup=main_menu(m.from_user.id))
            return

    await m.answer("🧪 ТЕСТОВИЙ БОТ (v3)\nНараховано 1000 💎", reply_markup=main_menu(m.from_user.id))

@dp.message(F.text == "👤 Профіль (TEST)")
async def prof(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"🧪 **ПРОФІЛЬ**\n💰 Баланс: {u['balance']} 💎", parse_mode="Markdown")

@dp.message(F.text == "🎮 Games")
async def g_menu(m: types.Message):
    kb = [[KeyboardButton(text="🎳 Боулінг"), KeyboardButton(text="❌ Скасувати")]]
    await m.answer("Оберіть гру:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(F.text == "🎳 Боулінг")
async def b_start(m: types.Message, state: FSMContext):
    await state.set_state(GameState.wait_mode)
    kb = [[KeyboardButton(text="Матч 1"), KeyboardButton(text="Матч 5")]]
    await m.answer("Режим:", reply_markup=ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True))

@dp.message(GameState.wait_mode)
async def b_mode(m: types.Message, state: FSMContext):
    await state.update_data(mode=m.text)
    await state.set_state(GameState.wait_bet)
    await m.answer("Ставка (число):")

@dp.message(GameState.wait_bet)
async def b_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": gid, "creator_id": m.from_user.id, 
            "bet": bet, "status": "waiting", "mode": data['mode']
        })
        link = f"https://t.me/{(await bot.get_me()).username}?start=game_{gid}"
        await m.answer(f"🛠 Гра створена:\n{link}", reply_markup=main_menu(m.from_user.id))
        await state.clear()
    except: await m.answer("Введіть число!")

@dp.message(F.dice)
async def dice_handler(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g or g.get('turn') != uid: return 

    val = m.dice.value
    # Тут логіка кидків як раніше (спрощена для тесту)
    await m.answer(f"Ви збили {val} кеглів!")

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
