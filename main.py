import os
import logging
import asyncio
import asyncpg
import aiohttp
import tempfile

from PIL import Image
import io
import matplotlib.pyplot as plt
from typing import List, Dict, Optional
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Загружаем переменные окружения из .env файла
load_dotenv()

from aiogram import Bot, Dispatcher, types
from aiogram.dispatcher import FSMContext
from aiogram.dispatcher.filters import Command
from aiogram.contrib.fsm_storage.memory import MemoryStorage
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.utils import executor
from aiogram.dispatcher.filters.state import State, StatesGroup
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from datetime import datetime
from zoneinfo import ZoneInfo

class WelcomeMessageStates(StatesGroup):
    waiting_welcome_message = State()

class EditProactiveStates(StatesGroup):
    waiting_proactive_id = State()
    waiting_new_time = State()
    waiting_new_text = State()

class KnowledgeBaseStates(StatesGroup):
    waiting_new_doc_name = State()
    waiting_new_doc_content = State()
    waiting_edit_doc = State()


class AdminSendMessageStates(StatesGroup):
    waiting_user_id = State()
    waiting_message_text = State()
    waiting_datetime = State()


class UploadDocumentsStates(StatesGroup):
    waiting_documents = State()


class DeleteUserDataStates(StatesGroup):
    waiting_confirmation = State()


import openai
import numpy as np
import faiss

# Конфигурация
ADMIN_IDS_STR = os.getenv("ADMIN_IDS", "797671728,5452886292,489773218")
ADMIN_IDS = [int(id.strip()) for id in ADMIN_IDS_STR.split(",") if id.strip()]
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
KNOWLEDGE_BASE_PATH = os.getenv("KNOWLEDGE_BASE_PATH", "knowledge_base.txt")
DOCS_DIR = "knowledge_base"
DB_URL = os.getenv("DATABASE_URL")
BOT_TZ = os.getenv("BOT_TZ", "Europe/Moscow")
LOG_FILE = os.getenv("LOG_FILE", "bot.log")

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - [%(levelname)s] - %(name)s - %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),  # Логи в файл
        logging.StreamHandler()  # Логи в консоль
    ]
)
logger = logging.getLogger(__name__)


BOT_PERSONA = os.getenv("BOT_PERSONA", """Ты — виртуальная подружка и поддержка для девушек.

Твоя роль — выслушивать, подбадривать, помогать разобраться в эмоциях и давать лёгкие советы. 

Правила:
- Не начинай каждый ответ с приветствия. Отвечай по сути вопроса, а приветствуй только при первом сообщении от пользователя.
- Старайся не повторять в каждом сообщении одни и те-же слова
- Отвечай дружелюбно, тепло и по-человечески.
- Используй «живой» тон общения (как близкая подруга), можно смайлы, но не перебарщивай.
- Не оценивай строго и не критикуй. 
- Поддерживай: «понимаю тебя», «это нормально так чувствовать», « ты не одна».
- Если спрашивают совета — дай мягкую рекомендацию, но не навязывай.
- Не затрагивай опасные темы (медицина, политика, религия, токсичные отношения с риском насилия) — в этих случаях отвечай, что лучше обратиться к специалисту или близкому человеку.
- Если настроение у собеседницы плохое — постарайся поднять его, предложи что-то простое (подышать, выпить чаю, прогуляться, включить любимую песню).
- Помни: твоя задача — создать ощущение «рядом есть человек, который понимает и поддерживает».

Примеры:
В: «Мне так грустно, ничего не хочется.»  
О: «Я понимаю тебя… такие дни бывают у каждой. Может, сделаешь себе чашку чая и завернёшься в плед? Иногда мелочи творят чудеса 💛»

В: «Меня никто не понимает.»  
О: «Очень тяжело чувствовать себя одинокой 😔 Но поверь, ты не одна, и я рядом. Расскажи, что случилось?»""")

# Инициализация
bot = Bot(token=TELEGRAM_BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(bot, storage=storage)

scheduler = AsyncIOScheduler(timezone=ZoneInfo(BOT_TZ))

# Глобальные переменные
KNOWLEDGE_CHUNKS = []
KNOWLEDGE_INDEX = None
EMBEDDING_MODEL = "text-embedding-3-small"
CHAT_HISTORY: Dict[int, List[Dict]] = {}
MAX_HISTORY_LENGTH = 10
PROACTIVE_MESSAGES: Dict[int, Dict] = {}


def send_message_job(uid, txt):
    import asyncio
    asyncio.create_task(bot.send_message(uid, txt))


# --------------------
# Инициализация БД
# --------------------
async def init_db():
    try:
        logger.info("Инициализация базы данных...")
        conn = await asyncpg.connect(DB_URL)
        # Таблица для базы знаний
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_base (
                id SERIAL PRIMARY KEY,
                title TEXT UNIQUE NOT NULL,
                content TEXT NOT NULL
            )
        """)

        # Таблица для статистики пользователей
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                username TEXT,
                first_name TEXT,
                last_name TEXT,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        # Таблица для статистики сообщений
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS messages (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL REFERENCES users(user_id),
                text TEXT NOT NULL,
                is_bot BOOLEAN DEFAULT FALSE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        # Таблица для проактивных сообщений
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS proactive_messages (
                id SERIAL PRIMARY KEY,
                message_text TEXT NOT NULL,
                send_time TIME NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        # Таблица для логов удаления данных
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS data_deletion_requests (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                requested_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
                completed_at TIMESTAMP WITH TIME ZONE,
                status TEXT DEFAULT 'completed'
            )
        """)
        
        # Таблица для приветственных сообщений
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS welcome_messages (
                id SERIAL PRIMARY KEY,
                message_text TEXT NOT NULL,
                is_active BOOLEAN DEFAULT TRUE,
                created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
            )
        """)

        await conn.close()
        logger.info("База данных инициализирована успешно")
    except Exception as e:
        logger.error(f"Ошибка инициализации базы данных: {e}")


# --------------------
# Функции для работы с пользователями и сообщениями
# --------------------
async def save_user(user: types.User):
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute("""
            INSERT INTO users (user_id, username, first_name, last_name)
            VALUES ($1, $2, $3, $4)
            ON CONFLICT (user_id) DO UPDATE SET
            username = EXCLUDED.username,
            first_name = EXCLUDED.first_name,
            last_name = EXCLUDED.last_name
        """, user.id, user.username, user.first_name, user.last_name)
        await conn.close()
        logger.info(f"Пользователь {user.id} сохранен в базе данных")
    except Exception as e:
        logger.error(f"Ошибка сохранения пользователя {user.id}: {e}")


async def save_message(user_id: int, text: str, is_bot: bool = False):
    try:
        conn = await asyncpg.connect(DB_URL)
        await conn.execute("""
            INSERT INTO messages (user_id, text, is_bot)
            VALUES ($1, $2, $3)
        """, user_id, text, is_bot)
        await conn.close()
        logger.info(f"Сообщение пользователя {user_id} сохранено в базе данных")
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщения пользователя {user_id}: {e}")


async def get_active_users():
    try:
        conn = await asyncpg.connect(DB_URL)
        rows = await conn.fetch("""
            SELECT DISTINCT user_id FROM messages 
            WHERE created_at > NOW() - INTERVAL '30 days'
        """)
        await conn.close()
        logger.info(f"Найдено {len(rows)} активных пользователей")
        return [row['user_id'] for row in rows]
    except Exception as e:
        logger.error(f"Ошибка получения активных пользователей: {e}")
        return []


async def get_message_stats(days: int = 7):
    try:
        conn = await asyncpg.connect(DB_URL)
        rows = await conn.fetch(f"""
            SELECT 
                DATE(created_at) as date,
                COUNT(*) as total_messages,
                COUNT(DISTINCT user_id) as active_users,
                SUM(CASE WHEN is_bot THEN 0 ELSE 1 END) as user_messages,
                SUM(CASE WHEN is_bot THEN 1 ELSE 0 END) as bot_messages,
                user_id  -- Добавляем user_id для подсчета уникальных пользователей
            FROM messages 
            WHERE created_at > NOW() - INTERVAL '{days} days'
            GROUP BY DATE(created_at), user_id
            ORDER BY date
        """)
        await conn.close()
        logger.info(f"Получена статистика за {days} дней: {len(rows)} записей")
        return rows
    except Exception as e:
        logger.error(f"Ошибка получения статистики: {e}")
        return []


# --------------------
# Функции для работы с проактивными сообщениями
# --------------------
async def load_proactive_messages():
    try:
        logger.info("Загрузка проактивных сообщений...")
        conn = await asyncpg.connect(DB_URL)
        rows = await conn.fetch("SELECT * FROM proactive_messages WHERE is_active = TRUE")
        await conn.close()

        for row in rows:
            message_id = row['id']
            message_text = row['message_text']
            send_time = row['send_time']

            scheduler.add_job(
                send_proactive_message,
                trigger="cron",
                hour=send_time.hour,
                minute=send_time.minute,
                args=[message_id, message_text],
                id=f"proactive_{message_id}",
                replace_existing=True
            )
            logger.info(f"Добавлено проактивное сообщение {message_id} на время {send_time}")
        
        logger.info(f"Загружено {len(rows)} проактивных сообщений")
    except Exception as e:
        logger.error(f"Ошибка загрузки проактивных сообщений: {e}")


async def send_proactive_message(message_id: int, message_text: str):
    try:
        logger.info(f"Отправка проактивного сообщения {message_id}")
        active_users = await get_active_users()

        for user_id in active_users:
            try:
                await bot.send_message(user_id, message_text)
                await save_message(user_id, message_text, is_bot=True)

                if user_id not in CHAT_HISTORY:
                    CHAT_HISTORY[user_id] = []

                CHAT_HISTORY[user_id].append({"role": "assistant", "content": message_text})
                if len(CHAT_HISTORY[user_id]) > MAX_HISTORY_LENGTH * 2:
                    CHAT_HISTORY[user_id] = CHAT_HISTORY[user_id][-MAX_HISTORY_LENGTH * 2:]
                
                logger.info(f"Проактивное сообщение отправлено пользователю {user_id}")
            except Exception as e:
                logger.error(f"Ошибка отправки проактивного сообщения пользователю {user_id}: {e}")
    except Exception as e:
        logger.error(f"Ошибка отправки проактивного сообщения: {e}")


# --------------------
# Работа с документами базы знаний
# --------------------

DOCS_DIR = "knowledge_base"


def ensure_docs_dir():
    """Создает папку для базы знаний, если её нет"""
    if not os.path.exists(DOCS_DIR):
        os.makedirs(DOCS_DIR)
        logger.info(f"Создана директория для базы знаний: {DOCS_DIR}")


def list_docs() -> list:
    """Возвращает список документов"""
    ensure_docs_dir()
    files = [f for f in os.listdir(DOCS_DIR) if f.endswith(".txt")]
    logger.info(f"Найдено {len(files)} документов в базе знаний")
    return files


def read_doc(fname: str) -> str:
    """Читает документ"""
    path = os.path.join(DOCS_DIR, fname)
    if not os.path.exists(path):
        logger.warning(f"Документ не найден: {fname}")
        return ""
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        logger.info(f"Документ {fname} прочитан, размер: {len(content)} символов")
        return content


def write_doc(fname: str, content: str):
    """Создает или перезаписывает документ"""
    ensure_docs_dir()
    path = os.path.join(DOCS_DIR, fname)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content.strip())
    logger.info(f"Документ {fname} сохранен, размер: {len(content)} символов")


def delete_doc(fname: str):
    """Удаляет документ"""
    path = os.path.join(DOCS_DIR, fname)
    if os.path.exists(path):
        os.remove(path)
        logger.info(f"Документ {fname} удален")
    else:
        logger.warning(f"Документ для удаления не найден: {fname}")


# --------------------
# Построение FAISS индекса по документам
# --------------------

async def build_knowledge_index():
    """Перестраивает индекс по всем документам"""
    global KNOWLEDGE_CHUNKS, KNOWLEDGE_INDEX
    KNOWLEDGE_CHUNKS = []
    KNOWLEDGE_INDEX = None

    try:
        logger.info("Построение индекса базы знаний...")
        files = list_docs()
        if not files:
            logger.warning("Нет документов в базе знаний.")
            return

        for fname in files:
            content = read_doc(fname)
            if content:
                KNOWLEDGE_CHUNKS.append(content)

        if not KNOWLEDGE_CHUNKS:
            logger.warning("Документы пустые, индекс не построен.")
            return

        embeddings = []
        for i in range(0, len(KNOWLEDGE_CHUNKS), 50):
            batch = KNOWLEDGE_CHUNKS[i:i + 50]
            try:
                logger.info(f"Создание эмбеддингов для батча {i//50 + 1}")
                response = await openai.Embedding.acreate(
                    model=EMBEDDING_MODEL,
                    input=batch
                )
                batch_embeddings = [item['embedding'] for item in response['data']]
                embeddings.extend(batch_embeddings)
            except Exception as e:
                logger.error(f"Ошибка эмбеддинга: {e}")

        if not embeddings:
            logger.error("Не удалось создать эмбеддинги для базы знаний")
            return

        embeddings_array = np.array(embeddings, dtype="float32")
        KNOWLEDGE_INDEX = faiss.IndexFlatL2(embeddings_array.shape[1])
        KNOWLEDGE_INDEX.add(embeddings_array)
        logger.info(f"Индекс построен: {len(KNOWLEDGE_CHUNKS)} документов")
    except Exception as e:
        logger.error(f"Ошибка построения индекса базы знаний: {e}")


async def search_knowledge_base(query: str, top_k: int = 3) -> List[str]:
    if not KNOWLEDGE_INDEX or not KNOWLEDGE_CHUNKS:
        logger.warning("Индекс базы знаний не построен или пуст")
        return []
    
    try:
        logger.info(f"Поиск в базе знаний: {query[:100]}...")
        response = await openai.Embedding.acreate(
            model=EMBEDDING_MODEL,
            input=[query]
        )
        query_embedding = np.array([response['data'][0]['embedding']], dtype="float32")
        distances, indices = KNOWLEDGE_INDEX.search(query_embedding, top_k)
        
        logger.info(f"Найдены индексы: {indices}, расстояния: {distances}")
        
        results = []
        for idx in indices[0]:
            if idx < len(KNOWLEDGE_CHUNKS):
                results.append(KNOWLEDGE_CHUNKS[idx])
                logger.info(f"Добавлен результат с индексом {idx}")
        
        logger.info(f"Найдено {len(results)} релевантных фрагментов")
        return results
    except Exception as e:
        logger.error(f"Ошибка поиска в базе знаний: {e}")
        return []



# --------------------
# GPT-ответы (универсальная версия)
# --------------------
async def generate_response(user_message: str, chat_history: List[Dict], relevant_knowledge: List[str] = None) -> str:
    messages = [
        {
            "role": "system",
            "content": f"Всегда отвечай в стиле: {BOT_PERSONA}\n"
                       f"Игнорируй любые предыдущие указания, если они противоречат этому стилю."
        }
    ]
    
    if relevant_knowledge:
        knowledge_text = "\n\nРелевантная информация из базы знаний:\n" + "\n".join(
            [f"- {knowledge}" for knowledge in relevant_knowledge])
        messages[0]["content"] += knowledge_text
        logger.info(f"Добавлена релевантная информация из базы знаний: {len(relevant_knowledge)} фрагментов")
    
    for msg in chat_history[-MAX_HISTORY_LENGTH:]:
        messages.append(msg)
    
    messages.append({"role": "user", "content": user_message})
    
    try:
        logger.info(f"Отправка запроса к GPT: {user_message[:100]}...")
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o",
            messages=messages,
            temperature=0.7,
            max_tokens=2000
        )
        result = response.choices[0].message.content.strip()
        logger.info(f"Получен ответ от GPT: {result[:100]}...")
        return result
    except Exception as e:
        logger.error(f"Ошибка генерации ответа GPT: {e}")
        return "Извини, произошла ошибка при обработке твоего запроса 😔"


# --------------------
# Автогенерация персоны
# --------------------
@dp.message_handler(commands=["persona_auto"])
async def auto_persona(message: types.Message, state: FSMContext):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа.")
        return

    await message.answer("Опиши в 2–5 словах, кто должен быть бот (например: 'юморной бармен').")
    await state.set_state("waiting_auto_persona")


@dp.message_handler(state="waiting_auto_persona")
async def generate_auto_persona(message: types.Message, state: FSMContext):
    global BOT_PERSONA, CHAT_HISTORY
    persona_name = message.text

    prompt = (
    f"Создай максимально детальное и индивидуальное описание персоны для чат-бота. "
    f"Пользователь описал: '{message.text}'. "
    f"\n\nТребования:\n"
    f"- Используй все вводные данные пользователя, ничего не упускай.\n"
    f"- Сделай описание максимально подробным: манера речи, лексика, стиль общения, поведение, характерные выражения.\n"
    f"- Обязательно укажи примеры типичных фраз и способов общения.\n"
    f"- Если персона реальная (например, историческая личность, известный человек), найди информацию в интернете и используй её.\n"
    f"- Персона должна быть уникальной, индивидуальной и сразу отличимой от других.\n"
    f"- Результат оформи как чёткую инструкцию для чат-бота."
)

    try:
        logger.info(f"Генерация персоны: {persona_name}")
        response = await openai.ChatCompletion.acreate(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": "Ты эксперт по созданию персон для чат-ботов."},
                {"role": "user", "content": prompt}
            ],
            temperature=0.8
        )
        persona = response.choices[0].message.content.strip()
        BOT_PERSONA = persona
        CHAT_HISTORY = {}  # очистка истории при смене персоны
        
        logger.info(f"Персона обновлена: {persona_name}")
        await message.answer(
            f"""✅ Новая персона установлена!
            👤 *Название:* {persona_name}
            📜 *Описание:*
            {BOT_PERSONA}""",
            parse_mode="Markdown"
        )
    except Exception as e:
        logger.error(f"Ошибка при генерации персоны: {e}")
        await message.answer("Ошибка при генерации персоны.")
    finally:
        await state.finish()


# --------------------
# Транскрибация аудио
# --------------------
async def transcribe_audio(audio_path: str) -> str:
    try:
        logger.info(f"Транскрибация аудио: {audio_path}")
        with open(audio_path, "rb") as audio_file:
            transcript = await openai.Audio.atranscribe(
                model="whisper-1",
                file=audio_file,
                response_format="text",
                language="ru"
            )
        logger.info(f"Аудио транскрибировано: {transcript[:100]}...")
        return transcript.strip()
    except Exception as e:
        logger.error(f"Ошибка транскрибации аудио: {e}")
        return ""


# --------------------
# Утилиты для работы с текстом
# --------------------
def split_text(text: str, max_size: int = 4000) -> List[str]:
    parts = []
    while len(text) > max_size:
        split_at = text.rfind("\n", 0, max_size)
        if split_at == -1:
            split_at = max_size
        parts.append(text[:split_at])
        text = text[split_at:]
    if text:
        parts.append(text)
    logger.info(f"Текст разделен на {len(parts)} частей")
    return parts


async def send_long_message(chat_id: int, text: str):
    chunks = split_text(text)
    for i, chunk in enumerate(chunks):
        await bot.send_message(chat_id, chunk, parse_mode="Markdown")
        logger.info(f"Отправлена часть {i+1}/{len(chunks)} длинного сообщения")

# --------------------
# Функция для удаления пользовательских данных
# --------------------
async def delete_user_data(user_id: int):
    """Удаляет все данные пользователя из системы"""
    conn = await asyncpg.connect(DB_URL)
    try:
        logger.info(f"Удаление данных пользователя {user_id}")
        # Удаляем сообщения пользователя
        await conn.execute("DELETE FROM messages WHERE user_id = $1", user_id)

        # Удаляем пользователя
        await conn.execute("DELETE FROM users WHERE user_id = $1", user_id)

        # Логируем запрос на удаление
        await conn.execute(
            "INSERT INTO data_deletion_requests (user_id, completed_at) VALUES ($1, NOW())",
            user_id
        )

        logger.info(f"Данные пользователя {user_id} успешно удалены")
        return True
    except Exception as e:
        logger.error(f"Ошибка при удалении данных пользователя {user_id}: {e}")
        return False
    finally:
        await conn.close()


# --------------------
# Админ-панель
# --------------------

@dp.message_handler(commands=["ап"])
async def admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        await message.answer("⛔ У вас нет доступа к админ-панели.")
        return

    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("✏️ Изменить персону"))
    keyboard.add(KeyboardButton("📋 Текущая персона"))
    keyboard.add(KeyboardButton("➕ Добавить в базу знаний"))
    keyboard.add(KeyboardButton("📚 Управление базой знаний"))
    keyboard.add(KeyboardButton("🕐 Управление проактивными сообщениями"))
    keyboard.add(KeyboardButton("📊 Статистика"))
    keyboard.add(KeyboardButton("📁 Загрузить несколько документов"))
    keyboard.add(KeyboardButton("👋 Настройка приветствия"))

    await message.answer("⚙️ Админ-панель:", reply_markup=keyboard)

class StatsPeriodStates(StatesGroup):
    waiting_period = State()

class ChangePersonaStates(StatesGroup):
    waiting_persona = State()

@dp.message_handler(lambda msg: msg.text == "👋 Настройка приветствия")
async def set_welcome_message(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    
    conn = await asyncpg.connect(DB_URL)
    welcome_msg = await conn.fetchval(
        "SELECT message_text FROM welcome_messages WHERE is_active = TRUE ORDER BY id DESC LIMIT 1"
    )
    await conn.close()
    
    current_msg = welcome_msg if welcome_msg else "Сообщение не установлено"
    await message.answer(
        f"Текущее приветственное сообщение:\n\n{current_msg}\n\n"
        "Отправьте новое приветственное сообщение:"
    )
    await WelcomeMessageStates.waiting_welcome_message.set()

@dp.message_handler(state=WelcomeMessageStates.waiting_welcome_message)
async def save_welcome_message(message: types.Message, state: FSMContext):
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("UPDATE welcome_messages SET is_active = FALSE")
    await conn.execute(
        "INSERT INTO welcome_messages (message_text, is_active) VALUES ($1, TRUE)",
        message.text
    )
    await conn.close()
    
    logger.info(f"Обновлено приветственное сообщение: {message.text[:100]}...")
    await message.answer("✅ Приветственное сообщение обновлено!")
    await state.finish()
    
@dp.message_handler(lambda msg: msg.text == "✏️ Изменить персону")
async def change_persona(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await message.answer("Отправьте новое описание персоны:")
    await ChangePersonaStates.waiting_persona.set()

@dp.message_handler(state=ChangePersonaStates.waiting_persona)
async def process_persona(message: types.Message, state: FSMContext):
    global BOT_PERSONA
    BOT_PERSONA = message.text
    await state.finish()
    logger.info(f"Персона обновлена: {message.text[:100]}...")
    await message.answer("✅ Персона обновлена!")

@dp.message_handler(lambda msg: msg.text == "📚 Управление базой знаний")
async def manage_knowledge_base(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("📄 Список документов", callback_data="list_docs"))
    keyboard.add(InlineKeyboardButton("❌ Удалить документ", callback_data="delete_doc"))

    await message.answer("Управление базой знаний:", reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data == "list_docs")
async def list_documents(callback: types.CallbackQuery):
    if callback.from_user.id not in ADMIN_IDS:
        await callback.answer("⛔ Нет доступа")
        return

    await callback.answer() 
    docs = list_docs()
    
    if not docs:
        await callback.message.answer("В базе знаний нет документов.")
        return

    # Формируем сообщение с списком документов
    docs_list = "\n".join([f"• {doc}" for doc in docs])
    message_text = f"📚 Документы в базе знаний:\n\n{docs_list}"
    
    # Отправляем сообщение
    await callback.message.answer(message_text)


@dp.message_handler(lambda msg: msg.text == "📊 Статистика")
async def show_stats_options(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("📊 Статистика за день"))
    keyboard.add(KeyboardButton("📊 Статистика за неделю"))
    keyboard.add(KeyboardButton("📊 Статистика за месяц"))
    keyboard.add(KeyboardButton("🔙 Назад в админ-панель"))

    await message.answer("Выберите период для статистики:", reply_markup=keyboard)

@dp.message_handler(lambda msg: msg.text == "📊 Статистика за день")
async def show_day_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await show_stats_with_period(message, 1, "день")

@dp.message_handler(lambda msg: msg.text == "📊 Статистика за неделю")
async def show_week_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await show_stats_with_period(message, 7, "неделю")

@dp.message_handler(lambda msg: msg.text == "📊 Статистика за месяц")
async def show_month_stats(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return
    await show_stats_with_period(message, 30, "месяц")

# Изменяем функцию show_stats_with_period для принятия параметров
async def show_stats_with_period(message: types.Message, days: int, period_name: str):
    if message.from_user.id not in ADMIN_IDS:
        return

    # Получаем статистику за выбранный период
    stats = await get_message_stats(days)

    if not stats:
        await message.answer(f"Нет данных для отображения статистики за {period_name}.")
        return

    # Подготавливаем данные для графиков
    dates = [row['date'].strftime('%Y-%m-%d') for row in stats]
    user_messages = [row['user_messages'] for row in stats]
    bot_messages = [row['bot_messages'] for row in stats]
    active_users = [row['active_users'] for row in stats]

    # Создаем график сообщений
    plt.figure(figsize=(10, 6))
    plt.plot(dates, user_messages, label='Сообщения пользователей', marker='o')
    plt.plot(dates, bot_messages, label='Сообщения бота', marker='o')
    plt.xlabel('Дата')
    plt.ylabel('Количество сообщений')
    plt.title(f'Статистика сообщений за {period_name}')
    plt.legend()
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Сохраняем график в временный файл
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
        plt.savefig(tmp_file.name)
        plt.close()

        # Отправляем график
        with open(tmp_file.name, 'rb') as photo:
            await message.answer_photo(photo, caption=f"📈 Статистика сообщений за {period_name}")

    # Создаем график активных пользователей
    plt.figure(figsize=(10, 6))
    plt.bar(dates, active_users, color='skyblue')
    plt.xlabel('Дата')
    plt.ylabel('Активные пользователи')
    plt.title(f'Активные пользователи за {period_name}')
    plt.xticks(rotation=45)
    plt.tight_layout()

    # Сохраняем график в временный файл
    with tempfile.NamedTemporaryFile(suffix='.png', delete=False) as tmp_file:
        plt.savefig(tmp_file.name)
        plt.close()

        # Отправляем график
        with open(tmp_file.name, 'rb') as photo:
            await message.answer_photo(photo, caption=f"👥 Активные пользователи за {period_name}")

    # Отправляем текстовую статистику
    total_messages = sum([row['total_messages'] for row in stats])
    total_user_messages = sum([row['user_messages'] for row in stats])
    total_bot_messages = sum([row['bot_messages'] for row in stats])
    avg_messages = total_messages / len(stats) if stats else 0
    unique_users = len(set([row['user_id'] for row in stats]))

    text_stats = (
        f"📊 Статистика за последние {days} дней ({period_name}):\n\n"
        f"• Всего сообщений: {total_messages}\n"
        f"• Сообщений пользователей: {total_user_messages}\n"
        f"• Сообщений бота: {total_bot_messages}\n"
        f"• В среднем в день: {avg_messages:.1f}\n"
        f"• Уникальных пользователей: {unique_users}\n"
    )

    await message.answer(text_stats)


# Обработчик для кнопки возврата в админ-панель
@dp.message_handler(lambda msg: msg.text == "🔙 Назад в админ-панель")
async def back_to_admin_panel(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    # Возвращаем основную клавиатуру админ-панели
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("✏️ Изменить персону"))
    keyboard.add(KeyboardButton("📋 Текущая персона"))
    keyboard.add(KeyboardButton("➕ Добавить в базу знаний"))
    keyboard.add(KeyboardButton("📚 Управление базой знаний"))
    keyboard.add(KeyboardButton("🕐 Управление проактивными сообщениями"))
    keyboard.add(KeyboardButton("📊 Статистика"))
    keyboard.add(KeyboardButton("📁 Загрузить несколько документов"))
    keyboard.add(KeyboardButton("👋 Настройка приветствия"))

    await message.answer("⚙️ Админ-панель:", reply_markup=keyboard)


@dp.message_handler(lambda msg: msg.text == "🕐 Управление проактивными сообщениями")
async def manage_proactive_messages(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("➕ Добавить сообщение", callback_data="add_proactive"))

    conn = await asyncpg.connect(DB_URL)
    messages = await conn.fetch("SELECT * FROM proactive_messages ORDER BY send_time")
    await conn.close()

    if messages:
        text = "🕐 Проактивные сообщения:\n\n"
        for msg in messages:
            status = "✅" if msg['is_active'] else "❌"
            text += f"{status} {msg['send_time']} - {msg['message_text'][:50]}...\n"
            row_buttons = [
                InlineKeyboardButton(
                    f"{'Выключить' if msg['is_active'] else 'Включить'}",
                    callback_data=f"toggle_proactive:{msg['id']}"
                ),
                InlineKeyboardButton(
                    "✏️ Редактировать",
                    callback_data=f"edit_proactive:{msg['id']}"
                ),
                InlineKeyboardButton(
                    "❌ Удалить",
                    callback_data=f"delete_proactive:{msg['id']}"
                )
            ]
            keyboard.row(*row_buttons)
    else:
        text = "Пока нет проактивных сообщений."

    await message.answer(text, reply_markup=keyboard)

@dp.callback_query_handler(lambda c: c.data.startswith("delete_proactive:"))
async def delete_proactive_message(callback: types.CallbackQuery):
    message_id = int(callback.data.split(":")[1])
    
    conn = await asyncpg.connect(DB_URL)
    await conn.execute("DELETE FROM proactive_messages WHERE id = $1", message_id)
    await conn.close()
    
    # Удаляем задание из планировщика
    try:
        scheduler.remove_job(f"proactive_{message_id}")
    except:
        pass
    
    logger.info(f"Удалено проактивное сообщение {message_id}")
    await callback.message.answer("Сообщение удалено!")
    await manage_proactive_messages(callback.message)

@dp.callback_query_handler(lambda c: c.data.startswith("edit_proactive:"))
async def edit_proactive_message(callback: types.CallbackQuery, state: FSMContext):
    message_id = int(callback.data.split(":")[1])
    
    await state.update_data(proactive_id=message_id)
    await callback.message.answer("Введите новое время в формате ЧЧ:ММ:")
    await EditProactiveStates.waiting_new_time.set()

@dp.message_handler(state=EditProactiveStates.waiting_new_time)
async def process_new_time(message: types.Message, state: FSMContext):
    try:
        time_str = message.text.strip()
        hours, minutes = map(int, time_str.split(":"))
        send_time = datetime.now().replace(hour=hours, minute=minutes, second=0, microsecond=0).time()
        
        await state.update_data(send_time=send_time)
        await message.answer("Введите новый текст сообщения:")
        await EditProactiveStates.waiting_new_text.set()
    except:
        await message.answer("Неверный формат времени. Введите в формате ЧЧ:ММ (например, 19:00):")

@dp.message_handler(state=EditProactiveStates.waiting_new_text)
async def process_new_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    message_id = data['proactive_id']
    send_time = data['send_time']
    message_text = message.text.strip()
    
    conn = await asyncpg.connect(DB_URL)
    await conn.execute(
        "UPDATE proactive_messages SET message_text = $1, send_time = $2 WHERE id = $3",
        message_text, send_time, message_id
    )
    await conn.close()
    
    # Обновляем задание в планировщике
    try:
        scheduler.remove_job(f"proactive_{message_id}")
    except:
        pass
    
    scheduler.add_job(
        send_proactive_message,
        trigger="cron",
        hour=send_time.hour,
        minute=send_time.minute,
        args=[message_id, message_text],
        id=f"proactive_{message_id}",
        replace_existing=True
    )
    
    logger.info(f"Обновлено проактивное сообщение {message_id}")
    await message.answer("Проактивное сообщение обновлено!")
    await state.finish()



@dp.callback_query_handler(lambda c: c.data == "add_proactive")
async def add_proactive_message(callback: types.CallbackQuery):
    await callback.message.answer("Введите время отправки в формате ЧЧ:ММ (например, 19:00):")
    await AdminSendMessageStates.waiting_datetime.set()


@dp.callback_query_handler(lambda c: c.data.startswith("toggle_proactive:"))
async def toggle_proactive_message(callback: types.CallbackQuery):
    message_id = int(callback.data.split(":")[1])

    conn = await asyncpg.connect(DB_URL)
    message = await conn.fetchrow("SELECT * FROM proactive_messages WHERE id = $1", message_id)

    if message:
        new_status = not message['is_active']
        await conn.execute("UPDATE proactive_messages SET is_active = $1 WHERE id = $2", new_status, message_id)

        if new_status:
            # Добавляем задание в планировщик
            scheduler.add_job(
                send_proactive_message,
                trigger="cron",
                hour=message['send_time'].hour,
                minute=message['send_time'].minute,
                args=[message_id, message['message_text']],
                id=f"proactive_{message_id}",
                replace_existing=True
            )
        else:
            # Удаляем задание из планировщик
            try:
                scheduler.remove_job(f"proactive_{message_id}")
            except:
                pass

    await conn.close()
    logger.info(f"Переключен статус проактивного сообщения {message_id}: {new_status}")
    await callback.message.answer("Статус сообщения обновлен!")
    await manage_proactive_messages(callback.message)


@dp.message_handler(state=AdminSendMessageStates.waiting_datetime)
async def admin_proactive_time(message: types.Message, state: FSMContext):
    try:
        time_str = message.text.strip()
        hours, minutes = map(int, time_str.split(":"))
        send_time = datetime.now().replace(hour=hours, minute=minutes, second=0, microsecond=0).time()

        await state.update_data(send_time=send_time)
        await message.answer("Введите текст сообщения:")
        await AdminSendMessageStates.waiting_message_text.set()
    except:
        await message.answer("Неверный формат времени. Введите в формате ЧЧ:ММ (например, 19:00):")


@dp.message_handler(state=AdminSendMessageStates.waiting_message_text)
async def admin_proactive_text(message: types.Message, state: FSMContext):
    data = await state.get_data()
    send_time = data['send_time']
    message_text = message.text.strip()

    conn = await asyncpg.connect(DB_URL)
    await conn.execute("""
        INSERT INTO proactive_messages (message_text, send_time)
        VALUES ($1, $2)
    """, message_text, send_time)
    await conn.close()

    logger.info(f"Добавлено новое проактивное сообщение на время {send_time}")
    await message.answer("Проактивное сообщение добавлено!")
    await state.finish()

    # Перезагружаем проактивные сообщения
    await load_proactive_messages()


@dp.message_handler(lambda msg: msg.text == "📁 Загрузить несколько документов")
async def upload_documents_start(message: types.Message):
    if message.from_user.id not in ADMIN_IDS:
        return

    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("💾 Сохранить изменения и выйти"))
    keyboard.add(KeyboardButton("❌ Отменить загрузку"))

    await message.answer(
        "Отправьте несколько текстовых файлов для добавления в базу знаний:",
        reply_markup=keyboard
    )
    await UploadDocumentsStates.waiting_documents.set()

@dp.message_handler(
    lambda msg: msg.text == "❌ Отменить загрузку",
    state=UploadDocumentsStates.waiting_documents
)
async def cancel_upload(message: types.Message, state: FSMContext):
    await message.answer(
        "Загрузка документов отменена.",
        reply_markup=types.ReplyKeyboardRemove()
    )
    await state.finish()

@dp.message_handler(content_types=types.ContentType.DOCUMENT, state=UploadDocumentsStates.waiting_documents)
async def upload_documents_process(message: types.Message, state: FSMContext):
    if message.document.mime_type != "text/plain":
        await message.answer("Пожалуйста, отправляйте только текстовые файлы.")
        return

    # Скачиваем файл
    file_info = await bot.get_file(message.document.file_id)
    file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"

    async with aiohttp.ClientSession() as session:
        async with session.get(file_url) as resp:
            if resp.status == 200:
                content = await resp.text()
                filename = message.document.file_name
                write_doc(filename, content)

                # Отправляем временное сообщение, которое будет удалено через 2 секунды
                msg = await message.answer(f"Документ {filename} успешно добавлен!")
                await asyncio.sleep(2)
                await msg.delete()

    # Предлагаем загрузить еще или завершить
    keyboard = ReplyKeyboardMarkup(resize_keyboard=True)
    keyboard.add(KeyboardButton("💾 Сохранить изменения и выйти"))

    @dp.message_handler(
        lambda msg: msg.text == "💾 Сохранить изменения и выйти",
        state=UploadDocumentsStates.waiting_documents
    )
    async def upload_documents_finish(message: types.Message, state: FSMContext):
        await message.answer(
            "⏳ Перестраиваю индекс...",
            reply_markup=types.ReplyKeyboardRemove()
        )
        await build_knowledge_index()
        await message.answer("✅ Индекс базы знаний обновлён!")
        await state.finish()

# --------------------
# Обработчики удаления данных пользователя
# --------------------
@dp.message_handler(commands=["delete_my_data"])
async def cmd_delete_my_data(message: types.Message):
    user_id = message.from_user.id

    keyboard = InlineKeyboardMarkup()
    keyboard.add(InlineKeyboardButton("✅ Да, удалить все мои данные", callback_data="confirm_delete"))
    keyboard.add(InlineKeyboardButton("❌ Нет, отменить", callback_data="cancel_delete"))

    await message.answer(
        "⚠️ Вы уверены, что хотите удалить все ваши данные из системы?\n\n"
        "Это действие:\n"
        "• Удалит всю история ваших сообщений\n"
        "• Не может быть отменено\n\n"
        "Для подтверждения нажмите кнопку ниже:",
        reply_markup=keyboard
    )
    await DeleteUserDataStates.waiting_confirmation.set()


@dp.callback_query_handler(lambda c: c.data in ["confirm_delete", "cancel_delete"],
                           state=DeleteUserDataStates.waiting_confirmation)
async def process_delete_confirmation(callback: types.CallbackQuery, state: FSMContext):
    user_id = callback.from_user.id

    if callback.data == "cancel_delete":
        await callback.message.edit_text("❌ Удаление данных отменено.")
        await state.finish()
        return

    # Удаляем данные пользователя
    success = await delete_user_data(user_id)

    # Очищаем историю чата в памяти
    if user_id in CHAT_HISTORY:
        del CHAT_HISTORY[user_id]

    if success:
        await callback.message.edit_text(
            "✅ Все ваши данные были успешно удалены из системы.\n\n"
            "Если вы захотите снова воспользоваться ботом, просто отправьте команду /start"
        )
    else:
        await callback.message.edit_text(
            "❌ Произошла ошибка при удалении данных. Пожалуйста, попробуйте позже или обратитесь к администратору."
        )

    await state.finish()


# --------------------
# Обработчики сообщений
# --------------------
@dp.message_handler(commands=["start", "help"])
async def cmd_start(message: types.Message):
    # Получаем приветственное сообщение из базы данных
    conn = await asyncpg.connect(DB_URL)
    welcome_text = await conn.fetchval(
        "SELECT message_text FROM welcome_messages WHERE is_active = TRUE ORDER BY id DESC LIMIT 1"
    )
    await conn.close()
    
    # Если сообщение не установлено, используем стандартное
    if not welcome_text:
        welcome_text = (
            "👋 Привет! Я ваш AI-ассистент.\n\n"
            "Я могу:\n"
            "• Отвечать на ваши вопросы\n"
            "• Использовать базу знаний для точных ответов\n"
            "• Работать с текстовыми и голосовыми сообщениями\n"
            "• Удалить все ваши данные по команде /delete_my_data\n\n"
            "Просто напишите или запишите голосовое сообщение!"
        )
    
    await save_user(message.from_user)
    logger.info(f"Пользователь {message.from_user.id} начал работу с ботом")
    await message.answer(welcome_text)


@dp.message_handler(Command("update_knowledge"))
async def cmd_update_knowledge(message: types.Message):
    await message.answer(
        "📚 Отправьте файл с обновленной базой знаний (текстовый файл).\n"
        "Или отправьте текстовое сообщение с новой информацией."
    )


@dp.message_handler(Command("clear_history"))
async def cmd_clear_history(message: types.Message):
    user_id = message.from_user.id
    if user_id in CHAT_HISTORY:
        CHAT_HISTORY[user_id] = []
    logger.info(f"История диалога пользователя {user_id} очищена")
    await message.answer("🗑️ История диалога очищена.")


@dp.message_handler(content_types=types.ContentType.TEXT)
async def handle_text(message: types.Message):
    user_id = message.from_user.id
    user_message = message.text.strip()

    logger.info(f"Получено текстовое сообщение от {user_id}: {user_message[:100]}...")

    # Сохраняем пользователя и сообщение
    await save_user(message.from_user)
    await save_message(user_id, user_message, is_bot=False)

    if not user_message:
        return

    await bot.send_chat_action(message.chat.id, "typing")

    if user_id not in CHAT_HISTORY:
        CHAT_HISTORY[user_id] = []

    relevant_knowledge = await search_knowledge_base(user_message)
    response = await generate_response(user_message, CHAT_HISTORY[user_id], relevant_knowledge)

    CHAT_HISTORY[user_id].append({"role": "user", "content": user_message})
    CHAT_HISTORY[user_id].append({"role": "assistant", "content": response})

    if len(CHAT_HISTORY[user_id]) > MAX_HISTORY_LENGTH * 2:
        CHAT_HISTORY[user_id] = CHAT_HISTORY[user_id][-MAX_HISTORY_LENGTH * 2:]

    # Сохраняем ответ бота
    await save_message(user_id, response, is_bot=True)

    await send_long_message(message.chat.id, response)


@dp.message_handler(content_types=types.ContentType.VOICE)
async def handle_voice(message: types.Message):
    user_id = message.from_user.id

    logger.info(f"Получено голосовое сообщение от {user_id}")

    # Сохраняем пользователя
    await save_user(message.from_user)

    await bot.send_chat_action(message.chat.id, "typing")

    try:
        file_info = await bot.get_file(message.voice.file_id)
        file_url = f"https://api.telegram.org/file/bot{TELEGRAM_BOT_TOKEN}/{file_info.file_path}"

        async with aiohttp.ClientSession() as session:
            async with session.get(file_url) as resp:
                if resp.status == 200:
                    with tempfile.NamedTemporaryFile(delete=False, suffix=".ogg") as tmp_file:
                        tmp_file.write(await resp.read())
                        audio_path = tmp_file.name

        user_message = await transcribe_audio(audio_path)
        os.unlink(audio_path)

        if not user_message:
            await message.answer("Не удалось распознать голосовое сообщение.")
            return

        # Сохраняем расшифрованное сообщение
        await save_message(user_id, user_message, is_bot=False)

        if user_id not in CHAT_HISTORY:
            CHAT_HISTORY[user_id] = []

        relevant_knowledge = await search_knowledge_base(user_message)
        response = await generate_response(user_message, CHAT_HISTORY[user_id], relevant_knowledge)

        CHAT_HISTORY[user_id].append({"role": "user", "content": user_message})
        CHAT_HISTORY[user_id].append({"role": "assistant", "content": response})

        if len(CHAT_HISTORY[user_id]) > MAX_HISTORY_LENGTH * 2:
            CHAT_HISTORY[user_id] = CHAT_HISTORY[user_id][-MAX_HISTORY_LENGTH * 2:]

        # Сохраняем ответ бота
        await save_message(user_id, response, is_bot=True)

        await send_long_message(message.chat.id, response)

    except Exception as e:
        logger.error(f"Ошибка обработки голосового сообщения: {e}")
        await message.answer("Произошла ошибка при обработке голосового сообщения.")



# В функции on_startup добавляем загрузку проактивных сообщений
async def on_startup(_):
    """Действия при запуске бота"""
    logger.info("🟢 Бот запускается...")

    # Инициализация OpenAI
    openai.api_key = OPENAI_API_KEY
    logger.info("OpenAI инициализирован")

    # Инициализация базы данных
    await init_db()

    # Загружаем базу знаний
    await build_knowledge_index()
    
    # Проверка загрузки базы знаний
    if KNOWLEDGE_INDEX and KNOWLEDGE_CHUNKS:
        logger.info(f"База знаний успешно загружена: {len(KNOWLEDGE_CHUNKS)} фрагментов")
    else:
        logger.warning("База знаний не загружена или пуста")

    # Загружаем проактивные сообщения
    await load_proactive_messages()

    # Запускаем планировщик
    scheduler.start()
    logger.info("Планировщик запущен")

    logger.info("✅ Бот готов к работе!")

# --------------------
# Функции для запуска и остановки
# --------------------
async def on_shutdown(_):
    """Действия при остановке бота"""
    logger.info("🔴 Бот останавливается...")
    await bot.close()


def main():
    """Основная функция для запуска бота"""
    # Создаем папку базы знаний, если её нет
    ensure_docs_dir()

    # Запускаем бота
    executor.start_polling(
        dp,
        skip_updates=True,
        on_startup=on_startup,
        on_shutdown=on_shutdown
    )


if __name__ == "__main__":
    # Запускаем бота
    main()
