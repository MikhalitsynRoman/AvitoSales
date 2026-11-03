import json
import os
import re
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from config import ADMIN_ID  # noqa: E402  (грузит avito/bot/.env до импорта клиента Avito)
import client as avito_client  # noqa: E402
import sheets  # noqa: E402
import telegram_api as tg  # noqa: E402
from script_engine import TIME_RE, classify_request, generate_variants  # noqa: E402

MSK = timezone(timedelta(hours=3))
MAX_CHATS_TO_SCAN = 40
AVITO_CHECK_INTERVAL = 60
# 0 = один проход (локальный запуск); на хостинге задаётся ~3300 секунд
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "0") or "0")

HELP_TEXT = (
    "Что я умею:\n"
    "• Присылаю каждое новое сообщение с Avito с кнопками «✅ Отправить» / «❌ Исправить».\n"
    "• Свободное время на сегодня — просто напишите часы: 15:00, 18:30\n"
    "• Свой ответ клиенту — ответьте (reply) на карточку клиента или просто напишите текст, "
    "я спрошу кнопками, кому его отправить.\n\n"
    "Команды:\n"
    "/status — работает ли бот, заявки за сегодня, что отправлено\n"
    "/booked Имя | Телефон | Когда | Класс | chat_id — записать на пробный"
)

# Статистика процесса для /status (в памяти, сбрасывается при перезапуске)
_stats = {"started": "", "host": "", "last_check": 0.0, "check_secs": 0.0, "avito_fails": 0, "errors": []}
_variant_msgs = {}  # row_id -> message_id уже открытого экрана вариантов (чтобы не слать его повторно)


def _now() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y %H:%M:%S")


def _hhmm() -> str:
    return datetime.now(MSK).strftime("%H:%M")


def today_str() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y")


def _same_day(value: str, today: str) -> bool:
    # Таблица может отдать "2.11.2026" вместо "02.11.2026" — сравниваем числа, а не строки
    try:
        return [int(p) for p in value.strip().split(".")] == [int(p) for p in today.split(".")]
    except ValueError:
        return False


def today_slots() -> str:
    if not _same_day(sheets.get_state("slots_date", ""), today_str()):
        return ""
    return sheets.get_state("slots_text", "")


def _note_error(where: str, err) -> None:
    _stats["errors"] = (_stats["errors"] + [f"{_hhmm()} {where}: {tg.clean_error(err)[:200]}"])[-5:]


# ---------- карточки ----------

def _card_text(v: dict) -> str:
    return (
        f"Новое сообщение на Avito\n"
        f"От: {v['Имя']} ({v['Город']})\n"
        f"Категория: {v['Категория']}\n"
        f"Текст: {v['Сообщение клиента']}\n\n"
        f"Черновик ответа:\n{v['Черновик ответа']}"
    )


def _sent_card_text(v: dict, sent_text: str) -> str:
    return (
        f"✅ ОТПРАВЛЕНО КЛИЕНТУ в {_hhmm()}\n\n"
        f"{v['Имя']} ({v['Город']}) писал(а):\n{v['Сообщение клиента']}\n\n"
        f"Ваш ответ:\n{sent_text}"
    )


def _card_buttons(row_id: str) -> dict:
    return tg.inline_keyboard([("✅ Отправить", f"send:{row_id}"), ("❌ Исправить", f"edit:{row_id}")])


# ---------- варианты ответа ----------

def _variants_for(row_id: str, values: dict) -> list[str]:
    stored = sheets.get_state(f"var_{row_id}", "")
    if stored:
        return json.loads(stored)
    variants = generate_variants(values["Имя"], values["Сообщение клиента"], today_slots())
    sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
    return variants


def _variant_screen(row_id: str, name: str, variants: list[str], idx: int) -> tuple[str, dict]:
    n = len(variants)
    text = f"Ответ для {name} — вариант {idx + 1} из {n}:\n\n{variants[idx]}"
    markup = tg.keyboard_rows([
        [("◀️", f"nav:{row_id}:{(idx - 1) % n}"), (f"{idx + 1}/{n}", "noop"), ("▶️", f"nav:{row_id}:{(idx + 1) % n}")],
        [("✅ Отправить этот вариант", f"sv:{row_id}:{idx}")],
        [("✍️ Написать свой текст", f"own:{row_id}")],
    ])
    return text, markup


def _is_sent(v: dict) -> bool:
    return v["Статус"].startswith("отправлено")


def _send_to_client(user_id: int, row_id: str, text: str, status: str) -> bool:
    """Отправляет клиенту и всегда явно сообщает Роману результат: карточка становится «✅ ОТПРАВЛЕНО» или приходит «❌ НЕ ОТПРАВЛЕНО»."""
    contact = sheets.get_contact_by_id(row_id)
    if not contact:
        tg.send_message(ADMIN_ID, "❌ НЕ ОТПРАВЛЕНО: не нашёл эту заявку в таблице.")
        return False
    v = contact["values"]
    if _is_sent(v):
        tg.send_message(ADMIN_ID, f"Клиенту {v['Имя']} уже отправлено раньше — второй раз не отправляю.")
        return False
    try:
        avito_client.send_message(user_id, v["chat_id"], text)
    except Exception as e:
        _note_error("отправка в Avito", e)
        sheets.update_contact_status(row_id, f"ошибка: {str(e)[:100]}")
        tg.send_message(
            ADMIN_ID,
            f"❌ НЕ ОТПРАВЛЕНО клиенту {v['Имя']}.\nПричина: {str(e)[:200]}\n\n"
            "Нажмите кнопку ещё раз через минуту или ответьте клиенту прямо в Avito.",
        )
        return False

    # клиент уже получил сообщение: сразу помечаем это в памяти, чтобы повторное нажатие не отправило второй раз,
    # даже если Telegram сейчас недоступен. Затем показываем результат, затем медленная запись в таблицу.
    sheets.mark_status_cached(row_id, status, text)
    try:
        _show_sent(row_id, v, text)
    finally:
        sheets.update_contact_status(row_id, status, new_draft=text)
    return True


def _show_sent(row_id: str, v: dict, text: str) -> None:
    if v.get("telegram_msg_id"):
        try:
            tg.edit_message_text(ADMIN_ID, int(v["telegram_msg_id"]), _sent_card_text(v, text), {"inline_keyboard": []})
        except Exception:
            tg.send_message(ADMIN_ID, _sent_card_text(v, text))
    else:
        tg.send_message(ADMIN_ID, _sent_card_text(v, text))
    screen_id = _variant_msgs.pop(row_id, None)
    if screen_id:
        try:
            tg.edit_message_text(ADMIN_ID, screen_id, f"✅ Отправлено клиенту {v['Имя']} — см. карточку выше.",
                                 {"inline_keyboard": []})
        except Exception:
            pass


# ---------- Telegram ----------

def handle_callback(user_id: int, cq: dict) -> None:
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")

    if chat_id != ADMIN_ID or data == "noop":
        tg.answer_callback_query(cq["id"])
        return

    action, _, rest = data.partition(":")

    if action in ("slots", "tcancel"):  # кнопки вопроса «Кому отправить этот текст?»
        tg.answer_callback_query(cq["id"])
        text = sheets.get_state(f"txt_{rest}", "")
        if action == "slots" and text:
            tg.edit_message_text(ADMIN_ID, message_id, f"🕐 Сохранил как свободное время: {text}", {"inline_keyboard": []})
            _save_slots(text)
        else:
            tg.edit_message_text(ADMIN_ID, message_id, f"✖️ Никому не отправлял:\n«{text}»", {"inline_keyboard": []})
        sheets.set_state(f"txt_{rest}", "")
        return

    row_id = rest.split(":")[0]
    contact = sheets.get_contact_by_id(row_id)  # из памяти — мгновенно
    if not contact:
        tg.answer_callback_query(cq["id"], "Не нашёл эту заявку")
        return
    v = contact["values"]

    if action in ("send", "sv", "edit", "to") and _is_sent(v):
        tg.answer_callback_query(cq["id"], "Этому клиенту уже отправлено ✅")
        return

    if action == "to":
        pid = rest.split(":")[1]
        text = sheets.get_state(f"txt_{pid}", "")
        if not text:
            tg.answer_callback_query(cq["id"], "Этот текст уже обработан")
            return
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
        sheets.set_state(f"txt_{pid}", "")  # второй тап по кнопке не отправит текст ещё раз
        if _send_to_client(user_id, row_id, text, "отправлено (свой текст)"):
            tg.edit_message_text(ADMIN_ID, message_id, f"✅ Отправлено: {v['Имя']} ({v['Город']})\n\n«{text}»",
                                 {"inline_keyboard": []})
        else:
            sheets.set_state(f"txt_{pid}", text)  # не ушло — кнопки остаются, можно нажать ещё раз
        return

    if action == "send":
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
        _send_to_client(user_id, row_id, v["Черновик ответа"], "отправлено")
        return

    if action == "edit":
        if row_id in _variant_msgs:
            tg.answer_callback_query(cq["id"], "Варианты уже открыты ниже ⬇️")
            return
        tg.answer_callback_query(cq["id"])
        variants = _variants_for(row_id, v)
        text, markup = _variant_screen(row_id, v["Имя"], variants, 0)
        sent = tg.send_message(ADMIN_ID, text, reply_markup=markup)
        _variant_msgs[row_id] = sent.get("message_id")
        return

    if action == "nav":
        tg.answer_callback_query(cq["id"])
        idx = int(rest.split(":")[1])
        variants = _variants_for(row_id, v)
        text, markup = _variant_screen(row_id, v["Имя"], variants, idx)
        tg.edit_message_text(ADMIN_ID, message_id, text, markup)
        _variant_msgs[row_id] = message_id
        return

    if action == "sv":
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
        idx = int(rest.split(":")[1])
        chosen = _variants_for(row_id, v)[idx]
        _variant_msgs[row_id] = message_id
        _send_to_client(user_id, row_id, chosen, f"отправлено (вариант {idx + 1})")
        return

    if action == "own":
        tg.answer_callback_query(cq["id"])
        prompt = tg.send_message(
            ADMIN_ID, f"Напишите свой текст для {v['Имя']} ответом (reply) на ЭТО сообщение — отправлю его клиенту."
        )
        sheets.set_state(f"reply_{prompt.get('message_id')}", row_id)
        return

    tg.answer_callback_query(cq["id"])


def status_text() -> str:
    lines = []
    ago = int(time.time() - _stats["last_check"]) if _stats["last_check"] else None
    healthy = ago is not None and ago < 5 * 60 and _stats["avito_fails"] == 0
    lines.append(("🟢 Бот работает" if healthy else "🟠 Бот запущен, но есть проблемы") + f" · {_stats['host']}")
    lines.append(f"Запущен: {_stats['started'][11:16]}")
    if ago is None:
        lines.append("Avito ещё не проверялся после запуска")
    else:
        lines.append(f"Avito проверен {ago} с назад (проверка заняла {_stats['check_secs']:.1f} с)")
    slots = today_slots()
    lines.append(f"Свободное время сегодня: {slots}" if slots else "Свободное время на сегодня не указано — пришлите часы, например 15:00, 18:30")

    today = today_str()
    todays = [c for c in sheets.all_contacts() if _same_day(c["Дата"], today) and c["Имя"] != "—"]
    lines.append("")
    lines.append(f"Заявки за сегодня: {len(todays)}")
    for c in todays:
        mark = "✅" if _is_sent(c) else ("❌" if c["Статус"].startswith("ошибка") else "⏳")
        lines.append(f"{mark} {c['Время']} {c['Имя']} ({c['Город']}) — {c['Статус']}")
    waiting = [c for c in sheets.all_contacts() if c["Статус"] == "ожидает" and not _same_day(c["Дата"], today)]
    if waiting:
        lines.append(f"\nЕщё ждут ответа с прошлых дней: {len(waiting)}")
        for c in waiting[-5:]:
            lines.append(f"⏳ {c['Дата']} {c['Имя']} ({c['Город']})")

    s = tg.stats
    lines.append("")
    lines.append(f"Связь с Telegram: запросов {s['calls']}, не прошло {s['fails']}, медленных (>3 с) {s['slow']}, "
                 f"самый долгий {s['max_secs']:.1f} с")
    if _stats["errors"]:
        lines.append("⚠️ Последние ошибки:")
        lines.extend(_stats["errors"])
    else:
        lines.append("Ошибок нет")
    return "\n".join(lines)


def _save_slots(text: str) -> None:
    sheets.set_state("slots_text", text)
    sheets.set_state("slots_date", today_str())
    tg.send_message(ADMIN_ID, f"Принял свободное время на сегодня: {text}\nБуду предлагать клиентам эти часы.")


def handle_message(user_id: int, msg: dict) -> None:
    if msg.get("chat", {}).get("id") != ADMIN_ID:
        return
    text = (msg.get("text") or "").strip()
    if not text:
        return

    if text in ("/start", "/help"):
        tg.send_message(ADMIN_ID, HELP_TEXT)
        return

    if text.startswith("/status"):
        tg.send_message(ADMIN_ID, status_text())
        return

    if text.startswith("/booked"):
        fields = [p.strip() for p in text[len("/booked"):].split("|")]
        fields += ["—"] * (5 - len(fields))
        name, phone, when, goal, chat_id = fields[:5]
        sheets.log_booking(name, phone, chat_id, when, goal)
        tg.send_message(ADMIN_ID, f"Записал в таблицу бронирований: {name}, {phone}, {when}")
        return

    reply_to = msg.get("reply_to_message")
    if reply_to:
        replied_id = reply_to.get("message_id")
        if str(replied_id) == sheets.get_state("slots_prompt_msg_id", ""):
            _save_slots(text)
            return
        row_id = sheets.find_row_id_by_telegram_msg_id(replied_id) or sheets.get_state(f"reply_{replied_id}", "")
        if not row_id:
            tg.send_message(ADMIN_ID, "Не понял, к какой заявке это ответ. Ответьте (reply) на карточку клиента.")
            return
        tg.send_message(ADMIN_ID, "Отправляю клиенту…")
        _send_to_client(user_id, row_id, text, "отправлено (свой текст)")
        return

    # Только часы («15:00, 18:30», «сегодня 13:00 и 15:00») — это свободное время. Любой другой текст скорее всего
    # ответ клиенту, написанный без reply, — спрашиваем кнопками, кому его отправить.
    leftover = re.sub(r"[^a-zа-яё]", "", TIME_RE.sub("", text.lower()))
    if TIME_RE.search(text) and len(leftover) <= 12:
        _save_slots(text)
        return
    _ask_whom(text)


def _pending_contacts(limit: int = 4) -> list[dict]:
    pending = [c for c in sheets.all_contacts()
               if c["Имя"] != "—" and (c["Статус"] == "ожидает" or c["Статус"].startswith("ошибка"))]
    return pending[::-1][:limit]


def _ask_whom(text: str) -> None:
    pending = _pending_contacts()
    has_times = bool(TIME_RE.search(text))
    if not pending and not has_times:
        tg.send_message(ADMIN_ID, "Не понял сообщение 🙂 Сейчас нет клиентов, которые ждут ответа.\n\n" + HELP_TEXT)
        return
    question = tg.send_message(ADMIN_ID, f"Кому отправить этот текст?\n\n«{text}»")
    pid = question.get("message_id")
    sheets.set_state(f"txt_{pid}", text)
    rows = [[(f"➡️ {c['Имя']} ({c['Город']}, {c['Дата'][:5]} {c['Время']})", f"to:{c['id']}:{pid}")] for c in pending]
    if has_times:
        rows.append([("🕐 Это моё свободное время на сегодня", f"slots:{pid}")])
    rows.append([("✖️ Никому, отмена", f"tcancel:{pid}")])
    tg.edit_message_reply_markup(ADMIN_ID, pid, tg.keyboard_rows(rows))


def process_telegram_updates(user_id: int, offset: int, timeout: int) -> int:
    updates = tg.get_updates(offset=offset + 1 if offset else None, timeout=timeout)
    new_offset = offset
    for upd in updates:
        new_offset = max(new_offset, upd["update_id"])
        # след каждого обработанного обновления — чтобы снаружи было видно, что бот получил и ответил
        if "callback_query" in upd:
            what = f"кнопка {upd['callback_query'].get('data', '')}"
        else:
            m = upd.get("message") or {}
            what = f"сообщение из чата {m.get('chat', {}).get('id')}: {(m.get('text') or '')[:40]}"
        try:
            if "callback_query" in upd:
                handle_callback(user_id, upd["callback_query"])
            elif "message" in upd:
                handle_message(user_id, upd["message"])
            sheets.set_state("last_update", f"{_now()} {what} → обработано")
        except Exception as e:
            print("ERROR handling update:", tg.clean_error(traceback.format_exc()))
            # лог на хостинге не виден снаружи — оставляем след в таблице и предупреждаем Романа
            _note_error(what, e)
            try:
                sheets.set_state("last_update_error",
                                 f"{_now()} [{what}] {tg.clean_error(traceback.format_exc())[-1500:]}")
                tg.send_message(ADMIN_ID, f"⚠️ Не получилось обработать ({what}): {tg.clean_error(e)[:200]}\n"
                                          "Попробуйте нажать ещё раз.")
            except Exception:
                pass
    if new_offset != offset:
        sheets.set_state("telegram_last_update_id", str(new_offset))
    return new_offset


# ---------- Avito ----------

_asked_today = ""  # страховка внутри процесса: даже если таблица подведёт, спросим не чаще раза в день


def maybe_ask_daily_slots() -> None:
    global _asked_today
    today = today_str()
    if _asked_today == today:
        return
    if _same_day(sheets.get_state("slots_prompt_date", ""), today) or _same_day(sheets.get_state("slots_date", ""), today):
        _asked_today = today
        return
    if datetime.now(MSK).hour < 9:
        return
    _asked_today = today
    prompt = tg.send_message(
        ADMIN_ID,
        "Доброе утро! Какое время сегодня свободно для пробных уроков?\n"
        "Напишите через запятую, например: 15:00, 18:30",
    )
    sheets.set_state("slots_prompt_date", today)
    sheets.set_state("slots_prompt_msg_id", str(prompt.get("message_id", "")))


def check_new_avito_messages(user_id: int) -> None:
    slots_text = today_slots()
    bootstrapped = sheets.get_state("bootstrapped", "") == "1"
    seen_ids = sheets.get_logged_msg_ids()

    chats = avito_client.get_all_chats(user_id, max_chats=MAX_CHATS_TO_SCAN)
    for chat in chats:
        last = chat.get("last_message") or {}
        if last.get("direction") != "in":
            continue
        avito_msg_id = last.get("id", "")
        if not avito_msg_id or avito_msg_id in seen_ids:
            continue

        if not bootstrapped:
            sheets.log_contact(
                chat["id"], avito_msg_id, "", "", "", "", "", None,
                status="пропущено (старое, до запуска бота)",
            )
            continue

        client_user = next((u for u in chat.get("users", []) if u["id"] != user_id), None)
        name = client_user["name"] if client_user else "—"
        item = chat.get("context", {}).get("value", {})
        city = (item.get("location") or {}).get("title", "—")
        client_text = (last.get("content") or {}).get("text", "")
        category = classify_request(client_text)
        variants = generate_variants(name, client_text, slots_text)
        draft = variants[0]

        # карточка уходит с кнопками сразу, а запись в таблицу — после
        row_id = sheets.new_row_id()
        card = {"Имя": name, "Город": city, "Категория": category, "Сообщение клиента": client_text,
                "Черновик ответа": draft}
        sent = tg.send_message(ADMIN_ID, _card_text(card), reply_markup=_card_buttons(row_id))
        print(f"NEW_MESSAGE: {name} ({city}) -> notified")
        sheets.log_contact(chat["id"], avito_msg_id, name, city, category, client_text, draft,
                           sent.get("message_id"), row_id=row_id)
        sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
        seen_ids.add(avito_msg_id)

    if not bootstrapped:
        sheets.set_state("bootstrapped", "1")


def _run_avito_check(user_id: int) -> None:
    t = time.time()
    try:
        maybe_ask_daily_slots()
        check_new_avito_messages(user_id)
    except Exception as e:
        print("ERROR avito check:", traceback.format_exc())
        _note_error("проверка Avito", e)
        _stats["avito_fails"] += 1
        if _stats["avito_fails"] == 3:  # одно предупреждение, а не каждую минуту
            try:
                tg.send_message(ADMIN_ID, f"⚠️ Уже 3 раза подряд не получается проверить Avito: {tg.clean_error(e)[:200]}")
            except Exception:
                pass
        return
    if _stats["avito_fails"] >= 3:
        tg.send_message(ADMIN_ID, "✅ Проверка Avito снова работает.")
    _stats["avito_fails"] = 0
    _stats["last_check"] = time.time()
    _stats["check_secs"] = time.time() - t
    s = tg.stats
    sheets.set_state("last_alive", f"{_now()} ({_stats['host']}) | Telegram: запросов {s['calls']}, "
                                   f"не прошло {s['fails']}, медленных {s['slow']}, макс {s['max_secs']:.1f} с")


# ---------- главный цикл ----------

def _code_signature() -> tuple:
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, f) for f in os.listdir(here) if f.endswith(".py")]
    files.append(os.path.join(here, "..", "client.py"))
    return tuple(sorted((f, os.path.getmtime(f)) for f in files if os.path.exists(f)))


def _telegram_diag() -> str:
    """Сколько занимает установка соединения с api.telegram.org с этой машины (по IPv4)."""
    import socket
    parts = []
    addrs = sorted({(a[0], a[4][0]) for a in socket.getaddrinfo("api.telegram.org", 443, type=socket.SOCK_STREAM)})
    for family, ip in addrs:
        res = []
        for _ in range(3):
            t = time.time()
            try:
                s = socket.socket(family, socket.SOCK_STREAM)
                s.settimeout(5)
                s.connect((ip, 443))
                s.close()
                res.append(f"{time.time() - t:.2f}с")
            except OSError as e:
                res.append(f"нет за {time.time() - t:.0f}с ({e.__class__.__name__})")
        parts.append(f"{ip}: {', '.join(res)}")
    return "; ".join(parts)


def main() -> None:
    host = os.uname().nodename if hasattr(os, "uname") else "local"
    sheets.set_state("tg_diag", f"{_now()} соединение с Telegram: {_telegram_diag()}")
    _stats["host"] = "хостинг reg.ru" if "reg.ru" in host else host
    _stats["started"] = _now()
    sheets.set_state("last_start", f"{_now()} ({host})")
    user_id = avito_client.get_self_account()["id"]
    sheets.get_logged_msg_ids()  # прогреваем кэш таблицы до первых нажатий
    sheets.set_state("last_run", f"{_now()} ({host})")
    offset = int(sheets.get_state("telegram_last_update_id", "0") or "0")
    deadline = time.time() + RUN_SECONDS
    until_second = os.getenv("RUN_UNTIL_SECOND")
    if until_second:
        now = time.time()
        deadline = max(now - now % 60 + int(until_second), now + 5)
    last_avito_check = 0.0
    code_sig = _code_signature()

    while True:
        if RUN_SECONDS and _code_signature() != code_sig:
            print("Код обновился — завершаюсь, cron перезапустит с новой версией")
            break
        remaining = deadline - time.time()
        # к следующей проверке Avito просыпаемся вовремя, но нажатия кнопок long-poll отдаёт мгновенно
        until_check = AVITO_CHECK_INTERVAL - (time.time() - last_avito_check)
        try:
            poll_timeout = int(max(0, min(25, remaining, until_check))) if RUN_SECONDS else 0
            offset = process_telegram_updates(user_id, offset, poll_timeout)
        except Exception as e:
            print("ERROR telegram poll:", tg.clean_error(traceback.format_exc()))
            _note_error("Telegram", e)
            time.sleep(2)

        if time.time() - last_avito_check >= AVITO_CHECK_INTERVAL:
            _run_avito_check(user_id)
            last_avito_check = time.time()

        if time.time() >= deadline:
            break
    sheets.set_state("last_end", _now())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = tg.clean_error(traceback.format_exc())
        print("ERROR fatal:", err)
        try:
            sheets.set_state("last_error", f"{_now()}: {err[-600:]}")
        except Exception:
            pass
        raise
