#!/usr/bin/env python3
"""
PlayerOk Stars Monitor Bot
Мониторит лоты Telegram Stars на playerok.com и уведомляет в Telegram.
"""

import asyncio
import json
import logging
import os
import random
import re
import sys
from pathlib import Path

import httpx
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

# ─────────────────── Логгинг ───────────────────
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    datefmt="%H:%M:%S",
)
logger = logging.getLogger(__name__)

# ─────────────────── Пути к файлам ───────────────────
ENV_FILE = Path(".env")
CONFIG_FILE = Path("config.json")
SEEN_FILE = Path("seen_lots.json")

PLAYEROK_GRAPHQL = "https://playerok.com/graphql"
ITEMS_HASH = "63eefcfd813442882ad846360d925279bc376e8bc85a577ebefbee0f9c78b557"

STAR_AMOUNTS = [50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

DEFAULT_CONFIG = {
    "enabled": False,
    "interval": 30,
    "filters": {
        "star_amounts": [],
        "max_price": None,
        "username_filter": None,
        "by_username_only": False,
        "by_gift_only": False,
    },
    "admin_ids": [],
}


# ══════════════════════════════════════════════════════
#   МАСТЕР ПЕРВОГО ЗАПУСКА
# ══════════════════════════════════════════════════════

def load_env_file() -> dict:
    """Читает .env файл и возвращает словарь ключ=значение."""
    result = {}
    if not ENV_FILE.exists():
        return result
    for line in ENV_FILE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        result[key.strip()] = val.strip()
    return result


def save_env_file(data: dict):
    """Сохраняет словарь в .env файл."""
    lines = []
    for key, val in data.items():
        lines.append(f"{key}={val}")
    ENV_FILE.write_text("\n".join(lines) + "\n", encoding="utf-8")
    logger.info("Данные сохранены в .env")


def validate_bot_token(token: str) -> bool:
    """Проверяет формат токена (цифры:буквы_цифры)."""
    return bool(re.match(r"^\d+:[A-Za-z0-9_-]{35,}$", token.strip()))


async def check_token_online(token: str) -> tuple[bool, str]:
    """Проверяет токен через Telegram API."""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.get(f"https://api.telegram.org/bot{token}/getMe")
            data = resp.json()
            if data.get("ok"):
                bot_name = data["result"].get("username", "")
                return True, bot_name
            return False, data.get("description", "Ошибка")
    except Exception as e:
        return False, str(e)


def setup_wizard():
    """
    Мастер первого запуска.
    Запускается если BOT_TOKEN или NOTIFY_CHAT_IDS не заданы.
    Спрашивает данные в терминале и сохраняет в .env
    """
    env = load_env_file()

    # Перезаписываем из переменных окружения (приоритет у реальных env vars)
    for key in ("BOT_TOKEN", "NOTIFY_CHAT_IDS"):
        if os.environ.get(key):
            env[key] = os.environ[key]

    needs_setup = not env.get("BOT_TOKEN") or not env.get("NOTIFY_CHAT_IDS")
    if not needs_setup:
        return env

    print()
    print("╔══════════════════════════════════════════════╗")
    print("║     PlayerOk Stars Bot — Первый запуск       ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    # ── BOT_TOKEN ──────────────────────────────────────
    if not env.get("BOT_TOKEN"):
        print("📌 Шаг 1: Токен Telegram бота")
        print("   Создай бота через @BotFather в Telegram.")
        print("   Скопируй токен вида: 1234567890:ABCDefgh...")
        print()

        while True:
            token = input("   Вставь токен бота: ").strip()
            if not token:
                print("   ❌ Токен не может быть пустым. Попробуй снова.")
                continue
            if not validate_bot_token(token):
                print("   ❌ Неверный формат. Токен должен быть вида: 1234567890:ABCDef...")
                continue

            print("   ⏳ Проверяю токен онлайн...")
            ok, info = asyncio.run(check_token_online(token))
            if ok:
                print(f"   ✅ Токен валиден! Бот: @{info}")
                env["BOT_TOKEN"] = token
                break
            else:
                print(f"   ❌ Telegram отклонил токен: {info}")
                retry = input("   Попробовать другой токен? (да/нет): ").strip().lower()
                if retry not in ("да", "д", "yes", "y"):
                    print("   Выход.")
                    sys.exit(1)
        print()

    # ── NOTIFY_CHAT_IDS ────────────────────────────────
    if not env.get("NOTIFY_CHAT_IDS"):
        print("📌 Шаг 2: Chat ID для уведомлений")
        print()
        print("   Как узнать свой Chat ID:")
        print("   1. Напиши боту @userinfobot в Telegram")
        print("   2. Он пришлёт твой ID (число, например: 123456789)")
        print()
        print("   Для группы/канала:")
        print("   1. Добавь своего бота как администратора")
        print("   2. Отправь любое сообщение в чат")
        print("   3. Запусти бота командой /start — он покажет ID через /getchatid")
        print()
        print("   💡 Можно указать несколько ID через запятую:")
        print("      123456789,-1001234567890")
        print()
        print("   Или оставь пустым и используй /getchatid после запуска бота.")
        print()

        chat_ids_raw = input("   Введи Chat ID (или Enter чтобы пропустить): ").strip()
        if chat_ids_raw:
            env["NOTIFY_CHAT_IDS"] = chat_ids_raw
            print(f"   ✅ Chat ID сохранены: {chat_ids_raw}")
        else:
            env["NOTIFY_CHAT_IDS"] = ""
            print("   ⚠️  Chat ID не указаны.")
            print("   После запуска бота напиши ему /getchatid чтобы получить свой ID.")
        print()

    # ── Сохраняем ──────────────────────────────────────
    save_env_file(env)

    print("╔══════════════════════════════════════════════╗")
    print("║          ✅ Настройка завершена!              ║")
    print("╚══════════════════════════════════════════════╝")
    print()

    return env


# ══════════════════════════════════════════════════════
#   ХРАНИЛИЩЕ
# ══════════════════════════════════════════════════════

def load_config() -> dict:
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()


def save_config(cfg: dict):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")


def load_seen() -> set:
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def save_seen(seen: set):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")


def get_notify_chat_ids() -> list[str]:
    """Читает актуальный список chat_id из .env каждый раз."""
    env = load_env_file()
    raw = os.environ.get("NOTIFY_CHAT_IDS") or env.get("NOTIFY_CHAT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]


def add_notify_chat_id(new_id: str):
    """Добавляет новый chat_id в .env если его там нет."""
    env = load_env_file()
    raw = env.get("NOTIFY_CHAT_IDS", "")
    ids = [c.strip() for c in raw.split(",") if c.strip()]
    if new_id not in ids:
        ids.append(new_id)
        env["NOTIFY_CHAT_IDS"] = ",".join(ids)
        save_env_file(env)
        return True
    return False


# ══════════════════════════════════════════════════════
#   ГЛОБАЛЬНОЕ СОСТОЯНИЕ
# ══════════════════════════════════════════════════════

seen_lots: set = load_seen()
monitor_task = None


# ══════════════════════════════════════════════════════
#   PLAYEROK API
# ══════════════════════════════════════════════════════

def get_headers() -> dict:
    return {
        "User-Agent": random.choice(USER_AGENTS),
        "Accept": "application/json",
        "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8",
        "Content-Type": "application/json",
        "Origin": "https://playerok.com",
        "Referer": "https://playerok.com/apps/telegram/stars",
        "Sec-Fetch-Dest": "empty",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
    }


async def fetch_lots(client: httpx.AsyncClient, filters_cfg: dict = None) -> list[dict]:
    """Получить список лотов с playerok.com через GraphQL."""
    filters_cfg = filters_cfg or {}

    variables = {
        "pagination": {"first": 40, "after": None},
        "filters": {
            "gameSlug": "telegram",
            "categorySlug": "stars",
        },
        "sort": "CREATED_AT_DESC",
    }

    star_amounts = filters_cfg.get("star_amounts", [])
    if star_amounts:
        variables["filters"]["dataFieldsFilter"] = [
            {"fieldId": "count", "value": str(amt)} for amt in star_amounts
        ]

    max_price = filters_cfg.get("max_price")
    if max_price:
        variables["filters"]["priceRange"] = {"max": float(max_price)}

    by_username_only = filters_cfg.get("by_username_only", False)
    by_gift_only = filters_cfg.get("by_gift_only", False)
    if by_username_only:
        variables["filters"]["obtainingTypeSlug"] = "username"
    elif by_gift_only:
        variables["filters"]["obtainingTypeSlug"] = "gift"

    payload = {
        "operationName": "items",
        "extensions": {
            "persistedQuery": {
                "version": 1,
                "sha256Hash": ITEMS_HASH,
            }
        },
        "variables": variables,
    }

    try:
        resp = await client.post(
            PLAYEROK_GRAPHQL,
            json=payload,
            headers=get_headers(),
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()

        if "errors" in data:
            logger.warning("GraphQL errors: %s", data["errors"])
            return []

        edges = data.get("data", {}).get("items", {}).get("edges", [])
        return [e["node"] for e in edges if e.get("node")]

    except httpx.HTTPStatusError as e:
        logger.error("HTTP %s: %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        logger.error("Fetch error: %s", e)
        return []


def apply_local_filters(lots: list[dict], filters_cfg: dict) -> list[dict]:
    result = []
    username_filter = (filters_cfg.get("username_filter") or "").lower() or None
    for lot in lots:
        if username_filter:
            seller = (lot.get("user") or {}).get("username", "").lower()
            if username_filter not in seller:
                continue
        result.append(lot)
    return result


def format_lot_message(lot: dict) -> str:
    lot_id = lot.get("id", "?")
    slug = lot.get("slug", lot_id)
    name = lot.get("name", "Telegram Stars")
    price = lot.get("price", "?")
    raw_price = lot.get("rawPrice", price)
    seller = (lot.get("user") or {}).get("username", "Неизвестен")
    obtaining = (lot.get("obtainingType") or {}).get("name", "") or \
                (lot.get("category") or {}).get("name", "")

    stars_count = ""
    for field in lot.get("dataFields") or []:
        if field.get("id") in ("count", "stars_count", "amount"):
            stars_count = field.get("value", "")
            break
    if not stars_count:
        m = re.search(r"\d+", name)
        stars_count = m.group(0) if m else "?"

    url = f"https://playerok.com/products/{slug}"

    return "\n".join([
        f"⭐ *Новый лот: {stars_count} Stars*",
        f"",
        f"💰 Цена: *{price} ₽* (без комиссии: {raw_price} ₽)",
        f"👤 Продавец: `{seller}`",
        f"📦 Доставка: {obtaining or 'не указана'}",
        f"",
        f"🔗 [Открыть лот]({url})",
        f"🆔 ID: `{lot_id}`",
    ])


# ══════════════════════════════════════════════════════
#   МОНИТОРИНГ
# ══════════════════════════════════════════════════════

async def monitor_loop(app: Application):
    """Основной цикл мониторинга."""
    global seen_lots
    logger.info("Фоновый мониторинг запущен (ожидает /monitor on)")
    _idle_ticks = 0

    async with httpx.AsyncClient(follow_redirects=True, verify=True) as client:
        while True:
            try:
                cfg = load_config()
                if not cfg.get("enabled", False):
                    _idle_ticks += 1
                    if _idle_ticks == 1 or _idle_ticks % 120 == 0:
                        logger.info("Мониторинг выключен — жду /monitor on ...")
                    await asyncio.sleep(5)
                    continue
                _idle_ticks = 0

                interval = cfg.get("interval", 30)
                filters_cfg = cfg.get("filters", {})

                logger.info("Проверяю лоты PlayerOk Stars...")
                lots = await fetch_lots(client, filters_cfg=filters_cfg)
                lots = apply_local_filters(lots, filters_cfg)

                chat_ids = get_notify_chat_ids()
                new_count = 0

                for lot in lots:
                    lot_id = str(lot.get("id", ""))
                    if not lot_id or lot_id in seen_lots:
                        continue

                    seen_lots.add(lot_id)
                    save_seen(seen_lots)
                    new_count += 1

                    msg = format_lot_message(lot)
                    for chat_id in chat_ids:
                        try:
                            await app.bot.send_message(
                                chat_id=chat_id,
                                text=msg,
                                parse_mode="Markdown",
                                disable_web_page_preview=False,
                            )
                        except Exception as e:
                            logger.error("Ошибка отправки в %s: %s", chat_id, e)

                if new_count:
                    logger.info("Найдено %d новых лотов", new_count)
                else:
                    logger.info("Новых лотов нет (всего: %d)", len(lots))

                jitter = interval * 0.2
                await asyncio.sleep(max(interval + random.uniform(-jitter, jitter), 5))

            except asyncio.CancelledError:
                logger.info("Мониторинг остановлен")
                break
            except Exception as e:
                logger.error("Ошибка в цикле: %s", e)
                await asyncio.sleep(10)


# ══════════════════════════════════════════════════════
#   TELEGRAM КОМАНДЫ
# ══════════════════════════════════════════════════════

def is_admin(user_id: int) -> bool:
    cfg = load_config()
    admins = cfg.get("admin_ids", [])
    return not admins or user_id in admins


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cfg = load_config()

    # Первый /start — автоматически делаем этого пользователя admin
    if not cfg.get("admin_ids"):
        cfg["admin_ids"] = [user_id]
        save_config(cfg)
        logger.info("Новый admin: %d", user_id)

    # Проверяем есть ли chat_ids — если нет, предлагаем добавить
    chat_ids = get_notify_chat_ids()
    chat_warn = ""
    if not chat_ids:
        chat_warn = (
            "\n⚠️ *Chat ID не настроены!*\n"
            "Нажми /getchatid — бот автоматически добавит этот чат для уведомлений.\n"
        )

    text = (
        "👋 *PlayerOk Stars Monitor*\n\n"
        "Слежу за лотами Telegram Stars на playerok.com\n"
        f"{chat_warn}\n"
        "📋 *Команды:*\n"
        "/status — текущий статус\n"
        "/monitor on|off — вкл/выкл мониторинг\n"
        "/interval 30 — интервал в секундах\n"
        "/filter — настроить фильтры\n"
        "/filters — показать фильтры\n"
        "/clearfilters — сбросить фильтры\n"
        "/clearseen — очистить историю лотов\n"
        "/getchatid — добавить этот чат в уведомления\n"
        "/chatids — список чатов для уведомлений\n"
        "/test — тестовый запрос к PlayerOk\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_getchatid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает chat_id текущего чата и предлагает добавить его в уведомления."""
    chat = update.effective_chat
    user = update.effective_user
    chat_id = str(chat.id)

    chat_type_map = {
        "private": "личный чат",
        "group": "группа",
        "supergroup": "супергруппа",
        "channel": "канал",
    }
    chat_type = chat_type_map.get(chat.type, chat.type)
    chat_title = chat.title or f"@{user.username or user.first_name}"

    existing = get_notify_chat_ids()
    already_added = chat_id in existing

    keyboard = []
    if not already_added:
        keyboard.append([
            InlineKeyboardButton("✅ Добавить этот чат в уведомления", callback_data=f"addchat_{chat_id}")
        ])

    text = (
        f"🆔 *Chat ID этого чата:*\n"
        f"`{chat_id}`\n\n"
        f"📋 Тип: {chat_type}\n"
        f"📝 Название: {chat_title}\n\n"
    )

    if already_added:
        text += "✅ *Этот чат уже добавлен* в список уведомлений."
    else:
        text += "Нажми кнопку ниже чтобы добавить этот чат для уведомлений:"

    await update.message.reply_text(
        text,
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard) if keyboard else None,
    )


async def cmd_chatids(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает список всех Chat ID для уведомлений."""
    ids = get_notify_chat_ids()
    if not ids:
        await update.message.reply_text(
            "⚠️ Нет ни одного Chat ID.\n"
            "Используй /getchatid чтобы добавить текущий чат."
        )
        return

    lines = ["📋 *Чаты для уведомлений:*\n"]
    for i, cid in enumerate(ids, 1):
        lines.append(f"{i}. `{cid}`")

    keyboard = [[InlineKeyboardButton("🗑 Очистить список чатов", callback_data="clearchats")]]
    await update.message.reply_text(
        "\n".join(lines),
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    enabled = cfg.get("enabled", False)
    interval = cfg.get("interval", 30)
    f = cfg.get("filters", {})

    star_amounts = f.get("star_amounts", [])
    stars_str = ", ".join(str(s) for s in star_amounts) if star_amounts else "все"
    max_price = f.get("max_price")
    price_str = f"до {max_price} ₽" if max_price else "без ограничений"

    delivery = []
    if f.get("by_username_only"):
        delivery.append("по username")
    if f.get("by_gift_only"):
        delivery.append("подарком")
    delivery_str = ", ".join(delivery) if delivery else "все"

    chat_ids = get_notify_chat_ids()
    chats_str = ", ".join(f"`{c}`" for c in chat_ids) if chat_ids else "⚠️ не настроены"

    status_icon = "🟢" if enabled else "🔴"
    status_text = "ВКЛЮЧЁН" if enabled else "ВЫКЛЮЧЕН"

    text = (
        f"{status_icon} *Мониторинг: {status_text}*\n\n"
        f"⏱ Интервал: {interval} сек\n"
        f"📊 Просмотрено лотов: {len(seen_lots)}\n"
        f"📣 Уведомления: {chats_str}\n\n"
        f"*Фильтры:*\n"
        f"⭐ Звёзды: {stars_str}\n"
        f"💰 Цена: {price_str}\n"
        f"👤 Username продавца: {f.get('username_filter') or 'не задан'}\n"
        f"📦 Доставка: {delivery_str}\n"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_monitor(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return

    args = context.args
    if not args or args[0] not in ("on", "off"):
        await update.message.reply_text("Использование: /monitor on или /monitor off")
        return

    # Проверяем chat_ids перед включением
    if args[0] == "on" and not get_notify_chat_ids():
        await update.message.reply_text(
            "⚠️ Не настроены Chat ID для уведомлений!\n"
            "Сначала используй /getchatid чтобы добавить чат."
        )
        return

    cfg = load_config()
    cfg["enabled"] = args[0] == "on"
    save_config(cfg)

    icon = "🟢" if cfg["enabled"] else "🔴"
    state = "включён" if cfg["enabled"] else "выключен"
    await update.message.reply_text(f"{icon} Мониторинг {state}.")


async def cmd_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return

    args = context.args
    if not args or not args[0].isdigit():
        await update.message.reply_text("Использование: /interval <секунды>\nМинимум: 10")
        return

    secs = int(args[0])
    if secs < 10:
        await update.message.reply_text("❌ Минимум 10 секунд.")
        return

    cfg = load_config()
    cfg["interval"] = secs
    save_config(cfg)
    await update.message.reply_text(f"✅ Интервал: {secs} сек.")


async def cmd_filter(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return

    keyboard = [
        [InlineKeyboardButton("⭐ Количество звёзд", callback_data="filter_stars")],
        [InlineKeyboardButton("💰 Макс. цена", callback_data="filter_price")],
        [InlineKeyboardButton("👤 По username продавца", callback_data="filter_username")],
        [InlineKeyboardButton("📦 Только доставка по username", callback_data="filter_delivery_username")],
        [InlineKeyboardButton("🎁 Только доставка Подарком", callback_data="filter_delivery_gift")],
        [InlineKeyboardButton("🔄 Сбросить все фильтры", callback_data="filter_clear")],
    ]
    await update.message.reply_text(
        "⚙️ *Настройка фильтров*\nВыбери что настроить:",
        parse_mode="Markdown",
        reply_markup=InlineKeyboardMarkup(keyboard),
    )


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await cmd_status(update, context)


async def cmd_clearfilters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return
    cfg = load_config()
    cfg["filters"] = DEFAULT_CONFIG["filters"].copy()
    save_config(cfg)
    await update.message.reply_text("✅ Фильтры сброшены.")


async def cmd_clearseen(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return
    global seen_lots
    seen_lots = set()
    save_seen(seen_lots)
    await update.message.reply_text("✅ История просмотренных лотов очищена.")


async def cmd_test(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔍 Делаю тестовый запрос к PlayerOk...")
    cfg = load_config()
    async with httpx.AsyncClient(follow_redirects=True) as client:
        lots = await fetch_lots(client, filters_cfg=cfg.get("filters", {}))

    if lots:
        msg = format_lot_message(lots[0])
        await update.message.reply_text(
            f"✅ Получено *{len(lots)}* лотов. Первый:\n\n{msg}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "⚠️ Лоты не найдены или PlayerOk заблокировал запрос.\n"
            "Попробуй позже или увеличь интервал."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Справка PlayerOk Stars Monitor*\n\n"
        "*Основные:*\n"
        "/start — главное меню\n"
        "/status — статус, фильтры, чаты\n"
        "/monitor on — запустить мониторинг\n"
        "/monitor off — остановить\n"
        "/interval 60 — интервал проверки (сек)\n\n"
        "*Уведомления:*\n"
        "/getchatid — получить ID этого чата и добавить его\n"
        "/chatids — список чатов для уведомлений\n\n"
        "*Фильтры:*\n"
        "/filter — меню настройки фильтров\n"
        "/filters — показать текущие фильтры\n"
        "/clearfilters — сбросить все фильтры\n\n"
        "*Прочее:*\n"
        "/clearseen — очистить историю лотов\n"
        "/test — тестовый запрос к PlayerOk\n\n"
        f"⭐ *Количества звёзд на PlayerOk:*\n"
        f"{', '.join(str(x) for x in STAR_AMOUNTS)}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ══════════════════════════════════════════════════════
#   CALLBACK КНОПОК
# ══════════════════════════════════════════════════════

async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    # ── Добавить чат в уведомления ──
    if data.startswith("addchat_"):
        new_id = data.split("_", 1)[1]
        added = add_notify_chat_id(new_id)
        if added:
            await query.edit_message_text(
                f"✅ Чат `{new_id}` добавлен в список уведомлений!\n\n"
                f"Теперь можешь запустить мониторинг: /monitor on",
                parse_mode="Markdown",
            )
        else:
            await query.edit_message_text(f"ℹ️ Чат `{new_id}` уже был в списке.", parse_mode="Markdown")

    # ── Очистить список чатов ──
    elif data == "clearchats":
        env = load_env_file()
        env["NOTIFY_CHAT_IDS"] = ""
        save_env_file(env)
        await query.edit_message_text("✅ Список чатов для уведомлений очищен.")

    # ── Фильтр по звёздам ──
    elif data == "filter_stars":
        cfg = load_config()
        current = cfg.get("filters", {}).get("star_amounts", [])
        keyboard = _build_stars_keyboard(current)
        await query.edit_message_text(
            "⭐ Выбери количества звёзд (можно несколько):\nПусто = все",
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    elif data.startswith("toggle_stars_"):
        amt = int(data.split("_")[-1])
        cfg = load_config()
        current = cfg.get("filters", {}).get("star_amounts", [])
        if amt in current:
            current.remove(amt)
        else:
            current.append(amt)
        cfg.setdefault("filters", {})["star_amounts"] = sorted(current)
        save_config(cfg)
        await query.edit_message_reply_markup(InlineKeyboardMarkup(_build_stars_keyboard(current)))

    elif data == "filter_stars_done":
        cfg = load_config()
        current = cfg.get("filters", {}).get("star_amounts", [])
        stars_str = ", ".join(str(s) for s in current) if current else "все"
        await query.edit_message_text(f"✅ Фильтр по звёздам: {stars_str}")

    # ── Цена ──
    elif data == "filter_price":
        context.user_data["awaiting"] = "price"
        await query.edit_message_text(
            "💰 Введи максимальную цену в рублях (например: 120):\n"
            "Отправь 0 чтобы убрать ограничение."
        )

    # ── Username продавца ──
    elif data == "filter_username":
        context.user_data["awaiting"] = "username"
        await query.edit_message_text(
            "👤 Введи username продавца для фильтрации:\n"
            "Отправь - чтобы убрать фильтр."
        )

    # ── Тип доставки ──
    elif data == "filter_delivery_username":
        cfg = load_config()
        cfg.setdefault("filters", {})
        new_val = not cfg["filters"].get("by_username_only", False)
        cfg["filters"]["by_username_only"] = new_val
        cfg["filters"]["by_gift_only"] = False
        save_config(cfg)
        state = "включён ✅" if new_val else "выключен"
        await query.edit_message_text(f"📦 Фильтр 'только по username': {state}")

    elif data == "filter_delivery_gift":
        cfg = load_config()
        cfg.setdefault("filters", {})
        new_val = not cfg["filters"].get("by_gift_only", False)
        cfg["filters"]["by_gift_only"] = new_val
        cfg["filters"]["by_username_only"] = False
        save_config(cfg)
        state = "включён ✅" if new_val else "выключен"
        await query.edit_message_text(f"🎁 Фильтр 'только Подарком': {state}")

    elif data == "filter_clear":
        cfg = load_config()
        cfg["filters"] = DEFAULT_CONFIG["filters"].copy()
        save_config(cfg)
        await query.edit_message_text("✅ Все фильтры сброшены.")


def _build_stars_keyboard(current: list) -> list:
    keyboard = []
    row = []
    for i, amt in enumerate(STAR_AMOUNTS):
        mark = "✅ " if amt in current else ""
        row.append(InlineKeyboardButton(f"{mark}{amt}⭐", callback_data=f"toggle_stars_{amt}"))
        if len(row) == 3:
            keyboard.append(row)
            row = []
    if row:
        keyboard.append(row)
    keyboard.append([InlineKeyboardButton("✔️ Готово", callback_data="filter_stars_done")])
    return keyboard


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового ввода при настройке фильтров."""
    awaiting = context.user_data.get("awaiting")
    if not awaiting:
        return

    text = update.message.text.strip()
    cfg = load_config()
    cfg.setdefault("filters", {})

    if awaiting == "price":
        try:
            val = float(text)
            cfg["filters"]["max_price"] = None if val <= 0 else val
            save_config(cfg)
            msg = f"✅ Макс. цена: {val} ₽" if val > 0 else "✅ Ограничение по цене снято."
            await update.message.reply_text(msg)
        except ValueError:
            await update.message.reply_text("❌ Введи число (например: 120)")
            return

    elif awaiting == "username":
        if text == "-":
            cfg["filters"]["username_filter"] = None
            save_config(cfg)
            await update.message.reply_text("✅ Фильтр по username убран.")
        else:
            cfg["filters"]["username_filter"] = text.lstrip("@")
            save_config(cfg)
            await update.message.reply_text(f"✅ Фильтр по username: @{text.lstrip('@')}")

    context.user_data.pop("awaiting", None)


# ══════════════════════════════════════════════════════
#   ЗАПУСК
# ══════════════════════════════════════════════════════

async def post_init(app: Application):
    global monitor_task
    cfg = load_config()
    status = "ВКЛЮЧЁН" if cfg.get("enabled") else "ВЫКЛЮЧЕН"
    logger.info(
        "Бот запущен | Мониторинг: %s | Интервал: %s сек | /monitor on — чтобы включить",
        status, cfg.get("interval", 30)
    )
    monitor_task = asyncio.create_task(monitor_loop(app))


async def post_shutdown(app: Application):
    """Корректная остановка фонового мониторинга при выходе."""
    global monitor_task
    if monitor_task and not monitor_task.done():
        logger.info("Останавливаю фоновый мониторинг...")
        monitor_task.cancel()
        try:
            await monitor_task
        except asyncio.CancelledError:
            pass
    logger.info("Бот остановлен. Пока!")


def main():
    # ── Мастер настройки ──────────────────────────────
    env = setup_wizard()

    bot_token = env.get("BOT_TOKEN") or os.environ.get("BOT_TOKEN", "")
    if not bot_token:
        logger.error("BOT_TOKEN не задан. Запусти бота снова.")
        sys.exit(1)

    # ── Запуск бота ───────────────────────────────────
    app = (
        Application.builder()
        .token(bot_token)
        .post_init(post_init)
        .post_shutdown(post_shutdown)
        .build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("monitor", cmd_monitor))
    app.add_handler(CommandHandler("interval", cmd_interval))
    app.add_handler(CommandHandler("filter", cmd_filter))
    app.add_handler(CommandHandler("filters", cmd_filters))
    app.add_handler(CommandHandler("clearfilters", cmd_clearfilters))
    app.add_handler(CommandHandler("clearseen", cmd_clearseen))
    app.add_handler(CommandHandler("getchatid", cmd_getchatid))
    app.add_handler(CommandHandler("chatids", cmd_chatids))
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Запускаю бота...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
