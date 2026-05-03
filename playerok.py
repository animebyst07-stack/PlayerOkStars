#!/usr/bin/env python3
  """PlayerOk Stars Monitor — standalone scraper."""

  import asyncio
  import json
  import os
  import random
  import re
  import signal
  import sys
  from datetime import datetime
  from pathlib import Path

  import httpx

  # ── ANSI palette ────────────────────────────────────────────────────────
  R    = "\033[0m"
  BOLD = "\033[1m"
  DIM  = "\033[2m"
  B    = "\033[94m"   # bright blue
  C    = "\033[96m"   # bright cyan
  W    = "\033[97m"   # bright white
  BL   = "\033[34m"   # dim-able blue
  RED  = "\033[91m"   # error red
  LN   = f"{DIM}{BL}{'─' * 54}{R}"
  LN2  = f"{B}{'═' * 54}{R}"

  def p(*a, **kw):
      print(*a, **kw, flush=True)

  def ts():
      return datetime.now().strftime("%H:%M:%S")

  def log(msg):
      p(f"  {DIM}{ts()}{R}  {W}{msg}{R}")

  def log_ok(msg):
      p(f"  {DIM}{ts()}{R}  {C}{msg}{R}")

  def log_err(msg):
      p(f"  {DIM}{ts()}{R}  {RED}ERR  {msg}{R}")

  # ── Paths ────────────────────────────────────────────────────────────────
  ENV_FILE    = Path(".env")
  CONFIG_FILE = Path("config.json")
  SEEN_FILE   = Path("seen_lots.json")

  PLAYEROK_GRAPHQL = "https://playerok.com/graphql"
  ITEMS_HASH = "63eefcfd813442882ad846360d925279bc376e8bc85a577ebefbee0f9c78b557"
  STAR_AMOUNTS = [50,75,100,150,200,250,300,350,400,500,750,1000,1500,2000,2500,3000,5000]
  USER_AGENTS = [
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
      "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
      "Mozilla/5.0 (Macintosh; Intel Mac OS X 14.4; rv:125.0) Gecko/20100101 Firefox/125.0",
  ]

  # ── .env ─────────────────────────────────────────────────────────────────
  def load_env() -> dict:
      result = {}
      if not ENV_FILE.exists():
          return result
      for line in ENV_FILE.read_text("utf-8").splitlines():
          line = line.strip()
          if not line or line.startswith("#") or "=" not in line:
              continue
          k, _, v = line.partition("=")
          result[k.strip()] = v.strip()
      return result

  def save_env(data: dict):
      ENV_FILE.write_text(
          "\n".join(f"{k}={v}" for k, v in data.items()) + "\n",
          encoding="utf-8",
      )

  def get_chat_ids() -> list:
      env = load_env()
      raw = os.environ.get("NOTIFY_CHAT_IDS") or env.get("NOTIFY_CHAT_IDS", "")
      return [c.strip() for c in raw.split(",") if c.strip()]

  # ── Config ────────────────────────────────────────────────────────────────
  def load_config() -> dict:
      if CONFIG_FILE.exists():
          try:
              return json.loads(CONFIG_FILE.read_text("utf-8"))
          except Exception:
              pass
      return {"interval": 30, "filters": {}}

  def load_seen() -> set:
      if SEEN_FILE.exists():
          try:
              return set(json.loads(SEEN_FILE.read_text("utf-8")))
          except Exception:
              pass
      return set()

  def save_seen(seen: set):
      SEEN_FILE.write_text(json.dumps(list(seen), ensure_ascii=False), "utf-8")

  # ── Setup wizard ──────────────────────────────────────────────────────────
  def setup_wizard() -> dict:
      env = load_env()
      for k in ("BOT_TOKEN", "NOTIFY_CHAT_IDS"):
          if os.environ.get(k):
              env[k] = os.environ[k]

      if env.get("BOT_TOKEN") and env.get("NOTIFY_CHAT_IDS"):
          return env

      p()
      p(LN2)
      p(f"  {BOLD}{C}PLAYEROK STARS MONITOR{R}  {DIM}first run setup{R}")
      p(LN2)
      p()

      if not env.get("BOT_TOKEN"):
          p(f"  {DIM}{BL}Bot token{R}  (from @BotFather)")
          tok = input(f"  {DIM}>{R} ").strip()
          if not tok:
              p(f"  {RED}Token cannot be empty.{R}")
              sys.exit(1)
          env["BOT_TOKEN"] = tok
          p()

      if not env.get("NOTIFY_CHAT_IDS"):
          p(f"  {DIM}{BL}Chat ID{R}  (from @userinfobot, or leave empty)")
          cid = input(f"  {DIM}>{R} ").strip()
          env["NOTIFY_CHAT_IDS"] = cid
          p()

      save_env(env)
      p(f"  {C}Saved to .env{R}")
      p()
      return env

  # ── Headers ───────────────────────────────────────────────────────────────
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

  # ── PlayerOk API ─────────────────────────────────────────────────────────
  async def fetch_lots(client: httpx.AsyncClient, filters_cfg: dict) -> list:
      variables: dict = {
          "pagination": {"first": 40, "after": None},
          "filters": {"gameSlug": "telegram", "categorySlug": "stars"},
          "sort": "CREATED_AT_DESC",
      }
      star_amounts = filters_cfg.get("star_amounts") or []
      if star_amounts:
          variables["filters"]["dataFieldsFilter"] = [
              {"fieldId": "count", "value": str(a)} for a in star_amounts
          ]
      max_price = filters_cfg.get("max_price")
      if max_price:
          variables["filters"]["priceRange"] = {"max": float(max_price)}
      if filters_cfg.get("by_username_only"):
          variables["filters"]["obtainingTypeSlug"] = "username"
      elif filters_cfg.get("by_gift_only"):
          variables["filters"]["obtainingTypeSlug"] = "gift"

      payload = {
          "operationName": "items",
          "extensions": {"persistedQuery": {"version": 1, "sha256Hash": ITEMS_HASH}},
          "variables": variables,
      }
      try:
          resp = await client.post(
              PLAYEROK_GRAPHQL, json=payload, headers=get_headers(), timeout=20
          )
          resp.raise_for_status()
          data = resp.json()
          if "errors" in data:
              log_err(f"GraphQL: {data['errors']}")
              return []
          edges = data.get("data", {}).get("items", {}).get("edges", [])
          return [e["node"] for e in edges if e.get("node")]
      except httpx.HTTPStatusError as e:
          log_err(f"HTTP {e.response.status_code}")
          return []
      except Exception as e:
          log_err(f"Fetch: {e}")
          return []

  def apply_filters(lots: list, filters_cfg: dict) -> list:
      username_f = (filters_cfg.get("username_filter") or "").lower() or None
      result = []
      for lot in lots:
          if username_f:
              seller = (lot.get("user") or {}).get("username", "").lower()
              if username_f not in seller:
                  continue
          result.append(lot)
      return result

  def get_stars(lot: dict) -> str:
      for field in (lot.get("dataFields") or []):
          if field.get("id") in ("count", "stars_count", "amount"):
              return field.get("value", "?")
      m = re.search(r"\d+", lot.get("name", ""))
      return m.group(0) if m else "?"

  def format_terminal(lot: dict) -> str:
      stars   = get_stars(lot)
      price   = lot.get("price", "?")
      raw     = lot.get("rawPrice", price)
      seller  = (lot.get("user") or {}).get("username", "?")
      slug    = lot.get("slug", lot.get("id", ""))
      url     = f"https://playerok.com/products/{slug}"
      return (
          f"    {DIM}{BL}Stars{R}   {BOLD}{W}{stars}{R}  "
          f"{DIM}{BL}Price{R}  {C}{price} RUB{R}  {DIM}(no fee: {raw} RUB){R}\n"
          f"    {DIM}{BL}Seller{R}  {W}{seller}{R}\n"
          f"    {DIM}{BL}URL{R}     {DIM}{url}{R}"
      )

  def format_tg(lot: dict) -> str:
      stars  = get_stars(lot)
      price  = lot.get("price", "?")
      raw    = lot.get("rawPrice", price)
      seller = (lot.get("user") or {}).get("username", "?")
      slug   = lot.get("slug", lot.get("id", ""))
      url    = f"https://playerok.com/products/{slug}"
      obtaining = (lot.get("obtainingType") or {}).get("name", "")
      return (
          f"New lot — {stars} Stars\n"
          f"Price: {price} RUB  (no fee: {raw} RUB)\n"
          f"Seller: {seller}\n"
          f"Delivery: {obtaining or 'not specified'}\n"
          f"{url}"
      )

  # ── Telegram send ─────────────────────────────────────────────────────────
  async def tg_send(client: httpx.AsyncClient, token: str, chat_id: str, text: str):
      try:
          await client.post(
              f"https://api.telegram.org/bot{token}/sendMessage",
              json={"chat_id": chat_id, "text": text, "disable_web_page_preview": False},
              timeout=10,
          )
      except Exception as e:
          log_err(f"Telegram -> {chat_id}: {e}")

  # ── Monitor loop ──────────────────────────────────────────────────────────
  async def monitor(bot_token: str, chat_ids: list, cfg: dict, stop: asyncio.Event):
      filters_cfg = cfg.get("filters", {})
      interval    = cfg.get("interval", 30)
      seen        = load_seen()

      async with httpx.AsyncClient(follow_redirects=True, verify=True) as client:
          first = True
          while not stop.is_set():
              log(f"Checking lots...  {DIM}(seen: {len(seen)}){R}")
              lots = await fetch_lots(client, filters_cfg)
              lots = apply_filters(lots, filters_cfg)

              new = []
              for lot in lots:
                  lid = str(lot.get("id", ""))
                  if not lid or lid in seen:
                      continue
                  seen.add(lid)
                  save_seen(seen)
                  if not first:
                      new.append(lot)

              if new:
                  for lot in new:
                      p()
                      p(f"  {BOLD}{C}New lot!{R}")
                      p(format_terminal(lot))
                      p()
                      for cid in chat_ids:
                          await tg_send(client, bot_token, cid, format_tg(lot))
                  log_ok(f"Sent {len(new)} lot(s) to Telegram")
              else:
                  if first:
                      log(f"Seeded {len(lots)} lots. Watching for new ones...")
                  else:
                      log(f"No new lots  {DIM}({len(lots)} fetched){R}")

              first = False
              jitter = interval * 0.15
              wait = max(interval + random.uniform(-jitter, jitter), 10)
              try:
                  await asyncio.wait_for(stop.wait(), timeout=wait)
              except asyncio.TimeoutError:
                  pass

  # ── Entry point ───────────────────────────────────────────────────────────
  async def main():
      env        = setup_wizard()
      bot_token  = env.get("BOT_TOKEN", "")
      chat_ids   = get_chat_ids()
      cfg        = load_config()
      filters_f  = cfg.get("filters", {})
      stars_f    = filters_f.get("star_amounts") or []
      price_f    = filters_f.get("max_price")

      # ── Header ─────────────────────────────────────────────
      p()
      p(LN2)
      p(f"  {BOLD}{C}PLAYEROK STARS MONITOR{R}")
      p(f"  {DIM}playerok.com  ->  Telegram Stars{R}")
      p(LN2)
      p()

      # ── Verify token ───────────────────────────────────────
      bot_username = "?"
      async with httpx.AsyncClient(timeout=10) as client:
          try:
              r = await client.get(f"https://api.telegram.org/bot{bot_token}/getMe")
              d = r.json()
              if d.get("ok"):
                  bot_username = d["result"].get("username", "?")
              else:
                  p(f"  {RED}Invalid BOT_TOKEN: {d.get('description', '')}{R}")
                  sys.exit(1)
          except Exception as e:
              p(f"  {RED}Cannot reach Telegram: {e}{R}")
              p(f"  {DIM}Check internet connection and try again.{R}")
              sys.exit(1)

      # ── Config summary ─────────────────────────────────────
      lbl = f"{DIM}{BL}"
      val = f"{R}{W}"
      p(f"  {lbl}Bot     {val}@{bot_username}{R}")
      p(f"  {lbl}Chat    {val}{', '.join(chat_ids) if chat_ids else 'not set'}{R}")
      p(f"  {lbl}Stars   {val}{', '.join(map(str, stars_f)) if stars_f else 'all'}{R}")
      p(f"  {lbl}Price   {val}{f'max {price_f} RUB' if price_f else 'no limit'}{R}")
      p(f"  {lbl}Interval{val}{cfg.get('interval', 30)}s{R}")
      p()
      p(LN)
      p(f"  {DIM}Press Ctrl+C to stop{R}")
      p(LN)
      p()

      if not chat_ids:
          p(f"  {RED}Warning: no chat IDs set — notifications disabled{R}")
          p()

      stop = asyncio.Event()

      # Use signal module directly — works on Android/Termux
      def _stop(*_):
          p()
          p(f"  {DIM}Stopping...{R}")
          stop.set()

      try:
          signal.signal(signal.SIGINT,  _stop)
          signal.signal(signal.SIGTERM, _stop)
      except Exception:
          pass  # signal may not work in all envs, Ctrl+C still works via KeyboardInterrupt

      try:
          await monitor(bot_token, chat_ids, cfg, stop)
      except KeyboardInterrupt:
          _stop()

      p()
      p(f"  {DIM}Done.{R}")
      p()


  if __name__ == "__main__":
      asyncio.run(main())
  