import os, uuid, logging, asyncio
from aiogram import Bot, Dispatcher, executor, types
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, ReplyKeyboardMarkup, KeyboardButton
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters.state import State, StatesGroup
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv

# --- НАЛАШТУВАННЯ ---
load_dotenv()
API_TOKEN = os.getenv("TEST_BOT_TOKEN") # Токен ТЕСТОВОГО бота
MONGO_URL = os.getenv("MONGO_URL")
ADMIN_ID = int(os.getenv("ADMIN_ID")) if os.getenv("ADMIN_ID") else 0

RATE = 44.50 
logging.basicConfig(level=logging.INFO)
storage = MemoryStorage()
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot, storage=storage)

# Підключення до окремої ТЕСТОВОЇ бази даних
cluster = AsyncIOMotorClient(MONGO_URL)
db = cluster["test_game_bot_db"] 
users_col = db["users"]
games_col = db["games"]

# --- СТАНИ FSM ---
class DepositState(StatesGroup):
    wait_amount = State()
    wait_receipt = State()

class WithdrawState(StatesGroup):
    wait_amount = State()
    wait_details = State()

class GameState(StatesGroup):
    wait_mode = State()
    wait_bet = State()

# --- КЛАВІАТУРИ ---
def main_menu(user_id):
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("👤 Профіль (TEST)"), KeyboardButton("🎮 Games"))
    markup.add(KeyboardButton("💎 Баланс (FREE)"), KeyboardButton("🤝 Рефералка"))
    if user_id == ADMIN_ID:
        markup.add(KeyboardButton("🛡 Адмін-Панель"))
    return markup

def games_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("🎳 Боулінг"), KeyboardButton("❌ Скасувати"))

def match_selection_menu():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("Матч 1"), KeyboardButton("Матч 5"), KeyboardButton("❌ Скасувати"))

def cancel_kb():
    return ReplyKeyboardMarkup(resize_keyboard=True).add(KeyboardButton("❌ Скасувати"))

async def get_u(user_id):
    u = await users_col.find_one({"_id": user_id})
    if not u:
        # У ТЕСТОВОМУ боті даємо 1000 💎 відразу для перевірок
        u = {"_id": user_id, "balance": 1000.0, "referred_count": 0}
        await users_col.insert_one(u)
    return u

# --- КОМАНДИ ---
@dp.message_handler(lambda m: m.text == "❌ Скасувати", state="*")
async def cancel(m: types.Message, state: FSMContext):
    await state.finish()
    await m.answer("🧪 Тестову дію скасовано.", reply_markup=main_menu(m.from_user.id))

@dp.message_handler(commands=['start'], state="*")
async def start_cmd(m: types.Message, state: FSMContext):
    await state.finish()
    args = m.get_args()
    u = await get_u(m.from_user.id)
    
    if args and args.startswith("game_"):
        gid = args.replace("game_", "")
        g = await games_col.find_one({"game_id": gid, "status": "waiting"})
        if g and g['creator_id'] != m.from_user.id:
            await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -g['bet']}})
            await games_col.update_one({"game_id": gid}, {"$set": {
                "opponent_id": m.from_user.id, "status": "playing", "turn": g['creator_id'],
                "c_throws": [], "o_throws": []
            }})
            await bot.send_message(g['creator_id'], f"🧪 [TEST] Гра почалася!\nТвій хід 🎳")
            await m.answer(f"🧪 [TEST] Приєдналися до гри!\nСтавка: {g['bet']} 💎. Зараз хід суперника.", reply_markup=main_menu(m.from_user.id))
            return
            
    await m.answer("🧪 Ласкаво просимо до ТЕСТОВАНОГО БОТА!\n\nТут нараховано 1000 безкоштовних 💎 для тестів.", reply_markup=main_menu(m.from_user.id))

# --- ЛОГІКА ГРИ ---
@dp.message_handler(content_types=types.ContentTypes.DICE, state="*")
async def dice_handler(m: types.Message):
    if m.dice.emoji != "🎳": return
    uid = m.from_user.id
    g = await games_col.find_one({"status": "playing", "$or": [{"creator_id": uid}, {"opponent_id": uid}]})
    if not g or g.get('turn') != uid: return 

    val = m.dice.value
    cid, oid, gid = g['creator_id'], g['opponent_id'], g['game_id']
    max_r = 5 if g.get('mode') == "Матч 5" else 1
    c_t, o_t = g.get('c_throws', []), g.get('o_throws', [])

    if uid == cid:
        c_t.append(val)
        await games_col.update_one({"game_id": gid}, {"$set": {"c_throws": c_t, "turn": oid}})
        await m.answer(f"Ви вибили {val} 🎳 (Раунд {len(c_t)}/{max_r})\n**Зараз хід суперника ⏳**")
        await bot.send_message(oid, f"Суперник вибив {val}. **Твій хід! 🎳**")
    else:
        o_t.append(val)
        if len(o_t) >= max_r:
            cs, os = sum(c_t), sum(o_t)
            win = round(g['bet'] * 2, 2)
            await games_col.update_one({"game_id": gid}, {"$set": {"status": "finished", "o_throws": o_t}})
            res = f"🏁 [TEST] Фінал: {cs} : {os}\n"
            if cs > os:
                await users_col.update_one({"_id": cid}, {"$inc": {"balance": win}})
                res_c, res_o = "🏆 Перемога! (Test)", "📉 Програш."
            elif os > cs:
                await users_col.update_one({"_id": oid}, {"$inc": {"balance": win}})
                res_o, res_c = "🏆 Перемога! (Test)", "📉 Програш."
            else:
                await users_col.update_many({"_id": {"$in":[cid,oid]}}, {"$inc": {"balance": g['bet']}})
                res_c = res_o = "🤝 Нічия!"
            await bot.send_message(cid, res + res_c)
            await bot.send_message(oid, res + res_o)
        else:
            await games_col.update_one({"game_id": gid}, {"$set": {"o_throws": o_t, "turn": cid}})
            next_r = len(o_t) + 1
            await m.answer(f"Ви вибили {val} 🎳\n**Зараз хід суперника (Раунд {next_r}) ⏳**")
            await bot.send_message(cid, f"Суперник вибив {val}. **Твій хід! (Раунд {next_r}) 🎳**")

# --- СТВОРЕННЯ ГРИ ---
@dp.message_handler(lambda m: m.text == "🎮 Games", state="*")
async def g_menu(m: types.Message):
    await m.answer("Оберіть гру (TEST MODE):", reply_markup=games_menu())

@dp.message_handler(lambda m: m.text == "🎳 Боулінг", state="*")
async def b_menu(m: types.Message):
    await GameState.wait_mode.set()
    await m.answer("Оберіть режим матчу:", reply_markup=match_selection_menu())

@dp.message_handler(lambda m: m.text in ["Матч 1", "Матч 5"], state=GameState.wait_mode)
async def mode_set(m: types.Message, state: FSMContext):
    await state.update_data(mode=m.text)
    await GameState.wait_bet.set()
    await m.answer(f"Введіть ставку (💎):", reply_markup=cancel_kb())

@dp.message_handler(state=GameState.wait_bet)
async def bet_set(m: types.Message, state: FSMContext):
    try:
        bet = float(m.text.replace(',', '.'))
        data = await state.get_data()
        gid = str(uuid.uuid4())[:8]
        await users_col.update_one({"_id": m.from_user.id}, {"$inc": {"balance": -bet}})
        await games_col.insert_one({"game_id": gid, "creator_id": m.from_user.id, "bet": bet, "status": "waiting", "mode": data['mode']})
        me = await bot.get_me()
        await m.answer(f"🛠 [TEST] Гра створена!\n`https://t.me/{me.username}?start=game_{gid}`", reply_markup=main_menu(m.from_user.id), parse_mode="Markdown")
        await state.finish()
    except: await m.answer("Число!")

# --- БАЛАНС / ВИВІД / ПОПОВНЕННЯ ---
@dp.message_handler(lambda m: m.text == "💎 Баланс (FREE)", state="*")
async def bal(m: types.Message):
    u = await get_u(m.from_user.id)
    kb = InlineKeyboardMarkup().add(
        InlineKeyboardButton("💳 Тест Поповнення", callback_data="dep"),
        InlineKeyboardButton("📤 Тест Вивід", callback_data="with")
    )
    await m.answer(f"🧪 Твій TEST Баланс: {round(u['balance'], 2)} 💎", reply_markup=kb)

@dp.callback_query_handler(lambda c: c.data == "dep", state="*")
async def dep_start(c: types.CallbackQuery):
    await DepositState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Введіть суму для ТЕСТОВОГО поповнення:", reply_markup=cancel_kb())

@dp.message_handler(state=DepositState.wait_amount)
async def dep_amt(m: types.Message, state: FSMContext):
    await state.update_data(amt=m.text)
    await m.answer("Надішліть БУДЬ-ЯКЕ фото (імітація квитанції):")
    await DepositState.wait_receipt.set()

@dp.message_handler(state=DepositState.wait_receipt, content_types=['photo'])
async def dep_rec(m: types.Message, state: FSMContext):
    d = await state.get_data()
    user_mention = f"@{m.from_user.username}" if m.from_user.username else f"ID: {m.from_user.id}"
    kb = InlineKeyboardMarkup().add(InlineKeyboardButton("✅ Підтвердити (TEST)", callback_data=f"adm_ok:{m.from_user.id}:{d['amt']}"))
    await bot.send_photo(ADMIN_ID, m.photo[-1].file_id, caption=f"🧪 ТЕСТ ПОПОВНЕННЯ\nВід: {user_mention}\nСума: {d['amt']}", reply_markup=kb)
    await m.answer("✅ Тестову заявку надіслано.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data == "with", state="*")
async def w_start(c: types.CallbackQuery):
    await WithdrawState.wait_amount.set()
    await bot.send_message(c.from_user.id, "Сума виводу (TEST):", reply_markup=cancel_kb())

@dp.message_handler(state=WithdrawState.wait_amount)
async def w_amt(m: types.Message, state: FSMContext):
    await state.update_data(amt=m.text)
    await m.answer("Введіть будь-які дані для виводу:")
    await WithdrawState.wait_details.set()

@dp.message_handler(state=WithdrawState.wait_details)
async def w_fin(m: types.Message, state: FSMContext):
    d = await state.get_data()
    user_mention = f"@{m.from_user.username}" if m.from_user.username else f"ID: {m.from_user.id}"
    await bot.send_message(ADMIN_ID, f"🧪 **ТЕСТ ВИВІД**\nКористувач: {user_mention}\nСума: {d['amt']}\nДані: {m.text}")
    await m.answer("✅ Тестову заявку прийнято.")
    await state.finish()

@dp.callback_query_handler(lambda c: c.data.startswith("adm_ok:"), state="*")
async def admin_ok(c: types.CallbackQuery):
    if c.from_user.id != ADMIN_ID: return
    _, uid, amt = c.data.split(":")
    await users_col.update_one({"_id": int(uid)}, {"$inc": {"balance": float(amt)}})
    await bot.send_message(int(uid), f"🧪 Нараховано {amt} TEST 💎!")
    await c.message.delete()

@dp.message_handler(lambda m: m.text == "👤 Профіль (TEST)", state="*")
async def prof(m: types.Message):
    u = await get_u(m.from_user.id)
    await m.answer(f"🧪 **ПРОФІЛЬ (ТЕСТ)**\n🆔 ID: `{m.from_user.id}`\n💰 Баланс: {round(u['balance'], 2)} 💎", parse_mode="Markdown")

if __name__ == '__main__':
    executor.start_polling(dp, skip_updates=True)
