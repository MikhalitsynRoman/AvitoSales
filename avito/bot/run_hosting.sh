#!/bin/sh
# Запускается cron'ом на хостинге reg.ru раз в минуту; запуск живёт ~40 с + старт, чтобы уложиться в минуту.
APP="$HOME/AvitoSales"
LOG="$HOME/avito_bot.log"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 2000000 ]; then
    tail -c 500000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

cd "$APP" || exit 1
RUN_SECONDS=40 RUN_UNTIL_SECOND=52 exec flock -n "$HOME/.avito_bot.lock" "$APP/.venv/bin/python" avito/bot/main.py >> "$LOG" 2>&1
