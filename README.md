# iru-bot

Telegram-бот для [ИРУ](https://github.com/GiviSamali/IRU) — системы управления ПК голосом и текстом.

## Возможности

- 📋 Подача заявки на получение токена доступа (3-шаговая форма)
- 🔔 Уведомление администратора о новых заявках
- ✅ Одобрение / ❌ отклонение заявок прямо из Telegram
- 👤 Просмотр профиля и плана пользователя
- 🗄 Прямая работа с `iru.db` без дополнительного API

## Структура

```
├── bot.py           # Основной код бота (aiogram 3)
├── config.py        # Конфиг через переменные окружения
├── requirements.txt
├── .env.example     # Шаблон переменных окружения
└── iru-bot.service  # systemd unit
```

## Установка на сервер

```bash
# 1. Клонировать
git clone https://github.com/GiviSamali/iru-bot.git /opt/iru/bot
cd /opt/iru/bot

# 2. Установить зависимости в существующий venv IRU
/opt/iru/venv/bin/pip install aiogram==3.7.0

# 3. Создать .env
cp .env.example .env
nano .env  # заполнить токен и admin TG ID

# 4. Установить systemd сервис
cp iru-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable iru-bot
systemctl start iru-bot
systemctl status iru-bot
```

## Переменные окружения

| Переменная | Описание | Пример |
|---|---|---|
| `IRU_BOT_TOKEN` | Токен бота от @BotFather | `1234567890:ABCdef...` |
| `IRU_ADMIN_TG_ID` | Твой Telegram ID (число) | `123456789` |
| `IRU_DB_PATH` | Путь к базе данных ИРУ | `/opt/iru/app/server/iru.db` |
| `IRU_SERVER_URL` | URL сервера ИРУ | `http://localhost:8000` |

## Как получить свой Telegram ID

Напиши боту [@userinfobot](https://t.me/userinfobot) — он пришлёт твой числовой ID.

## Команды бота

| Команда / Кнопка | Доступ | Описание |
|---|---|---|
| `/start` | Все | Приветствие, определение статуса |
| `📋 Подать заявку` | Незарегистрированные | Форма заявки на доступ |
| `👤 Мой профиль` | Зарегистрированные | Просмотр плана и роли |
| `ℹ️ Что такое ИРУ?` | Все | Описание системы |
| `/admin` | Только admin | Панель управления |
