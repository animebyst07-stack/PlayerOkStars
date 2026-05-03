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
import time
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

# ─────────────────── Конфиг ───────────────────
BOT_TOKEN = os.environ.get("BOT_TOKEN", "")
NOTIFY_CHAT_IDS_RAW = os.environ.get("NOTIFY_CHAT_IDS", "")
NOTIFY_CHAT_IDS = [c.strip() for c in NOTIFY_CHAT_IDS_RAW.split(",") if c.strip()]

CONFIG_FILE = Path("config.json")
SEEN_FILE = Path("seen_lots.json")

PLAYEROK_GRAPHQL = "https://playerok.com/graphql"

# Персистированный хэш запроса items
ITEMS_HASH = "63eefcfd813442882ad846360d925279bc376e8bc85a577ebefbee0f9c78b557"

# Допустимые количества звёзд на PlayerOk
STAR_AMOUNTS = [50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000]

# Ротация User-Agent
USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
]

# ─────────────────── Дефолтный конфиг ───────────────────
DEFAULT_CONFIG = {
    "enabled": False,
    "interval": 30,
    "filters": {
        "star_amounts": [],        # пустой = все количества
        "max_price": None,         # макс цена в рублях
        "username_filter": None,   # фильтр по username продавца
        "by_username_only": False, # только лоты с доставкой по username
        "by_gift_only": False,     # только лоты с доставкой Подарком
    },
    "admin_ids": [],
}


# ─────────────────── Хранилище ───────────────────
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


# ─────────────────── Глобальное состояние ───────────────────
config = load_config()
seen_lots: set = load_seen()
monitor_task = None


# ─────────────────── PlayerOk API ───────────────────
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


async def fetch_lots(client: httpx.AsyncClient, page: int = 1, filters_cfg: dict = None) -> list[dict]:
    """Получить список лотов с playerok.com через GraphQL."""
    filters_cfg = filters_cfg or {}

    # Переменные для GraphQL запроса items
    variables = {
        "pagination": {"first": 40, "after": None},
        "filters": {
            "gameSlug": "telegram",
            "categorySlug": "stars",
        },
        "sort": "CREATED_AT_DESC",
    }

    # Фильтр по количеству звёзд (через dataFields)
    star_amounts = filters_cfg.get("star_amounts", [])
    if star_amounts:
        # PlayerOk фильтрует по dataFieldsFilter
        variables["filters"]["dataFieldsFilter"] = [
            {"fieldId": "count", "value": str(amt)} for amt in star_amounts
        ]

    # Фильтр по цене
    max_price = filters_cfg.get("max_price")
    if max_price:
        variables["filters"]["priceRange"] = {"max": float(max_price)}

    # Фильтр по типу доставки
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

        items_data = data.get("data", {}).get("items", {})
        edges = items_data.get("edges", [])
        return [edge["node"] for edge in edges if edge.get("node")]

    except httpx.HTTPStatusError as e:
        logger.error("HTTP error %s: %s", e.response.status_code, e.response.text[:200])
        return []
    except Exception as e:
        logger.error("Fetch error: %s", e)
        return []


def apply_local_filters(lots: list[dict], filters_cfg: dict) -> list[dict]:
    """Дополнительная локальная фильтрация лотов."""
    result = []
    username_filter = filters_cfg.get("username_filter", "").lower() if filters_cfg.get("username_filter") else None

    for lot in lots:
        # Фильтр по username продавца
        if username_filter:
            seller = (lot.get("user") or {}).get("username", "").lower()
            if username_filter not in seller:
                continue
        result.append(lot)
    return result


def format_lot_message(lot: dict) -> str:
    """Форматировать сообщение о лоте."""
    lot_id = lot.get("id", "?")
    slug = lot.get("slug", lot_id)
    name = lot.get("name", "Telegram Stars")
    price = lot.get("price", "?")
    raw_price = lot.get("rawPrice", price)
    seller = (lot.get("user") or {}).get("username", "Неизвестен")

    # Тип доставки
    obtaining = (lot.get("obtainingType") or {}).get("name", "")
    if not obtaining:
        category = (lot.get("category") or {})
        obtaining = category.get("name", "")

    # Количество звёзд из dataFields
    stars_count = ""
    for field in lot.get("dataFields") or []:
        if field.get("id") in ("count", "stars_count", "amount"):
            stars_count = field.get("value", "")
            break
    if not stars_count:
        stars_count = re.search(r"\d+", name)
        stars_count = stars_count.group(0) if stars_count else "?"

    url = f"https://playerok.com/products/{slug}"

    lines = [
        f"⭐ **Новый лот: {stars_count} Stars**",
        f"",
        f"💰 Цена: **{price} ₽** (без комиссии: {raw_price} ₽)",
        f"👤 Продавец: `{seller}`",
        f"📦 Доставка: {obtaining or 'не указана'}",
        f"",
        f"🔗 [Открыть лот]({url})",
        f"🆔 ID: `{lot_id}`",
    ]
    return "\n".join(lines)


# ─────────────────── Мониторинг ───────────────────
async def monitor_loop(app: Application):
    """Основной цикл мониторинга."""
    global seen_lots, config

    logger.info("Мониторинг запущен")

    async with httpx.AsyncClient(
        follow_redirects=True,
        verify=True,
    ) as client:
        while True:
            try:
                cfg = load_config()
                if not cfg.get("enabled", False):
                    await asyncio.sleep(5)
                    continue

                interval = cfg.get("interval", 30)
                filters_cfg = cfg.get("filters", {})

                logger.info("Проверяю лоты PlayerOk Stars...")
                lots = await fetch_lots(client, filters_cfg=filters_cfg)
                lots = apply_local_filters(lots, filters_cfg)

                new_count = 0
                for lot in lots:
                    lot_id = str(lot.get("id", ""))
                    if not lot_id or lot_id in seen_lots:
                        continue

                    seen_lots.add(lot_id)
                    save_seen(seen_lots)
                    new_count += 1

                    msg = format_lot_message(lot)
                    for chat_id in NOTIFY_CHAT_IDS:
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
                    logger.info("Новых лотов нет (всего лотов: %d)", len(lots))

                # Случайная задержка ±20% для анти-бан
                jitter = interval * 0.2
                sleep_time = interval + random.uniform(-jitter, jitter)
                await asyncio.sleep(max(sleep_time, 5))

            except asyncio.CancelledError:
                logger.info("Мониторинг остановлен")
                break
            except Exception as e:
                logger.error("Ошибка в цикле мониторинга: %s", e)
                await asyncio.sleep(10)


# ─────────────────── Telegram команды ───────────────────
def is_admin(user_id: int) -> bool:
    cfg = load_config()
    admins = cfg.get("admin_ids", [])
    return not admins or user_id in admins


async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    cfg = load_config()

    # Первый запуск — добавляем как admin
    if not cfg.get("admin_ids"):
        cfg["admin_ids"] = [user_id]
        save_config(cfg)

    text = (
        "👋 *PlayerOk Stars Monitor*\n\n"
        "Слежу за лотами Telegram Stars на playerok.com\n\n"
        "📋 *Команды:*\n"
        "/status — текущий статус\n"
        "/monitor on|off — вкл/выкл мониторинг\n"
        "/interval 30 — интервал в секундах\n"
        "/filter — настроить фильтры\n"
        "/filters — показать фильтры\n"
        "/clearfilters — сбросить фильтры\n"
        "/clearseen — очистить историю лотов\n"
        "/test — тестовый запрос к PlayerOk\n"
        "/help — помощь"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cfg = load_config()
    enabled = cfg.get("enabled", False)
    interval = cfg.get("interval", 30)
    f = cfg.get("filters", {})

    status_icon = "🟢" if enabled else "🔴"
    status_text = "ВКЛЮЧЁН" if enabled else "ВЫКЛЮЧЕН"

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

    text = (
        f"{status_icon} *Мониторинг: {status_text}*\n\n"
        f"⏱ Интервал: {interval} сек\n"
        f"📊 Просмотрено лотов: {len(seen_lots)}\n\n"
        f"*Фильтры:*\n"
        f"⭐ Звёзды: {stars_str}\n"
        f"💰 Цена: {price_str}\n"
        f"👤 Username: {f.get('username_filter') or 'не задан'}\n"
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
    """Интерактивное меню фильтров."""
    if not is_admin(update.effective_user.id):
        await update.message.reply_text("❌ Нет прав.")
        return

    keyboard = [
        [InlineKeyboardButton("⭐ Количество звёзд", callback_data="filter_stars")],
        [InlineKeyboardButton("💰 Макс. цена", callback_data="filter_price")],
        [InlineKeyboardButton("👤 По username продавца", callback_data="filter_username")],
        [InlineKeyboardButton("📦 По username доставки", callback_data="filter_delivery_username")],
        [InlineKeyboardButton("🎁 По доставке Подарком", callback_data="filter_delivery_gift")],
        [InlineKeyboardButton("🔄 Сбросить все фильтры", callback_data="filter_clear")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚙️ *Настройка фильтров*\nВыбери что настроить:",
        parse_mode="Markdown",
        reply_markup=reply_markup,
    )


async def cmd_filters(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показать текущие фильтры."""
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
    """Тестовый запрос к PlayerOk."""
    await update.message.reply_text("🔍 Делаю тестовый запрос к PlayerOk...")

    cfg = load_config()
    filters_cfg = cfg.get("filters", {})

    async with httpx.AsyncClient(follow_redirects=True) as client:
        lots = await fetch_lots(client, filters_cfg=filters_cfg)

    if lots:
        lot = lots[0]
        msg = format_lot_message(lot)
        await update.message.reply_text(
            f"✅ Получено {len(lots)} лотов. Первый:\n\n{msg}",
            parse_mode="Markdown",
        )
    else:
        await update.message.reply_text(
            "⚠️ Лоты не найдены или PlayerOk заблокировал запрос.\n"
            "Попробуй позже или проверь подключение."
        )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📖 *Справка PlayerOk Stars Monitor*\n\n"
        "*Основные команды:*\n"
        "/start — главное меню\n"
        "/status — статус и текущие фильтры\n"
        "/monitor on — запустить мониторинг\n"
        "/monitor off — остановить мониторинг\n"
        "/interval 60 — проверять каждые 60 сек\n\n"
        "*Фильтры:*\n"
        "/filter — меню настройки фильтров\n"
        "/filters — показать текущие фильтры\n"
        "/clearfilters — сбросить все фильтры\n\n"
        "*Прочее:*\n"
        "/clearseen — очистить историю лотов (найдёт снова)\n"
        "/test — тестовый запрос к PlayerOk\n\n"
        f"⭐ *Доступные количества звёзд:*\n"
        f"{', '.join(str(x) for x in STAR_AMOUNTS)}"
    )
    await update.message.reply_text(text, parse_mode="Markdown")


# ─────────────────── Callback кнопок ───────────────────
async def callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == "filter_stars":
        # Показываем кнопки для каждого количества звёзд
        cfg = load_config()
        current = cfg.get("filters", {}).get("star_amounts", [])

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

        await query.edit_message_text(
            "⭐ Выбери нужные количества звёзд (можно несколько):\n(пусто = все)",
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

        # Обновить те же кнопки
        keyboard = []
        row = []
        for i, a in enumerate(STAR_AMOUNTS):
            mark = "✅ " if a in current else ""
            row.append(InlineKeyboardButton(f"{mark}{a}⭐", callback_data=f"toggle_stars_{a}"))
            if len(row) == 3:
                keyboard.append(row)
                row = []
        if row:
            keyboard.append(row)
        keyboard.append([InlineKeyboardButton("✔️ Готово", callback_data="filter_stars_done")])
        await query.edit_message_reply_markup(InlineKeyboardMarkup(keyboard))

    elif data == "filter_stars_done":
        cfg = load_config()
        current = cfg.get("filters", {}).get("star_amounts", [])
        stars_str = ", ".join(str(s) for s in current) if current else "все"
        await query.edit_message_text(f"✅ Фильтр по звёздам: {stars_str}")

    elif data == "filter_price":
        context.user_data["awaiting"] = "price"
        await query.edit_message_text(
            "💰 Введи максимальную цену в рублях (например: 120):\n"
            "Или отправь 0 чтобы убрать ограничение."
        )

    elif data == "filter_username":
        context.user_data["awaiting"] = "username"
        await query.edit_message_text(
            "👤 Введи username продавца для фильтрации:\n"
            "Или отправь - чтобы убрать фильтр."
        )

    elif data == "filter_delivery_username":
        cfg = load_config()
        cfg.setdefault("filters", {})
        current = cfg["filters"].get("by_username_only", False)
        cfg["filters"]["by_username_only"] = not current
        cfg["filters"]["by_gift_only"] = False
        save_config(cfg)
        state = "включён ✅" if not current else "выключен"
        await query.edit_message_text(f"📦 Фильтр по username доставке: {state}")

    elif data == "filter_delivery_gift":
        cfg = load_config()
        cfg.setdefault("filters", {})
        current = cfg["filters"].get("by_gift_only", False)
        cfg["filters"]["by_gift_only"] = not current
        cfg["filters"]["by_username_only"] = False
        save_config(cfg)
        state = "включён ✅" if not current else "выключен"
        await query.edit_message_text(f"🎁 Фильтр по Подарку: {state}")

    elif data == "filter_clear":
        cfg = load_config()
        cfg["filters"] = DEFAULT_CONFIG["filters"].copy()
        save_config(cfg)
        await query.edit_message_text("✅ Все фильтры сброшены.")


async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработка текстового ввода для настройки фильтров."""
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


# ─────────────────── Запуск ───────────────────
async def post_init(app: Application):
    """Запуск мониторинга вместе с ботом."""
    global monitor_task
    monitor_task = asyncio.create_task(monitor_loop(app))
    logger.info("Бот запущен. Мониторинг: %s", "активен" if config.get("enabled") else "ожидает /monitor on")


def main():
    if not BOT_TOKEN:
        logger.error("BOT_TOKEN не задан! Укажи его в .env или переменных окружения.")
        return

    if not NOTIFY_CHAT_IDS:
        logger.warning("NOTIFY_CHAT_IDS не задан. Уведомления будут отправлены только в личку с ботом.")

    app = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init)
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
    app.add_handler(CommandHandler("test", cmd_test))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CallbackQueryHandler(callback_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    logger.info("Запускаю бота...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
