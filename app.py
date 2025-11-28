import asyncio
import json
import logging
import os
import tempfile
from datetime import datetime
from pathlib import Path
from typing import List, Optional
from aiofiles import open as async_open

from dotenv import load_dotenv
import openai
from aiodav import Client as WebDavClient
from aiogram import Bot, Dispatcher
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.types import (
    Message,
    ContentType,
    FSInputFile,
)

# Настройка логирования
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

load_dotenv()

TELEGRAM_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
WEBDAV_URL = os.getenv("WEBDAV_URL")
WEBDAV_USERNAME = os.getenv("WEBDAV_USERNAME")
WEBDAV_PASSWORD = os.getenv("WEBDAV_PASSWORD")
WEBDAV_ROOT = os.getenv("WEBDAV_ROOT", "/notes")
NOTES_PASSWORD = os.getenv("NOTES_PASSWORD", "changeme")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

if not TELEGRAM_TOKEN:
    logger.error("TELEGRAM_BOT_TOKEN не задан в .env")
    raise SystemExit(1)

if not WEBDAV_URL or not WEBDAV_USERNAME or not WEBDAV_PASSWORD:
    logger.error("WEBDAV параметры не заданы в .env")
    raise SystemExit(1)

if not OPENAI_API_KEY:
    logger.warning("OPENAI_API_KEY не найден, распознавание аудио будет недоступно")

openai.api_key = OPENAI_API_KEY

USERS_FILE = Path("allowed_users.json")
USERS_FILE.parent.mkdir(parents=True, exist_ok=True)
if not USERS_FILE.exists():
    USERS_FILE.write_text(json.dumps({"allowed": []}, ensure_ascii=False))

class AuthStates(StatesGroup):
    """FSM-состояния для авторизации новых пользователей."""
    waiting_password = State()

async def load_allowed_users() -> List[int]:
    """Загрузить список разрешённых пользователей из файла."""
    data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    return data.get("allowed", [])

async def add_allowed_user(user_id: int) -> None:
    """Добавить пользователя в список разрешённых и сохранить на диск."""
    data = json.loads(USERS_FILE.read_text(encoding="utf-8"))
    allowed = set(data.get("allowed", []))
    allowed.add(user_id)
    data["allowed"] = list(allowed)
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def webdav_client() -> WebDavClient:
    """Создать и вернуть клиента для WebDAV (aiodav.Client)."""
    return WebDavClient(
        WEBDAV_URL,
        login=WEBDAV_USERNAME,
        password=WEBDAV_PASSWORD
    )

async def ensure_folder_exists(client: WebDavClient, path: str) -> None:
    """Убедиться, что папка path существует на WebDAV. Создать при необходимости."""
    exists = await client.exists(path)
    if not exists:
        await client.create_directory(path)

async def upload_file_to_webdav(client: WebDavClient, remote_path: str, local_path: str) -> None:
    """Загрузить локальный файл local_path в WebDAV по пути remote_path (перезаписывает)."""
    await client.upload(remote_path, local_path) 

async def save_note_and_files(
    client: WebDavClient,
    user_id: int,
    note_md: str,
    data_files: List[Path],
) -> str:
    """Сохранить заметку note_md и сопутствующие файлы data_files в папке по текущей дате на WebDAV.
    Возвращает полный удалённый путь к сохранённой заметке.
    """
    date_str = datetime.utcnow().strftime("%Y-%m-%d")
    remote_date_folder = f"{WEBDAV_ROOT.rstrip('/')}/{date_str}"
    remote_data_folder = f"{remote_date_folder}/data"
    await ensure_folder_exists(client, remote_date_folder)
    await ensure_folder_exists(client, remote_data_folder)

    ts = datetime.now().strftime("%H%M%S")
    note_filename = f"note_{ts}.md"
    remote_note_path = f"{remote_date_folder}/{note_filename}"

    # Загрузить файлы в data под уникальными именами и заменить локальные ссылки в markdown при необходимости
    for local in data_files:
        remote_name = f"{local.name}"
        remote_file_path = f"{remote_data_folder}/{remote_name}"
        await client.upload(remote_file_path, local)

    local_file_path = Path(tempfile.mkdtemp(prefix="tgmsg_") + note_filename)
    async with async_open(local_file_path, "wb") as file:
        await file.write(note_md.encode("utf-8"))

    await client.upload(remote_note_path, local_file_path)
    return remote_note_path

async def transcribe_audio(local_path: str) -> Optional[str]:
    """Вызвать OpenAI API для распознавания аудио. Возвращает распознанный текст или None."""
    if not OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY не задан — распознавание недоступно")
        return None

    #FIXME async workaround, should be AsyncOpenAI
    try:
        with open(local_path, "rb") as audio_file:
            client = openai.OpenAI(
                    api_key=OPENAI_API_KEY,
                    base_url="https://api.proxyapi.ru/openai/v1",
                )
            transcription = client.audio.transcriptions.create(
                model="gpt-4o-mini-transcribe", 
                file=audio_file
            )
            return transcription.text
    except Exception as e:
        logger.exception("Ошибка при распознавании аудио: %s", e)
        return None

async def download_telegram_file(bot: Bot, file_id: str, dest_path: Path) -> None:
    """Скачать файл из Telegram (по file_id) в указанный локальный путь."""
    file = await bot.get_file(file_id)
    await bot.download_file(file.file_path, destination=dest_path)

async def handle_media_and_build_markdown(bot: Bot, message: Message) -> (str, List[Path]):
    """Обработать мультимедиа из сообщения: скачать файлы локально, собрать markdown с ссылками.
    Возвращает (markdown_content, list_of_local_files) где local files — пути к скачанным медиа.
    """
    inline_links = []
    saved_files: List[Path] = []
    base_tmp_dir = Path(tempfile.mkdtemp(prefix="tgmsg_"))

    # Текст — будет добавлен в конце как обычный текст заметки
    text_body = message.text or message.caption or ""

    # Фото
    if message.photo:
        photo = message.photo[-1]
        local = base_tmp_dir / f"{photo.file_id}.jpg"
        await download_telegram_file(bot, photo.file_id, str(local))
        saved_files.append(local)
        inline_links.append(f"![](/data/{local.name})")

    # Документы (включая видео, документы любого рода)
    if message.document:
        doc = message.document
        ext = Path(doc.file_name or f"{doc.file_id}").suffix or ""
        local = base_tmp_dir / f"{doc.file_id}{ext}"
        await download_telegram_file(bot, doc.file_id, str(local))
        saved_files.append(local)
        inline_links.append(f"[{doc.file_name or local.name}](/data/{local.name})")

    # Видео
    if message.video:
        vid = message.video
        local = base_tmp_dir / f"{vid.file_id}.mp4"
        await download_telegram_file(bot, vid.file_id, str(local))
        saved_files.append(local)
        inline_links.append(f"[Видео](/data/{local.name})")

    # Аудио (mp3, ogg и т.п.)
    audio_text = None
    audio_local_path = None
    if message.audio:
        aud = message.audio
        ext = Path(aud.file_name or f"{aud.file_id}").suffix or ".mp3"
        local = base_tmp_dir / f"{aud.file_id}{ext}"
        await download_telegram_file(bot, aud.file_id, str(local))
        saved_files.append(local)
        inline_links.append(f"[Аудио](/data/{local.name})")
        audio_local_path = str(local)

    # Voice (voice messages)
    if message.voice:
        voice = message.voice
        local = base_tmp_dir / f"{voice.file_id}.ogg"
        await download_telegram_file(bot, voice.file_id, str(local))
        saved_files.append(local)
        inline_links.append(f"[Voice](/data/{local.name})")
        audio_local_path = str(local)

    # Если есть аудио — попытка распознать
    if audio_local_path:
        recognized = await transcribe_audio(audio_local_path)
        if recognized:
            audio_text = recognized

    # Построим markdown: сначала ссылки на файлы, затем распознанный текст (если есть), затем оригинальный текст
    md_parts: List[str] = []
    if inline_links:
        md_parts.append("\n".join(inline_links))
        md_parts.append("\n---\n")
    if audio_text:
        md_parts.append("**Распознанное аудио:**\n\n")
        md_parts.append(audio_text)
        md_parts.append("\n---\n")
    if text_body:
        md_parts.append(text_body)

    md_content = "\n\n".join(part for part in md_parts if part is not None)
    if not md_content.strip():
        md_content = "_(пустое сообщение)_"

    return md_content, saved_files

# Инициализация бота и диспетчера
bot = Bot(token=TELEGRAM_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

@dp.message(CommandStart())
async def cmd_start(message: Message, state: FSMContext):
    """Обработчик /start — требует пароль для новых пользователей."""
    allowed = await load_allowed_users()
    if message.from_user and message.from_user.id in allowed:
        await message.reply("Вы уже авторизованы. Пришлите сообщение — я сохраню его в WebDAV.")
        return
    await message.reply("Здравствуйте! Введите пароль для доступа к сохранению заметок:")
    await state.set_state(AuthStates.waiting_password)

@dp.message(AuthStates.waiting_password)
async def process_password(message: Message, state: FSMContext):
    """Обработчик введённого пароля — если правильный, добавляет пользователя в allowlist."""
    text = (message.text or "").strip()
    if text == NOTES_PASSWORD:
        uid = message.from_user.id if message.from_user else None
        if uid:
            await add_allowed_user(uid)
            await message.reply("Пароль верный. Доступ предоставлен — можете отправлять сообщения.")
            await state.clear()
            return
    await message.reply("Неверный пароль. Попробуйте ещё раз или отправьте /start для начала.")
    # состояние остаётся — пользователь сможет попытаться снова

@dp.message()
async def handle_all_messages(message: Message):
    """Главный обработчик сообщений — сохраняет текст/медиа в WebDAV (после проверки прав)."""
    uid = message.from_user.id if message.from_user else None
    if not uid:
        await message.reply("Не удалось определить пользователя.")
        return

    allowed = await load_allowed_users()
    if uid not in allowed:
        await message.reply("Вы не авторизованы. Отправьте /start и введите пароль.")
        return

    logger.info("Получено сообщение от %s (id=%s), тип=%s", message.from_user.username, uid, message.content_type)

    client = webdav_client()
    try:
        md, local_files = await handle_media_and_build_markdown(bot, message)
        remote_note_path = await save_note_and_files(client, uid, md, local_files)
        await message.reply(f"Сохранено: {remote_note_path}")
        logger.info("Сохранено сообщение пользователя %s -> %s", uid, remote_note_path)
    except Exception as e:
        logger.exception("Ошибка при сохранении сообщения пользователя %s: %s", uid, e)
        await message.reply("Произошла ошибка при сохранении. Подробности в логах.")

async def main():
    """Запуск бота — регистрирует обработчики и стартует polling."""
    try:
        logger.info("Запуск бота...")
        await dp.start_polling(bot)
    finally:
        await bot.session.close()

if __name__ == "__main__":
    asyncio.run(main())
