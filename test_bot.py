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
    InlineKeyboardMarkup, InlineKeyboardButton
)
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- КОНФІГУРАЦІЯ ---
load_dotenv()
API_TOKEN = os.getenv("TEST_BOT_TOKEN")
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

logging.basicConfig(level=logging.INFO)
bot = Bot(token=API_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# Підключення до БД (та сама, що в оригіналі)
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["test_game_bot_db"]
users_col = db["users"]
games_col = db["games"]

# --- СТАНЫ (FSM) ---
class GameStates(StatesGroup):
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def get_main_kb(user_id):
    buttons = [
        [KeyboardButton(text="👤 Профіль"), KeyboardButton(text="🎲 Створити гру")],
        [KeyboardButton(text="🤝 Рефералка"), KeyboardButton(text="💰 Баланс (FREE)")],
        [KeyboardButton(text="📊 Топ гравців"), KeyboardButton(text="ℹ️ Допомога")]
    ]
    if user_id == ADMIN_ID:
        buttons.append([KeyboardButton(text="🛡 Адмін-панель")])
    return ReplyKeyboardMarkup(keyboard=buttons, resize_keyboard=True)

# --- ДОПОМІЖНІ ФУНКЦІЇ ---
async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 1000.0, "referals": 0, "wins": 0, "losses": 0}
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ КОМАНД ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    u = await get_u(uid)
    
    # Реферальна система (як в оригіналі)
    args = m.text.split()
    if len(args) > 1:
        payload = args[1]
        # Якщо це приєднання до гри
        if payload.startswith("game_"):
            gid = payload.replace("game_", "")
            game = await games_col.find_one({"game_id": gid, "status": "waiting"})
            if game:
                if game['creator_id'] == uid:
                    return await m.answer("❌ Не можна грати з собою!")
                if u['balance'] < game['bet']:
                    return await m.answer("❌ Мало 💎")
                
                await users_col.update_one({"_id": uid}, {"$inc": {"balance": -game['bet']}})
                await games_col.update_one({"game_id": gid}, {"$set": {
                    "opponent_id": uid, "status": "playing", "turn": game['creator_id']
                }})
                await bot.send_message(game['creator_id'], "🔔 Гравець приєднався! Ваш хід — кидайте 🎳")
                return await m.answer(f"🕹 Ви зайшли в гру на {game['bet']} 💎. Чекайте ходу суперника.")
        
        # Якщо це реферал
        elif payload.isdigit() and int(payload) != uid:
            ref_id = int(payload)
            if not await users_col.find_one({"_id": uid}): # Тільки для нових
                await users_col.update_one({"_id": ref_id}, {"$inc": {"balance": 250, "referals": 1}})
                await bot.send_message(ref_id, "🤝 По вашому лінку прийшов новий гравець! +250 💎")

    await m.answer("🧪 Вітаю в ТЕСТ-БОТІ! Всі функції активовані.", reply_markup=get_main_kb(uid))

@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **ПРОФІЛЬ**\n\n"
            f"💰 Баланс: `{u['balance']}` 💎\n"
            f"🏆 Перемог: `{u.get('wins', 0)}` | Поразок: `{u.get('losses', 0)}`\n"
            f"🤝 Рефералів: `{u.get('referals', 0)}`ID: `{m.from_user.id}`")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🎲 Створити гру")
async def game_create(m: types.Message, state: FSMContext):
    await m.answer("Введіть суму ставки:")
    await state.set_state(GameStates.wait_bet)

@dp.message(GameStates.wait_bet)
async def set_bet(m: types.Message, state: FSMContext):
    if not m.text.isdigit():
        return await m.answer("❌ Введіть число!")
    
    bet = float(m.text)
    u = await get_u(m.from_user.id)
    if u['balance'] < bet:
        return await m.answer("❌ Недостатньо коштів!")

    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
    await games_col.insert_one({
        "game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting"
    })
    
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start=game_{gid}"
    await m.answer(f"🎲 Гра створена!\nСтавка: {bet} 💎\n\nЛінк: `{link}`", parse_mode="Markdown")
    await state.clear()

@dp.message(F.dice)
async def dice_logic(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    
    # Шукаємо активну гру
    game = await games_col.find_one({
        "status": "playing", "turn": uid,
        "$or": [{"creator_id": uid}, {"opponent_id": uid}]
    })
    
    if not game: return
    
    score = m.dice.value
    opp_id = game['opponent_id'] if game['creator_id'] == uid else game['creator_id']
    
    # Логіка гри (спрощено: хто кинув — той передав хід)
    await games_col.update_one({"game_id": game['game_id']}, {"$set": {"turn": opp_id}})
    await bot.send_message(opp_id, f"⚡️ Суперник збив {score} кеглів. Ваш хід!")
    await m.answer(f"Ви збили {score}! Хід передано.")

@dp.message(F.text == "🤝 Рефералка")
async def referral(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 Твоє посилання: `{link}`\n\nЗа кожного друга отримаєш 250 💎", parse_mode="Markdown")

@dp.message(F.text == "💰 Баланс (FREE)")
async def free_gems(m: types.Message):
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": 500}})
    await m.answer("🎁 +500 тестових 💎 на баланс!")

@dp.message(F.text == "🛡 Адмін-панель")
async def admin_p(m: types.Message):
    if m.from_user.id != ADMIN_ID: return
    await m.answer("🛡 Вітаю в адмінці. Тут можна розсилати повідомлення або редагувати БД.")

# Запуск
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
