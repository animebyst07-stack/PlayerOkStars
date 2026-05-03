import asyncio
import json
import random
import signal
import sys
import os
from datetime import datetime
from typing import List, Dict, Optional, Any

import httpx

# Constants
PLAYEROK_GRAPHQL = "https://playerok.com/graphql"
ITEMS_HASH = "63eefcfd813442882ad846360d925279bc376e8bc85a577ebefbee0f9c78b557"
STAR_AMOUNTS = [50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000]

USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (X11; Linux i686; rv:125.0) Gecko/20100101 Firefox/125.0"
]

# UI Colors (ANSI)
CLR_BLUE = "\033[34m"
CLR_CYAN = "\033[36m"
CLR_WHITE_BRIGHT = "\033[97m"
CLR_BLUE_DIM = "\033[2;34m"
CLR_DIM = "\033[2m"
CLR_CYAN_BOLD = "\033[1;36m"
CLR_RED = "\033[31m"
CLR_RESET = "\033[0m"

class Config:
    def __init__(self):
        self.bot_token = ""
        self.notify_chat_ids = []
        self.interval = 30
        self.star_amounts = []
        self.max_price = 0.0
        self.username_filter = []
        self.by_username_only = False
        self.by_gift_only = False

def load_env():
    env = {}
    if os.path.exists(".env"):
        with open(".env", "r") as f:
            for line in f:
                if "=" in line:
                    key, val = line.strip().split("=", 1)
                    env[key] = val
    return env

def save_env(bot_token, chat_ids):
    with open(".env", "a" if os.path.exists(".env") else "w") as f:
        if not os.path.exists(".env") or "BOT_TOKEN" not in load_env():
            f.write(f"BOT_TOKEN={bot_token}\n")
            f.write(f"NOTIFY_CHAT_IDS={','.join(chat_ids)}\n")

def load_config():
    if os.path.exists("config.json"):
        with open("config.json", "r") as f:
            return json.load(f)
    return {}

async def setup_wizard():
    print(f"{CLR_BLUE}══════════════════════════════════════════════════════{CLR_RESET}")
    print(f"  {CLR_WHITE_BRIGHT}SETUP WIZARD{CLR_RESET}")
    print(f"{CLR_BLUE}══════════════════════════════════════════════════════{CLR_RESET}\n")
    
    bot_token = input(f"  {CLR_BLUE_DIM}Enter BOT_TOKEN:{CLR_RESET} ").strip()
    chat_ids_raw = input(f"  {CLR_BLUE_DIM}Enter NOTIFY_CHAT_IDS (comma separated):{CLR_RESET} ").strip()
    chat_ids = [c.strip() for c in chat_ids_raw.split(",") if c.strip()]
    
    save_env(bot_token, chat_ids)
    return bot_token, chat_ids

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
        "DNT": "1",
    }

async def fetch_lots(client: httpx.AsyncClient, star_amount: Optional[int], max_price: float) -> list[dict]:
    data_fields_filter = []
    if star_amount:
        data_fields_filter.append({"fieldId": "count", "value": str(star_amount)})
    
    variables = {
        "pagination": {"first": 40, "after": None},
        "filters": {
            "gameSlug": "telegram",
            "categorySlug": "stars",
            "dataFieldsFilter": data_fields_filter,
            "priceRange": {"max": max_price} if max_price > 0 else None,
        },
        "sort": "CREATED_AT_DESC",
    }

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

    resp = await client.post(
        PLAYEROK_GRAPHQL,
        json=payload,
        headers=get_headers(),
        timeout=25,
    )

    if resp.status_code != 200:
        return []

    data = resp.json()
    if "errors" in data:
        return []

    edges = data.get("data", {}).get("items", {}).get("edges", [])
    return [e["node"] for e in edges if e.get("node")]

async def send_telegram_msg(client: httpx.AsyncClient, token: str, chat_id: str, text: str):
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    try:
        await client.post(url, json={
            "chat_id": chat_id,
            "text": text,
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        })
    except Exception:
        pass

async def main():
    env = load_env()
    bot_token = env.get("BOT_TOKEN")
    notify_chat_ids = env.get("NOTIFY_CHAT_IDS", "").split(",")
    notify_chat_ids = [c.strip() for c in notify_chat_ids if c.strip()]

    if not bot_token or not notify_chat_ids:
        bot_token, notify_chat_ids = await setup_wizard()

    cfg_data = load_config()
    config = Config()
    config.bot_token = bot_token
    config.notify_chat_ids = notify_chat_ids
    config.interval = cfg_data.get("interval", 30)
    config.star_amounts = cfg_data.get("star_amounts", [])
    config.max_price = cfg_data.get("max_price", 0.0)
    config.username_filter = cfg_data.get("username_filter", [])
    config.by_username_only = cfg_data.get("by_username_only", False)
    config.by_gift_only = cfg_data.get("by_gift_only", False)

    stop_event = asyncio.Event()
    loop = asyncio.get_running_loop()

    def handle_stop():
        loop.call_soon_threadsafe(stop_event.set)

    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, handle_stop)

    async with httpx.AsyncClient(timeout=30) as client:
        # getMe check
        try:
            me_resp = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
            me_data = me_resp.json()
            if not me_data.get("ok"):
                print(f"{CLR_RED}Invalid BOT_TOKEN{CLR_RESET}")
                return
            bot_username = me_data["result"]["username"]
        except Exception as e:
            print(f"{CLR_RED}Failed to connect to Telegram: {e}{CLR_RESET}")
            return

        print(f"{CLR_BLUE}══════════════════════════════════════════════════════{CLR_RESET}")
        print(f"  {CLR_WHITE_BRIGHT}PLAYEROK STARS MONITOR{CLR_RESET}")
        print(f"  {CLR_DIM}playerok.com  ->  Telegram Stars{CLR_RESET}")
        print(f"{CLR_BLUE}══════════════════════════════════════════════════════{CLR_RESET}\n")

        print(f"  {CLR_BLUE_DIM}Bot{CLR_RESET}       {CLR_WHITE_BRIGHT}@{bot_username}{CLR_RESET}")
        print(f"  {CLR_BLUE_DIM}Chat{CLR_RESET}      {CLR_WHITE_BRIGHT}{', '.join(notify_chat_ids)}{CLR_RESET}")
        print(f"  {CLR_BLUE_DIM}Stars{CLR_RESET}     {CLR_WHITE_BRIGHT}{', '.join(map(str, config.star_amounts)) if config.star_amounts else 'all'}{CLR_RESET}")
        print(f"  {CLR_BLUE_DIM}Price{CLR_RESET}     {CLR_WHITE_BRIGHT}{config.max_price if config.max_price > 0 else 'no limit'}{CLR_RESET}")
        print(f"  {CLR_BLUE_DIM}Interval{CLR_RESET}  {CLR_WHITE_BRIGHT}{config.interval}s{CLR_RESET}\n")

        print(f"{CLR_BLUE_DIM}──────────────────────────────────────────────────────{CLR_RESET}")
        print(f"  {CLR_WHITE_BRIGHT}Press Ctrl+C to stop{CLR_RESET}")
        print(f"{CLR_BLUE_DIM}──────────────────────────────────────────────────────{CLR_RESET}\n")

        seen_ids = set()
        first_run = True

        while not stop_event.is_set():
            t_now = datetime.now().strftime("%H:%M:%S")
            print(f"  {CLR_DIM}{t_now}{CLR_RESET}  {CLR_WHITE_BRIGHT}Checking lots...{CLR_RESET}  {CLR_DIM}(seen: {len(seen_ids)}){CLR_RESET}")
            
            try:
                all_found_lots = []
                if not config.star_amounts:
                    all_found_lots = await fetch_lots(client, None, config.max_price)
                else:
                    for amt in config.star_amounts:
                        lots = await fetch_lots(client, amt, config.max_price)
                        all_found_lots.extend(lots)
                
                new_lots = []
                for lot in all_found_lots:
                    lot_id = lot["id"]
                    if lot_id not in seen_ids:
                        seen_ids.add(lot_id)
                        if not first_run:
                            # Apply filters
                            seller = (lot.get("user") or {}).get("username", "")
                            if config.username_filter and seller not in config.username_filter:
                                continue
                            if config.by_username_only and not seller:
                                continue
                            
                            # gift check - often in name or obtaining
                            name = lot.get("name", "").lower()
                            obtaining = ((lot.get("obtainingType") or {}).get("name", "") or "").lower()
                            is_gift = "gift" in name or "гифт" in name or "gift" in obtaining or "гифт" in obtaining
                            if config.by_gift_only and not is_gift:
                                continue
                                
                            new_lots.append(lot)
                
                if new_lots:
                    print(f"\n  {CLR_CYAN_BOLD}New lot!{CLR_RESET}")
                    for lot in new_lots:
                        stars = ""
                        for f in lot.get("dataFields", []):
                            if f.get("id") == "count":
                                stars = f.get("value", "")
                                break
                        
                        price = lot.get("price", "?")
                        raw_price = lot.get("rawPrice", price)
                        seller = (lot.get("user") or {}).get("username", "Unknown")
                        url = f"https://playerok.com/products/{lot.get('slug', lot['id'])}"
                        
                        print(f"    {CLR_BLUE_DIM}Stars{CLR_RESET}    {CLR_WHITE_BRIGHT}{stars}{CLR_RESET}    {CLR_BLUE_DIM}Price{CLR_RESET}   {CLR_WHITE_BRIGHT}{price} RUB{CLR_RESET}  {CLR_DIM}(no fee: {raw_price} RUB){CLR_RESET}")
                        print(f"    {CLR_BLUE_DIM}Seller{CLR_RESET}   {CLR_WHITE_BRIGHT}{seller}{CLR_RESET}")
                        print(f"    {CLR_BLUE_DIM}URL{CLR_RESET}      {CLR_WHITE_BRIGHT}{url}{CLR_RESET}\n")
                        
                        msg = (
                            f"New lot!\n"
                            f"Stars: {stars}\n"
                            f"Price: {price} RUB (no fee: {raw_price} RUB)\n"
                            f"Seller: {seller}\n"
                            f"URL: {url}"
                        )
                        for cid in config.notify_chat_ids:
                            await send_telegram_msg(client, config.bot_token, cid, msg)
                    
                    print(f"  {CLR_DIM}{datetime.now().strftime('%H:%M:%S')}{CLR_RESET}  {CLR_WHITE_BRIGHT}Sent {len(new_lots)} lot(s) to Telegram{CLR_RESET}")
                else:
                    if not first_run:
                        print(f"  {CLR_DIM}{t_now}{CLR_RESET}  {CLR_WHITE_BRIGHT}No new lots{CLR_RESET}  {CLR_DIM}({len(all_found_lots)} fetched){CLR_RESET}")
                
                first_run = False
            except Exception as e:
                print(f"  {CLR_RED}Error: {e}{CLR_RESET}")

            try:
                await asyncio.wait_for(stop_event.wait(), timeout=config.interval)
            except asyncio.TimeoutError:
                pass

    print(f"\n  {CLR_WHITE_BRIGHT}Stopping...{CLR_RESET}")

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
