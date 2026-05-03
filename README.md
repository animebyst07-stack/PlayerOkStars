# PlayerOk Stars Bot 🌟

Telegram бот для мониторинга лотов **Telegram Stars** на [playerok.com](https://playerok.com/apps/telegram/stars).

## Возможности

- 🔍 Автоматическая проверка новых лотов с заданным интервалом
- ⭐ Фильтр по количеству звёзд (50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000)
- 💰 Фильтр по максимальной цене (₽)
- 👤 Фильтр по username продавца
- 📦 Фильтр по способу доставки (по username / Подарком)
- 🔔 Уведомления в личку и/или канал/группу
- 🛡 Анти-бан: ротация User-Agent, случайные задержки
- 💾 История просмотренных лотов (повторно не присылает)
- 🎛 Полное управление через Telegram команды

## Установка в Termux

### Быстрая установка
```bash
# Клонируй репозиторий
pkg install -y git
git clone https://github.com/YOUR_USERNAME/PlayerOkStars.git
cd PlayerOkStars

# Запусти установщик
bash install.sh
```

### Ручная установка
```bash
pkg update -y && pkg upgrade -y
pkg install -y python python-pip
pip install -r requirements.txt
cp .env.example .env
nano .env  # заполни BOT_TOKEN и NOTIFY_CHAT_IDS
```

## Настройка

Отредактируй `.env`:
```env
BOT_TOKEN=токен_от_BotFather
NOTIFY_CHAT_IDS=твой_chat_id,-1001234567890
```

**Как узнать Chat ID:**
- Личный: напиши боту [@userinfobot](https://t.me/userinfobot)
- Канал/группа: добавь бота как администратора

## Запуск

```bash
bash run.sh
```

Или напрямую:
```bash
export $(grep -v '^#' .env | xargs)
python bot.py
```

## Команды бота

| Команда | Описание |
|---------|----------|
| `/start` | Главное меню |
| `/status` | Статус и фильтры |
| `/monitor on\|off` | Вкл/выкл мониторинг |
| `/interval 30` | Интервал проверки (сек) |
| `/filter` | Настройка фильтров (меню) |
| `/filters` | Показать текущие фильтры |
| `/clearfilters` | Сбросить фильтры |
| `/clearseen` | Очистить историю лотов |
| `/test` | Тестовый запрос к PlayerOk |
| `/help` | Справка |

## Количества звёзд на PlayerOk

50, 75, 100, 150, 200, 250, 300, 350, 400, 500, 750, 1000, 1500, 2000, 2500, 3000, 5000

## Автозапуск в Termux

Установи [Termux:Boot](https://f-droid.org/packages/com.termux.boot/) из F-Droid.
Скрипт автозапуска создаётся автоматически при `bash install.sh`.

## Защита от блокировок

- Ротация User-Agent браузеров
- Случайные задержки ±20% от интервала
- Реалистичные HTTP-заголовки (Origin, Referer, Sec-Fetch-*)
- Если PlayerOk заблокирует — увеличь интервал (рекомендую 60+ сек)

## Структура файлов

```
PlayerOkStars/
├── bot.py          # Основной файл бота
├── requirements.txt
├── install.sh      # Установщик для Termux
├── run.sh          # Скрипт запуска
├── .env.example    # Пример конфига
├── .env            # Твой конфиг (не в git)
├── config.json     # Настройки бота (создаётся автоматически)
└── seen_lots.json  # История лотов (создаётся автоматически)
```
