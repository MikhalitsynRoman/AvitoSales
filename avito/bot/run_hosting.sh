#!/bin/sh
# Cron на хостинге reg.ru вызывает это раз в минуту. Бот работает одним процессом ~55 минут;
# flock не даёт запустить второй экземпляр, а если процесс упал — следующий вызов поднимет его в течение минуты.
APP="$HOME/AvitoSales"
LOG="$HOME/avito_bot.log"

if [ -f "$LOG" ] && [ "$(stat -c%s "$LOG")" -gt 2000000 ]; then
    tail -c 500000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

cd "$APP" || exit 1

# Новые зависимости (requirements.txt изменился) — ставим в фоне под своей блокировкой, бот при этом работает.
# Отметку о версии пишем только после успешной установки: если pip упал, попробуем снова через минуту.
REQ="$APP/avito/bot/requirements.txt"
REQ_HASH="$(md5sum "$REQ" | cut -d' ' -f1)"
if [ "$REQ_HASH" != "$(cat "$HOME/.avito_req_hash" 2>/dev/null)" ]; then
    (flock -n "$HOME/.avito_pip.lock" sh -c "\"$APP/.venv/bin/pip\" install -q -r \"$REQ\" >> \"$LOG\" 2>&1 \
        && echo $REQ_HASH > \"$HOME/.avito_req_hash\" && echo 'PIP_OK $REQ_HASH' >> \"$LOG\"") &
fi

RUN_SECONDS=3300 exec flock -n "$HOME/.avito_bot.lock" "$APP/.venv/bin/python" avito/bot/main.py >> "$LOG" 2>&1
