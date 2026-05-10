import os
import uuid
import logging
import asyncio
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# Налаштування
load_dotenv()
API_TOKEN = os.getenv("TEST_BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)

# Ініціалізація бота (Aiogram 3.x)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# База даних
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["test_game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# Стани
class GameState(StatesGroup):
    wait_bet = State()

# Клавіатури
def main_menu(user_id):
    kb = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎮 Створити гру")],
        [KeyboardButton(text="💎 Безкоштовні монети"), KeyboardButton(text="🤝 Рефералка")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡 Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# Допоміжна функція отримання юзера
async def get_user(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 1000.0, "referals": 0}
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ ---

@dp.message(Command("start"))
async def start_cmd(m: types.Message, state: FSMContext):
    await state.clear()
    user_id = m.from_user.id
    u = await get_user(user_id)
    
    # Перевірка на вхід за посиланням (приєднання до гри)
    args = m.text.split()[1:] if len(m.text.split()) > 1 else None
    if args and args[0].startswith("game_"):
        game_id = args[0].replace("game_", "")
        game = await games_col.find_one({"game_id": game_id, "status": "waiting"})
        
        if game:
            if game['creator_id'] == user_id:
                return await m.answer("❌ Ви не можете грати самі з собою!")
            if u['balance'] < game['bet']:
                return await m.answer("❌ Недостатньо 💎 для ставки!")
            
            # Початок гри
            await users_col.update_one({"_id": user_id}, {"$inc": {"balance": -game['bet']}})
            await games_col.update_one({"game_id": game_id}, {"$set": {
                "opponent_id": user_id,
                "status": "playing",
                "turn": game['creator_id']
            }})
            
            await bot.send_message(game['creator_id'], "✅ Гравця знайдено! Ваш хід — кидайте 🎳")
            await m.answer(f"🕹 Ви приєдналися до гри на {game['bet']} 💎\nЧекайте на хід суперника.")
            return

    await m.answer("🧪 Вітаємо у ТЕСТОВОМУ боті!\nТут все як у оригіналі, але для тестів.", reply_markup=main_menu(user_id))

@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_user(m.from_user.id)
    await m.answer(f"👤 **ВАШ ПРОФІЛЬ**\n\n💰 Баланс: `{u['balance']}` 💎\n🤝 Рефералів: `{u.get('referals', 0)}`", parse_mode="Markdown")

@dp.message(F.text == "🎮 Створити гру")
async def create_game_start(m: types.Message, state: FSMContext):
    await m.answer("Напишіть суму ставки (число):")
    await state.set_state(GameState.wait_bet)

@dp.message(GameState.wait_bet)
async def process_bet(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text)
        u = await get_user(m.from_user.id)
        
        if bet <= 0: raise ValueError
        if u['balance'] < bet:
            return await m.answer("❌ У вас недостатньо коштів!")
        
        game_id = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({
            "game_id": game_id,
            "creator_id": m.from_user.id,
            "bet": bet,
            "status": "waiting"
        })
        
        bot_user = await bot.get_me()
        link = f"https://t.me/{bot_user.username}?start=game_{game_id}"
        
        await m.answer(f"🎲 Гра створена на {bet} 💎\n\nНадішліть це посилання другу:\n`{link}`", 
                     parse_mode="Markdown", reply_markup=main_menu(m.from_user.id))
        await state.clear()
    except ValueError:
        await m.answer("❌ Будь ласка, введіть коректне число.")

@dp.message(F.dice)
async def handle_dice(m: types.Message):
    # Логіка кидка кубика/боулінгу
    if m.dice.emoji != "🎳": return
    
    uid = m.from_user.id
    game = await games_col.find_one({
        "status": "playing", 
        "$or": [{"creator_id": uid}, {"opponent_id": uid}],
        "turn": uid
    })
    
    if not game:
        return # Не його хід або немає гри

    # Спрощена логіка результату (можна додати підрахунок очок за 5 кидків)
    score = m.dice.value
    opponent_id = game['opponent_id'] if game['creator_id'] == uid else game['creator_id']
    
    await m.answer(f"Ви збили: {score} 🎳")
    await bot.send_message(opponent_id, f"Суперник збив: {score} 🎳. Тепер ваш хід!")
    
    # Перемикаємо хід
    await games_col.update_one({"game_id": game['game_id']}, {"$set": {"turn": opponent_id}})

@dp.message(F.text == "💎 Безкоштовні монети")
async def free_money(m: types.Message):
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": 100.0}})
    await m.answer("🎁 Тобі нараховано 100 тестових 💎!")

async def main():
    print("Бот запущений...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
