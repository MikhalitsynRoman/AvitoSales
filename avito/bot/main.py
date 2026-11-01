import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.append(os.path.join(os.path.dirname(__file__), ".."))

import client as avito_client  # noqa: E402
import sheets  # noqa: E402
import telegram_api as tg  # noqa: E402
from config import ADMIN_ID  # noqa: E402
from script_engine import classify_request, generate_draft  # noqa: E402

MSK = timezone(timedelta(hours=3))
MAX_CHATS_TO_SCAN = 40


def today_str() -> str:
    return datetime.now(MSK).strftime("%d.%m.%Y")


def process_telegram_updates(user_id: int) -> None:
    last_update_id = int(sheets.get_state("telegram_last_update_id", "0") or "0")
    updates = tg.get_updates(offset=last_update_id + 1 if last_update_id else None)

    max_seen = last_update_id
    for upd in updates:
        max_seen = max(max_seen, upd["update_id"])

        if "callback_query" in upd:
            cq = upd["callback_query"]
            data = cq.get("data", "")
            if data.startswith("send:"):
                row_id = data.split(":", 1)[1]
                contact = sheets.get_contact_by_id(row_id)
                if not contact:
                    tg.answer_callback_query(cq["id"], "Не нашёл эту запись")
                    continue
                v = contact["values"]
                try:
                    avito_client.send_message(user_id, v["chat_id"], v["Черновик ответа"])
                    sheets.update_contact_status(row_id, "отправлено")
                    tg.answer_callback_query(cq["id"], "Отправлено клиенту")
                except Exception as e:
                    sheets.update_contact_status(row_id, f"ошибка: {e}")
                    tg.answer_callback_query(cq["id"], "Ошибка отправки, смотри таблицу")
            elif data.startswith("edit:"):
                row_id = data.split(":", 1)[1]
                tg.answer_callback_query(cq["id"], "Жду ваш текст")
                tg.send_message(
                    cq["message"]["chat"]["id"],
                    "Ответьте (reply) на сообщение с черновиком своим текстом — отправлю его клиенту вместо черновика.",
                )
            continue

        if "message" not in upd:
            continue
        msg = upd["message"]
        if msg.get("chat", {}).get("id") != ADMIN_ID:
            continue  # ignore anyone except the admin
        text = (msg.get("text") or "").strip()

        if text.startswith("/booked"):
            payload = text[len("/booked"):].strip()
            fields = [p.strip() for p in payload.split("|")]
            name = fields[0] if len(fields) > 0 else "—"
            phone = fields[1] if len(fields) > 1 else "—"
            when = fields[2] if len(fields) > 2 else "—"
            goal = fields[3] if len(fields) > 3 else "—"
            chat_id = fields[4] if len(fields) > 4 else "—"
            sheets.log_booking(name, phone, chat_id, when, goal)
            tg.send_message(ADMIN_ID, f"Записал в таблицу бронирований: {name}, {phone}, {when}")
            continue

        reply_to = msg.get("reply_to_message")
        if reply_to:
            replied_msg_id = reply_to.get("message_id")
            row_id = sheets.find_row_id_by_telegram_msg_id(replied_msg_id)
            if row_id:
                contact = sheets.get_contact_by_id(row_id)
                v = contact["values"]
                try:
                    avito_client.send_message(user_id, v["chat_id"], text)
                    sheets.update_contact_status(row_id, "отправлено (ред.)", new_draft=text)
                    tg.send_message(ADMIN_ID, "Отправил исправленный вариант клиенту.")
                except Exception as e:
                    tg.send_message(ADMIN_ID, f"Не получилось отправить: {e}")
            else:
                tg.send_message(ADMIN_ID, "Не нашёл, к какому обращению это относится.")
            continue

        # plain text with no reply-context => today's available slots
        sheets.set_state("slots_text", text)
        sheets.set_state("slots_date", today_str())
        tg.send_message(ADMIN_ID, f"Принял свободное время на сегодня: {text}")

    if max_seen > last_update_id:
        sheets.set_state("telegram_last_update_id", str(max_seen))


def maybe_ask_daily_slots() -> None:
    today = today_str()
    if sheets.get_state("slots_prompt_date", "") == today:
        return
    now_msk = datetime.now(MSK)
    if now_msk.hour < 9:
        return
    tg.send_message(
        ADMIN_ID,
        "Доброе утро! Какое время сегодня свободно для пробных уроков?\n"
        "Напишите через запятую, например: 15:00, 18:30",
    )
    sheets.set_state("slots_prompt_date", today)


def check_new_avito_messages(user_id: int) -> None:
    slots_text = sheets.get_state("slots_text", "") if sheets.get_state("slots_date", "") == today_str() else ""

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
            # Первый запуск: не считаем старую переписку новыми обращениями,
            # просто запоминаем её как увиденную, без уведомлений и черновиков.
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

        draft = generate_draft(name, client_text, slots_text)

        notif_text = (
            f"Новое сообщение на Avito\n"
            f"От: {name} ({city})\n"
            f"Категория: {category}\n"
            f"Текст: {client_text}\n\n"
            f"Черновик ответа:\n{draft}\n\n"
            f"Чтобы изменить — ответьте (reply) на ЭТО сообщение своим текстом."
        )
        sent = tg.send_message(ADMIN_ID, notif_text)
        telegram_msg_id = sent.get("message_id")

        row_id = sheets.log_contact(chat["id"], avito_msg_id, name, city, category, client_text, draft, telegram_msg_id)

        tg.edit_message_reply_markup(
            ADMIN_ID, telegram_msg_id, tg.inline_keyboard([
                ("✅ Отправить", f"send:{row_id}"),
                ("❌ Исправить", f"edit:{row_id}"),
            ])
        )

    if not bootstrapped:
        sheets.set_state("bootstrapped", "1")


def main() -> None:
    account = avito_client.get_self_account()
    user_id = account["id"]

    process_telegram_updates(user_id)
    maybe_ask_daily_slots()
    check_new_avito_messages(user_id)


if __name__ == "__main__":
    main()
