import json
import os
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

from config import ADMIN_ID  # noqa: E402  (грузит avito/bot/.env до импорта клиента Avito)
import client as avito_client  # noqa: E402
import sheets  # noqa: E402
import telegram_api as tg  # noqa: E402
from script_engine import classify_request, generate_variants  # noqa: E402

MSK = timezone(timedelta(hours=3))
MAX_CHATS_TO_SCAN = 40
AVITO_CHECK_INTERVAL = 60
# 0 = один проход (локальный запуск); в GitHub Actions задаётся ~270 секунд
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "0") or "0")


def today_str() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y")


def today_slots() -> str:
    if not _same_day(sheets.get_state("slots_date", ""), today_str()):
        return ""
    return sheets.get_state("slots_text", "")


# ---------- варианты ответа ----------

def _variants_for(row_id: str, values: dict) -> list[str]:
    stored = sheets.get_state(f"var_{row_id}", "")
    if stored:
        return json.loads(stored)
    variants = generate_variants(values["Имя"], values["Сообщение клиента"], today_slots())
    sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
    return variants


def _variant_screen(row_id: str, variants: list[str], idx: int) -> tuple[str, dict]:
    n = len(variants)
    text = f"Вариант {idx + 1} из {n}:\n\n{variants[idx]}"
    markup = tg.keyboard_rows([
        [("◀️", f"nav:{row_id}:{(idx - 1) % n}"), (f"{idx + 1}/{n}", "noop"), ("▶️", f"nav:{row_id}:{(idx + 1) % n}")],
        [("✅ Отправить этот вариант", f"sv:{row_id}:{idx}")],
        [("✍️ Написать свой текст", f"own:{row_id}")],
    ])
    return text, markup


def _send_to_client(user_id: int, row_id: str, text: str, status: str) -> str:
    contact = sheets.get_contact_by_id(row_id)
    if not contact:
        return "Не нашёл эту запись"
    v = contact["values"]
    if v["Статус"].startswith("отправлено"):
        return "Уже отправлено раньше"
    try:
        avito_client.send_message(user_id, v["chat_id"], text)
    except Exception as e:
        sheets.update_contact_status(row_id, f"ошибка: {e}")
        return "Ошибка отправки, смотри таблицу"
    sheets.update_contact_status(row_id, status, new_draft=text)
    if v.get("telegram_msg_id"):
        tg.edit_message_reply_markup(ADMIN_ID, int(v["telegram_msg_id"]), None)
    return "Отправлено клиенту ✅"


# ---------- Telegram ----------

def handle_callback(user_id: int, cq: dict) -> None:
    data = cq.get("data", "")
    msg = cq.get("message") or {}
    chat_id = msg.get("chat", {}).get("id")
    message_id = msg.get("message_id")

    if chat_id != ADMIN_ID:
        tg.answer_callback_query(cq["id"])
        return

    if data == "noop":
        tg.answer_callback_query(cq["id"])
        return

    if data.startswith("send:"):
        row_id = data.split(":", 1)[1]
        contact = sheets.get_contact_by_id(row_id)
        if not contact:
            tg.answer_callback_query(cq["id"], "Не нашёл эту запись")
            return
        result = _send_to_client(user_id, row_id, contact["values"]["Черновик ответа"], "отправлено")
        tg.answer_callback_query(cq["id"], result)
        return

    if data.startswith("edit:"):
        row_id = data.split(":", 1)[1]
        contact = sheets.get_contact_by_id(row_id)
        if not contact:
            tg.answer_callback_query(cq["id"], "Не нашёл эту запись")
            return
        variants = _variants_for(row_id, contact["values"])
        text, markup = _variant_screen(row_id, variants, 0)
        tg.send_message(ADMIN_ID, text, reply_markup=markup)
        tg.answer_callback_query(cq["id"])
        return

    if data.startswith("nav:"):
        _, row_id, idx = data.split(":")
        contact = sheets.get_contact_by_id(row_id)
        if not contact:
            tg.answer_callback_query(cq["id"], "Не нашёл эту запись")
            return
        variants = _variants_for(row_id, contact["values"])
        text, markup = _variant_screen(row_id, variants, int(idx))
        tg.edit_message_text(ADMIN_ID, message_id, text, markup)
        tg.answer_callback_query(cq["id"])
        return

    if data.startswith("sv:"):
        _, row_id, idx = data.split(":")
        contact = sheets.get_contact_by_id(row_id)
        if not contact:
            tg.answer_callback_query(cq["id"], "Не нашёл эту запись")
            return
        variants = _variants_for(row_id, contact["values"])
        chosen = variants[int(idx)]
        result = _send_to_client(user_id, row_id, chosen, f"отправлено (вариант {int(idx) + 1})")
        tg.answer_callback_query(cq["id"], result)
        if result.startswith("Отправлено"):
            tg.edit_message_text(ADMIN_ID, message_id, f"Отправлено клиенту ✅\n\n{chosen}", {"inline_keyboard": []})
        return

    if data.startswith("own:"):
        row_id = data.split(":", 1)[1]
        prompt = tg.send_message(
            ADMIN_ID, "Напишите свой текст ответом (reply) на ЭТО сообщение — отправлю его клиенту."
        )
        sheets.set_state(f"reply_{prompt.get('message_id')}", row_id)
        tg.answer_callback_query(cq["id"])
        return

    tg.answer_callback_query(cq["id"])


def handle_message(user_id: int, msg: dict) -> None:
    if msg.get("chat", {}).get("id") != ADMIN_ID:
        return
    text = (msg.get("text") or "").strip()
    if not text or text == "/start":
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
        row_id = sheets.find_row_id_by_telegram_msg_id(replied_id) or sheets.get_state(f"reply_{replied_id}", "")
        if not row_id:
            tg.send_message(ADMIN_ID, "Не нашёл, к какому обращению это относится.")
            return
        result = _send_to_client(user_id, row_id, text, "отправлено (свой текст)")
        tg.send_message(ADMIN_ID, result)
        return

    sheets.set_state("slots_text", text)
    sheets.set_state("slots_date", today_str())
    tg.send_message(ADMIN_ID, f"Принял свободное время на сегодня: {text}")


def process_telegram_updates(user_id: int, offset: int, timeout: int) -> int:
    updates = tg.get_updates(offset=offset + 1 if offset else None, timeout=timeout)
    new_offset = offset
    for upd in updates:
        new_offset = max(new_offset, upd["update_id"])
        try:
            if "callback_query" in upd:
                handle_callback(user_id, upd["callback_query"])
            elif "message" in upd:
                handle_message(user_id, upd["message"])
        except Exception:
            print("ERROR handling update:", traceback.format_exc())
    if new_offset != offset:
        sheets.set_state("telegram_last_update_id", str(new_offset))
    return new_offset


# ---------- Avito ----------

def _same_day(value: str, today: str) -> bool:
    # Таблица может отдать "2.11.2026" вместо "02.11.2026" — сравниваем числа, а не строки
    try:
        return [int(p) for p in value.strip().split(".")] == [int(p) for p in today.split(".")]
    except ValueError:
        return False


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
    tg.send_message(
        ADMIN_ID,
        "Доброе утро! Какое время сегодня свободно для пробных уроков?\n"
        "Напишите через запятую, например: 15:00, 18:30",
    )
    sheets.set_state("slots_prompt_date", today)


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

        notif_text = (
            f"Новое сообщение на Avito\n"
            f"От: {name} ({city})\n"
            f"Категория: {category}\n"
            f"Текст: {client_text}\n\n"
            f"Черновик ответа:\n{draft}"
        )
        sent = tg.send_message(ADMIN_ID, notif_text)
        telegram_msg_id = sent.get("message_id")
        print(f"NEW_MESSAGE: {name} ({city}) -> notified")

        row_id = sheets.log_contact(chat["id"], avito_msg_id, name, city, category, client_text, draft, telegram_msg_id)
        sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
        seen_ids.add(avito_msg_id)

        tg.edit_message_reply_markup(
            ADMIN_ID, telegram_msg_id, tg.inline_keyboard([
                ("✅ Отправить", f"send:{row_id}"),
                ("❌ Исправить", f"edit:{row_id}"),
            ])
        )

    if not bootstrapped:
        sheets.set_state("bootstrapped", "1")


# ---------- главный цикл ----------

def _now() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y %H:%M:%S")


def _code_signature() -> tuple:
    here = os.path.dirname(os.path.abspath(__file__))
    files = [os.path.join(here, f) for f in os.listdir(here) if f.endswith(".py")]
    files.append(os.path.join(here, "..", "client.py"))
    return tuple(sorted((f, os.path.getmtime(f)) for f in files if os.path.exists(f)))


def main() -> None:
    host = os.uname().nodename if hasattr(os, "uname") else "local"
    sheets.set_state("last_start", f"{_now()} ({host})")
    user_id = avito_client.get_self_account()["id"]
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
        try:
            poll_timeout = int(max(0, min(25, remaining))) if RUN_SECONDS else 0
            offset = process_telegram_updates(user_id, offset, poll_timeout)

            if time.time() - last_avito_check >= AVITO_CHECK_INTERVAL:
                maybe_ask_daily_slots()
                check_new_avito_messages(user_id)
                last_avito_check = time.time()
                sheets.set_state("last_alive", f"{_now()} ({host})")
        except Exception:
            print("ERROR in cycle:", traceback.format_exc())
            time.sleep(5)

        if time.time() >= deadline:
            break
    sheets.set_state("last_end", _now())


if __name__ == "__main__":
    try:
        main()
    except Exception:
        err = traceback.format_exc()
        print("ERROR fatal:", err)
        try:
            sheets.set_state("last_error", f"{_now()}: {err[-600:]}")
        except Exception:
            pass
        raise
