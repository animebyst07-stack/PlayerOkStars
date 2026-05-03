#!/usr/bin/env python3
import asyncio
import json
import logging
import os
import random
import re
import sys
from pathlib import Path

import httpx
from telegram import Bot

logging.basicConfig(format="%(asctime)s [%(levelname)s] %(message)s", level=logging.INFO, datefmt="%H:%M:%S")
logger = logging.getLogger(__name__)

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
DEFAULT_CONFIG = {"enabled": False, "interval": 30, "filters": {"star_amounts": [], "max_price": None, "username_filter": None, "by_username_only": False, "by_gift_only": False}, "admin_ids": []}

def load_env_file():
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

def save_env_file(data):
    ENV_FILE.write_text("\n".join(f"{k}={v}" for k, v in data.items()) + "\n", encoding="utf-8")

def setup_wizard():
    env = load_env_file()
    for key in ("BOT_TOKEN", "NOTIFY_CHAT_IDS"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    if env.get("BOT_TOKEN") and env.get("NOTIFY_CHAT_IDS"):
        return env
    if not env.get("BOT_TOKEN"):
        while True:
            token = input("BOT_TOKEN: ").strip()
            if token:
                env["BOT_TOKEN"] = token
                break
    if not env.get("NOTIFY_CHAT_IDS"):
        env["NOTIFY_CHAT_IDS"] = input("NOTIFY_CHAT_IDS: ").strip()
    save_env_file(env)
    return env

def load_config():
    if CONFIG_FILE.exists():
        try:
            return json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_CONFIG.copy()

def save_config(cfg):
    CONFIG_FILE.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding="utf-8")

def load_seen():
    if SEEN_FILE.exists():
        try:
            return set(json.loads(SEEN_FILE.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()

def save_seen(seen):
    SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), encoding="utf-8")

def get_notify_chat_ids():
    env = load_env_file()
    raw = os.environ.get("NOTIFY_CHAT_IDS") or env.get("NOTIFY_CHAT_IDS", "")
    return [c.strip() for c in raw.split(",") if c.strip()]

def add_notify_chat_id(new_id):
    env = load_env_file()
    ids = [c.strip() for c in env.get("NOTIFY_CHAT_IDS", "").split(",") if c.strip()]
    if new_id not in ids:
        ids.append(new_id)
        env["NOTIFY_CHAT_IDS"] = ",".join(ids)
        save_env_file(env)
        return True
    return False

seen_lots = load_seen()

def get_headers():
    return {"User-Agent": random.choice(USER_AGENTS), "Accept": "application/json", "Accept-Language": "ru-RU,ru;q=0.9,en;q=0.8", "Content-Type": "application/json", "Origin": "https://playerok.com", "Referer": "https://playerok.com/apps/telegram/stars", "Sec-Fetch-Dest": "empty", "Sec-Fetch-Mode": "cors", "Sec-Fetch-Site": "same-origin"}

async def fetch_lots(client, filters_cfg=None):
    filters_cfg = filters_cfg or {}
    variables = {"pagination": {"first": 40, "after": None}, "filters": {"gameSlug": "telegram", "categorySlug": "stars"}, "sort": "CREATED_AT_DESC"}
    if filters_cfg.get("star_amounts"):
        variables["filters"]["dataFieldsFilter"] = [{"fieldId": "count", "value": str(amt)} for amt in filters_cfg["star_amounts"]]
    if filters_cfg.get("max_price"):
        variables["filters"]["priceRange"] = {"max": float(filters_cfg["max_price"])}
    if filters_cfg.get("by_username_only"):
        variables["filters"]["obtainingTypeSlug"] = "username"
    elif filters_cfg.get("by_gift_only"):
        variables["filters"]["obtainingTypeSlug"] = "gift"
    payload = {"operationName": "items", "extensions": {"persistedQuery": {"version": 1, "sha256Hash": ITEMS_HASH}}, "variables": variables}
    try:
        resp = await client.post(PLAYEROK_GRAPHQL, json=payload, headers=get_headers(), timeout=20)
        resp.raise_for_status()
        data = resp.json()
        if "errors" in data:
            return []
        return [e["node"] for e in data.get("data", {}).get("items", {}).get("edges", []) if e.get("node")]
    except Exception as e:
        logger.error("Fetch error: %s", e)
        return []

def apply_local_filters(lots, filters_cfg):
    user = (filters_cfg.get("username_filter") or "").lower() or None
    out = []
    for lot in lots:
        if user and user not in ((lot.get("user") or {}).get("username", "").lower()):
            continue
        out.append(lot)
    return out

def format_lot_message(lot):
    lot_id = lot.get("id", "?")
    slug = lot.get("slug", lot_id)
    price = lot.get("price", "?")
    raw_price = lot.get("rawPrice", price)
    seller = (lot.get("user") or {}).get("username", "Неизвестен")
    obtaining = (lot.get("obtainingType") or {}).get("name", "") or (lot.get("category") or {}).get("name", "")
    stars_count = ""
    for field in lot.get("dataFields") or []:
        if field.get("id") in ("count", "stars_count", "amount"):
            stars_count = field.get("value", "")
            break
    if not stars_count:
        m = re.search(r"\d+", lot.get("name", ""))
        stars_count = m.group(0) if m else "?"
    url = f"https://playerok.com/products/{slug}"
    return "\n".join([f"⭐ *Новый лот: {stars_count} Stars*", "", f"💰 Цена: *{price} ₽* (без комиссии: {raw_price} ₽)", f"👤 Продавец: `{seller}`", f"📦 Доставка: {obtaining or 'не указана'}", "", f"🔗 [Открыть лот]({url})", f"🆔 ID: `{lot_id}`"])

async def monitor_loop(bot):
    async with httpx.AsyncClient(follow_redirects=True, verify=True) as client:
        while True:
            cfg = load_config()
            if not cfg.get("enabled", False):
                await asyncio.sleep(5)
                continue
            lots = apply_local_filters(await fetch_lots(client, cfg.get("filters", {})), cfg.get("filters", {}))
            chat_ids = get_notify_chat_ids()
            for lot in lots:
                lot_id = str(lot.get("id", ""))
                if not lot_id or lot_id in seen_lots:
                    continue
                seen_lots.add(lot_id)
                save_seen(seen_lots)
                msg = format_lot_message(lot)
                for chat_id in chat_ids:
                    await bot.send_message(chat_id=chat_id, text=msg, parse_mode="Markdown", disable_web_page_preview=False)
            interval = cfg.get("interval", 30)
            await asyncio.sleep(max(interval + random.uniform(-(interval * 0.2), interval * 0.2), 5))

def is_admin(user_id):
    admins = load_config().get("admin_ids", [])
    return not admins or user_id in admins

def main():
    env = setup_wizard()
    token = env.get("BOT_TOKEN", "")
    if not token:
        sys.exit(1)
    bot = Bot(token=token)
    asyncio.run(monitor_loop(bot))

if __name__ == "__main__":
    main()