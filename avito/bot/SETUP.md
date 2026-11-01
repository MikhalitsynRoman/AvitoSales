# Настройка Avito-бота

Бот раз в ~10 минут проверяет новые сообщения на Avito, присылает вам в Telegram черновик ответа
по новому скрипту продаж, и отправляет его клиенту только после вашего подтверждения.

## 1. Создать Telegram-бота

1. Написать [@BotFather](https://t.me/BotFather) → `/newbot`
2. Дать имя и username (например `roman_avito_bot`)
3. Скопировать токен вида `7xxxxxxxxx:AAxxxxxxxxxxxxxxxx`
4. Написать [@userinfobot](https://t.me/userinfobot) — он пришлёт ваш числовой Telegram ID (это `ADMIN_ID`)

## 2. Настроить Google Таблицу

1. Зайти на [console.cloud.google.com](https://console.cloud.google.com), создать проект
2. Включить API: **Google Sheets API** и **Google Drive API**
3. **IAM и администрирование → Сервисные аккаунты → Создать сервисный аккаунт**
4. Создать ключ → JSON → скачать файл, переименовать в `service_account.json`
5. Создать новую Google Таблицу (пустую, с любым названием)
6. Из URL скопировать ID таблицы: `docs.google.com/spreadsheets/d/ВОТ_ЭТО/edit`
7. **Поделиться** таблицей → добавить email сервисного аккаунта (поле `client_email` в JSON-файле) с правами **Редактора**

Три листа («Обращения», «Записаны на пробный», «Состояние») бот создаст сам при первом запуске.

## 3. Выложить код на GitHub

```bash
cd "C:\Users\mihal\Desktop\сайт"
git init
git add .
git commit -m "Avito sales bot"
```

Создать пустой репозиторий на [github.com/new](https://github.com/new) (можно приватный), затем:

```bash
git remote add origin https://github.com/ВАШ_АККАУНТ/НАЗВАНИЕ_РЕПО.git
git branch -M main
git push -u origin main
```

## 4. Добавить секреты в GitHub

В репозитории: **Settings → Secrets and variables → Actions → New repository secret**. Добавить:

| Имя секрета | Значение |
| --- | --- |
| `BOT_TOKEN` | токен из шага 1 |
| `ADMIN_ID` | ваш Telegram ID из шага 1 |
| `AVITO_CLIENT_ID` | из `avito/.env` |
| `AVITO_CLIENT_SECRET` | из `avito/.env` |
| `SPREADSHEET_ID` | ID таблицы из шага 2 |
| `GOOGLE_SERVICE_ACCOUNT_JSON` | весь текст файла `service_account.json` целиком |

## 5. Проверить

Во вкладке **Actions** репозитория найти workflow `avito-bot` → **Run workflow** (кнопка справа) — запустит
цикл немедленно, не дожидаясь расписания. Дальше он будет сам срабатывать каждые ~10 минут.

Напишите боту в Telegram что угодно (не в ответ на уведомление) — это будет воспринято как список
свободного времени на сегодня, например: `15:00, 18:30`.

## Как это работает дальше

- Приходит новое сообщение от клиента на Avito → бот присылает вам черновик ответа и кнопку **«✅ Отправить черновик»**
- Хотите отправить другой текст — **ответьте (reply)** на то же сообщение бота своим текстом, он уйдёт клиенту вместо черновика
- Клиент записался на пробный урок → `/booked Имя | Телефон | Когда | Класс/цель | chat_id` (chat_id можно посмотреть в таблице «Обращения»)
- Каждое утро после 9:00 бот сам спросит, какое время свободно сегодня

## Локальный запуск для проверки (не обязательно)

```bash
cd avito/bot
cp .env.example .env   # заполнить значениями
mkdir credentials      # положить туда service_account.json
pip install -r requirements.txt
python main.py
```
