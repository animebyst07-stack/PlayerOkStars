#!/bin/bash
# Установка PlayerOk Stars Bot в Termux
set -e

echo "=========================================="
echo "  PlayerOk Stars Bot — Установка в Termux"
echo "=========================================="

# Обновление пакетов
echo "[1/6] Обновление пакетов Termux..."
pkg update -y && pkg upgrade -y

# Установка Python
echo "[2/6] Установка Python..."
pkg install -y python python-pip

# Установка зависимостей Python
echo "[3/6] Установка зависимостей..."
pip install -r requirements.txt

# Создание .env если не существует
if [ ! -f .env ]; then
    echo "[4/6] Создание .env файла..."
    cp .env.example .env
    echo ""
    echo "⚠️  Отредактируй файл .env:"
    echo "    nano .env"
    echo ""
    echo "    Укажи BOT_TOKEN и NOTIFY_CHAT_IDS"
else
    echo "[4/6] Файл .env уже существует."
fi

# Создание скрипта запуска
echo "[5/6] Создание скрипта запуска..."
cat > run.sh << 'RUNEOF'
#!/bin/bash
set -e
chmod +x playerok.py 2>/dev/null || true
chmod +x run.sh 2>/dev/null || true
export $(grep -v '^#' .env | xargs)
python playerok.py
RUNEOF
chmod +x run.sh

# Создание скрипта для автозапуска через Termux:Boot
echo "[6/6] Настройка автозапуска (опционально)..."
mkdir -p ~/.termux/boot/
cat > ~/.termux/boot/playerok-stars.sh << BOOTEOF
#!/data/data/com.termux/files/usr/bin/bash
cd $(pwd)
export \$(grep -v '^#' .env | xargs)
python playerok.py >> bot.log 2>&1 &
BOOTEOF
chmod +x ~/.termux/boot/playerok-stars.sh

echo ""
echo "=========================================="
echo "✅ Установка завершена!"
echo "=========================================="
echo ""
echo "Следующие шаги:"
echo "1. Отредактируй .env: nano .env"
echo "2. Запусти бота: bash run.sh"
echo "   или: chmod +x run.sh && ./run.sh"
echo ""
echo "Автозапуск настроен через Termux:Boot"
echo "Установи Termux:Boot из F-Droid для автозапуска при старте телефона."
echo ""
