import asyncio
import random
import sqlite3
import json
from datetime import datetime, timedelta
from io import BytesIO
import logging

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup

# Импортируем PIL для рисования колеса
from PIL import Image, ImageDraw, ImageFont

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = "8274028629:AAGOTRJWn9Ua6Es2ajdeRajwzZgnlkLctbQ"
ADMIN_ID = 8210954671  # Ваш Telegram ID для администрирования

# Пути к файлам
DB_NAME = "fortune_bot.db"
WHEEL_IMAGE_PATH = "wheel_temp.png"

# Настройка логирования
logging.basicConfig(level=logging.INFO)

# Сектора колеса (туркменские названия, призы)
# Вероятности: 90%, 1%, 0.5%, 0.5%, 8%
SECTORS = [
    {"name": "0 TMT", "color": (200, 200, 200), "type": "prize", "value": 0, "probability": 90},
    {"name": "Promo5 TMT", "color": (100, 200, 255), "type": "prize", "value": 5, "probability": 1},
    {"name": "Promo10 TMT", "color": (255, 215, 0), "type": "prize", "value": 10, "probability": 0.5},
    {"name": "Promo20 TMT", "color": (255, 165, 0), "type": "prize", "value": 20, "probability": 0.5},
    {"name": "Depozit +10%", "color": (255, 105, 180), "type": "bonus", "value": 10, "probability": 8}
]

# Нормализация вероятностей (приведение к сумме 100%)
total_prob = sum(s["probability"] for s in SECTORS)
for sector in SECTORS:
    sector["probability"] = sector["probability"] / total_prob * 100

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS users (
            user_id INTEGER PRIMARY KEY,
            username TEXT,
            full_name TEXT,
            balance INTEGER DEFAULT 0,
            total_won INTEGER DEFAULT 0,
            last_spin TEXT,
            bonus_active INTEGER DEFAULT 0,
            bonus_until TEXT,
            spins_count INTEGER DEFAULT 0
        )
    ''')
    conn.commit()
    
    # Создаем таблицу для истории выигрышей
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS spin_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            prize_type TEXT,
            prize_value INTEGER,
            spin_date TEXT
        )
    ''')
    conn.commit()
    conn.close()

def get_user(user_id, username=None, full_name=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT balance, total_won, last_spin, bonus_active, bonus_until, spins_count FROM users WHERE user_id = ?", (user_id,))
    result = cursor.fetchone()
    
    if result:
        # Проверяем, не истек ли бонус
        if result[3] and result[4]:
            if datetime.now() > datetime.fromisoformat(result[4]):
                cursor.execute("UPDATE users SET bonus_active = 0, bonus_until = NULL WHERE user_id = ?", (user_id,))
                conn.commit()
                return result[0], result[1], result[2], 0, None, result[5]
        return result
    else:
        # Создаем нового пользователя
        cursor.execute("INSERT INTO users (user_id, username, full_name, balance, spins_count) VALUES (?, ?, ?, ?, ?)", 
                      (user_id, username, full_name, 0, 0))
        conn.commit()
        conn.close()
        return 0, 0, None, 0, None, 0

def update_user(user_id, balance, total_won, last_spin, spins_count, bonus_active=None, bonus_until=None):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET balance = ?, total_won = ?, last_spin = ?, spins_count = ? WHERE user_id = ?", 
                  (balance, total_won, last_spin, spins_count, user_id))
    if bonus_active is not None:
        cursor.execute("UPDATE users SET bonus_active = ?, bonus_until = ? WHERE user_id = ?", 
                      (bonus_active, bonus_until, user_id))
    conn.commit()
    conn.close()

def save_spin_history(user_id, prize_type, prize_value):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("INSERT INTO spin_history (user_id, prize_type, prize_value, spin_date) VALUES (?, ?, ?, ?)",
                  (user_id, prize_type, prize_value, datetime.now().isoformat()))
    conn.commit()
    conn.close()

def set_bonus(user_id, hours=24):
    bonus_until = (datetime.now() + timedelta(hours=hours)).isoformat()
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET bonus_active = 1, bonus_until = ? WHERE user_id = ?", (bonus_until, user_id))
    conn.commit()
    conn.close()

def can_spin(user_id, last_spin_str):
    if not last_spin_str:
        return True
    last_spin = datetime.fromisoformat(last_spin_str)
    return datetime.now() > last_spin + timedelta(days=1)

def get_random_sector():
    """Выбор сектора с учетом вероятностей"""
    rand_num = random.uniform(0, 100)
    cumulative = 0
    for sector in SECTORS:
        cumulative += sector["probability"]
        if rand_num <= cumulative:
            return sector
    return SECTORS[0]

# --- ГЕНЕРАЦИЯ КОЛЕСА ---
def draw_wheel(selected_index):
    """Рисует круглое колесо и выделяет выбранный сектор"""
    width, height = 800, 800
    center = (width // 2, height // 2)
    radius = 350
    
    img = Image.new('RGB', (width, height), color=(30, 30, 50))
    draw = ImageDraw.Draw(img)
    
    angle_per_sector = 360 / len(SECTORS)
    start_angle = -90  # Начинаем сверху (как стрелка)
    
    # Попытка загрузить шрифт
    try:
        font = ImageFont.truetype("DejaVuSans.ttf", 24)
        small_font = ImageFont.truetype("DejaVuSans.ttf", 18)
    except:
        try:
            font = ImageFont.truetype("arial.ttf", 24)
            small_font = ImageFont.truetype("arial.ttf", 18)
        except:
            font = ImageFont.load_default()
            small_font = ImageFont.load_default()
    
    # Рисуем сектора
    for i, sector in enumerate(SECTORS):
        angle1 = start_angle + i * angle_per_sector
        angle2 = start_angle + (i + 1) * angle_per_sector
        
        # Если это выбранный сектор, делаем его ярче
        color = sector["color"]
        if i == selected_index:
            color = tuple(min(c + 60, 255) for c in color)
            # Добавляем свечение
            for j in range(3):
                glow_color = tuple(min(c + 40 - j*10, 255) for c in sector["color"])
                draw.pieslice([center[0] - radius - j, center[1] - radius - j, 
                              center[0] + radius + j, center[1] + radius + j],
                             start=angle1, end=angle2, fill=glow_color, outline=None)
        
        # Рисуем сектор
        draw.pieslice([center[0] - radius, center[1] - radius, 
                       center[0] + radius, center[1] + radius],
                      start=angle1, end=angle2, fill=color, outline="gold", width=2)
        
        # Текст внутри сектора
        mid_angle = angle1 + angle_per_sector / 2
        text_rad = radius * 0.65
        text_x = center[0] + text_rad * (mid_angle / 180 * 3.14159)
        text_y = center[1] + text_rad * (mid_angle / 180 * 3.14159)
        
        text = sector["name"]
        bbox = draw.textbbox((text_x, text_y), text, font=small_font)
        draw.text((text_x - (bbox[2]-bbox[0])//2, text_y - (bbox[3]-bbox[1])//2), 
                 text, fill="white", font=small_font, stroke_width=1, stroke_fill="black")
    
    # Рисуем стрелку
    arrow_points = [
        (center[0] - 25, center[1] - radius - 15),
        (center[0] + 25, center[1] - radius - 15),
        (center[0], center[1] - radius + 25)
    ]
    draw.polygon(arrow_points, fill="red", outline="yellow", width=2)
    
    # Рисуем центральный круг с логотипом
    draw.ellipse([center[0] - 60, center[1] - 60, center[0] + 60, center[1] + 60], 
                 fill="gold", outline="orange", width=3)
    
    # Добавляем текст в центр
    center_text = "ASTRA\nKASSA"
    try:
        center_font = ImageFont.truetype("arial.ttf", 20)
    except:
        center_font = ImageFont.load_default()
    
    lines = center_text.split('\n')
    y_offset = center[1] - 15
    for line in lines:
        bbox = draw.textbbox((center[0], y_offset), line, font=center_font)
        draw.text((center[0] - (bbox[2]-bbox[0])//2, y_offset), line, fill="black", font=center_font)
        y_offset += 25
    
    return img

# --- КЛАВИАТУРЫ ---
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Tekerlemi aýla", callback_data="spin")],
        [InlineKeyboardButton(text="💰 Balansym", callback_data="balance")],
        [InlineKeyboardButton(text="🏆 Ýeňişlerim", callback_data="wins")],
        [InlineKeyboardButton(text="⭐ Bonus ýagdaýy", callback_data="bonus_status")],
        [InlineKeyboardButton(text="ℹ️ Kömek", callback_data="help")]
    ])
    return kb

def admin_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Statistika", callback_data="admin_stats")],
        [InlineKeyboardButton(text="🎁 Premia ber", callback_data="admin_give_bonus")],
        [InlineKeyboardButton(text="📋 Ulanyjylar", callback_data="admin_users")]
    ])
    return kb

# --- СОСТОЯНИЯ ДЛЯ FSM ---
class AdminStates(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    user_id = message.from_user.id
    username = message.from_user.username
    full_name = message.from_user.full_name
    get_user(user_id, username, full_name)  # Инициализация пользователя
    
    welcome_text = (
        "🌟 *ASTRA KASSA Tekerleme* 🌟\n\n"
        f"Hormatly {full_name}, hoş geldiňiz! 🎰\n\n"
        "Sizi günlik tekerleme oýnuna çagyrýarys!\n\n"
        "✨ *Düzgünler:*\n"
        "• Her gün 1 gezek aýlap bilersiňiz\n"
        "• Tekerleme aýlanýar we baýragyňyz kesgitlenýär\n"
        "• Baýraklar: 0 TMT, Promo5 TMT, Promo10 TMT, Promo20 TMT, Depozit +10%\n\n"
        "🎯 *Baýrak derejeleri:*\n"
        "🎁 0 TMT - 90% (şowly gün däl)\n"
        "🎁 Promo5 TMT - 1% (5 manat)\n"
        "🎁 Promo10 TMT - 0.5% (10 manat)\n"
        "🎁 Promo20 TMT - 0.5% (20 manat)\n"
        "🎁 Depozit +10% - 8% (depozite goşmaça 10%)\n\n"
        "Aşakdaky düwmä basyp, tekerleme aýlaň! 🎡"
    )
    
    await message.answer(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "balance")
async def show_balance(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance, total_won, _, bonus_active, _, spins_count = get_user(user_id)
    
    text = (
        f"💰 *Balansym:* {balance} TMT\n\n"
        f"🏆 *Jemi ýeňiş:* {total_won} TMT\n"
        f"🎡 *Aýlanmalar sany:* {spins_count}\n"
        f"⭐ *Bonus +10%:* {'Aktiw' if bonus_active else 'Aktiw däl'}\n\n"
        "Her gün 1 gezek aýlap bilersiňiz! 🎡"
    )
    
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "wins")
async def show_wins(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT prize_type, prize_value, spin_date FROM spin_history WHERE user_id = ? ORDER BY spin_date DESC LIMIT 10", (user_id,))
    history = cursor.fetchall()
    conn.close()
    
    if not history:
        text = "🏆 *Siziň ýeňiş taryhyňyz:*\n\nHiç hili ýeňiş ýok. Tekerleme aýlap görüň! 🎡"
    else:
        text = "🏆 *Soňky 10 ýeňiş:*\n\n"
        for prize_type, prize_value, spin_date in history:
            date_obj = datetime.fromisoformat(spin_date)
            date_str = date_obj.strftime("%d.%m.%Y %H:%M")
            if prize_type == "prize":
                text += f"🎁 {prize_value} TMT - {date_str}\n"
            else:
                text += f"⭐ Depozit +10% - {date_str}\n"
    
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "bonus_status")
async def show_bonus(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    _, _, _, bonus_active, bonus_until, _ = get_user(user_id)
    
    text = "⭐ *Bonus ýagdaýy:*\n\n"
    if bonus_active and bonus_until:
        until_time = datetime.fromisoformat(bonus_until)
        remain = until_time - datetime.now()
        hours = remain.seconds // 3600
        minutes = (remain.seconds % 3600) // 60
        text += f"✅ *Aktiw!*\nGalyn wagty: {hours} sagat {minutes} minut\n\n"
        text += "Indiki depozitde +10% goşmaça alarsyňyz! 💰"
    else:
        text += "❌ *Aktiw däl*\n\n"
        text += "Bonus aktiwleşdirmek üçin tekerlemede 'Depozit +10%' sektoryny aýlamaly! 🎡"
    
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "help")
async def show_help(callback: types.CallbackQuery):
    text = (
        "ℹ️ *Kömek we düzgünler:*\n\n"
        "🎡 *Tekerleme düzgünleri:*\n"
        "• Her ulanyjy günde 1 gezek aýlap biler\n"
        "• Tekerleme awtomatik usulda aýlanýar\n"
        "• Netije surat görnüşinde görkezilýär\n\n"
        "🏆 *Baýrak görnüşleri:*\n"
        "• 0 TMT - 90% (şowly gün däl)\n"
        "• Promo5 TMT - 1% (5 manat)\n"
        "• Promo10 TMT - 0.5% (10 manat)\n"
        "• Promo20 TMT - 0.5% (20 manat)\n"
        "• Depozit +10% - 8% (depozite 10% goşmaça)\n\n"
        "💰 *Balansy nädip doldurmaly:*\n"
        "Balans doldurmak üçin kassa bilen habarlaşyň: @AstraKassa\n\n"
        "📞 *Goldaw:*\n"
        "Soraglar ýüze çykan ýagdaýynda @AstraKassa bilen habarlaşyň!"
    )
    
    await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "spin")
async def spin_wheel(callback: types.CallbackQuery):
    user_id = callback.from_user.id
    balance, total_won, last_spin_str, bonus_active, _, spins_count = get_user(user_id)
    
    # Проверка на лимит 1 раз в день
    if not can_spin(user_id, last_spin_str):
        last_spin = datetime.fromisoformat(last_spin_str)
        next_spin = last_spin + timedelta(days=1)
        wait_seconds = (next_spin - datetime.now()).seconds
        hours = wait_seconds // 3600
        minutes = (wait_seconds % 3600) // 60
        
        await callback.answer(f"⏳ Siz şu gün aýladyňyz! Indiki gezek {hours} sagat {minutes} minutdan soň aýlap bilersiňiz.", show_alert=True)
        return
    
    # Отправляем сообщение о вращении
    msg = await callback.message.answer("🎡 Tekerleme aýlanýar... 🎡")
    
    # Выбираем случайный сектор с учетом вероятностей
    selected_sector = get_random_sector()
    selected_index = SECTORS.index(selected_sector)
    
    # Генерируем изображение колеса
    wheel_img = draw_wheel(selected_index)
    wheel_img.save(WHEEL_IMAGE_PATH)
    
    # Отправляем фото
    await bot.send_photo(chat_id=user_id, photo=FSInputFile(WHEEL_IMAGE_PATH), 
                        caption="🎲 *Netije!*", parse_mode="Markdown")
    
    # Обрабатываем результат
    result_text = ""
    reward = 0
    
    if selected_sector["type"] == "prize":
        reward = selected_sector["value"]
        
        # Применяем бонус, если активен
        if bonus_active and reward > 0:
            old_reward = reward
            reward = int(reward * 1.1)
            result_text += f"✨ *Bonus +10% aktiw!* {old_reward} TMT → {reward} TMT ✨\n"
        
        balance += reward
        total_won += reward
        spins_count += 1
        
        if reward > 0:
            result_text += f"🎉 *Baýrak: {selected_sector['name']}* 🎉\n+{reward} TMT balansyňyza goşuldy!"
        else:
            result_text += f"😞 *{selected_sector['name']}*\nŞowly gün däl, ertir täzeden synanyşyň!"
    
    elif selected_sector["type"] == "bonus":
        # Активируем бонус на 24 часа
        set_bonus(user_id, hours=24)
        spins_count += 1
        result_text += f"⭐ *Depozit +10% bonus aktiwleşdi!* ⭐\n"
        result_text += "24 sagadyň dowamynda depozit edeniňizde +10% goşmaça alarsyňyz!\n"
        result_text += f"💡 Bonus: 5 TMT balansyňyza goşuldy!"
        balance += 5
        total_won += 5
    
    # Сохраняем историю
    save_spin_history(user_id, selected_sector["type"], selected_sector["value"] if selected_sector["type"] == "prize" else 0)
    
    # Обновляем данные в БД
    update_user(user_id, balance, total_won, datetime.now().isoformat(), spins_count)
    
    # Удаляем сообщение о вращении и отправляем результат
    await msg.delete()
    
    final_text = (
        f"{result_text}\n\n"
        f"💰 *Täze balans:* {balance} TMT\n"
        f"🏆 *Jemi ýeňişler:* {total_won} TMT\n\n"
        f"Ertir täzeden synanyşyň! 🎡"
    )
    
    await callback.message.answer(final_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()

# --- АДМИН КОМАНДЫ ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buýruk diňe administrator üçin!")
        return
    
    text = (
        "👑 *Administrator paneli* 👑\n\n"
        "Aşakdaky düwmeler arkaly dolandyryň:"
    )
    
    await message.answer(text, reply_markup=admin_keyboard(), parse_mode="Markdown")

@dp.callback_query(F.data == "admin_stats")
async def admin_stats(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Rugsat ýok!", show_alert=True)
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    # Общая статистика
    cursor.execute("SELECT COUNT(*) FROM users")
    total_users = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(balance) FROM users")
    total_balance = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT SUM(total_won) FROM users")
    total_won_all = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT SUM(spins_count) FROM users")
    total_spins = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM users WHERE bonus_active = 1")
    active_bonus = cursor.fetchone()[0]
    
    cursor.execute("SELECT prize_value, COUNT(*) FROM spin_history WHERE prize_type = 'prize' AND prize_value > 0 GROUP BY prize_value")
    prizes_stats = cursor.fetchall()
    
    conn.close()
    
    text = (
        "📊 *Umumy statistika:*\n\n"
        f"👥 Ulanyjylar: {total_users}\n"
        f"💰 Umumy balans: {total_balance} TMT\n"
        f"🏆 Jemi ýeňişler: {total_won_all} TMT\n"
        f"🎡 Jemi aýlanmalar: {total_spins}\n"
        f"⭐ Aktiw bonus: {active_bonus}\n\n"
        "🎁 *Baýrak statistikasy:*\n"
    )
    
    for value, count in prizes_stats:
        text += f"• {value} TMT: {count} gezek\n"
    
    await callback.message.edit_text(text, reply_markup=admin_keyboard(), parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(F.data == "admin_users")
async def admin_users(callback: types.CallbackQuery):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Rugsat ýok!", show_alert=True)
        return
    
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT user_id, username, full_name, balance, total_won, spins_count FROM users ORDER BY balance DESC LIMIT 20")
    users = cursor.fetchall()
    conn.close()
    
    text = "👥 *Iň köp balansly ulanyjylar:*\n\n"
    for user in users:
        user_id, username, full_name, balance, total_won, spins_count = user
        name = username or full_name or str(user_id)
        text += f"👤 {name}\n   💰 {balance} TMT | 🏆 {total_won} TMT | 🎡 {spins_count}\n\n"
    
    await callback.message.edit_text(text, reply_markup=admin_keyboard(), parse_mode="Markdown")
    await callback.answer()

class AdminBonus(StatesGroup):
    waiting_for_user_id = State()
    waiting_for_amount = State()

@dp.callback_query(F.data == "admin_give_bonus")
async def admin_give_bonus_start(callback: types.CallbackQuery, state: FSMContext):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("⛔ Rugsat ýok!", show_alert=True)
        return
    
    await callback.message.edit_text(
        "🎁 *Premium bermek*\n\n"
        "Ulanyjynyň ID-sini ýazyň (san görnüşinde):\n"
        "Mysal: 123456789\n\n"
        "Ýa-da /cancel bilen ýatyryň.",
        reply_markup=None,
        parse_mode="Markdown"
    )
    await state.set_state(AdminBonus.waiting_for_user_id)
    await callback.answer()

@dp.message(AdminBonus.waiting_for_user_id)
async def process_user_id(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Rugsat ýok!")
        return
    
    try:
        user_id = int(message.text.strip())
        await state.update_data(user_id=user_id)
        
        await message.answer(
            f"💵 *Mukdar ýazyň*\n\n"
            f"Ulanyjy ID: {user_id}\n"
            f"Näçe TMT bermek isleýärsiňiz? (san görnüşinde)",
            parse_mode="Markdown"
        )
        await state.set_state(AdminBonus.waiting_for_amount)
    except ValueError:
        await message.answer("❌ Ýalňyş ID! San görnüşinde ýazyň.")
        await state.clear()

@dp.message(AdminBonus.waiting_for_amount)
async def process_amount(message: types.Message, state: FSMContext):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Rugsat ýok!")
        return
    
    try:
        amount = int(message.text.strip())
        if amount <= 0:
            await message.answer("❌ Mukdar 0-dan uly bolmaly!")
            return
        
        data = await state.get_data()
        user_id = data['user_id']
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT balance, total_won FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            current_balance, current_won = result
            new_balance = current_balance + amount
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            conn.commit()
            
            await message.answer(
                f"✅ *Premium berildi!*\n\n"
                f"👤 Ulanyjy ID: {user_id}\n"
                f"💵 Mukdar: {amount} TMT\n"
                f"💰 Köne balans: {current_balance} TMT\n"
                f"💰 Täze balans: {new_balance} TMT",
                parse_mode="Markdown"
            )
            
            # Отправляем уведомление пользователю
            try:
                await bot.send_message(
                    user_id,
                    f"🎉 *Siziň balansyňyz {amount} TMT bilen dolduryldy!*\n\n"
                    f"💰 Täze balans: {new_balance} TMT\n"
                    f"Tekerleme aýlap görüň! 🎡",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await message.answer(f"❌ {user_id} ID-li ulanyjy tapylmady!")
        
        conn.close()
        
    except ValueError:
        await message.answer("❌ Ýalňyş mukdar! San görnüşinde ýazyň.")
    
    await state.clear()
    await admin_panel(message)

@dp.message(Command("cancel"))
async def cancel_command(message: types.Message, state: FSMContext):
    await state.clear()
    await message.answer("❌ Amal ýatyryldy!")

async def main():
    init_db()
    print("🚀 Bot işledi...")
    print(f"👑 Administrator ID: {ADMIN_ID}")
    print("🎡 Astra Kassa Tekerleme Bot")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
