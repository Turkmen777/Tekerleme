import asyncio
import random
import sqlite3
import logging
import sys
from datetime import datetime, timedelta
from io import BytesIO

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram import F

# Импортируем PIL для рисования колеса
from PIL import Image, ImageDraw, ImageFont

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = "8274028629:AAGOTRJWn9Ua6Es2ajdeRajwzZgnlkLctbQ"  # ВСТАВЬТЕ ВАШ ТОКЕН
ADMIN_ID = 8210954671  # ВСТАВЬТЕ ВАШ ID

# Пути к файлам
DB_NAME = "fortune_bot.db"
WHEEL_IMAGE_PATH = "wheel_temp.png"

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('bot.log'),
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

# Сектора колеса
SECTORS = [
    {"name": "0 TMT", "color": (200, 200, 200), "type": "prize", "value": 0, "probability": 90},
    {"name": "Promo5 TMT", "color": (100, 200, 255), "type": "prize", "value": 5, "probability": 1},
    {"name": "Promo10 TMT", "color": (255, 215, 0), "type": "prize", "value": 10, "probability": 0.5},
    {"name": "Promo20 TMT", "color": (255, 165, 0), "type": "prize", "value": 20, "probability": 0.5},
    {"name": "Depozit +10%", "color": (255, 105, 180), "type": "bonus", "value": 10, "probability": 8}
]

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    try:
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
        logger.info("База данных успешно инициализирована")
        return True
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
        return False

def get_user(user_id, username=None, full_name=None):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT balance, total_won, last_spin, bonus_active, bonus_until, spins_count FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            # Проверяем, не истек ли бонус
            if result[3] and result[4]:
                try:
                    if datetime.now() > datetime.fromisoformat(result[4]):
                        cursor.execute("UPDATE users SET bonus_active = 0, bonus_until = NULL WHERE user_id = ?", (user_id,))
                        conn.commit()
                        return result[0], result[1], result[2], 0, None, result[5]
                except:
                    pass
            conn.close()
            return result
        else:
            # Создаем нового пользователя
            cursor.execute("INSERT INTO users (user_id, username, full_name, balance, spins_count) VALUES (?, ?, ?, ?, ?)", 
                          (user_id, username, full_name, 0, 0))
            conn.commit()
            conn.close()
            return 0, 0, None, 0, None, 0
    except Exception as e:
        logger.error(f"Ошибка получения пользователя: {e}")
        return 0, 0, None, 0, None, 0

def update_user(user_id, balance, total_won, last_spin, spins_count, bonus_active=None, bonus_until=None):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = ?, total_won = ?, last_spin = ?, spins_count = ? WHERE user_id = ?", 
                      (balance, total_won, last_spin, spins_count, user_id))
        if bonus_active is not None:
            cursor.execute("UPDATE users SET bonus_active = ?, bonus_until = ? WHERE user_id = ?", 
                          (bonus_active, bonus_until, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления пользователя: {e}")
        return False

def save_spin_history(user_id, prize_type, prize_value):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO spin_history (user_id, prize_type, prize_value, spin_date) VALUES (?, ?, ?, ?)",
                      (user_id, prize_type, prize_value, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {e}")

def set_bonus(user_id, hours=24):
    try:
        bonus_until = (datetime.now() + timedelta(hours=hours)).isoformat()
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET bonus_active = 1, bonus_until = ? WHERE user_id = ?", (bonus_until, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка установки бонуса: {e}")
        return False

def can_spin(user_id, last_spin_str):
    if not last_spin_str:
        return True
    try:
        last_spin = datetime.fromisoformat(last_spin_str)
        return datetime.now() > last_spin + timedelta(days=1)
    except:
        return True

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
    try:
        width, height = 600, 600
        center = (width // 2, height // 2)
        radius = 250
        
        img = Image.new('RGB', (width, height), color=(30, 30, 50))
        draw = ImageDraw.Draw(img)
        
        angle_per_sector = 360 / len(SECTORS)
        start_angle = -90
        
        # Используем шрифт по умолчанию
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 18)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 18)
            except:
                font = ImageFont.load_default()
        
        # Рисуем сектора
        for i, sector in enumerate(SECTORS):
            angle1 = start_angle + i * angle_per_sector
            angle2 = start_angle + (i + 1) * angle_per_sector
            
            color = sector["color"]
            if i == selected_index:
                color = tuple(min(c + 60, 255) for c in color)
            
            draw.pieslice([center[0] - radius, center[1] - radius, 
                           center[0] + radius, center[1] + radius],
                          start=angle1, end=angle2, fill=color, outline="gold", width=2)
            
            # Текст
            mid_angle = angle1 + angle_per_sector / 2
            text_rad = radius * 0.7
            text_x = center[0] + text_rad * (mid_angle / 180 * 3.14159)
            text_y = center[1] + text_rad * (mid_angle / 180 * 3.14159)
            
            text = sector["name"]
            bbox = draw.textbbox((text_x, text_y), text, font=font)
            draw.text((text_x - (bbox[2]-bbox[0])//2, text_y - (bbox[3]-bbox[1])//2), 
                     text, fill="white", font=font)
        
        # Рисуем стрелку
        arrow_points = [
            (center[0] - 20, center[1] - radius - 10),
            (center[0] + 20, center[1] - radius - 10),
            (center[0], center[1] - radius + 20)
        ]
        draw.polygon(arrow_points, fill="red", outline="yellow", width=2)
        
        # Центральный круг
        draw.ellipse([center[0] - 40, center[1] - 40, center[0] + 40, center[1] + 40], 
                     fill="gold", outline="orange", width=2)
        
        return img
    except Exception as e:
        logger.error(f"Ошибка отрисовки колеса: {e}")
        return None

# --- КЛАВИАТУРЫ ---
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Tekerlemi aýla", callback_data="spin")],
        [InlineKeyboardButton(text="💰 Balansym", callback_data="balance")],
        [InlineKeyboardButton(text="🏆 Ýeňişlerim", callback_data="wins")],
        [InlineKeyboardButton(text="⭐ Bonus ýagdaýy", callback_data="bonus_status")]
    ])
    return kb

# --- ОБРАБОТЧИКИ ---
@dp.message(Command("start"))
async def start_command(message: types.Message):
    try:
        user_id = message.from_user.id
        username = message.from_user.username
        full_name = message.from_user.full_name
        
        logger.info(f"Пользователь {user_id} запустил бота")
        
        get_user(user_id, username, full_name)
        
        welcome_text = (
            "🌟 *ASTRA KASSA Tekerleme* 🌟\n\n"
            f"Hormatly {full_name}, hoş geldiňiz! 🎰\n\n"
            "Sizi günlik tekerleme oýnuna çagyrýarys!\n\n"
            "✨ *Düzgünler:*\n"
            "• Her gün 1 gezek aýlap bilersiňiz\n"
            "• Baýraklar: 0 TMT, Promo5 TMT, Promo10 TMT, Promo20 TMT, Depozit +10%\n\n"
            "Aşakdaky düwmä basyp, tekerleme aýlaň! 🎡"
        )
        
        await message.answer(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        logger.info(f"Ответ отправлен пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка в start_command: {e}")
        await message.answer("⚠️ Tekniki ýalňyşlyk! Soňra synanyşyň.")

@dp.message(Command("help"))
async def help_command(message: types.Message):
    try:
        help_text = (
            "ℹ️ *Kömek*\n\n"
            "• /start - bota başlamak\n"
            "• Aşakdaky düwmeler bilen dolandyryň\n"
            "• Her gün 1 gezek tekerleme aýlap bilersiňiz\n\n"
            "📞 Soraglar üçin: @AstraKassa"
        )
        await message.answer(help_text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка в help_command: {e}")

@dp.callback_query(F.data == "balance")
async def show_balance(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        balance, total_won, _, bonus_active, _, spins_count = get_user(user_id)
        
        text = (
            f"💰 *Balansym:* {balance} TMT\n\n"
            f"🏆 *Jemi ýeňiş:* {total_won} TMT\n"
            f"🎡 *Aýlanmalar sany:* {spins_count}\n"
            f"⭐ *Bonus:* {'Aktiw' if bonus_active else 'Aktiw däl'}"
        )
        
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        await callback.answer()
        logger.info(f"Баланс показан пользователю {user_id}")
    except Exception as e:
        logger.error(f"Ошибка в show_balance: {e}")
        await callback.answer("⚠️ Ýalňyşlyk!", show_alert=True)

@dp.callback_query(F.data == "wins")
async def show_wins(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT prize_type, prize_value, spin_date FROM spin_history WHERE user_id = ? ORDER BY spin_date DESC LIMIT 10", (user_id,))
        history = cursor.fetchall()
        conn.close()
        
        if not history:
            text = "🏆 *Siziň ýeňiş taryhyňyz:*\n\nHiç hili ýeňiş ýok."
        else:
            text = "🏆 *Soňky 10 ýeňiş:*\n\n"
            for prize_type, prize_value, spin_date in history:
                date_obj = datetime.fromisoformat(spin_date)
                date_str = date_obj.strftime("%d.%m.%Y")
                if prize_type == "prize":
                    text += f"🎁 {prize_value} TMT - {date_str}\n"
                else:
                    text += f"⭐ Depozit +10% - {date_str}\n"
        
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в show_wins: {e}")
        await callback.answer("⚠️ Ýalňyşlyk!", show_alert=True)

@dp.callback_query(F.data == "bonus_status")
async def show_bonus(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        _, _, _, bonus_active, bonus_until, _ = get_user(user_id)
        
        text = "⭐ *Bonus ýagdaýy:*\n\n"
        if bonus_active and bonus_until:
            try:
                until_time = datetime.fromisoformat(bonus_until)
                remain = until_time - datetime.now()
                hours = remain.seconds // 3600
                text += f"✅ *Aktiw!*\nGalyn wagty: {hours} sagat\n\n"
                text += "Indiki depozitde +10% goşmaça alarsyňyz!"
            except:
                text += "✅ Aktiw"
        else:
            text += "❌ *Aktiw däl*\n\nTekerlemede 'Depozit +10%' aýlaň!"
        
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в show_bonus: {e}")
        await callback.answer("⚠️ Ýalňyşlyk!", show_alert=True)

@dp.callback_query(F.data == "spin")
async def spin_wheel(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        balance, total_won, last_spin_str, bonus_active, _, spins_count = get_user(user_id)
        
        # Проверка на лимит
        if not can_spin(user_id, last_spin_str):
            last_spin = datetime.fromisoformat(last_spin_str)
            next_spin = last_spin + timedelta(days=1)
            wait_seconds = (next_spin - datetime.now()).seconds
            hours = wait_seconds // 3600
            minutes = (wait_seconds % 3600) // 60
            
            await callback.answer(f"⏳ Siz şu gün aýladyňyz! Indiki gezek {hours} sagat {minutes} minutdan soň.", show_alert=True)
            return
        
        await callback.answer("🎡 Tekerleme aýlanýar...")
        
        # Выбираем сектор
        selected_sector = get_random_sector()
        selected_index = SECTORS.index(selected_sector)
        
        # Рисуем колесо
        wheel_img = draw_wheel(selected_index)
        
        if wheel_img:
            wheel_img.save(WHEEL_IMAGE_PATH)
            await bot.send_photo(chat_id=user_id, photo=FSInputFile(WHEEL_IMAGE_PATH), 
                                caption="🎲 *Netije!*", parse_mode="Markdown")
        else:
            await callback.message.answer("⚠️ Surat döredilip bilmedi, ýöne netije aşakda:")
        
        # Обрабатываем результат
        result_text = ""
        reward = 0
        
        if selected_sector["type"] == "prize":
            reward = selected_sector["value"]
            
            if bonus_active and reward > 0:
                old_reward = reward
                reward = int(reward * 1.1)
                result_text += f"✨ Bonus +10%: {old_reward} → {reward} TMT ✨\n"
            
            balance += reward
            total_won += reward
            spins_count += 1
            
            if reward > 0:
                result_text += f"🎉 *Baýrak: {reward} TMT* 🎉"
            else:
                result_text += f"😞 *0 TMT*\nŞowly gün däl, ertir synanyş!"
        
        elif selected_sector["type"] == "bonus":
            set_bonus(user_id, hours=24)
            spins_count += 1
            result_text += f"⭐ *Depozit +10% bonus aktiwleşdi!* ⭐\n24 sagat dowam eder!\n"
            balance += 5
            total_won += 5
            result_text += f"🎁 5 TMT balansyňa goşuldy!"
        
        # Сохраняем
        save_spin_history(user_id, selected_sector["type"], selected_sector["value"] if selected_sector["type"] == "prize" else 0)
        update_user(user_id, balance, total_won, datetime.now().isoformat(), spins_count)
        
        final_text = (
            f"{result_text}\n\n"
            f"💰 *Täze balans:* {balance} TMT\n"
            f"🏆 *Jemi ýeňiş:* {total_won} TMT\n\n"
            f"Ertir täzeden synanyş! 🎡"
        )
        
        await callback.message.answer(final_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        logger.info(f"Пользователь {user_id} выиграл {reward} TMT")
        
    except Exception as e:
        logger.error(f"Ошибка в spin_wheel: {e}")
        await callback.message.answer("⚠️ Tekniki ýalňyşlyk! Soňra synanyşyň.")
        await callback.answer()

# --- АДМИН КОМАНДЫ ---
@dp.message(Command("admin"))
async def admin_panel(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buýruk diňe administrator üçin!")
        return
    
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        cursor.execute("SELECT SUM(balance) FROM users")
        total_balance = cursor.fetchone()[0] or 0
        conn.close()
        
        text = (
            f"👑 *Administrator paneli* 👑\n\n"
            f"👥 Ulanyjylar: {total_users}\n"
            f"💰 Umumy balans: {total_balance} TMT\n\n"
            f"Balans doldurmak üçin:\n"
            f"/add @username 100"
        )
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка в admin_panel: {e}")
        await message.answer("⚠️ Ýalňyşlyk!")

@dp.message(Command("add"))
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Formula: /add user_id 100")
            return
        
        user_id = int(args[1])
        amount = int(args[2])
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            new_balance = result[0] + amount
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            conn.commit()
            await message.answer(f"✅ {user_id} ID-li ulanyjynyň balansy {amount} TMT artyp, {new_balance} TMT boldy!")
            
            try:
                await bot.send_message(user_id, f"💰 Balansyňyz {amount} TMT bilen dolduryldy! Täze balans: {new_balance} TMT")
            except:
                pass
        else:
            await message.answer(f"❌ {user_id} ID-li ulanyjy tapylmady!")
        
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка в add_balance: {e}")
        await message.answer("⚠️ Ýalňyşlyk! ID we san dogry ýazyň.")

# --- ЗАПУСК ---
async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск бота Astra Kassa Tekerleme...")
    
    # Инициализация БД
    if not init_db():
        logger.error("Не удалось инициализировать базу данных!")
        return
    
    # Проверка токена
    if API_TOKEN == "ВАШ_ТОКЕН_БОТА":
        logger.error("⚠️ ВНИМАНИЕ: Не установлен API_TOKEN бота!")
        print("\n" + "="*50)
        print("⚠️  ОШИБКА: Не установлен токен бота!")
        print("Измените API_TOKEN в файле bot.py на ваш токен")
        print("Получить токен можно у @BotFather")
        print("="*50 + "\n")
        return
    
    logger.info(f"✅ Бот запущен с токеном: {API_TOKEN[:10]}...")
    logger.info(f"👑 Администратор ID: {ADMIN_ID}")
    
    # Запуск поллинга
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
