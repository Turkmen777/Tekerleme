import asyncio
import random
import sqlite3
import logging
import sys
import os
from datetime import datetime, timedelta
from io import BytesIO

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

def can_spin(user_id, last_spin_str):
    if not last_spin_str:
        return True
    try:
        last_spin = datetime.fromisoformat(last_spin_str)
        return datetime.now() > last_spin + timedelta(days=1)
    except:
        return True

def get_random_sector():
    rand_num = random.uniform(0, 100)
    cumulative = 0
    for sector in SECTORS:
        cumulative += sector["probability"]
        if rand_num <= cumulative:
            return sector
    return SECTORS[0]

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
            font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 20)
            big_font = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", 36)
        except:
            try:
                font = ImageFont.truetype("arial.ttf", 20)
                big_font = ImageFont.truetype("arial.ttf", 36)
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
            y_offset += 35
        
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
            "• Baýraklar: 0 TMT, Promo5 TMT, Promo10 TMT, Promo20 TMT, Depozit +10%\n\n"
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
        cursor.execute("SELECT prize_type, prize_value, spin_date FROM spin_history WHERE user_id = ? ORDER BY spin_date DESC LIMIT 10", (user_id,))
        history = cursor.fetchall()
        conn.close()
        
        if not history:
            text = "🏆 *Siziň ýeňiş taryhyňyz:*\n\nHiç hili ýeňiş ýok.\n\nTekerleme aýlap görüň! 🎡"
        else:
            text = "🏆 *Soňky 10 ýeňiş:*\n\n"
            for prize_type, prize_value, spin_date in history:
                date_obj = datetime.fromisoformat(spin_date)
                date_str = date_obj.strftime("%d.%m.%Y %H:%M")
                if prize_type == "prize":
                    if prize_value > 0:
                        text += f"🎉 +{prize_value} TMT - {date_str}\n"
                    else:
                        text += f"😞 0 TMT - {date_str}\n"
                else:
                    text += f"⭐ Depozit +10% - {date_str}\n"
        
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
        
        # Выбираем сектор
        selected_sector = get_random_sector()
        selected_index = SECTORS.index(selected_sector)
        
        # Обрабатываем результат
        result_text = ""
        win_text = ""
        reward = 0
        
        if selected_sector["type"] == "prize":
            reward = selected_sector["value"]
            balance += reward
            total_won += reward
            spins_count += 1
            
            if reward > 0:
                win_text = f"+{reward}\nTMT"
                result_text = f"🎉 *Siz {reward} TMT gazandyňyz!* 🎉"
                # Отправляем уведомление в группу
                try:
                    await bot.send_message(
                        GROUP_ID,
                        f"🎉 *Ýeňiş!* 🎉\n\n"
                        f"👤 @{username} ({full_name})\n"
                        f"💰 {reward} TMT gazandy!\n"
                        f"🏆 Promo{reward} TMT\n\n"
                        f"Balans doldurmak üçin: @astra_kassa"
                    )
                except:
                    logger.warning("Не удалось отправить сообщение в группу")
            else:
                win_text = "0\nTMT"
                result_text = f"😞 *Siz 0 TMT gazandyňyz!* 😞\nŞowly gün däl, ertir synanyşyň!"
                # Отправляем уведомление в группу
                try:
                    await bot.send_message(
                        GROUP_ID,
                        f"😞 *Şowsuzlyk!* 😞\n\n"
                        f"👤 @{username} ({full_name})\n"
                        f"💰 0 TMT gazandy!\n\n"
                        f"Ertir täzeden synanyşar!"
                    )
                except:
                    logger.warning("Не удалось отправить сообщение в группу")
        
        elif selected_sector["type"] == "bonus":
            reward = 5
            balance += reward
            total_won += reward
            spins_count += 1
            win_text = "+10%\nBonus"
            result_text = f"⭐ *Depozit +10% bonus gazandyňyz!* ⭐\nBalansyňyza 5 TMT goşuldy!\n\nDepozit edeniňizde +10% goşmaça alarsyňyz!"
            # Отправляем уведомление в группу
            try:
                await bot.send_message(
                    GROUP_ID,
                    f"⭐ *Bonus!* ⭐\n\n"
                    f"👤 @{username} ({full_name})\n"
                    f"🎁 Depozit +10% bonus gazandy!\n"
                    f"💰 +5 TMT balansyna goşuldy!\n\n"
                    f"Gutlaýarys! 🎉"
                )
            except:
                logger.warning("Не удалось отправить сообщение в группу")
        
        # Рисуем колесо
        wheel_img = draw_wheel(selected_index, win_text)
        
        # Сохраняем в БД
        save_spin_history(user_id, selected_sector["type"], selected_sector["value"] if selected_sector["type"] == "prize" else 0)
        update_user(user_id, balance, total_won, datetime.now().isoformat(), spins_count)
        
        # Отправляем результат
        if wheel_img:
            # Сохраняем временный файл
            wheel_img.save(WHEEL_IMAGE_PATH)
            
            # Создаем новое сообщение с фото, а не редактируем старое
            await callback.message.delete()
            await callback.message.answer_photo(
                photo=FSInputFile(WHEEL_IMAGE_PATH),
                caption=f"{result_text}\n\n💰 *Täze balans:* {balance} TMT\n🏆 *Jemi ýeňiş:* {total_won} TMT",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
            
            # Удаляем временный файл
            try:
                os.remove(WHEEL_IMAGE_PATH)
            except:
                pass
        else:
            # Если не удалось создать изображение, отправляем текст
            await callback.message.edit_text(
                f"{result_text}\n\n"
                f"💰 *Täze balans:* {balance} TMT\n"
                f"🏆 *Jemi ýeňiş:* {total_won} TMT",
                reply_markup=main_menu_keyboard(),
                parse_mode="Markdown"
            )
        
        # Если выигрыш больше 0, отправляем дополнительное сообщение с контактом админа
        if reward > 0:
            await callback.message.answer(
                f"🎉 *Gutlaýarys!* 🎉\n\n"
                f"Siz {reward} TMT gazandyňyz!\n\n"
                f"📞 *Balansyňyzy almak üçin* @astra_kassa bilen habarlaşyň!",
                parse_mode="Markdown"
            )
        
        logger.info(f"Пользователь {user_id} выиграл {reward} TMT")
        
    except Exception as e:
        logger.error(f"Ошибка в spin_wheel: {e}", exc_info=True)
        try:
            await callback.message.answer("⚠️ Tekniki ýalňyşlyk! Soňra synanyşyň.")
        except:
            pass
        await callback.answer("⚠️ Ýalňyşlyk boldy!", show_alert=True)

# --- АДМИН КОМАНДЫ ---
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
    
    if GROUP_ID == -100123456789:
        logger.warning("⚠️ Не установлен GROUP_ID!")
        print("\n" + "="*50)
        print("⚠️  ВНИМАНИЕ: Не установлен ID группы для уведомлений!")
        print("1. Добавьте бота в группу")
        print("2. Напишите /start в группе")
        print("3. Получите ID группы (например через @getmyid_bot)")
        print("4. Укажите GROUP_ID в файле bot.py")
        print("="*50 + "\n")
    
    logger.info(f"✅ Бот запущен")
    logger.info(f"👑 Администратор ID: {ADMIN_ID}")
    logger.info(f"📢 Группа для уведомлений ID: {GROUP_ID}")
    
    try:
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Ошибка при запуске бота: {e}")

if __name__ == "__main__":
    asyncio.run(main())
