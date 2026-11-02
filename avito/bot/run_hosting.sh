#!/bin/sh
# Cron на хостинге reg.ru вызывает это раз в минуту. Бот работает одним процессом ~55 минут;
# flock не даёт запустить второй экземпляр, а если процесс упал — следующий вызов поднимет его в течение минуты.
APP="$HOME/AvitoSales"
LOG="$HOME/avito_bot.log"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 2000000 ]; then
    tail -c 500000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

cd "$APP" || exit 1
RUN_SECONDS=3300 exec flock -n "$HOME/.avito_bot.lock" "$APP/.venv/bin/python" avito/bot/main.py >> "$LOG" 2>&1
