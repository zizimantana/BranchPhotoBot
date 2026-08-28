import asyncio
import hashlib
import hmac
import html
import json
import os
import secrets
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import parse_qsl

from aiohttp import web
from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    Message,
    ReplyKeyboardMarkup,
    KeyboardButton,
    FSInputFile,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    WebAppInfo,
)
from dotenv import load_dotenv
from PIL import Image, ImageDraw, ImageFont


# =========================================================
# SETTINGS
# =========================================================

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
ADMIN_ID_RAW = os.getenv("ADMIN_ID")

WEBAPP_URL = os.getenv(
    "WEBAPP_URL",
    "https://branchphotobot.onrender.com",
)

if not BOT_TOKEN:
    raise RuntimeError(
        "BOT_TOKEN .env faylida topilmadi"
    )

if not ADMIN_ID_RAW:
    raise RuntimeError(
        "ADMIN_ID .env faylida topilmadi"
    )

try:
    ADMIN_ID = int(ADMIN_ID_RAW)
except ValueError:
    raise RuntimeError(
        "ADMIN_ID raqam bo'lishi kerak"
    )


# =========================================================
# PATHS
# =========================================================

BASE_DIR = Path(__file__).resolve().parent

CAMERA_APP_DIR = BASE_DIR / "camera_app"
PHOTOS_DIR = BASE_DIR / "photos"

DB_FILE = BASE_DIR / "database.db"

CAMERA_APP_DIR.mkdir(
    parents=True,
    exist_ok=True
)

PHOTOS_DIR.mkdir(
    parents=True,
    exist_ok=True
)


# =========================================================
# SERVER
# =========================================================

HOST = "0.0.0.0"

PORT = int(
    os.getenv(
        "PORT",
        os.getenv(
            "WEB_PORT",
            "8080"
        )
    )
)


# =========================================================
# DATABASE
# =========================================================

db = sqlite3.connect(
    DB_FILE,
    check_same_thread=False
)

db.row_factory = sqlite3.Row


db.execute(
    """
    CREATE TABLE IF NOT EXISTS branches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL UNIQUE,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """
)


db.execute(
    """
    CREATE TABLE IF NOT EXISTS employees (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE NOT NULL,
        name TEXT NOT NULL,
        branch_id INTEGER NOT NULL,
        active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL
    )
    """
)


db.execute(
    """
    CREATE TABLE IF NOT EXISTS photos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER NOT NULL,
        employee_name TEXT NOT NULL,
        branch_id INTEGER NOT NULL,
        branch_name TEXT NOT NULL,
        image_path TEXT NOT NULL,
        comment TEXT,
        device_time TEXT NOT NULL,
        created_at TEXT NOT NULL
    )
    """
)


db.commit()


# =========================================================
# BOT
# =========================================================

bot = Bot(
    token=BOT_TOKEN
)

dp = Dispatcher()


# =========================================================
# STATES
# =========================================================

class AdminStates(StatesGroup):

    waiting_branch_name = State()

    waiting_employee_telegram_id = State()

    waiting_employee_name = State()

    waiting_employee_branch = State()


# =========================================================
# TIME
# =========================================================

def now_local():
    return datetime.now().astimezone()


def iso_now():
    return now_local().isoformat()


# =========================================================
# ADMIN
# =========================================================

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID


# =========================================================
# EMPLOYEE
# =========================================================

def get_employee(telegram_id: int):

    return db.execute(
        """
        SELECT
            e.*,
            b.name AS branch_name,
            b.active AS branch_active
        FROM employees e
        JOIN branches b
            ON b.id = e.branch_id
        WHERE e.telegram_id = ?
        """,
        (telegram_id,)
    ).fetchone()


# =========================================================
# BRANCH
# =========================================================

def get_branch(branch_id: int):

    return db.execute(
        """
        SELECT *
        FROM branches
        WHERE id = ?
        """,
        (branch_id,)
    ).fetchone()


# =========================================================
# FONT
# =========================================================

def get_font(size: int):

    possible_fonts = [
        "C:/Windows/Fonts/arial.ttf",
        "C:/Windows/Fonts/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for font_path in possible_fonts:

        try:
            return ImageFont.truetype(
                font_path,
                size
            )

        except Exception:
            pass

    return ImageFont.load_default()


# =========================================================
# WATERMARK
# =========================================================

def make_watermarked_image(
    source_path: Path,
    destination_path: Path,
    branch_name: str,
    capture_dt: datetime,
):

    with Image.open(source_path) as source:

        source.load()

        image = source.convert("RGB")


    width, height = image.size


    font_size = max(
        24,
        min(
            60,
            width // 35
        )
    )


    font = get_font(
        font_size
    )


    padding = max(
        20,
        width // 50
    )


    line_height = int(
        font_size * 1.5
    )


    lines = [
        f"Branch: {branch_name}",
        f"Date: {capture_dt.strftime('%d.%m.%Y')}",
        f"Time: {capture_dt.strftime('%H:%M:%S')}",
    ]


    box_height = (
        padding * 2
        +
        line_height * len(lines)
    )


    overlay = Image.new(
        "RGBA",
        (
            width,
            box_height
        ),
        (
            0,
            0,
            0,
            175
        )
    )


    draw = ImageDraw.Draw(
        overlay
    )


    y = padding


    for line in lines:

        draw.text(
            (
                padding,
                y
            ),
            line,
            font=font,
            fill=(
                255,
                255,
                255,
                255
            )
        )

        y += line_height


    image.paste(
        overlay,
        (
            0,
            max(
                0,
                height - box_height
            )
        ),
        overlay
    )


    image.save(
        destination_path,
        "JPEG",
        quality=92,
        optimize=True
    )


# =========================================================
# TELEGRAM WEB APP VALIDATION
# =========================================================

def telegram_webapp_valid(
    init_data: str
) -> bool:

    if not init_data:
        return False


    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )


        received_hash = pairs.pop(
            "hash",
            None
        )


        if not received_hash:
            return False


        auth_date = int(
            pairs.get(
                "auth_date",
                "0"
            )
        )


        current = int(
            datetime.now(
                timezone.utc
            ).timestamp()
        )


        # 24 soatdan eski initData qabul qilinmaydi
        if (
            auth_date <= 0
            or
            abs(current - auth_date) > 86400
        ):
            return False


        data_check_string = "\n".join(
            f"{key}={value}"
            for key, value
            in sorted(pairs.items())
        )


        secret_key = hmac.new(
            b"WebAppData",
            BOT_TOKEN.encode(),
            hashlib.sha256
        ).digest()


        calculated = hmac.new(
            secret_key,
            data_check_string.encode(),
            hashlib.sha256
        ).hexdigest()


        return hmac.compare_digest(
            calculated,
            received_hash
        )


    except Exception as error:

        print(
            "WEBAPP VALIDATION ERROR:",
            type(error).__name__,
            str(error)
        )

        return False


# =========================================================
# WEB APP USER
# =========================================================

def webapp_user(
    init_data: str
):

    if not telegram_webapp_valid(
        init_data
    ):
        return None


    try:

        pairs = dict(
            parse_qsl(
                init_data,
                keep_blank_values=True
            )
        )


        user_json = pairs.get(
            "user"
        )


        if not user_json:
            return None


        user = json.loads(
            user_json
        )


        user_id = int(
            user["id"]
        )


        return user


    except Exception as error:

        print(
            "WEBAPP USER ERROR:",
            type(error).__name__,
            str(error)
        )

        return None


# =========================================================
# JSON RESPONSE
# =========================================================

def json_response(
    data,
    status=200
):

    return web.json_response(
        data,
        status=status
    )


# =========================================================
# ADMIN KEYBOARD
# =========================================================

def admin_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗ ╥Ы╤Ю╤И╨╕╤И"
                ),
                KeyboardButton(
                    text="ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗╨╗╨░╤А"
                ),
            ],
            [
                KeyboardButton(
                    text="ЁЯСд ╨е╨╛╨┤╨╕╨╝ ╥Ы╤Ю╤И╨╕╤И"
                ),
                KeyboardButton(
                    text="ЁЯСе ╨е╨╛╨┤╨╕╨╝╨╗╨░╤А"
                ),
            ],
            [
                KeyboardButton(
                    text="ЁЯУ╕ ╨а╨░╤Б╨╝╨╗╨░╤А"
                ),
            ],
        ],
        resize_keyboard=True
    )


# =========================================================
# EMPLOYEE KEYBOARD
# =========================================================

def employee_keyboard():

    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text="ЁЯУ╕ ╨а╨Р╨б╨Ь╨У╨Р ╨Ю╨Ы╨Ш╨и",
                    web_app=WebAppInfo(
                        url=WEBAPP_URL
                    )
                ),
            ],
            [
                KeyboardButton(
                    text="ЁЯУВ ╨Ь╨╡╨╜╨╕╨╜╨│ ╤А╨░╤Б╨╝╨╗╨░╤А╨╕╨╝"
                ),
                KeyboardButton(
                    text="ЁЯСд ╨Я╤А╨╛╤Д╨╕╨╗╤М"
                ),
            ],
        ],
        resize_keyboard=True
    )


# =========================================================
# START
# =========================================================

@dp.message(
    Command("start")
)
async def start_handler(
    message: Message,
    state: FSMContext
):

    await state.clear()


    user_id = message.from_user.id


    if is_admin(user_id):

        await message.answer(
            "ЁЯСС <b>ADMIN PANEL</b>\n\n"
            "Branch Photo Control'╨│╨░ ╤Е╤Г╤И ╨║╨╡╨╗╨┤╨╕╨╜╨│╨╕╨╖.",
            reply_markup=admin_keyboard(),
            parse_mode="HTML"
        )

        return


    employee = get_employee(
        user_id
    )


    if not employee:

        await message.answer(
            "тЭМ ╨б╨╕╨╖╨╜╨╕╨╜╨│ ╨░╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ "
            "╥│╨░╨╗╨╕ ╤Д╨╕╨╗╨╕╨░╨╗╨│╨░ ╨▒╨╕╤А╨╕╨║╤В╨╕╤А╨╕╨╗╨╝╨░╨│╨░╨╜.\n\n"
            "╨Р╨┤╨╝╨╕╨╜╨╕╤Б╤В╤А╨░╤В╨╛╤А ╨▒╨╕╨╗╨░╨╜ ╨▒╨╛╥У╨╗╨░╨╜╨╕╨╜╨│."
        )

        return


    if not employee["active"]:

        await message.answer(
            "тЭМ ╨б╨╕╨╖╨╜╨╕╨╜╨│ ╨░╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б."
        )

        return


    if not employee["branch_active"]:

        await message.answer(
            f"тЭМ <b>{html.escape(employee['branch_name'])}</b> "
            "╤Д╨╕╨╗╨╕╨░╨╗╨╕ ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б.",
            parse_mode="HTML"
        )

        return


    current = now_local()


    await message.answer(
        f"ЁЯСЛ ╨б╨░╨╗╨╛╨╝, "
        f"<b>{html.escape(employee['name'])}</b>!\n\n"
        f"ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗: "
        f"<b>{html.escape(employee['branch_name'])}</b>\n"
        f"ЁЯУЕ {current.strftime('%d.%m.%Y')}\n"
        f"ЁЯХР {current.strftime('%H:%M:%S')}\n\n"
        "ЁЯУ╕ ╨а╨░╤Б╨╝ ╨╛╨╗╨╕╤И ╤Г╤З╤Г╨╜ ╤В╤Г╨│╨╝╨░╨╜╨╕ ╨▒╨╛╤Б╨╕╨╜╨│.",
        reply_markup=employee_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# MY ID
# =========================================================

@dp.message(
    Command("myid")
)
async def myid_handler(
    message: Message
):

    await message.answer(
        "ЁЯЖФ ╨б╨╕╨╖╨╜╨╕╨╜╨│ Telegram ID:\n\n"
        f"<code>{message.from_user.id}</code>",
        parse_mode="HTML"
    )


# =========================================================
# ADMIN - ADD BRANCH
# =========================================================

@dp.message(
    F.text == "ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗ ╥Ы╤Ю╤И╨╕╤И"
)
async def add_branch_start(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    await state.set_state(
        AdminStates.waiting_branch_name
    )


    await message.answer(
        "ЁЯПв ╨п╨╜╨│╨╕ ╤Д╨╕╨╗╨╕╨░╨╗ ╨╜╨╛╨╝╨╕╨╜╨╕ ╤С╨╖╨╕╨╜╨│.\n\n"
        "╨Ь╨░╤Б╨░╨╗╨░╨╜:\n"
        "╨д╨░╤А╥У╨╛╨╜╨░ ╨Ь╨░╤А╨║╨░╨╖"
    )


# =========================================================
# ADD BRANCH FINISH
# =========================================================

@dp.message(
    AdminStates.waiting_branch_name
)
async def add_branch_finish(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    name = (
        message.text or ""
    ).strip()


    if len(name) < 2:

        await message.answer(
            "тЭМ ╨д╨╕╨╗╨╕╨░╨╗ ╨╜╨╛╨╝╨╕ ╨╢╤Г╨┤╨░ ╥Ы╨╕╤Б╥Ы╨░."
        )

        return


    try:

        db.execute(
            """
            INSERT INTO branches
            (
                name,
                active,
                created_at
            )
            VALUES (?, 1, ?)
            """,
            (
                name,
                iso_now()
            )
        )


        db.commit()


    except sqlite3.IntegrityError:

        await state.clear()

        await message.answer(
            "тЭМ ╨С╤Г ╤Д╨╕╨╗╨╕╨░╨╗ ╨░╨╗╨╗╨░╥Ы╨░╤З╨╛╨╜ ╨╝╨░╨▓╨╢╤Г╨┤.",
            reply_markup=admin_keyboard()
        )

        return


    await state.clear()


    await message.answer(
        f"тЬЕ <b>╨д╨╕╨╗╨╕╨░╨╗ ╥Ы╤Ю╤И╨╕╨╗╨┤╨╕!</b>\n\n"
        f"ЁЯПв {html.escape(name)}",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# BRANCHES
# =========================================================

@dp.message(
    F.text == "ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗╨╗╨░╤А"
)
async def branches_handler(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return


    branches = db.execute(
        """
        SELECT *
        FROM branches
        ORDER BY id DESC
        """
    ).fetchall()


    if not branches:

        await message.answer(
            "тЭМ ╥▓╨░╨╗╨╕ ╤Д╨╕╨╗╨╕╨░╨╗╨╗╨░╤А ╥Ы╤Ю╤И╨╕╨╗╨╝╨░╨│╨░╨╜."
        )

        return


    buttons = []


    for branch in branches:

        status = (
            "ЁЯЯв"
            if branch["active"]
            else
            "ЁЯФ┤"
        )


        buttons.append(
            [
                InlineKeyboardButton(
                    text=(
                        f"{status} "
                        f"{branch['name']}"
                    ),
                    callback_data=(
                        f"branch:{branch['id']}"
                    )
                )
            ]
        )


    await message.answer(
        "ЁЯПв <b>╨д╨╕╨╗╨╕╨░╨╗╨╗╨░╤А</b>\n\n"
        "╨Ъ╨╡╤А╨░╨║╨╗╨╕ ╤Д╨╕╨╗╨╕╨░╨╗╨╜╨╕ ╤В╨░╨╜╨╗╨░╨╜╨│:",
        reply_markup=InlineKeyboardMarkup(
            inline_keyboard=buttons
        ),
        parse_mode="HTML"
    )


# =========================================================
# BRANCH SELECT
# =========================================================

@dp.callback_query(
    F.data.startswith("branch:")
)
async def branch_selected(
    callback
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "╨а╤Г╤Е╤Б╨░╤В ╨╣╤Ю╥Ы."
        )

        return


    branch_id = int(
        callback.data.split(":")[1]
    )


    branch = get_branch(
        branch_id
    )


    if not branch:

        await callback.answer(
            "╨д╨╕╨╗╨╕╨░╨╗ ╤В╨╛╨┐╨╕╨╗╨╝╨░╨┤╨╕."
        )

        return


    employees_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM employees
        WHERE branch_id = ?
        """,
        (branch_id,)
    ).fetchone()["count"]


    photos_count = db.execute(
        """
        SELECT COUNT(*) AS count
        FROM photos
        WHERE branch_id = ?
        """,
        (branch_id,)
    ).fetchone()["count"]


    status = (
        "ЁЯЯв ╨д╨░╨╛╨╗"
        if branch["active"]
        else
        "ЁЯФ┤ ╨Э╨╛╤Д╨░╨╛╨╗"
    )


    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ЁЯУ╕ ╨а╨░╤Б╨╝╨╗╨░╤А╨╜╨╕ ╨║╤Ю╤А╨╕╤И",
                    callback_data=(
                        f"photos_branch:{branch_id}"
                    )
                )
            ],
            [
                InlineKeyboardButton(
                    text="ЁЯСе ╨е╨╛╨┤╨╕╨╝╨╗╨░╤А",
                    callback_data=(
                        f"employees_branch:{branch_id}"
                    )
                )
            ],
        ]
    )


    await callback.message.answer(
        f"ЁЯПв <b>{html.escape(branch['name'])}</b>\n\n"
        f"╥▓╨╛╨╗╨░╤В╨╕: {status}\n"
        f"ЁЯСе ╨е╨╛╨┤╨╕╨╝╨╗╨░╤А: {employees_count}\n"
        f"ЁЯУ╕ ╨а╨░╤Б╨╝╨╗╨░╤А: {photos_count}",
        reply_markup=keyboard,
        parse_mode="HTML"
    )


    await callback.answer()


# =========================================================
# BRANCH PHOTOS
# =========================================================

@dp.callback_query(
    F.data.startswith("photos_branch:")
)
async def branch_photos(
    callback
):

    if not is_admin(
        callback.from_user.id
    ):

        await callback.answer(
            "╨а╤Г╤Е╤Б╨░╤В ╨╣╤Ю╥Ы."
        )

        return


    branch_id = int(
        callback.data.split(":")[1]
    )


    branch = get_branch(
        branch_id
    )


    if not branch:

        await callback.answer(
            "╨д╨╕╨╗╨╕╨░╨╗ ╤В╨╛╨┐╨╕╨╗╨╝╨░╨┤╨╕."
        )

        return


    rows = db.execute(
        """
        SELECT *
        FROM photos
        WHERE branch_id = ?
        ORDER BY id DESC
        LIMIT 30
        """,
        (branch_id,)
    ).fetchall()


    if not rows:

        await callback.message.answer(
            f"ЁЯУВ <b>{html.escape(branch['name'])}</b>\n\n"
            "╥▓╨░╨╗╨╕ ╨▒╤Г ╤Д╨╕╨╗╨╕╨░╨╗╨┤╨░╨╜ ╤А╨░╤Б╨╝ ╨║╨╡╨╗╨╝╨░╨│╨░╨╜.",
            parse_mode="HTML"
        )

        await callback.answer()

        return


    await callback.message.answer(
        f"ЁЯУ╕ <b>{html.escape(branch['name'])}</b>\n\n"
        f"╨Ц╨░╨╝╨╕: {len(rows)}",
        parse_mode="HTML"
    )


    for row in rows:

        image_path = Path(
            row["image_path"]
        )


        if not image_path.exists():
            continue


        try:

            dt = datetime.fromisoformat(
                row["device_time"]
            )

        except Exception:

            dt = now_local()


        caption = (
            f"ЁЯПв <b>{html.escape(row['branch_name'])}</b>\n"
            f"ЁЯСд ╨е╨╛╨┤╨╕╨╝: <b>{html.escape(row['employee_name'])}</b>\n"
            f"ЁЯУЕ {dt.strftime('%d.%m.%Y')}\n"
            f"ЁЯХР {dt.strftime('%H:%M:%S')}\n"
            f"ЁЯУЭ ╨Ш╨╖╨╛╥│: "
            f"{html.escape(row['comment'] or '╨Щ╤Ю╥Ы')}"
        )


        try:

            await bot.send_photo(
                chat_id=callback.from_user.id,
                photo=FSInputFile(
                    image_path
                ),
                caption=caption,
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "BRANCH PHOTO SEND ERROR:",
                type(error).__name__,
                error
            )


    await callback.answer()


# =========================================================
# ADMIN - ADD EMPLOYEE
# =========================================================

@dp.message(
    F.text == "ЁЯСд ╨е╨╛╨┤╨╕╨╝ ╥Ы╤Ю╤И╨╕╤И"
)
async def add_employee_start(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    branches = db.execute(
        """
        SELECT *
        FROM branches
        WHERE active = 1
        ORDER BY name
        """
    ).fetchall()


    if not branches:

        await message.answer(
            "тЭМ ╨Р╨▓╨▓╨░╨╗ ╨║╨░╨╝╨╕╨┤╨░ ╨▒╨╕╤В╤В╨░ "
            "╤Д╨░╨╛╨╗ ╤Д╨╕╨╗╨╕╨░╨╗ ╥Ы╤Ю╤И╨╕╨╜╨│."
        )

        return


    await state.set_state(
        AdminStates.waiting_employee_telegram_id
    )


    await message.answer(
        "ЁЯСд ╨е╨╛╨┤╨╕╨╝╨╜╨╕╨╜╨│ Telegram ID "
        "╤А╨░╥Ы╨░╨╝╨╕╨╜╨╕ ╤О╨▒╨╛╤А╨╕╨╜╨│.\n\n"
        "╨е╨╛╨┤╨╕╨╝ ╤Ю╨╖ Telegram'╨╕╨┤╨░ "
        "/myid ╨║╨╛╨╝╨░╨╜╨┤╨░╤Б╨╕╨╜╨╕ ╤О╨▒╨╛╤А╨╕╨▒,\n"
        "╤З╨╕╥Ы╥Ы╨░╨╜ ╤А╨░╥Ы╨░╨╝╨╜╨╕ ╤Б╨╕╨╖╨│╨░ ╨▒╨╡╤А╨╕╤И╨╕ ╨╝╤Г╨╝╨║╨╕╨╜."
    )


# =========================================================
# EMPLOYEE ID
# =========================================================

@dp.message(
    AdminStates.waiting_employee_telegram_id
)
async def employee_id_received(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    try:

        telegram_id = int(
            (message.text or "").strip()
        )

    except ValueError:

        await message.answer(
            "тЭМ Telegram ID ╤А╨░╥Ы╨░╨╝ "
            "╨▒╤Ю╨╗╨╕╤И╨╕ ╨║╨╡╤А╨░╨║."
        )

        return


    await state.update_data(
        employee_telegram_id=telegram_id
    )


    await state.set_state(
        AdminStates.waiting_employee_name
    )


    await message.answer(
        "ЁЯСд ╨е╨╛╨┤╨╕╨╝╨╜╨╕╨╜╨│ ╨╕╤Б╨╝╨╕╨╜╨╕ ╤С╨╖╨╕╨╜╨│."
    )


# =========================================================
# EMPLOYEE NAME
# =========================================================

@dp.message(
    AdminStates.waiting_employee_name
)
async def employee_name_received(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    name = (
        message.text or ""
    ).strip()


    if len(name) < 2:

        await message.answer(
            "тЭМ ╨Ш╤Б╨╝ ╨╢╤Г╨┤╨░ ╥Ы╨╕╤Б╥Ы╨░."
        )

        return


    await state.update_data(
        employee_name=name
    )


    branches = db.execute(
        """
        SELECT *
        FROM branches
        WHERE active = 1
        ORDER BY id
        """
    ).fetchall()


    text = (
        "ЁЯПв ╨е╨╛╨┤╨╕╨╝╨╜╨╕ ╥Ы╨░╨╣╤Б╨╕ ╤Д╨╕╨╗╨╕╨░╨╗╨│╨░ "
        "╨▒╨╕╤А╨╕╨║╤В╨╕╤А╨░╨╝╨╕╨╖?\n\n"
    )


    for branch in branches:

        text += (
            f"<b>{branch['id']}</b> тАФ "
            f"{html.escape(branch['name'])}\n"
        )


    await state.set_state(
        AdminStates.waiting_employee_branch
    )


    await message.answer(
        text +
        "\n╨д╨╕╨╗╨╕╨░╨╗ ID ╤А╨░╥Ы╨░╨╝╨╕╨╜╨╕ ╤О╨▒╨╛╤А╨╕╨╜╨│.",
        parse_mode="HTML"
    )


# =========================================================
# EMPLOYEE BRANCH
# =========================================================

@dp.message(
    AdminStates.waiting_employee_branch
)
async def employee_branch_received(
    message: Message,
    state: FSMContext
):

    if not is_admin(
        message.from_user.id
    ):
        return


    try:

        branch_id = int(
            (message.text or "").strip()
        )

    except ValueError:

        await message.answer(
            "тЭМ ╨д╨╕╨╗╨╕╨░╨╗ ID ╤А╨░╥Ы╨░╨╝ "
            "╨▒╤Ю╨╗╨╕╤И╨╕ ╨║╨╡╤А╨░╨║."
        )

        return


    branch = get_branch(
        branch_id
    )


    if (
        not branch
        or
        not branch["active"]
    ):

        await message.answer(
            "тЭМ ╨С╤Г╨╜╨┤╨░╨╣ ╤Д╨░╨╛╨╗ ╤Д╨╕╨╗╨╕╨░╨╗ "
            "╤В╨╛╨┐╨╕╨╗╨╝╨░╨┤╨╕."
        )

        return


    data = await state.get_data()


    telegram_id = data[
        "employee_telegram_id"
    ]

    employee_name = data[
        "employee_name"
    ]


    try:

        db.execute(
            """
            INSERT INTO employees
            (
                telegram_id,
                name,
                branch_id,
                active,
                created_at
            )
            VALUES (?, ?, ?, 1, ?)
            """,
            (
                telegram_id,
                employee_name,
                branch_id,
                iso_now()
            )
        )


        db.commit()


    except sqlite3.IntegrityError:

        await state.clear()


        await message.answer(
            "тЭМ ╨С╤Г Telegram ╨░╨║╨║╨░╤Г╨╜╤В "
            "╨░╨╗╨╗╨░╥Ы╨░╤З╨╛╨╜ ╤Е╨╛╨┤╨╕╨╝ ╤Б╨╕╤Д╨░╤В╨╕╨┤╨░ ╥Ы╤Ю╤И╨╕╨╗╨│╨░╨╜.",
            reply_markup=admin_keyboard()
        )

        return


    await state.clear()


    await message.answer(
        "тЬЕ <b>╨е╨╛╨┤╨╕╨╝ ╥Ы╤Ю╤И╨╕╨╗╨┤╨╕!</b>\n\n"
        f"ЁЯСд {html.escape(employee_name)}\n"
        f"ЁЯПв {html.escape(branch['name'])}\n"
        f"ЁЯЖФ {telegram_id}",
        reply_markup=admin_keyboard(),
        parse_mode="HTML"
    )


# =========================================================
# EMPLOYEES
# =========================================================

@dp.message(
    F.text == "ЁЯСе ╨е╨╛╨┤╨╕╨╝╨╗╨░╤А"
)
async def employees_handler(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return


    rows = db.execute(
        """
        SELECT
            e.*,
            b.name AS branch_name
        FROM employees e
        JOIN branches b
            ON b.id = e.branch_id
        ORDER BY e.id DESC
        """
    ).fetchall()


    if not rows:

        await message.answer(
            "тЭМ ╥▓╨░╨╗╨╕ ╤Е╨╛╨┤╨╕╨╝╨╗╨░╤А ╥Ы╤Ю╤И╨╕╨╗╨╝╨░╨│╨░╨╜."
        )

        return


    text = "ЁЯСе <b>╨е╨╛╨┤╨╕╨╝╨╗╨░╤А</b>\n\n"


    for employee in rows:

        status = (
            "ЁЯЯв"
            if employee["active"]
            else
            "ЁЯФ┤"
        )


        text += (
            f"{status} "
            f"<b>{html.escape(employee['name'])}</b>\n"
            f"ЁЯПв "
            f"{html.escape(employee['branch_name'])}\n"
            f"ЁЯЖФ {employee['telegram_id']}\n\n"
        )


    await message.answer(
        text,
        parse_mode="HTML"
    )


# =========================================================
# PROFILE
# =========================================================

@dp.message(
    F.text == "ЁЯСд ╨Я╤А╨╛╤Д╨╕╨╗╤М"
)
async def profile_handler(
    message: Message
):

    employee = get_employee(
        message.from_user.id
    )


    if not employee:

        await message.answer(
            "тЭМ ╨б╨╕╨╖╨╜╨╕╨╜╨│ ╨░╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ "
            "╤В╨╛╨┐╨╕╨╗╨╝╨░╨┤╨╕."
        )

        return


    await message.answer(
        f"ЁЯСд <b>╨Я╤А╨╛╤Д╨╕╨╗╤М</b>\n\n"
        f"╨Ш╤Б╨╝: "
        f"{html.escape(employee['name'])}\n"
        f"ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗: "
        f"{html.escape(employee['branch_name'])}\n"
        f"ЁЯЖФ Telegram ID: "
        f"{employee['telegram_id']}",
        parse_mode="HTML"
    )


# =========================================================
# MY PHOTOS
# =========================================================

@dp.message(
    F.text == "ЁЯУВ ╨Ь╨╡╨╜╨╕╨╜╨│ ╤А╨░╤Б╨╝╨╗╨░╤А╨╕╨╝"
)
async def my_photos_handler(
    message: Message
):

    employee = get_employee(
        message.from_user.id
    )


    if not employee:

        await message.answer(
            "тЭМ ╨Р╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤В╨╛╨┐╨╕╨╗╨╝╨░╨┤╨╕."
        )

        return


    rows = db.execute(
        """
        SELECT *
        FROM photos
        WHERE telegram_id = ?
        ORDER BY id DESC
        LIMIT 20
        """,
        (message.from_user.id,)
    ).fetchall()


    if not rows:

        await message.answer(
            "ЁЯУВ ╥▓╨░╨╗╨╕ ╤А╨░╤Б╨╝╨╗╨░╤А╨╕╨╜╨│╨╕╨╖ ╨╣╤Ю╥Ы."
        )

        return


    await message.answer(
        f"ЁЯУВ ╨б╤Ю╨╜╨│╨│╨╕ {len(rows)} ╤В╨░ ╤А╨░╤Б╨╝:"
    )


    for row in rows:

        image_path = Path(
            row["image_path"]
        )


        if not image_path.exists():
            continue


        try:

            dt = datetime.fromisoformat(
                row["device_time"]
            )

        except Exception:

            dt = now_local()


        caption = (
            f"ЁЯПв "
            f"{html.escape(row['branch_name'])}\n"
            f"ЁЯУЕ "
            f"{dt.strftime('%d.%m.%Y')}\n"
            f"ЁЯХР "
            f"{dt.strftime('%H:%M:%S')}\n"
            f"ЁЯУЭ "
            f"{html.escape(row['comment'] or '╨Ш╨╖╨╛╥│ ╨╣╤Ю╥Ы')}"
        )


        try:

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=FSInputFile(
                    image_path
                ),
                caption=caption
            )

        except Exception as error:

            print(
                "HISTORY PHOTO ERROR:",
                type(error).__name__,
                error
            )


# =========================================================
# ADMIN - ALL PHOTOS
# =========================================================

@dp.message(
    F.text == "ЁЯУ╕ ╨а╨░╤Б╨╝╨╗╨░╤А"
)
async def admin_photos_handler(
    message: Message
):

    if not is_admin(
        message.from_user.id
    ):
        return


    rows = db.execute(
        """
        SELECT *
        FROM photos
        ORDER BY id DESC
        LIMIT 20
        """
    ).fetchall()


    if not rows:

        await message.answer(
            "ЁЯУВ ╥▓╨░╨╗╨╕ ╤А╨░╤Б╨╝╨╗╨░╤А ╨║╨╡╨╗╨╝╨░╨│╨░╨╜."
        )

        return


    await message.answer(
        f"ЁЯУ╕ ╨б╤Ю╨╜╨│╨│╨╕ {len(rows)} ╤В╨░ ╤А╨░╤Б╨╝:"
    )


    for row in rows:

        image_path = Path(
            row["image_path"]
        )


        if not image_path.exists():
            continue


        try:

            dt = datetime.fromisoformat(
                row["device_time"]
            )

        except Exception:

            dt = now_local()


        caption = (
            "ЁЯУ╕ <b>╨п╨Э╨У╨Ш ╨а╨Р╨б╨Ь</b>\n\n"
            f"ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗: "
            f"<b>{html.escape(row['branch_name'])}</b>\n"
            f"ЁЯСд ╨е╨╛╨┤╨╕╨╝: "
            f"<b>{html.escape(row['employee_name'])}</b>\n"
            f"ЁЯУЕ ╨б╨░╨╜╨░: "
            f"{dt.strftime('%d.%m.%Y')}\n"
            f"ЁЯХР ╨Т╨░╥Ы╤В: "
            f"{dt.strftime('%H:%M:%S')}\n"
            f"ЁЯУЭ ╨Ш╨╖╨╛╥│: "
            f"{html.escape(row['comment'] or '╨Щ╤Ю╥Ы')}"
        )


        try:

            await bot.send_photo(
                chat_id=message.chat.id,
                photo=FSInputFile(
                    image_path
                ),
                caption=caption,
                parse_mode="HTML"
            )

        except Exception as error:

            print(
                "ADMIN PHOTO ERROR:",
                type(error).__name__,
                error
            )


# =========================================================
# API - USER
# =========================================================

async def api_me(
    request: web.Request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )


    user = webapp_user(
        init_data
    )


    if not user:

        return json_response(
            {
                "ok": False,
                "error":
                    "Telegram ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П╤Б╨╕ ╨╜╨╛╤В╤Ю╥У╤А╨╕."
            },
            401
        )


    employee = get_employee(
        int(user["id"])
    )


    if not employee:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨Р╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤Д╨╕╨╗╨╕╨░╨╗╨│╨░ "
                    "╨▒╨╕╤А╨╕╨║╤В╨╕╤А╨╕╨╗╨╝╨░╨│╨░╨╜."
            },
            403
        )


    if not employee["active"]:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨Р╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б."
            },
            403
        )


    if not employee["branch_active"]:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨д╨╕╨╗╨╕╨░╨╗ ╥│╨╛╨╖╨╕╤А ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б."
            },
            403
        )


    return json_response(
        {
            "ok": True,
            "user": {
                "id": int(user["id"]),
                "name": employee["name"],
                "branchId": employee["branch_id"],
                "branchName":
                    employee["branch_name"],
            }
        }
    )


# =========================================================
# API - SUBMIT PHOTO
# =========================================================

async def api_submit_photo(
    request: web.Request
):

    init_data = request.headers.get(
        "X-Telegram-Init-Data",
        ""
    )


    user = webapp_user(
        init_data
    )


    if not user:

        return json_response(
            {
                "ok": False,
                "error":
                    "Telegram ╨░╨▓╤В╨╛╤А╨╕╨╖╨░╤Ж╨╕╤П╤Б╨╕ ╨╜╨╛╤В╤Ю╥У╤А╨╕."
            },
            401
        )


    telegram_id = int(
        user["id"]
    )


    employee = get_employee(
        telegram_id
    )


    if not employee:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨Р╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤Д╨╕╨╗╨╕╨░╨╗╨│╨░ "
                    "╨▒╨╕╤А╨╕╨║╤В╨╕╤А╨╕╨╗╨╝╨░╨│╨░╨╜."
            },
            403
        )


    if not employee["active"]:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨Р╨║╨║╨░╤Г╨╜╤В╨╕╨╜╨│╨╕╨╖ ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б."
            },
            403
        )


    if not employee["branch_active"]:

        return json_response(
            {
                "ok": False,
                "error":
                    "╨д╨╕╨╗╨╕╨░╨╗ ╥│╨╛╨╖╨╕╤А ╤Д╨░╨╛╨╗ ╤Н╨╝╨░╤Б."
            },
            403
        )


    temp_path = None


    try:

        # =============================================
        # MULTIPART
        # =============================================

        reader = await request.multipart()


        image_field = None
        image_bytes = None

        comment = ""

        device_time_raw = ""


        async for field in reader:

            if field.name == "photo":

                # ╨н╨╜╨│ ╨╝╤Г╥│╨╕╨╝ ╨╢╨╛╨╣: multipart parser ╨║╨╡╨╣╨╕╨╜╨│╨╕ field'╨│╨░
                # ╤Ю╤В╨╕╤И╨╕╨┤╨░╨╜ ╨╛╨╗╨┤╨╕╨╜ photo ╨╝╨░╤К╨╗╤Г╨╝╨╛╤В╨╕╨╜╨╕ ╤В╤Ю╨╗╨╕╥Ы ╤Ю╥Ы╨╕╨╣╨╝╨╕╨╖.
                image_bytes = await field.read()
                image_field = True

                print(
                    "MULTIPART PHOTO READ:",
                    len(image_bytes),
                    "bytes"
                )


            elif field.name == "comment":

                comment = (
                    await field.text()
                ).strip()


            elif field.name == "deviceTimestamp":

                device_time_raw = (
                    await field.text()
                ).strip()


        if image_field is None or not image_bytes:

            return json_response(
                {
                    "ok": False,
                    "error":
                        "╨а╨░╤Б╨╝ ╨╝╨░╤К╨╗╤Г╨╝╨╛╤В╨╗╨░╤А╨╕ ╨▒╤Ю╤И."
                },
                400
            )


        # =============================================
        # DEVICE TIME
        # =============================================

        try:

            device_dt = datetime.fromisoformat(
                device_time_raw.replace(
                    "Z",
                    "+00:00"
                )
            )


            if device_dt.tzinfo is None:

                device_dt = (
                    device_dt.astimezone()
                )


        except Exception:

            device_dt = now_local()


        device_dt_local = (
            device_dt.astimezone()
        )


        # =============================================
        # TEMP FILE
        # =============================================

        token = secrets.token_hex(
            8
        )


        temp_path = (
            PHOTOS_DIR
            /
            f"upload_{telegram_id}_{token}.bin"
        )


        # =============================================
        # SAVE COMPLETE FILE
        # =============================================

        total_size = len(image_bytes)

        if total_size > (15 * 1024 * 1024):

            temp_path.unlink(
                missing_ok=True
            )

            return json_response(
                {
                    "ok": False,
                    "error":
                        "╨а╨░╤Б╨╝ ╥│╨░╨╢╨╝╨╕ 15 MB ╨┤╨░╨╜ ╨║╨░╤В╤В╨░."
                },
                413
            )

        with open(
            temp_path,
            "wb"
        ) as output:
            output.write(image_bytes)


        # =============================================
        # EMPTY FILE CHECK
        # =============================================

        if (
            not temp_path.exists()
            or
            temp_path.stat().st_size == 0
        ):

            temp_path.unlink(
                missing_ok=True
            )

            return json_response(
                {
                    "ok": False,
                    "error":
                        "╨а╨░╤Б╨╝ ╨╝╨░╤К╨╗╤Г╨╝╨╛╤В╨╗╨░╤А╨╕ ╨▒╤Ю╤И."
                },
                400
            )


        print(
            "PHOTO RECEIVED:",
            temp_path,
            temp_path.stat().st_size,
            "bytes"
        )


        # =============================================
        # OPEN IMAGE
        # =============================================

        try:

            with Image.open(
                temp_path
            ) as test_image:

                print(
                    "IMAGE FORMAT:",
                    test_image.format
                )

                print(
                    "IMAGE SIZE:",
                    test_image.size
                )

                print(
                    "IMAGE MODE:",
                    test_image.mode
                )

                # ╨а╨░╤Б╨╝╨╜╨╕ ╤В╤Ю╨╗╨╕╥Ы RAM'╨│╨░ ╤О╨║╨╗╨░╨╣╨╝╨╕╨╖
                test_image.load()

                # RGB'╨│╨░ ╨░╨╣╨╗╨░╨╜╤В╨╕╤А╨╕╨▒ ╤В╨╡╨║╤И╨╕╤А╨░╨╝╨╕╨╖
                test_image.convert(
                    "RGB"
                )


        except Exception as error:

            print(
                "IMAGE OPEN ERROR:",
                type(error).__name__,
                str(error)
            )


            temp_path.unlink(
                missing_ok=True
            )


            return json_response(
                {
                    "ok": False,
                    "error":
                        "╨а╨░╤Б╨╝ ╤Д╨░╨╣╨╗╨╕╨╜╨╕ ╤Ю╥Ы╨╕╨▒ ╨▒╤Ю╨╗╨╝╨░╨┤╨╕."
                },
                400
            )


        # =============================================
        # BRANCH FOLDER
        # =============================================

        branch_folder = (
            PHOTOS_DIR
            /
            str(employee["branch_id"])
        )


        branch_folder.mkdir(
            parents=True,
            exist_ok=True
        )


        # =============================================
        # FINAL FILE
        # =============================================

        photo_id = secrets.token_hex(
            12
        )


        final_path = (
            branch_folder
            /
            (
                f"{employee['branch_id']}_"
                f"{telegram_id}_"
                f"{photo_id}.jpg"
            )
        )


        # =============================================
        # WATERMARK
        # =============================================

        make_watermarked_image(
            temp_path,
            final_path,
            employee["branch_name"],
            device_dt_local
        )


        # TEMP DELETE
        temp_path.unlink(
            missing_ok=True
        )

        temp_path = None


        # =============================================
        # SERVER TIME
        # =============================================

        server_dt = now_local()


        # =============================================
        # DATABASE
        # =============================================

        db.execute(
            """
            INSERT INTO photos
            (
                telegram_id,
                employee_name,
                branch_id,
                branch_name,
                image_path,
                comment,
                device_time,
                created_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                telegram_id,
                employee["name"],
                employee["branch_id"],
                employee["branch_name"],
                str(final_path),
                comment,
                device_dt_local.isoformat(),
                server_dt.isoformat(),
            )
        )


        db.commit()


        # =============================================
        # ADMIN CAPTION
        # =============================================

        caption = (
            "ЁЯУ╕ <b>╨п╨Э╨У╨Ш ╨а╨Р╨б╨Ь</b>\n\n"
            f"ЁЯПв ╨д╨╕╨╗╨╕╨░╨╗: "
            f"<b>{html.escape(employee['branch_name'])}</b>\n"
            f"ЁЯСд ╨е╨╛╨┤╨╕╨╝: "
            f"<b>{html.escape(employee['name'])}</b>\n"
            f"ЁЯУЕ ╨б╨░╨╜╨░: "
            f"{device_dt_local.strftime('%d.%m.%Y')}\n"
            f"ЁЯХР ╨Т╨░╥Ы╤В: "
            f"{device_dt_local.strftime('%H:%M:%S')}\n"
            f"ЁЯУЭ ╨Ш╨╖╨╛╥│: "
            f"{html.escape(comment or '╨Щ╤Ю╥Ы')}"
        )


        # =============================================
        # SEND TO ADMIN
        # =============================================

        try:

            await bot.send_photo(
                chat_id=ADMIN_ID,
                photo=FSInputFile(
                    final_path
                ),
                caption=caption,
                parse_mode="HTML"
            )


            print(
                "PHOTO SENT TO ADMIN:",
                final_path
            )


        except Exception as error:

            print(
                "ADMIN PHOTO SEND ERROR:",
                type(error).__name__,
                str(error)
            )


        # =============================================
        # RESPONSE
        # =============================================

        return json_response(
            {
                "ok": True,
                "photoId": photo_id,
                "branchName":
                    employee["branch_name"],
                "deviceTimestamp":
                    device_dt_local.isoformat(),
                "serverTimestamp":
                    server_dt.isoformat(),
                "comment": comment,
            }
        )


    except Exception as error:

        print(
            "WEBAPP SUBMIT PHOTO ERROR:",
            type(error).__name__,
            str(error)
        )


        if temp_path:

            try:

                temp_path.unlink(
                    missing_ok=True
                )

            except Exception:
                pass


        return json_response(
            {
                "ok": False,
                "error":
                    "╨а╨░╤Б╨╝╨╜╨╕ ╤Б╨░╥Ы╨╗╨░╤И╨┤╨░ "
                    "╤Б╨╡╤А╨▓╨╡╤А ╤Е╨░╤В╨╛╤Б╨╕."
            },
            500
        )


# =========================================================
# INDEX.HTML
# =========================================================

async def index_handler(
    request: web.Request
):

    index_path = (
        CAMERA_APP_DIR
        /
        "index.html"
    )


    if not index_path.exists():

        return web.Response(
            text=(
                "camera_app/index.html "
                "topilmadi."
            ),
            status=404
        )


    return web.FileResponse(
        index_path
    )


# =========================================================
# FAVICON
# =========================================================

async def favicon_handler(
    request: web.Request
):

    return web.Response(
        status=204
    )


# =========================================================
# WEB APP
# =========================================================

def create_web_app():

    app = web.Application(
        client_max_size=20 * 1024 * 1024
    )


    app.router.add_get(
        "/",
        index_handler
    )


    app.router.add_get(
        "/favicon.ico",
        favicon_handler
    )


    app.router.add_get(
        "/api/me",
        api_me
    )


    app.router.add_post(
        "/api/submit-photo",
        api_submit_photo
    )


    return app


# =========================================================
# MAIN
# =========================================================

async def main():

    print(
        "========================================"
    )

    print(
        "Branch Photo Control Bot"
    )

    print(
        "Bot + Mini App server is starting..."
    )

    print(
        f"Web App URL: {WEBAPP_URL}"
    )

    print(
        f"Local server: http://127.0.0.1:{PORT}"
    )

    print(
        "========================================"
    )


    app = create_web_app()


    runner = web.AppRunner(
        app
    )


    await runner.setup()


    site = web.TCPSite(
        runner,
        HOST,
        PORT
    )


    await site.start()


    try:

        await dp.start_polling(
            bot
        )


    finally:

        await runner.cleanup()

        await bot.session.close()

        db.close()


# =========================================================
# RUN
# =========================================================

if __name__ == "__main__":

    asyncio.run(
        main()
    )