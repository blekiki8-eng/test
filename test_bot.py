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
    InlineKeyboardMarkup, InlineKeyboardButton, BotCommand
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

# --- СТАНИ ---
class Form(StatesGroup):
    wait_bet = State()
    admin_broadcast = State()

# --- КЛАВІАТУРИ ---
def get_main_kb(user_id):
    kb = [
        [KeyboardButton(text="🎳 Грати (Боулінг)"), KeyboardButton(text="👤 Профіль")],
        [KeyboardButton(text="📊 Топ гравців"), KeyboardButton(text="🤝 Рефералка")],
        [KeyboardButton(text="💎 FREE Coins")]
    ]
    if user_id == ADMIN_ID:
        kb.append([KeyboardButton(text="🛡 АДМІН-ПАНЕЛЬ")])
    return ReplyKeyboardMarkup(keyboard=kb, resize_keyboard=True)

# --- ЛОГІКА ЮЗЕРІВ ---
async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        u = {"_id": user_id, "balance": 1000.0, "wins": 0, "losses": 0, "referals": 0}
        await users_col.insert_one(u)
    return u

# --- ОБРОБНИКИ ---

@dp.message(Command("start"))
async def cmd_start(m: types.Message, state: FSMContext):
    await state.clear()
    uid = m.from_user.id
    u = await get_u(uid)
    
    args = m.text.split()
    if len(args) > 1:
        payload = args[1]
        # Приєднання до гри
        if payload.startswith("game_"):
            gid = payload.replace("game_", "")
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
                await bot.send_message(game['creator_id'], "🔔 Гравець приєднався! Твій хід — кидай 🎳")
                return await m.answer(f"🕹 Ви приєдналися до гри на {game['bet']} 💎. Чекайте хід суперника.")
        
        # Рефералка
        elif payload.isdigit() and int(payload) != uid:
            if not await users_col.find_one({"_id": uid}): # Якщо юзер новий
                ref_id = int(payload)
                await users_col.update_one({"_id": ref_id}, {"$inc": {"balance": 250, "referals": 1}})
                await bot.send_message(ref_id, "🤝 Новий реферал! +250 💎")

    await m.answer(f"🎳 Вітаємо у TEST BOT!\nВаш баланс: {u['balance']} 💎", reply_markup=get_main_kb(uid))

@dp.message(F.text == "👤 Профіль")
async def profile(m: types.Message):
    u = await get_u(m.from_user.id)
    text = (f"👤 **ПРОФІЛЬ**\n\n"
            f"💰 Баланс: `{u['balance']}` 💎\n"
            f"🏆 Перемоги: `{u['wins']}`\n"
            f"💀 Поразки: `{u['losses']}`\n"
            f"🤝 Реферали: `{u['referals']}`\n"
            f"🆔 ID: `{m.from_user.id}`")
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🎳 Грати (Боулінг)")
async def create_game_init(m: types.Message, state: FSMContext):
    await m.answer("Напишіть ставку для гри (число):")
    await state.set_state(Form.wait_bet)

@dp.message(Form.wait_bet)
async def create_game_final(m: types.Message, state: FSMContext):
    if not m.text.replace('.','',1).isdigit():
        return await m.answer("❌ Введіть число!")
    
    bet = float(m.text)
    u = await get_u(m.from_user.id)
    if u['balance'] < bet: return await m.answer("❌ Недостатньо 💎")

    gid = str(uuid.uuid4())[:8]
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
    await games_col.insert_one({
        "game_id": gid, "creator_id": m.from_user.id, "bet": bet, 
        "status": "waiting", "c_throws": [], "o_throws": []
    })
    
    bot_info = await bot.get_me()
    link = f"https://t.me/{bot_info.username}?start=game_{gid}"
    await m.answer(f"🎲 Гра створена!\nСтавка: {bet} 💎\n\nВідправ посилання другу:\n`{link}`", 
                   parse_mode="Markdown", reply_markup=get_main_kb(m.from_user.id))
    await state.clear()

@dp.message(F.dice)
async def bowling_engine(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    
    g = await games_col.find_one({"status": "playing", "turn": uid, "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g: return

    val = m.dice.value
    gid = g['game_id']
    is_creator = (uid == g['creator_id'])
    
    # Записуємо кидок
    field = "c_throws" if is_creator else "o_throws"
    await games_col.update_one({"game_id": gid}, {"$push": {field: val}})
    
    # Отримуємо оновлені дані гри
    g = await games_col.find_one({"game_id": gid})
    c_len, o_len = len(g['c_throws']), len(g['o_throws'])
    opp_id = g['opponent_id'] if is_creator else g['creator_id']

    # Логіка завершення гри (по 5 кидків)
    if c_len >= 5 and o_len >= 5:
        sum_c, sum_o = sum(g['c_throws']), sum(g['o_throws'])
        win_amt = g['bet'] * 1.9
        
        if sum_c == sum_o:
            await users_col.update_one({"_id": g['creator_id']}, {"$inc": {"balance": g['bet']}})
            await users_col.update_one({"_id": g['opponent_id']}, {"$inc": {"balance": g['bet']}})
            res_text = f"🤝 Нічия! Обидва набрали по {sum_c}. Ставки повернуті."
        else:
            winner_id = g['creator_id'] if sum_c > sum_o else g['opponent_id']
            loser_id = g['opponent_id'] if sum_c > sum_o else g['creator_id']
            await users_col.update_one({"_id": winner_id}, {"$inc": {"balance": win_amt, "wins": 1}})
            await users_col.update_one({"_id": loser_id}, {"$inc": {"losses": 1}})
            res_text = f"🏆 Переміг той, хто набрав більше! ({max(sum_c, sum_o)} vs {min(sum_c, sum_o)})\nВиграш: {win_amt} 💎"

        await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished"}})
        await bot.send_message(g['creator_id'], res_text)
        await bot.send_message(g['opponent_id'], res_text)
    else:
        # Передача ходу
        await games_col.update_one({"game_id": gid}, {"$set": {"turn": opp_id}})
        await m.answer(f"Твій кидок: {val} 🎳. Передаю хід!")
        await bot.send_message(opp_id, f"🔔 Твій хід! Суперник кинув на {val}.")

@dp.message(F.text == "📊 Топ гравців")
async def top_players(m: types.Message):
    cursor = users_col.find().sort("balance", -1).limit(10)
    top_list = await cursor.to_list(length=10)
    text = "🏆 **ТОП-10 БАГАТІЇВ:**\n\n"
    for i, user in enumerate(top_list, 1):
        text += f"{i}. ID {user['_id']} — `{user['balance']}` 💎\n"
    await m.answer(text, parse_mode="Markdown")

@dp.message(F.text == "🤝 Рефералка")
async def ref_link(m: types.Message):
    me = await bot.get_me()
    link = f"https://t.me/{me.username}?start={m.from_user.id}"
    await m.answer(f"🤝 Запрошуй друзів та отримуй 250 💎 за кожного!\n\nТвоє посилання:\n`{link}`", parse_mode="Markdown")

@dp.message(F.text == "💎 FREE Coins")
async def free_money(m: types.Message):
    await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": 1000}})
    await m.answer("🧪 Тестові 1000 💎 нараховано!")

# --- АДМІНКА ---
@dp.message(F.text == "🛡 АДМІН-ПАНЕЛЬ")
async def admin_main(m: types.Message, state: FSMContext):
    if m.from_user.id != ADMIN_ID: return
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="📢 Розсилка", callback_data="broadcast")]])
    await m.answer("Вітаю, Адмін! Оберіть дію:", reply_markup=kb)

@dp.callback_query(F.data == "broadcast")
async def start_broadcast(cb: types.CallbackQuery, state: FSMContext):
    await cb.message.answer("Надішліть текст для розсилки (або напишіть 'відміна'):")
    await state.set_state(Form.admin_broadcast)

@dp.message(Form.admin_broadcast)
async def process_broadcast(m: types.Message, state: FSMContext):
    if m.text.lower() == 'відміна':
        await state.clear()
        return await m.answer("Скасовано.")
    
    users = await users_col.find().to_list(length=10000)
    count = 0
    for u in users:
        try:
            await bot.send_message(u['_id'], m.text)
            count += 1
            await asyncio.sleep(0.05) # Захист від спам-флуду
        except: continue
    
    await m.answer(f"✅ Розсилка завершена. Отримали {count} юзерів.")
    await state.clear()

async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
