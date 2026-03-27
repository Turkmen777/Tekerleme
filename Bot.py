import asyncio
import random
import sqlite3
import logging
import sys
import os
from datetime import datetime, timedelta
from collections import defaultdict

from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram import F

# Импортируем PIL для рисования колеса
from PIL import Image, ImageDraw, ImageFont

# --- КОНФИГУРАЦИЯ ---
API_TOKEN = "8274028629:AAGOTRJWn9Ua6Es2ajdeRajwzZgnlkLctbQ"  # ВСТАВЬТЕ ВАШ ТОКЕН
ADMIN_ID = 8210954671  # ВАШ ID
GROUP_ID = -1003795197483  # ID ГРУППЫ ДЛЯ УВЕДОМЛЕНИЙ

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

# Сектора колеса (только 4 сектора)
SECTORS = [
    {"name": "0 TMT", "color": (200, 200, 200), "type": "lose", "value": 0},
    {"name": "5 TMT", "color": (100, 200, 255), "type": "win", "value": 5},
    {"name": "10 TMT", "color": (255, 215, 0), "type": "win", "value": 10},
    {"name": "20 TMT", "color": (255, 165, 0), "type": "win", "value": 20}
]

# Инициализация бота
bot = Bot(token=API_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# Глобальный счетчик для отслеживания проигрышей
# Будем хранить в БД общий счетчик проигрышей

# --- РАБОТА С БАЗОЙ ДАННЫХ ---
def init_db():
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        
        # Таблица пользователей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                full_name TEXT,
                balance INTEGER DEFAULT 0,
                total_won INTEGER DEFAULT 0,
                last_spin TEXT,
                spins_count INTEGER DEFAULT 0
            )
        ''')
        
        # Таблица истории выигрышей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS spin_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER,
                prize_value INTEGER,
                spin_date TEXT
            )
        ''')
        
        # Таблица для глобального счетчика проигрышей
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS global_counter (
                id INTEGER PRIMARY KEY,
                lose_count INTEGER DEFAULT 0,
                last_win_cycle INTEGER DEFAULT 0
            )
        ''')
        
        # Инициализируем глобальный счетчик
        cursor.execute("SELECT COUNT(*) FROM global_counter")
        if cursor.fetchone()[0] == 0:
            cursor.execute("INSERT INTO global_counter (id, lose_count, last_win_cycle) VALUES (1, 0, 0)")
        
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
        cursor.execute("SELECT balance, total_won, last_spin, spins_count FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            conn.close()
            return result
        else:
            cursor.execute("INSERT INTO users (user_id, username, full_name, balance, spins_count) VALUES (?, ?, ?, ?, ?)", 
                          (user_id, username, full_name, 0, 0))
            conn.commit()
            conn.close()
            return 0, 0, None, 0
    except Exception as e:
        logger.error(f"Ошибка получения пользователя: {e}")
        return 0, 0, None, 0

def update_user(user_id, balance, total_won, last_spin, spins_count):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE users SET balance = ?, total_won = ?, last_spin = ?, spins_count = ? WHERE user_id = ?", 
                      (balance, total_won, last_spin, spins_count, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления пользователя: {e}")
        return False

def save_spin_history(user_id, prize_value):
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("INSERT INTO spin_history (user_id, prize_value, spin_date) VALUES (?, ?, ?)",
                      (user_id, prize_value, datetime.now().isoformat()))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Ошибка сохранения истории: {e}")

def get_global_counter():
    """Получить глобальный счетчик проигрышей"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT lose_count, last_win_cycle FROM global_counter WHERE id = 1")
        result = cursor.fetchone()
        conn.close()
        return result if result else (0, 0)
    except Exception as e:
        logger.error(f"Ошибка получения счетчика: {e}")
        return 0, 0

def update_global_counter(lose_count, last_win_cycle):
    """Обновить глобальный счетчик проигрышей"""
    try:
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("UPDATE global_counter SET lose_count = ?, last_win_cycle = ? WHERE id = 1", 
                      (lose_count, last_win_cycle))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления счетчика: {e}")
        return False

def can_spin(user_id, last_spin_str):
    if not last_spin_str:
        return True
    try:
        last_spin = datetime.fromisoformat(last_spin_str)
        return datetime.now() > last_spin + timedelta(days=1)
    except:
        return True

def get_prize_by_counter(lose_count):
    """
    Определяет какой приз выпадает на основе счетчика проигрышей
    Возвращает: (prize_value, is_win)
    """
    # Определяем какой цикл выигрыша сейчас (начиная с 0)
    # Циклы: 0-39 проигрыш, 40-й выигрыш 5, 41-79 проигрыш, 80-й выигрыш 10, 81-119 проигрыш, 120-й выигрыш 20
    # 121-159 проигрыш, 160-й выигрыш 5, 161-199 проигрыш, 200-й выигрыш 10, 201-239 проигрыш, 240-й выигрыш 20 и т.д.
    
    # Номер текущего спина (начиная с 1)
    spin_number = lose_count + 1
    
    # Проверяем, является ли текущий спин выигрышным
    # Выигрышные спины: 40, 80, 120, 160, 200, 240, 280...
    if spin_number % 40 == 0:
        # Определяем какой по счету выигрыш (1-й, 2-й, 3-й...)
        win_cycle = spin_number // 40
        # Цикл выигрышей: 1->5, 2->10, 3->20, 4->5, 5->10, 6->20, 7->5...
        if win_cycle % 3 == 1:
            return 5, True  # 5 TMT
        elif win_cycle % 3 == 2:
            return 10, True  # 10 TMT
        else:
            return 20, True  # 20 TMT
    else:
        return 0, False  # Проигрыш

# --- ГЕНЕРАЦИЯ КОЛЕСА ---
def draw_wheel(selected_index, win_text):
    try:
        width, height = 800, 800
        center = (width // 2, height // 2)
        radius = 300
        
        img = Image.new('RGB', (width, height), color=(30, 30, 50))
        draw = ImageDraw.Draw(img)
        
        angle_per_sector = 360 / len(SECTORS)
        start_angle = -90
        
        try:
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 24)
            big_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 40)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 24)
                big_font = ImageFont.truetype("arial.ttf", 40)
            except:
                font = ImageFont.load_default()
                big_font = ImageFont.load_default()
        
        for i, sector in enumerate(SECTORS):
            angle1 = start_angle + i * angle_per_sector
            angle2 = start_angle + (i + 1) * angle_per_sector
            
            color = sector["color"]
            if i == selected_index:
                color = tuple(min(c + 60, 255) for c in color)
            
            draw.pieslice([center[0] - radius, center[1] - radius, 
                           center[0] + radius, center[1] + radius],
                          start=angle1, end=angle2, fill=color, outline="gold", width=3)
            
            mid_angle = angle1 + angle_per_sector / 2
            text_rad = radius * 0.7
            text_x = center[0] + text_rad * (mid_angle / 180 * 3.14159)
            text_y = center[1] + text_rad * (mid_angle / 180 * 3.14159)
            
            text = sector["name"]
            bbox = draw.textbbox((text_x, text_y), text, font=font)
            draw.text((text_x - (bbox[2]-bbox[0])//2, text_y - (bbox[3]-bbox[1])//2), 
                     text, fill="white", font=font)
        
        arrow_points = [
            (center[0] - 25, center[1] - radius - 15),
            (center[0] + 25, center[1] - radius - 15),
            (center[0], center[1] - radius + 25)
        ]
        draw.polygon(arrow_points, fill="red", outline="yellow", width=3)
        
        draw.ellipse([center[0] - 80, center[1] - 80, center[0] + 80, center[1] + 80], 
                     fill="gold", outline="orange", width=3)
        
        lines = win_text.split('\n')
        y_offset = center[1] - 20
        for line in lines:
            bbox = draw.textbbox((center[0], y_offset), line, font=big_font)
            draw.text((center[0] - (bbox[2]-bbox[0])//2, y_offset), line, fill="black", font=big_font)
            y_offset += 45
        
        return img
    except Exception as e:
        logger.error(f"Ошибка отрисовки колеса: {e}")
        return None

# --- КЛАВИАТУРЫ ---
def main_menu_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎡 Tekerlemi aýla", callback_data="spin")],
        [InlineKeyboardButton(text="🏆 Ýeňişlerim", callback_data="wins")]
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
            "• Astra kassa siz bilendir\n"
            "• Baýraklar: 5 TMT, 10 TMT, 20 TMT\n\n"
            "Aşakdaky düwmä basyp, tekerleme aýlaň! 🎡"
        )
        
        await message.answer(welcome_text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        
    except Exception as e:
        logger.error(f"Ошибка в start_command: {e}")
        await message.answer("⚠️ Tekniki ýalňyşlyk! Soňra synanyşyň.")

@dp.callback_query(F.data == "wins")
async def show_wins(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT prize_value, spin_date FROM spin_history WHERE user_id = ? AND prize_value > 0 ORDER BY spin_date DESC LIMIT 20", (user_id,))
        history = cursor.fetchall()
        
        # Получаем общую статистику
        cursor.execute("SELECT SUM(prize_value) FROM spin_history WHERE user_id = ? AND prize_value > 0", (user_id,))
        total_won = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM spin_history WHERE user_id = ? AND prize_value = 0", (user_id,))
        total_lose = cursor.fetchone()[0] or 0
        
        conn.close()
        
        if not history:
            text = "🏆 *Siziň ýeňiş taryhyňyz:*\n\nHiç hili ýeňiş ýok.\n\nTekerleme aýlap görüň! 🎡"
        else:
            text = f"🏆 *Ýeňiş statistikasy:*\n\n"
            text += f"💰 Jemi ýeňiş: {total_won} TMT\n"
            text += f"🎡 Jemi aýlanma: {total_lose + len(history)}\n"
            text += f"🎉 Ýeňiş sany: {len(history)}\n\n"
            text += "*Soňky 20 ýeňiş:*\n"
            for prize_value, spin_date in history:
                date_obj = datetime.fromisoformat(spin_date)
                date_str = date_obj.strftime("%d.%m.%Y %H:%M")
                text += f"🎉 +{prize_value} TMT - {date_str}\n"
        
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
        await callback.answer()
    except Exception as e:
        logger.error(f"Ошибка в show_wins: {e}")
        await callback.message.edit_text("⚠️ Ýalňyşlyk! Soňra synanyşyň.", reply_markup=main_menu_keyboard())
        await callback.answer()

@dp.callback_query(F.data == "spin")
async def spin_wheel(callback: types.CallbackQuery):
    try:
        user_id = callback.from_user.id
        username = callback.from_user.username or f"user_{user_id}"
        full_name = callback.from_user.full_name
        
        balance, total_won, last_spin_str, spins_count = get_user(user_id)
        
        # Проверка на лимит
        if not can_spin(user_id, last_spin_str):
            if last_spin_str:
                last_spin = datetime.fromisoformat(last_spin_str)
                next_spin = last_spin + timedelta(days=1)
                wait_seconds = (next_spin - datetime.now()).seconds
                hours = wait_seconds // 3600
                minutes = (wait_seconds % 3600) // 60
                await callback.answer(f"⏳ Siz şu gün aýladyňyz! Indiki gezek {hours} sagat {minutes} minutdan soň.", show_alert=True)
            else:
                await callback.answer("⏳ Siz şu gün aýladyňyz! Ertir synanyşyň.", show_alert=True)
            return
        
        await callback.answer("🎡 Tekerleme aýlanýar...")
        
        # Получаем глобальный счетчик
        lose_count, last_win_cycle = get_global_counter()
        
        # Определяем выигрыш на основе счетчика
        prize_value, is_win = get_prize_by_counter(lose_count)
        
        # Обновляем баланс
        if is_win:
            balance += prize_value
            total_won += prize_value
            win_text = f"+{prize_value}\nTMT"
            result_text = f"🎉 *Siz {prize_value} TMT gazandyňyz!* 🎉"
            
            # Отправляем уведомление в группу
            try:
                await bot.send_message(
                    GROUP_ID,
                    f"🎉 *Ýeňiş!* 🎉\n\n"
                    f"👤 @{username} ({full_name})\n"
                    f"💰 {prize_value} TMT gazandy!\n"
                    f"🏆 Jemi aýlanma: {lose_count + 1}\n\n"
                    f"Balans doldurmak üçin: @astra_kassa"
                )
            except:
                logger.warning("Не удалось отправить сообщение в группу")
        else:
            prize_value = 0
            win_text = "0\nTMT"
            result_text = f"😞 *Siz 0 TMT gazandyňyz!* 😞\nŞowly gün däl, ertir synanyşyň!"
        
        # Находим индекс сектора для отображения
        if prize_value == 0:
            selected_index = 0  # 0 TMT
        elif prize_value == 5:
            selected_index = 1  # 5 TMT
        elif prize_value == 10:
            selected_index = 2  # 10 TMT
        else:
            selected_index = 3  # 20 TMT
        
        spins_count += 1
        
        # Рисуем колесо
        wheel_img = draw_wheel(selected_index, win_text)
        
        # Сохраняем историю
        save_spin_history(user_id, prize_value)
        update_user(user_id, balance, total_won, datetime.now().isoformat(), spins_count)
        
        # Обновляем глобальный счетчик
        new_lose_count = lose_count + 1
        update_global_counter(new_lose_count, last_win_cycle)
        
        # Отправляем результат
        if wheel_img:
            wheel_img.save(WHEEL_IMAGE_PATH)
            
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=FSInputFile(WHEEL_IMAGE_PATH),
                caption=f"{result_text}\n\n💰 *Täze balans:* {balance} TMT\n🏆 *Jemi ýeňiş:* {total_won} TMT",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
            
            try:
                os.remove(WHEEL_IMAGE_PATH)
            except:
                pass
        else:
            await callback.message.edit_text(
                f"{result_text}\n\n"
                f"💰 *Täze balans:* {balance} TMT\n"
                f"🏆 *Jemi ýeňiş:* {total_won} TMT",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
        
        # Если выигрыш, отправляем сообщение с контактом админа
        if is_win:
            await callback.message.answer(
                f"🎉 *Gutlaýarys!* 🎉\n\n"
                f"Siz {prize_value} TMT gazandyňyz!\n\n"
                f"📞 *Balansyňyzy almak üçin* @astra_kassa bilen habarlaşyň!",
                parse_mode="Markdown"
            )
        
        logger.info(f"Пользователь {user_id} - Спин #{new_lose_count} - Выигрыш: {prize_value} TMT")
        
    except Exception as e:
        logger.error(f"Ошибка в spin_wheel: {e}", exc_info=True)
        try:
            await callback.message.answer("⚠️ Tekniki ýalňyşlyk! Soňra synanyşyň.")
        except:
            pass
        await callback.answer("⚠️ Ýalňyşlyk boldy!", show_alert=True)

# --- АДМИН КОМАНДЫ ---
@dp.message(Command("stats"))
async def show_stats(message: types.Message):
    if message.from_user.id != 8210954671:
        await message.answer("⛔ Bu buýruk diňe administrator üçin!")
        return
    
    try:
        lose_count, last_win_cycle = get_global_counter()
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM users")
        total_users = cursor.fetchone()[0]
        
        cursor.execute("SELECT SUM(balance) FROM users")
        total_balance = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT SUM(total_won) FROM users")
        total_won_all = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM spin_history WHERE prize_value > 0")
        total_wins = cursor.fetchone()[0] or 0
        
        cursor.execute("SELECT COUNT(*) FROM spin_history")
        total_spins = cursor.fetchone()[0] or 0
        
        conn.close()
        
        # Определяем следующий выигрыш
        next_win_spin = ((lose_count // 40) + 1) * 40
        remaining = next_win_spin - lose_count
        
        win_cycle = (next_win_spin // 40)
        if win_cycle % 3 == 1:
            next_prize = 5
        elif win_cycle % 3 == 2:
            next_prize = 10
        else:
            next_prize = 20
        
        text = (
            f"📊 *Global statistika:*\n\n"
            f"👥 Ulanyjylar: {total_users}\n"
            f"💰 Umumy balans: {total_balance} TMT\n"
            f"🏆 Jemi ýeňişler: {total_won_all} TMT\n"
            f"🎡 Jemi aýlanmalar: {total_spins}\n"
            f"🎉 Jemi ýeňişler: {total_wins}\n\n"
            f"📈 *Tekerleme statistikasy:*\n"
            f"🔄 Jemi aýlanma: {lose_count}\n"
            f"🎁 Indiki ýeňiş: {remaining} aýlanmadan soň\n"
            f"💰 Indiki baýrak: {next_prize} TMT"
        )
        
        await message.answer(text, parse_mode="Markdown")
    except Exception as e:
        logger.error(f"Ошибка в show_stats: {e}")
        await message.answer("⚠️ Ýalňyşlyk!")

@dp.message(Command("add"))
async def add_balance(message: types.Message):
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buýruk diňe administrator üçin!")
        return
    
    try:
        args = message.text.split()
        if len(args) != 3:
            await message.answer("Formula: /add user_id 100\n\nMysal: /add 123456789 50")
            return
        
        user_id = int(args[1])
        amount = int(args[2])
        
        if amount <= 0:
            await message.answer("⚠️ Mukdar 0-dan uly bolmaly!")
            return
        
        conn = sqlite3.connect(DB_NAME)
        cursor = conn.cursor()
        cursor.execute("SELECT balance, full_name FROM users WHERE user_id = ?", (user_id,))
        result = cursor.fetchone()
        
        if result:
            current_balance, full_name = result
            new_balance = current_balance + amount
            cursor.execute("UPDATE users SET balance = ? WHERE user_id = ?", (new_balance, user_id))
            conn.commit()
            await message.answer(f"✅ *Balans dolduryldy!*\n\n"
                               f"👤 Ulanyjy: {full_name or user_id}\n"
                               f"💰 Köne balans: {current_balance} TMT\n"
                               f"💵 Goşulan: +{amount} TMT\n"
                               f"💰 Täze balans: {new_balance} TMT",
                               parse_mode="Markdown")
            
            try:
                await bot.send_message(
                    user_id, 
                    f"💰 *Balansyňyz dolduryldy!*\n\n"
                    f"Goşulan: +{amount} TMT\n"
                    f"Täze balans: {new_balance} TMT\n\n"
                    f"Tekerleme aýlap görüň! 🎡",
                    parse_mode="Markdown"
                )
            except:
                pass
        else:
            await message.answer(f"❌ {user_id} ID-li ulanyjy tapylmady!")
        
        conn.close()
    except ValueError:
        await message.answer("❌ Ýalňyş format! ID we san dogry ýazyň.\nMysal: /add 123456789 50")
    except Exception as e:
        logger.error(f"Ошибка в add_balance: {e}")
        await message.answer("⚠️ Ýalňyşlyk boldy!")

@dp.message(Command("reset"))
async def reset_counter(message: types.Message):
    """Сбросить глобальный счетчик (только для админа)"""
    if message.from_user.id != ADMIN_ID:
        await message.answer("⛔ Bu buýruk diňe administrator üçin!")
        return
    
    try:
        update_global_counter(0, 0)
        await message.answer("✅ Global hasaplaýjy sıfyrlandy!")
        logger.info("Глобальный счетчик сброшен администратором")
    except Exception as e:
        logger.error(f"Ошибка сброса счетчика: {e}")
        await message.answer("⚠️ Ýalňyşlyk boldy!")

# --- ЗАПУСК ---
async def main():
    logger.info("🚀 Запуск бота Astra Kassa Tekerleme...")
    
    if not init_db():
        logger.error("Не удалось инициализировать базу данных!")
        return
    
    if API_TOKEN == "ВАШ_ТОКЕН_БОТА":
        logger.error("⚠️ Не установлен API_TOKEN бота!")
        print("\n" + "="*50)
        print("⚠️  ОШИБКА: Не установлен токен бота!")
        print("Измените API_TOKEN в файле bot.py на ваш токен")
        print("="*50 + "\n")
        return
    
    lose_count, _ = get_global_counter()
    logger.info(f"✅ Бот запущен")
    logger.info(f"👑 Администратор ID: {ADMIN_ID}")
    logger.info(f"📊 Текущий счетчик проигрышей: {lose_count}")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
