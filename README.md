# 🤖 Простой Telegram Бот (@L_keys_bot)

## 🎯 Описание
Это простая версия Telegram бота - виртуальная подружка для девушек.

**Токен бота**: `5530663886:AAEDAX6rgGm5ILLxpnJoG9pa4ZsS1x79pog`

## ✅ Функции:
- ✅ Текстовые сообщения с GPT-4o
- ✅ Голосовые сообщения (Whisper)
- ✅ База знаний с поиском
- ✅ Админ-панель
- ✅ Статистика использования
- ✅ Проактивные сообщения по расписанию
- ❌ **НЕТ распознавания изображений**

## 📁 Файлы:
- `k.py` - основной код бота
- `.env` - конфигурация (токены, API ключи)
- `requirements.txt` - Python зависимости
- `telegram-bot.service` - файл автозапуска
- `Aptfile` - системные зависимости

## 🚀 Деплой в `/root/home/simpleBot/`:

### 1. WinSCP - Загрузка файлов:
1. Подключитесь к серверу через WinSCP
2. Создайте папку `/root/home/` (если её нет)
3. Создайте папку `/root/home/simpleBot/`
4. Загрузите ВСЕ файлы из папки `simple-bot/` в `/root/home/simpleBot/`

### 2. PuTTY - Установка:
```bash
# Обновление системы
sudo apt update && sudo apt upgrade -y

# Установка системных пакетов
sudo apt install -y python3 python3-pip python3-venv python3-dev
sudo apt install -y postgresql postgresql-contrib
sudo apt install -y tesseract-ocr tesseract-ocr-rus tesseract-ocr-eng
sudo apt install -y build-essential

# Создание пользователя для бота
sudo useradd -r -m -s /bin/bash botuser

# Создание рабочей директории
sudo mkdir -p /opt/telegram-bot-simple
sudo chown botuser:botuser /opt/telegram-bot-simple

# Копирование файлов из загруженной папки
sudo cp /root/home/simpleBot/* /opt/telegram-bot-simple/
sudo chown -R botuser:botuser /opt/telegram-bot-simple

# Переход в рабочую директорию
cd /opt/telegram-bot-simple

# Создание виртуального окружения
sudo -u botuser python3 -m venv venv
sudo -u botuser ./venv/bin/pip install --upgrade pip
sudo -u botuser ./venv/bin/pip install -r requirements.txt

# Настройка PostgreSQL
sudo -u postgres createuser --interactive --pwprompt botuser
# Введите пароль: bot123password
# Суперпользователь? n
# Создавать БД? y
# Создавать роли? n

sudo -u postgres createdb -O botuser telegram_bot_simple

# Установка systemd service
sudo cp telegram-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable telegram-bot
sudo systemctl start telegram-bot

# Проверка статуса
sudo systemctl status telegram-bot
```

### 3. Проверка работы:
```bash
# Статус бота
systemctl status telegram-bot

# Логи бота в реальном времени
journalctl -u telegram-bot -f

# Последние 50 строк логов
journalctl -u telegram-bot -n 50
```

## 📊 Управление:
```bash
# Статус бота
systemctl status telegram-bot

# Логи бота
journalctl -u telegram-bot -f

# Перезапуск
systemctl restart telegram-bot

# Остановка
systemctl stop telegram-bot
```

## ⚙️ Конфигурация:
Все настройки в файле `.env` уже готовы!

## 🧪 Тестирование:
1. Напишите боту текстовое сообщение - должен ответить
2. Отправьте голосовое - должен распознать и ответить  
3. Отправьте изображение - скажет что не поддерживает

## 🛠️ Диагностика проблем:

### Если бот не отвечает:
```bash
# Проверить статус сервиса
systemctl status telegram-bot

# Если сервис не запущен
sudo systemctl start telegram-bot

# Если есть ошибки, посмотреть логи
journalctl -u telegram-bot -n 100
```

### Частые проблемы:

**1. Ошибка с базой данных:**
```bash
# Проверить подключение к PostgreSQL
sudo -u postgres psql
\l  # должна быть база telegram_bot_simple
\q

# Если базы нет - создать заново
sudo -u postgres createdb -O botuser telegram_bot_simple
```

**2. Ошибка с Python зависимостями:**
```bash
cd /opt/telegram-bot-simple
sudo -u botuser ./venv/bin/pip install -r requirements.txt
```

**3. Ошибка прав доступа:**
```bash
sudo chown -R botuser:botuser /opt/telegram-bot-simple
sudo chmod +x /opt/telegram-bot-simple/k.py
```

**4. Проверка токена и API:**
```bash
# Проверить .env файл
cat /opt/telegram-bot-simple/.env
# Должен содержать правильные токены
```

### Полезные команды:
```bash
# Перезапуск бота
sudo systemctl restart telegram-bot

# Остановка бота  
sudo systemctl stop telegram-bot

# Отключить автозапуск
sudo systemctl disable telegram-bot

# Включить автозапуск
sudo systemctl enable telegram-bot

# Просмотр использования ресурсов
ps aux | grep python | grep k.py
```