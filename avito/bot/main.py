import difflib
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
import ai_reply  # noqa: E402
import amo_crm  # noqa: E402
from script_engine import (TIME_RE, ZOOM_MARK, analyze_dialog, auto_decision, booking_key,  # noqa: E402
                           category_text, confirm_block, day_label, expand_slots, generate_variants, is_past)

MSK = timezone(timedelta(hours=3))
MAX_CHATS_TO_SCAN = 40
AVITO_CHECK_INTERVAL = 60
# 0 = один проход (локальный запуск); на хостинге задаётся ~3300 секунд
RUN_SECONDS = int(os.getenv("RUN_SECONDS", "0") or "0")

HELP_TEXT = (
    "Что я умею:\n"
    "• Присылаю каждое новое сообщение с Avito с кнопками «✅ Отправить» / «❌ Исправить».\n"
    "• Свободное время на сегодня — напишите часы (15:00, 18:00) или промежуток (15:00-18:00): "
    "в каждый час записываю до 2 человек по 30 минут.\n"
    "• Когда клиент выбрал время, черновиком будет ваше сообщение со ссылкой на Zoom; "
    "после отправки сам внесу его в «Записаны на пробный».\n"
    "• Свой ответ клиенту — ответьте (reply) на карточку клиента или просто напишите текст, "
    "я спрошу кнопками, кому его отправить.\n\n"
    "Команды:\n"
    "/status — работает ли бот, заявки и записи за сегодня, свободные окна\n"
    "/auto on | /auto off — отвечать клиентам самому (в понятных ситуациях) или только через вас\n"
    "/ai on | /ai off — отвечать по смыслу через DeepSeek или только по правилам\n"
    "/amo on | /amo off — вести заявки сделками в amoCRM («Продажа курса») или нет\n"
    "/week — расписание на неделю (показать или задать: /week ср 14:00-16:00 пт 16-22 сб 11-22)\n"
    "/price около 1000 ₽ за час — что отвечать на вопрос о цене (/price без текста — не называть цену)\n"
    "/zoom https://… — ссылка Zoom для подтверждения записи\n"
    "/booked Имя | Телефон | Когда | Класс | chat_id — записать на пробный\n"
    "/team — кто ещё получает заявки. Помощнику: открыть этого бота и отправить /join, вам придёт запрос"
)

MEMBER_HELP = (
    "Вы подключены к рабочему боту Романа: сюда приходят те же заявки с Avito и уведомления, что и ему.\n"
    "• «✅ Отправить» — ответ сразу уходит клиенту в Avito.\n"
    "• «❌ Исправить» — другие варианты ответа.\n"
    "• Свой текст — ответьте (reply) на карточку клиента.\n"
    "• /status — как сейчас работает бот.\n"
    "Роман видит, что вы отправили клиентам. Настройки и доступы меняет только он."
)
# команды, которые меняют настройки бота, — только для Романа
OWNER_ONLY = ("/auto", "/ai", "/amo", "/week", "/zoom", "/price", "/team")

# Статистика процесса для /status (в памяти, сбрасывается при перезапуске)
_stats = {"started": "", "host": "", "last_check": 0.0, "check_secs": 0.0, "avito_fails": 0, "errors": []}
_variant_msgs = {}  # row_id -> message_id уже открытого экрана вариантов (чтобы не слать его повторно)
_notified_msgs = set()  # id сообщений Avito, по которым карточка уже ушла в этом процессе


# ---------- Помощники: получают всё то же, что и Роман (16.11, по просьбе Романа) ----------
# Человек пишет боту /join → Роману приходит запрос с кнопками → после «Открыть доступ» его id попадает
# в state "team", и telegram_api копирует ему всё, что бот пишет Роману.

def _team() -> dict:
    try:
        return {int(k): v for k, v in json.loads(sheets.get_state("team", "") or "{}").items()}
    except (ValueError, AttributeError, TypeError):
        return {}


def _save_team(team: dict) -> None:
    sheets.set_state("team", json.dumps({str(k): v for k, v in team.items()}, ensure_ascii=False))


tg.team_ids = lambda: list(_team())


def _person_name(frm: dict) -> str:
    name = " ".join(x for x in (frm.get("first_name"), frm.get("last_name")) if x) or "Без имени"
    return name + (f" (@{frm['username']})" if frm.get("username") else "")


def _handle_stranger(msg: dict) -> None:
    chat = msg.get("chat", {})
    cid = chat.get("id")
    if chat.get("type") != "private" or not cid:
        return
    text = (msg.get("text") or "").strip()
    if not text.startswith("/join"):
        if text.startswith("/start"):
            tg.send_direct(cid, "Это рабочий бот Романа для заявок с Avito. Если Роман попросил вас помогать, "
                                "отправьте /join — он получит запрос и откроет доступ.")
        return
    if _same_day(sheets.get_state(f"join_{cid}", "").split("|")[0], today_str()):
        tg.send_direct(cid, "Запрос уже у Романа. Как только он откроет доступ, я напишу сюда.")
        return
    name = _person_name(msg.get("from") or {})
    sheets.set_state(f"join_{cid}", f"{today_str()}|{name}")
    tg.send_direct(
        ADMIN_ID,
        f"👤 {name} (id {cid}) просит доступ к боту.\n\n"
        "Если открыть, он будет получать всё то же, что и вы: карточки клиентов с перепиской, записи, "
        "предупреждения. И сможет нажимать кнопки, то есть отправлять ответы клиентам в Avito.\n"
        "Настройки (/auto, /ai, /week, /zoom, /price, свободное время) и доступы останутся только у вас.",
        tg.inline_keyboard([("✅ Открыть доступ", f"team_ok:{cid}"), ("❌ Отказать", f"team_no:{cid}")]),
    )
    tg.send_direct(cid, "Отправил запрос Роману. Как только он откроет доступ, я напишу сюда.")


def _team_text() -> str:
    team = _team()
    if not team:
        return ("Сейчас заявки получаете только вы.\n"
                "Чтобы подключить помощника: пусть он откроет этого бота и отправит /join — вам придёт запрос.")
    return ("Кроме вас заявки получают:\n" + "\n".join(f"• {n}" for n in team.values()) +
            "\n\nНовый помощник: открыть этого бота и отправить /join.")


def _team_buttons() -> dict | None:
    team = _team()
    if not team:
        return None
    return tg.keyboard_rows([[(f"🚫 Закрыть доступ: {n[:30]}", f"team_rm:{cid}")] for cid, n in team.items()])


def _team_callback(cq: dict, action: str, rest: str, message_id: int) -> None:
    try:
        cid = int(rest)
    except ValueError:
        tg.answer_callback_query(cq["id"])
        return
    team = _team()
    name = sheets.get_state(f"join_{cid}", "").partition("|")[2] or team.get(cid) or f"id {cid}"
    if action == "team_ok":
        if cid not in team:
            team[cid] = name
            _save_team(team)
            tg.send_direct(cid, "✅ Роман открыл вам доступ.\n\n" + MEMBER_HELP)
        tg.answer_callback_query(cq["id"], "Доступ открыт")
        tg.edit_message_text(ADMIN_ID, message_id, f"✅ Доступ открыт: {name}. Теперь он получает всё то же, "
                                                   "что и вы.\nЗакрыть доступ: /team", {"inline_keyboard": []})
    elif action == "team_no":
        tg.answer_callback_query(cq["id"], "Отказано")
        tg.edit_message_text(ADMIN_ID, message_id, f"❌ Не дал доступ: {name}.", {"inline_keyboard": []})
    elif action == "team_rm":
        if team.pop(cid, None) is not None:
            _save_team(team)
            tg.send_direct(cid, "Роман закрыл вам доступ к заявкам — больше сюда ничего не придёт.")
        tg.answer_callback_query(cq["id"], "Доступ закрыт")
        tg.edit_message_text(ADMIN_ID, message_id, f"🚫 Закрыл доступ: {name}.\n\n" + _team_text(),
                             _team_buttons() or {"inline_keyboard": []})


def _member_message(chat_id: int, who: str, msg: dict) -> dict | None:
    """Сообщение помощника: на справку/статус отвечаем только ему; настройки не даём; reply на копию карточки
    переводим на карточку Романа. Возвращает сообщение для обычной обработки или None, если уже ответили."""
    text = (msg.get("text") or "").strip()
    if not text:
        return None
    if text.startswith(("/start", "/help", "/join")):
        tg.send_direct(chat_id, MEMBER_HELP)
        return None
    if text.startswith("/status"):
        tg.send_direct(chat_id, status_text())
        return None
    if text.startswith(OWNER_ONLY):
        tg.send_direct(chat_id, "Это меняет только Роман.")
        return None
    reply_to = msg.get("reply_to_message")
    if reply_to:
        orig = tg.to_admin_message(chat_id, reply_to.get("message_id"))
        if not orig:
            tg.send_direct(chat_id, "Не понял, к какой заявке это ответ. Ответьте (reply) на карточку клиента.")
            return None
        if str(orig) == sheets.get_state("slots_prompt_msg_id", ""):
            tg.send_direct(chat_id, "Свободное время задаёт только Роман.")
            return None
        msg = {**msg, "reply_to_message": {**reply_to, "message_id": orig}}
    # Роман не видит, что помощник пишет боту, — показываем ему (ответ бота придёт обоим)
    tg.send_direct(ADMIN_ID, f"👤 {who} пишет боту: «{text[:300]}»")
    return msg


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


def norm_when(value: str) -> str:
    """«19.11.2026 20.30» / «19.11.2026 9:00» → «19.11.2026 20:30» / «19.11.2026 09:00» (записи, внесённые руками)."""
    m = re.match(r"^\s*(\d{1,2})\.(\d{1,2})\.(\d{4})\s+(\d{1,2})[:.](\d{2})\s*$", value or "")
    if not m:
        return (value or "").strip()
    d, mo, y, h, mi = m.groups()
    return f"{int(d):02d}.{int(mo):02d}.{y} {int(h):02d}:{mi}"


def booked_set() -> set:
    """Занятые окна «дд.мм.гггг ЧЧ:ММ» из листа «Записаны на пробный»."""
    return {norm_when(b.get("Когда пробный урок", "")) for b in sheets.get_bookings()}


WEEKDAY_SHORT = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"]
SCHEDULE_DAYS = 7


def week_slots() -> dict:
    """Обычное расписание Романа по дням недели: {0..6: "14:00-16:00, 18:30-22:00"} (0 — понедельник)."""
    try:
        return {int(k): v for k, v in json.loads(sheets.get_state("week_slots", "") or "{}").items()}
    except ValueError:
        return {}


def day_slots_text(d) -> str:
    """Часы на день: на сегодня — то, что Роман прислал утром (если прислал), иначе — недельное расписание."""
    if d == datetime.now(MSK).date() and today_slots():
        return today_slots()
    return week_slots().get(d.weekday(), "")


def schedule() -> dict | None:
    """Свободные окна по 30 минут на ближайшие дни {дата: [...]}: без занятых и прошедших (+15 мин на ответ).
    None — расписания нет совсем (ни недельного, ни на сегодня): тогда бот спрашивает клиента, когда удобно."""
    if not week_slots() and not today_slots():
        return None
    now = datetime.now(MSK)
    booked = booked_set()
    result = {}
    for i in range(SCHEDULE_DAYS):
        d = (now + timedelta(days=i)).date()
        result[d] = [s for s in expand_slots(day_slots_text(d))
                     if not is_past(d, s, now) and booking_key(d, s) not in booked]
    return result


def free_slots_today() -> list[str]:
    return (schedule() or {}).get(datetime.now(MSK).date(), [])


def _build_variants(name: str, client_text: str, a: dict) -> list[str]:
    return generate_variants(name, client_text, ", ".join(free_slots_today()), a, booked_set(),
                             sheets.get_state("price_hint", ""), sheets.get_state("zoom_link", ""),
                             schedule=schedule())


def parse_week(text: str) -> dict:
    """«ср 14:00-16:00, 18:30-22:00 / вт 21:30 / чт 14-16, 19, 19:30 / сб 11-22» → {2: "...", 1: "...", ...}.
    Одиночное время — одно окно на 30 минут; «14-16» понимаем как 14:00-16:00."""
    names = {"пн": 0, "пон": 0, "вт": 1, "втор": 1, "ср": 2, "сред": 2, "чт": 3, "чет": 3, "пт": 4, "пят": 4,
             "сб": 5, "суб": 5, "вс": 6, "вос": 6}
    pattern = r"(?<![а-яё])(пн|понедельн\w*|вт|вторн\w*|ср|сред\w*|чт|четв\w*|пт|пятн\w*|сб|суббот\w*|вс|воскрес\w*)(?![а-яё])"
    parts = re.split(pattern, text.lower())
    result = {}
    for i in range(1, len(parts) - 1, 2):
        key = next((v for k, v in names.items() if parts[i].startswith(k)), None)
        body = parts[i + 1]
        if key is None:
            continue
        body = re.sub(r"(?<![:.\d])(\d{1,2})\s+(00|30)(?![:.\d])", r"\1:\2", body)  # «19 30» → 19:30
        body = re.sub(r"(?<![:.\d])(\d{1,2})(?![:.\d])", r"\1:00", body)  # «с 14 до 16» → 14:00 … 16:00
        body = re.sub(r"(\d{1,2}[:.]\d{2})\s*(?:-|–|—|до)\s*(\d{1,2}[:.]\d{2})", r"\1-\2", body.replace("с ", " "))
        windows = []
        for m in re.finditer(r"(\d{1,2}[:.]\d{2})(?:-(\d{1,2}[:.]\d{2}))?", body):
            start = m.group(1).replace(".", ":").zfill(5)
            if m.group(2):
                windows.append(f"{start}-{m.group(2).replace('.', ':').zfill(5)}")
            else:
                h, mm = map(int, start.split(":"))
                end = h * 60 + mm + 30
                windows.append(f"{start}-{end // 60:02d}:{end % 60:02d}")
        if windows:
            result[key] = ", ".join(windows)
    return result


def week_text(week: dict) -> str:
    return "\n".join(f"{WEEKDAY_SHORT[d]}: {week[d]}" for d in sorted(week)) or "не задано"


def _save_meta(row_id: str, a: dict) -> None:
    """Что пригодится при отправке: о каком времени договорились (для записи на пробный) и телефон."""
    book = ""
    if a.get("confirmed"):
        d, hhmm = a["confirmed"]
        key = booking_key(d or datetime.now(MSK).date(), hhmm)
        book = "" if key in booked_set() else key
    sheets.set_state(f"meta_{row_id}", json.dumps({"book": book, "phone": a.get("phone") or ""}, ensure_ascii=False))


def _note_error(where: str, err) -> None:
    _stats["errors"] = (_stats["errors"] + [f"{_hhmm()} {where}: {tg.clean_error(err)[:200]}"])[-5:]


# ---------- карточки ----------

HISTORY_ON_CARD = 8


def _load_dialog(user_id: int, chat_id: str) -> list[dict]:
    """Переписка с клиентом из Avito, от старых сообщений к новым (системные помечены)."""
    try:
        raw = avito_client.get_messages(user_id, chat_id, limit=40).get("messages", [])
    except Exception as e:
        _note_error("переписка Avito", e)
        return []
    return [{"direction": m.get("direction"), "text": (m.get("content") or {}).get("text") or "",
             "created": m.get("created", 0), "system": m.get("type") == "system"} for m in reversed(raw)]


def _history_lines(dialog: list[dict], name: str) -> str:
    msgs = [m for m in dialog if m["text"].strip() and not m.get("system")][-HISTORY_ON_CARD:]
    lines = []
    for m in msgs:
        when = datetime.fromtimestamp(m["created"], MSK).strftime("%d.%m %H:%M")
        who = f"👤 {name}" if m["direction"] == "in" else "🧑‍🏫 Вы"
        text = m["text"].strip().replace("\n", " ")
        lines.append(f"{when} {who}: {text[:180] + '…' if len(text) > 180 else text}")
    return "\n".join(lines)


def _card_text(v: dict, dialog: list[dict] | None = None, phone: str | None = None,
               title: str = "Новое сообщение на Avito") -> str:
    history = _history_lines(dialog or [], v["Имя"])
    return (
        f"{title}\n"
        f"От: {v['Имя']} ({v['Город']})\n"
        f"Категория: {v['Категория']}\n"
        + (f"📞 Телефон из переписки: {phone}\n" if phone else "")
        + f"Пишет сейчас: {v['Сообщение клиента']}\n\n"
        + (f"Переписка (последние сообщения):\n{history}\n\n" if history else "")
        + f"Черновик ответа:\n{v['Черновик ответа']}"
    )


def _retire_old_cards(row_id: str, old_card_id: str | None) -> None:
    """Клиент написал ещё раз: кнопки старой карточки и экрана вариантов больше не нужны."""
    note = tg.inline_keyboard([("↪️ Клиент написал ещё — новая карточка ниже", "noop")])
    for msg_id in {old_card_id, _variant_msgs.pop(row_id, None)}:
        if msg_id:
            tg.edit_message_reply_markup(ADMIN_ID, int(msg_id), note)


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


def _stale_reason(text: str, v: dict, row_id: str) -> str:
    """Почему черновик нельзя отправлять как есть: время в нём уже прошло. Пустая строка — можно.
    Черновик готовится в момент прихода сообщения, а нажать «Отправить» могут через несколько часов."""
    now = datetime.now(MSK)
    try:
        meta = json.loads(sheets.get_state(f"meta_{row_id}", "") or "{}")
    except ValueError:
        meta = {}
    if ZOOM_MARK in text and meta.get("book"):
        when = datetime.strptime(meta["book"], "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
        if when < now:
            return f"это запись на {meta['book']}, а это время уже прошло"
    if "сегодня" in text.lower():
        times = [t.replace(".", ":") for t in TIME_RE.findall(text)]
        drafted_today = _same_day(v.get("Дата", ""), today_str())
        if times and not drafted_today:
            return f"черновик писался {v.get('Дата')}, и «сегодня в {times[0]}» уже не про сегодня"
        past = [t for t in times if is_past(now.date(), t.zfill(5), now, margin_min=0)]
        if past:
            return f"время {', '.join(past)} сегодня уже прошло"
    return ""


def _refuse_if_stale(cq_id: str, text: str, v: dict, row_id: str) -> bool:
    reason = _stale_reason(text, v, row_id)
    if not reason:
        return False
    tg.answer_callback_query(cq_id, "Не отправил — время в черновике устарело")
    tg.send_message(ADMIN_ID, f"⚠️ Не отправил {v['Имя']}: {reason}.\n"
                              "Нажмите «❌ Исправить» — перечитаю переписку и предложу актуальное время.")
    return True


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
        lead = _amo_lead(row_id)
        if lead:
            who = "Бот ответил сам" if "бот сам" in status else "Ответ из Telegram"
            _amo_safe("ответ клиенту", amo_crm.note, lead, f"{who} в Avito:\n{text}")
        if ZOOM_MARK in text:
            _log_trial_booking(row_id, v)
    return True


def _log_trial_booking(row_id: str, v: dict) -> None:
    """Ушла ссылка на Zoom — значит, человек записан: заносим в «Записаны на пробный», окно становится занятым."""
    try:
        meta = json.loads(sheets.get_state(f"meta_{row_id}", "") or "{}")
    except ValueError:
        meta = {}
    when = meta.get("book") or "время не определил — уточните в таблице"
    sheets.log_booking(v["Имя"], meta.get("phone") or "—", v["chat_id"], when, v["Категория"])
    meta["book"] = ""
    sheets.set_state(f"meta_{row_id}", json.dumps(meta, ensure_ascii=False))

    _amo_safe("запись на пробный", _amo_set_booked, row_id, v, meta.get("phone"), when, "Ссылка Zoom отправлена.")
    tg.send_message(ADMIN_ID, f"📅 Записал на пробный: {v['Имя']} ({v['Город']}) — {when}"
                              + (f", тел. {meta['phone']}" if meta.get("phone") else "")
                              + "\nСтрока добавлена в лист «Записаны на пробный».")


def _amo_set_booked(row_id: str, v: dict, phone: str | None, when: str, how: str) -> int:
    """Сделка клиента (строка «Обращений» row_id) → «Назначен пробный урок», заметка и «· пробный …» в комментарии.
    Сделку не создаём: её заводит интеграция Avito ↔ amoCRM; не нашли — одно сообщение Роману."""
    if sheets.get_state(f"amo_skip_{row_id}", ""):
        return 0  # Роман попросил этого клиента в amoCRM не добавлять
    lead = _amo_lead(row_id)
    if not lead:
        lead = amo_crm.find_lead(v["Имя"], phone, _amo_first_note(v["Имя"], v["Город"], v["Категория"], v["chat_id"], ""),
                                 stage="booked", client_text=v.get("Сообщение клиента", ""), taken=_amo_taken())
        _amo_save_log()
        if not lead:
            tg.send_message(ADMIN_ID, f"📇 Не нашёл в amoCRM сделку {v['Имя']} от интеграции Avito — этап "
                                      "«Назначен пробный урок» не поставил. Если сделка есть, поставьте его вручную.")
            return 0
        sheets.set_state(f"amo_{row_id}", str(lead))
    amo_crm.advance(lead, "booked")
    amo_crm.note(lead, f"Записан на пробный урок: {when} (мск). {how}")
    written = _amo_written(row_id)
    base = written.get("Комментарий", "").split(" · пробный ")[0]
    _amo_save_written(row_id, amo_crm.fill_fields(lead, None, None, (base + " · " if base else "")
                                                  + f"пробный {when}", written))
    return lead


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

    if data == "noop":
        tg.answer_callback_query(cq["id"])
        return
    action, _, rest = data.partition(":")

    if chat_id == ADMIN_ID and action in ("team_ok", "team_no", "team_rm"):
        _team_callback(cq, action, rest, message_id)
        return
    if chat_id != ADMIN_ID:
        who = _team().get(chat_id)
        if not who or action.startswith("team"):
            tg.answer_callback_query(cq["id"])
            return
        if action == "slots":
            tg.answer_callback_query(cq["id"], "Свободное время задаёт только Роман")
            return
        orig = tg.to_admin_message(chat_id, message_id)
        if not orig:
            tg.answer_callback_query(cq["id"], "Эта карточка пришла до вашего доступа — дождитесь новой")
            return
        chat_id, message_id = ADMIN_ID, orig  # дальше — как будто нажал Роман: карточка обновится у всех
        if action in ("send", "sv", "to", "now"):
            v0 = (sheets.get_contact_by_id(rest.split(":")[0]) or {}).get("values") or {}
            if v0 and not _is_sent(v0):
                tg.send_direct(ADMIN_ID, f"👤 {who} отправляет ответ клиенту {v0.get('Имя', '')}.")

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

    if action in ("trok", "trno"):  # «Пробный прошёл?» → amoCRM
        lead = _amo_lead(row_id)
        if not lead or not _amo_on():
            tg.answer_callback_query(cq["id"], "amoCRM сейчас не подключён")
            return
        tg.answer_callback_query(cq["id"], "Отмечаю в amoCRM…")
        if action == "trok":
            ok = _amo_safe("пробный прошёл", lambda: (amo_crm.advance(lead, "trial_done"),
                                                     amo_crm.note(lead, "Пробный урок проведён."))[0])
            text = (f"✅ Пробный с {v['Имя']} проведён — в amoCRM «Проведен пробный урок»." if ok
                    else f"⚠️ Не получилось отметить в amoCRM пробный с {v['Имя']} — подробности в /status.")
        else:
            def not_held():
                moved = amo_crm.back_to_qualified(lead)
                amo_crm.note(lead, "Пробный урок не проведён (клиент не пришёл)."
                             + (" Сделка возвращена на «Квалифицирован»." if moved == "перевёл" else ""))
                amo_crm.task(lead, f"Назначить пробный урок: {v['Имя']} не пришёл на пробный — связаться и "
                                   "договориться о новом времени", hours=2)
                return moved
            moved = _amo_safe("не пришёл на пробный", not_held)
            ok = moved is not None
            text = (f"🚫 {v['Имя']} не пришёл на пробный — в amoCRM "
                    + ("вернул сделку на «Квалифицирован» и " if moved == "перевёл" else f"этап не менял ({moved}), ")
                    + "поставил задачу «Назначить пробный урок»." if ok
                    else f"⚠️ Не получилось отметить в amoCRM — подробности в /status.")
        # не получилось — кнопки оставляем, можно нажать ещё раз
        tg.edit_message_text(ADMIN_ID, message_id, text, {"inline_keyboard": []} if ok else tg.inline_keyboard(
            [("✅ Прошёл", f"trok:{row_id}"), ("🚫 Не пришёл", f"trno:{row_id}")]))
        return

    if action in ("send", "sv", "edit", "to", "now") and _is_sent(v):
        tg.answer_callback_query(cq["id"], "Этому клиенту уже отправлено ✅")
        return

    # любое ваше действие с карточкой отменяет автоответ: дальше решаете вы
    if action in ("send", "sv", "edit", "own", "to", "noauto"):
        cancelled = _cancel_auto(row_id)
        if action == "noauto":
            tg.answer_callback_query(cq["id"], "Не отправляю — ответьте сами" if cancelled else "Автоответа уже нет")
            tg.edit_message_reply_markup(ADMIN_ID, message_id, _card_buttons(row_id))
            return

    if action == "now":
        item = _pending_auto.get(row_id)
        if not item:
            tg.answer_callback_query(cq["id"], "Автоответа уже нет")
            return
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
        _send_auto(user_id, row_id, item)
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
        if _refuse_if_stale(cq["id"], v["Черновик ответа"], v, row_id):
            return
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
        _send_to_client(user_id, row_id, v["Черновик ответа"], "отправлено")
        return

    if action == "edit":
        if row_id in _variant_msgs:
            tg.answer_callback_query(cq["id"], "Варианты уже открыты ниже ⬇️")
            return
        tg.answer_callback_query(cq["id"], "Читаю переписку…")
        # перечитываем переписку заново: с момента карточки могли написать и клиент, и вы прямо в Avito,
        # а свободные окна могли заняться
        dialog = _load_dialog(user_id, v["chat_id"])
        if dialog:
            a = analyze_dialog(dialog)
            variants = _build_variants(v["Имя"], a["batch_text"] or v["Сообщение клиента"], a)
            if _ai_enabled() and not _hard_manual_reason(a):
                ai = ai_reply.ai_reply(v["Имя"], dialog, a, schedule(), sheets.get_state("price_hint", ""), variants[0])
                if ai and ai["action"] == "reply":
                    variants = [ai["text"]] + [x for x in variants if x != ai["text"]][:2]
                elif ai and ai["action"] == "book":
                    a = {**a, "confirmed": (ai["date_obj"], ai["time"])}
                    variants = [confirm_block(sheets.get_state("zoom_link", "")) + f"\n\nЖду вас "
                                f"{day_label(ai['date_obj'], datetime.now(MSK).date())} в {ai['time']} мск!"] + variants[:2]
            sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
            sheets.set_state(f"vardate_{row_id}", today_str())
            _save_meta(row_id, a)
        else:
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
        idx = int(rest.split(":")[1])
        chosen = _variants_for(row_id, v)[idx]
        # экран вариантов мог пролежать долго; «сегодня» в нём — про день, когда его открыли
        if _refuse_if_stale(cq["id"], chosen, {**v, "Дата": sheets.get_state(f"vardate_{row_id}", v["Дата"])}, row_id):
            return
        tg.answer_callback_query(cq["id"], "Отправляю клиенту…")
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
    day_auto = len([t for t, _ in _auto_sent_log if time.time() - t < 86400])
    lines.append(f"🤖 Автоответ: {'включён' if _auto_enabled() else 'выключен'} · отправил сам после запуска: {day_auto}"
                 f" · ждут отправки: {len(_pending_auto)}")
    u = ai_reply.usage()
    if not ai_reply.available():
        lines.append("🧠 DeepSeek: не подключён (нет ключа DEEPSEEK_API_KEY) — отвечаю по правилам")
    else:
        lines.append(f"🧠 DeepSeek: {'включён' if _ai_enabled() else 'выключен (/ai on)'} · сегодня запросов {u['calls']}, "
                     f"не удалось {u['fails']}, токенов {u['tokens_in']}+{u['tokens_out']}")
    s = amo_crm.stats
    if not amo_crm.available():
        lines.append("📇 amoCRM: не подключён (нет AMO_DOMAIN / AMO_TOKEN)")
    elif not amo_crm.ready():
        lines.append("📇 amoCRM: не веду сделки — " + sheets.get_state("amo_diag", "")[21:150])
    else:
        lines.append(f"📇 amoCRM: {'веду сделки' if _amo_on() else 'выключен (/amo on)'} · после запуска: нашёл сделок "
                     f"{s['created']}, перевёл {s['moved']}, задач {s['tasks']}, ошибок {s['errors']}"
                     + (f"\n   последняя ошибка: {s['last_error']}" if s["last_error"] else ""))
    if ago is None:
        lines.append("Avito ещё не проверялся после запуска")
    else:
        lines.append(f"Avito проверен {ago} с назад (проверка заняла {_stats['check_secs']:.1f} с)")
    sched = schedule()
    if sched is None:
        lines.append("Расписание не задано — пришлите часы на сегодня или неделю командой /week")
    else:
        now_d = datetime.now(MSK).date()
        lines.append("Свободные окна (по 30 мин):")
        for d in sorted(sched)[:4]:
            times = sched[d]
            lbl = "сегодня" if d == now_d else WEEKDAY_SHORT[d.weekday()] + d.strftime(" %d.%m")
            lines.append(f"  {lbl}: {', '.join(times[:8]) + (' …' if len(times) > 8 else '') if times else '—'}")

    def _when_dt(b):
        try:
            return datetime.strptime(b, "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
        except ValueError:
            return None
    upcoming = sorted((w, b) for b in booked_set() if (w := _when_dt(b)) and w > datetime.now(MSK) - timedelta(hours=1))
    if upcoming:
        lines.append(f"Записаны на пробный: {', '.join(b[:5] + ' ' + b[11:] for _, b in upcoming[:8])}")

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


def _save_week(week: dict) -> None:
    sheets.set_state("week_slots", json.dumps({str(k): v for k, v in week.items()}, ensure_ascii=False))
    nxt = []
    for d, times in sorted((schedule() or {}).items()):
        if times:
            nxt.append(f"{WEEKDAY_SHORT[d.weekday()]} {d:%d.%m}: {times[0]}–{times[-1]} ({len(times)} окон)")
    tg.send_message(ADMIN_ID, "Принял расписание на неделю:\n" + week_text(week) +
                    "\n\nБлижайшие свободные окна:\n" + ("\n".join(nxt[:5]) or "—") +
                    "\n\nПробный — 30 минут, в час записываю до двух человек. Дни, которых нет в списке, — выходные.")


def _save_slots(text: str) -> None:
    sheets.set_state("slots_text", text)
    sheets.set_state("slots_date", today_str())
    free = free_slots_today()
    tg.send_message(ADMIN_ID, f"Принял свободное время на сегодня: {text}\n"
                              f"Окна по 30 минут (по 2 человека в час): {', '.join(free) if free else 'все уже прошли'}\n"
                              "Буду предлагать клиентам ближайшие свободные.")


def handle_message(user_id: int, msg: dict) -> None:
    chat_id = msg.get("chat", {}).get("id")
    owner = chat_id == ADMIN_ID
    if not owner:
        team = _team()
        if chat_id not in team:
            _handle_stranger(msg)
            return
        msg = _member_message(chat_id, team[chat_id], msg)
        if msg is None:
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

    if text.startswith("/team"):
        tg.send_direct(ADMIN_ID, _team_text(), _team_buttons())
        return

    if text.startswith("/join"):
        tg.send_direct(ADMIN_ID, "Это команда для помощника: он отправляет /join в этом боте, а вам приходит "
                                 "запрос.\n\n" + _team_text())
        return

    if text.startswith("/week"):
        body = text[len("/week"):].strip()
        if body:
            week = parse_week(body)
            if not week:
                tg.send_message(ADMIN_ID, "Не разобрал дни. Пример: /week ср 14:00-16:00, 18:30-22:00 пт 16-22 сб 11-22")
                return
            _save_week(week)
        else:
            tg.send_message(ADMIN_ID, "Расписание на неделю:\n" + week_text(week_slots()) +
                            "\n\nИзменить: /week ср 14:00-16:00, 18:30-22:00 пт 16-22 сб 11-22 (дни, которых нет, — выходные)")
        return

    if text.startswith("/ai"):
        arg = text[len("/ai"):].strip().lower()
        if arg in ("on", "off"):
            sheets.set_state("ai_mode", arg)
        if not ai_reply.available():
            tg.send_message(ADMIN_ID, "🧠 DeepSeek не подключён: на хостинге нет ключа (DEEPSEEK_API_KEY в "
                                      "credentials/.env). Пока отвечаю по правилам.")
        else:
            tg.send_message(ADMIN_ID, f"🧠 DeepSeek {'включён' if _ai_enabled() else 'выключен'}. Когда включён — "
                                      "отвечаю по смыслу переписки, а если модель не справилась — по правилам.\n"
                                      "/ai on — включить, /ai off — только правила.")
        return

    if text.startswith("/amo"):
        arg = text[len("/amo"):].strip().lower()
        if arg in ("on", "off"):
            sheets.set_state("amo_mode", arg)
        if not amo_crm.ready():
            tg.send_message(ADMIN_ID, "📇 amoCRM не подключён: " + (sheets.get_state("amo_diag", "")[21:250] or "нет ключа"))
        else:
            tg.send_message(ADMIN_ID, f"📇 amoCRM {'включён' if _amo_on() else 'выключен'}. Когда включён — новые "
                                      "заявки с Avito становятся сделками в «Продажа курса» и сами идут по этапам.\n"
                                      "/amo on — включить, /amo off — выключить.")
        return

    if text.startswith("/auto"):
        arg = text[len("/auto"):].strip().lower()
        if arg in ("on", "off", "вкл", "выкл"):
            mode = "on" if arg in ("on", "вкл") else "off"
            sheets.set_state("auto_mode", mode)
            if mode == "off":
                for rid in list(_pending_auto):
                    _cancel_auto(rid)
        state = "включён" if _auto_enabled() else "выключен"
        tg.send_message(ADMIN_ID, f"🤖 Автоответ {state}.\n"
                                  "Когда включён: в понятных ситуациях отвечаю клиенту сам сразу и присылаю вам карточку "
                                  "с тем, что ответил; нестандартное — вам.\n"
                                  "/auto on — включить, /auto off — выключить.")
        return

    if text.startswith("/zoom"):
        link = text[len("/zoom"):].strip()
        if ZOOM_MARK not in link:
            tg.send_message(ADMIN_ID, "Пришлите так: /zoom https://us06web.zoom.us/j/…")
            return
        sheets.set_state("zoom_link", link)
        tg.send_message(ADMIN_ID, "Сохранил ссылку Zoom — буду отправлять её, когда клиент выберет время.")
        return

    if text.startswith("/price"):
        hint = text[len("/price"):].strip()
        sheets.set_state("price_hint", hint)
        tg.send_message(ADMIN_ID, f"На вопрос о цене буду отвечать: «По стоимости — {hint}: точнее сориентирую на "
                                  "пробном уроке…»" if hint else
                        "Цену больше не называю: отвечаю, что стоимость зависит от частоты и целей.")
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
        row_id = (sheets.find_row_id_by_telegram_msg_id(replied_id) or sheets.get_state(f"card_{replied_id}", "")
                  or sheets.get_state(f"reply_{replied_id}", ""))
        if not row_id:
            tg.send_message(ADMIN_ID, "Не понял, к какой заявке это ответ. Ответьте (reply) на карточку клиента.")
            return
        tg.send_message(ADMIN_ID, "Отправляю клиенту…")
        _send_to_client(user_id, row_id, text, "отправлено (свой текст)")
        return

    # Только часы («15:00, 18:30», «сегодня 13:00 и 15:00») — это свободное время. Любой другой текст скорее всего
    # ответ клиенту, написанный без reply, — спрашиваем кнопками, кому его отправить.
    # «в среду с 14 до 16, в пятницу с 16 до 22…» — расписание на неделю (минимум два дня с часами)
    week = parse_week(text) if owner else {}
    if len(week) >= 2:
        _save_week(week)
        return
    leftover = re.sub(r"[^a-zа-яё]", "", TIME_RE.sub("", text.lower()))
    if owner and TIME_RE.search(text) and len(leftover) <= 12:
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
    week = week_slots()
    if week:
        # есть недельное расписание — не спрашиваем, а напоминаем, что предлагаем сегодня; в выходной молчим
        todays = week.get(datetime.now(MSK).weekday())
        if not todays:
            sheets.set_state("slots_prompt_date", today)
            return
        prompt = tg.send_message(
            ADMIN_ID,
            f"Доброе утро! Сегодня по расписанию записываю на пробные: {todays}.\n"
            "Если что-то изменилось — пришлите новое время на сегодня (например 15:00-18:00).",
        )
    else:
        prompt = tg.send_message(
            ADMIN_ID,
            "Доброе утро! Какое время сегодня свободно для пробных уроков?\n"
            "Напишите свободные часы (15:00, 18:00) или промежуток (15:00-18:00) — "
            "в каждый час запишу до 2 человек по 30 минут.",
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
            # наше напоминание о пробном — это не ответ на вопрос клиента: заявку «ожидает» не гасим
            if not _is_reminder((last.get("content") or {}).get("text", "")):
                _mark_answered_in_avito(chat["id"])
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

        # Старое сообщение — не новая заявка: чат просто поднялся в списке Avito (03.11 так «пришли» 11 переписок
        # 21–27 октября, где давно всё решилось). Запоминаем, что видели, и молчим.
        created = last.get("created") or 0
        if created and time.time() - created > OLD_MESSAGE_SECONDS:
            existing = sheets.find_contact_by_chat(chat["id"])
            if existing and existing["values"]["id"]:
                sheets.update_contact(existing["values"]["id"], {"avito_msg_id": avito_msg_id})
            else:
                sheets.log_contact(chat["id"], avito_msg_id, "", "", "", "", "", None,
                                   status="пропущено (старое сообщение)")
            seen_ids.add(avito_msg_id)
            continue

        client_user = next((u for u in chat.get("users", []) if u["id"] != user_id), None)
        name = client_user["name"] if client_user else "—"
        item = chat.get("context", {}).get("value", {})
        city = (item.get("location") or {}).get("title", "—")
        last_text = (last.get("content") or {}).get("text", "")
        # Одно сообщение клиента — одна карточка, что бы ни случилось с таблицей: 08.11 из-за сбоя записи
        # в таблицу бот не узнавал заявку и присылал её каждую минуту (~1500 карточек).
        if avito_msg_id in _notified_msgs:
            continue
        _notified_msgs.add(avito_msg_id)
        _notify_client(user_id, chat["id"], name, city, avito_msg_id, last_text, slots_text)
        seen_ids.add(avito_msg_id)

    if not bootstrapped:
        sheets.set_state("bootstrapped", "1")


# ---------- напоминания о пробном (17.11, по просьбе Романа) ----------
# По листу «Записаны на пробный»: за 3 часа и за 30 минут до начала бот сам пишет клиенту в Avito.
# Окно: «за 3 часа» — пока до урока от 3 ч до 2 ч, «за 30 минут» — от 30 до 10 мин (если бот был выключен
# и окно прошло, поздно не напоминаем). Только что записавшемуся (меньше часа / 15 мин назад) не пишем —
# подтверждение со ссылкой и так перед глазами. Если после записи в чате звучала отмена или перенос — не пишем,
# а говорим Роману.
REMINDERS = (("3h", timedelta(hours=3), timedelta(hours=2), timedelta(hours=1)),
             ("30m", timedelta(minutes=30), timedelta(minutes=10), timedelta(minutes=15)))
REMINDER_MARKS = ("Напоминаю, что", "начинаем пробное занятие")
CANCEL_RE = re.compile(r"отмен|перенес|перенос|не\s+смож|не\s+получ|не\s+успева|не\s+прид|не\s+будем|"
                       r"заболе|боле(ет|ю|ла|л)\b|температур|передума|нет,?\s+спасибо", re.I)
# наши сообщения: «в 16:00, к сожалению, не получится, могу предложить…» — не отмена; отмена — только явная
# 19.11 Ольга: «занятие можем перенести на 21.00?» → «нет, спасибо» — любое «перенести/отменить» после ссылки = не напоминаем
OUR_CANCEL_RE = re.compile(r"отмен|перенес|перенос", re.I)
_reminded = set()  # ключи уже отправленных напоминаний в этом процессе (и в «Состоянии» — между процессами)


def _is_reminder(text: str) -> bool:
    return any(m in (text or "") for m in REMINDER_MARKS)


def _reminder_text(kind: str, name: str, dt: datetime, now: datetime) -> str:
    first = name.split()[0] if name and name.strip() not in ("", "—") else ""
    day = "сегодня" if dt.date() == now.date() else "завтра" if dt.date() == now.date() + timedelta(days=1) \
        else dt.strftime("%d.%m")
    if kind == "3h":
        h = now.hour
        word = "доброе утро" if 5 <= h < 12 else "добрый день" if 12 <= h < 18 else "добрый вечер"
        hello = f"{first}, {word}!" if first else f"{word.capitalize()}!"
        return (f"{hello} Напоминаю, что {day} в {dt:%H:%M} мск у нас пробное занятие по математике в Zoom 🙂\n"
                "За 30 минут до начала пришлю ссылку ещё раз.")
    zoom = sheets.get_state("zoom_link", "")
    return (f"{first + ', ' if first else ''}через 30 минут, в {dt:%H:%M} мск, начинаем пробное занятие 🙂\n"
            + (f"Ссылка для подключения:\n{zoom}\n\n" if zoom else "Ссылка для подключения — в сообщении выше.\n\n")
            + "До встречи!")


def _parse_msk(value: str) -> datetime | None:
    try:
        return datetime.strptime(norm_when(value), "%d.%m.%Y %H:%M").replace(tzinfo=MSK)
    except ValueError:
        return None


_chat_guess = {}  # (имя, когда) -> chat_id или "" (не понял)
_chat_miss = {}  # (имя, когда) -> когда подбор без уведомления не нашёл клиента (повторим через 30 минут)


def _booking_chat(user_id: int, b: dict, dt: datetime, notify: bool = True) -> str:
    """chat_id записи. Если его нет (Роман внёс запись руками) — ищем в «Обращениях» клиента с этим именем, которому
    за последние 4 дня отправляли ссылку Zoom; если таких несколько — того, с кем в переписке звучало это время.
    Не уверен — "" и одно сообщение Роману: напоминать наугад чужому человеку нельзя."""
    chat = (b.get("chat_id") or "").strip()
    if chat and chat != "—":
        return chat
    name = re.sub(r"\s+", " ", (b.get("Имя") or "").strip().lower())
    key = (name, f"{dt:%d%m%H%M}")
    if not name:
        return ""
    if key in _chat_guess:
        return _chat_guess[key]
    if not notify and time.time() - _chat_miss.get(key, 0) < 1800:
        return ""
    st_key = f"bchat_{dt:%d%m%H%M}_{name.replace(' ', '_')[:30]}"
    saved = sheets.get_state(st_key, "")
    if saved:
        _chat_guess[key] = "" if saved == "?" else saved
        return _chat_guess[key]
    first = name.split()[0]
    def similar(other: str) -> bool:
        other = re.sub(r"\s+", " ", other.strip().lower())
        if other == name or (" " not in name and other.split()[:1] == [first]):
            return True
        # «Яна Варфоломеева» в листе записей и «Яна Ворфоламеева» в Avito — тот же человек
        return other.split()[:1] == [first] and difflib.SequenceMatcher(None, other, name).ratio() >= 0.8
    cands = [c for c in sheets.all_contacts() if c.get("chat_id") and similar(c["Имя"])]
    since = time.time() - 4 * 86400
    with_link, with_time = [], []
    marks = (f"{dt:%H:%M}", f"{dt:%H.%M}", f"{dt.hour}:{dt:%M}", f"{dt.hour}.{dt:%M}", f"в {dt.hour}")
    for c in cands:
        recent = [m for m in _load_dialog(user_id, c["chat_id"]) if m["created"] >= since]
        if any(m["direction"] == "out" and ZOOM_MARK in m["text"] for m in recent):
            with_link.append(c)
            if any(mk in m["text"] for m in recent for mk in marks):
                with_time.append(c)
    pick = with_link if len(with_link) == 1 else with_time if len(with_time) == 1 else []
    chat = pick[0]["chat_id"] if pick else ""
    if not chat and not notify:
        _chat_miss[key] = time.time()
        return ""
    sheets.set_state(st_key, chat or "?")
    _chat_guess[key] = chat
    if not chat:
        who = ", ".join(f"{c['Имя']} ({c['Город']})" for c in (with_link or cands)[:4]) or "никого с таким именем"
        tg.send_message(ADMIN_ID, f"🔔 Не смогу напомнить «{b.get('Имя', '')}» о пробном {dt:%d.%m в %H:%M}: в листе "
                                  f"«Записаны на пробный» у этой строки нет chat_id, а по имени не понял, кто это "
                                  f"(подходят: {who}). Впишите в строку chat_id из листа «Обращения».")
    return chat


def _booking_cancel(user_id: int, chat: str, b: dict, dt: datetime, made: datetime) -> str:
    """Текст, которым после записи отменили или перенесли пробный (клиент или мы), или ""."""
    dialog = _load_dialog(user_id, chat)
    if not _parse_msk(b.get("Дата и время", "")):  # запись внесена руками — считаем от последней ссылки Zoom
        links = [m["created"] for m in dialog if m["direction"] == "out" and ZOOM_MARK in m["text"]
                 and m["created"] <= dt.timestamp()]
        made = datetime.fromtimestamp(links[-1], MSK) if links else made
    after = [m for m in dialog if m["created"] > made.timestamp() + 60 and not _is_reminder(m["text"])]
    return next((m["text"] for m in after if
                 (m["direction"] == "in" and (CANCEL_RE.search(m["text"]) or analyze_dialog([m])["declined"]))
                 or (m["direction"] == "out" and OUR_CANCEL_RE.search(m["text"]))), "")


def _trial_reminders(user_id: int) -> None:
    now = datetime.now(MSK)
    latest = {}  # chat_id -> (когда урок, когда записали, строка): напоминаем только о последней записи клиента
    for b in sheets.get_bookings():
        dt = _parse_msk(b.get("Когда пробный урок", ""))
        # chat ищем только для близких уроков: подбор по имени читает переписки
        if not dt or not timedelta(0) <= dt - now <= timedelta(hours=3, minutes=5):
            continue
        chat = _booking_chat(user_id, b, dt)
        if not chat:
            continue
        made = _parse_msk(b.get("Дата и время", "")) or dt - timedelta(days=1)
        if chat not in latest or made >= latest[chat][1]:
            latest[chat] = (dt, made, b)
    for chat, (dt, made, b) in latest.items():
        for kind, before, until, fresh in REMINDERS:
            left = dt - now
            if not until <= left <= before or now - made < fresh:
                continue
            key = f"rem{kind}_{chat}_{dt:%d%m%H%M}"
            if key in _reminded or sheets.get_state(key, ""):
                continue
            _reminded.add(key)
            name = b.get("Имя", "")
            cancel = _booking_cancel(user_id, chat, b, dt, made)
            if cancel:
                for k2 in (k for k, *_ in REMINDERS):  # ни одного напоминания по этой записи, предупреждение одно
                    _reminded.add(f"rem{k2}_{chat}_{dt:%d%m%H%M}")
                    sheets.set_state(f"rem{k2}_{chat}_{dt:%d%m%H%M}", f"не отправлено: {_now()}")
                tg.send_message(ADMIN_ID, f"⏸ Не напомнил {name} о пробном {dt:%d.%m в %H:%M}: после записи в чате было "
                                          f"«{cancel[:200]}». Проверьте, в силе ли пробный, — если перенесли, "
                                          "поменяйте время в листе «Записаны на пробный».")
                continue
            text = _reminder_text(kind, name, dt, now)
            try:
                avito_client.send_message(user_id, chat, text)
            except Exception as e:
                _reminded.discard(key)  # не ушло — попробуем при следующей проверке, пока окно не прошло
                _note_error("напоминание о пробном", e)
                continue
            sheets.set_state(key, _now())
            when = f"{'сегодня' if dt.date() == now.date() else dt.strftime('%d.%m')} в {dt:%H:%M}"
            tg.send_message(ADMIN_ID, f"🔔 Напомнил {name} о пробном {when} "
                                      f"({'за 3 часа' if kind == '3h' else 'за 30 минут, со ссылкой Zoom'}).")
            c = sheets.find_contact_by_chat(chat)
            lead = _amo_lead(c["values"]["id"]) if c else None
            if lead:
                _amo_safe("напоминание", amo_crm.note, lead, f"Бот напомнил о пробном ({kind}):\n{text}")


def _mark_answered_in_avito(chat_id: str) -> None:
    """Последнее сообщение в чате наше, а заявка всё ещё «ожидает» — значит, Роман ответил прямо в Avito.
    Отмечаем это и гасим кнопки карточки, иначе «Отправить» ушёл бы клиенту вторым ответом."""
    existing = sheets.find_contact_by_chat(chat_id)
    if not existing or existing["values"]["Статус"] != "ожидает":
        return
    v = existing["values"]
    sheets.update_contact(v["id"], {"Статус": "отвечено (в Avito)"})
    for msg_id in {v.get("telegram_msg_id"), _variant_msgs.pop(v["id"], None)}:
        if msg_id:
            tg.edit_message_reply_markup(ADMIN_ID, int(msg_id), tg.inline_keyboard(
                [("✍️ Вы уже ответили в Avito", "noop")]))


# ---------- автоответ ----------
# Роман просил отвечать клиентам без него. Бот отвечает сам только там, где ситуация распознана уверенно
# (script_engine.auto_decision), через AUTO_DELAY после карточки (0 — сразу; при задержке есть «Не отправлять»),
# и в жёстких лимитах: сбой не должен превратиться в рассылку клиентам (08.11 сбой таблицы дал 1500 карточек).

AUTO_DELAY = 0  # 14.11 Роман попросил отвечать сразу (было 120 с на «Не отправлять»)
AUTO_MAX_PER_CHAT_DAY = 5
AUTO_MAX_PER_HOUR = 20
_pending_auto = {}   # row_id -> {"due", "chat_id", "msg_id", "text", "card"}
_auto_sent_log = []  # (время, chat_id) отправленных автоответов — для лимитов


def _auto_enabled() -> bool:
    return sheets.get_state("auto_mode", "on") == "on"


def _auto_limits_ok(chat_id: str, a: dict, existing: dict | None) -> tuple[bool, str]:
    now = time.time()
    day = [t for t, c in _auto_sent_log if c == chat_id and now - t < 86400]
    hour = [t for t, _ in _auto_sent_log if now - t < 3600]
    if len(day) >= AUTO_MAX_PER_CHAT_DAY:
        return False, f"этому клиенту бот уже ответил сам {len(day)} раз за сутки — дальше лучше вы"
    if len(hour) >= AUTO_MAX_PER_HOUR:
        return False, "слишком много автоответов за час — бот приостановил их, проверьте, всё ли в порядке"
    # Роман сейчас сам ведёт этот диалог (его сообщение в Avito за последние 20 минут, а не наш автоответ)
    last_out, last_ts = a.get("our_last_text", ""), a.get("our_last_ts", 0)
    bot_text = (existing or {}).get("values", {}).get("Черновик ответа", "")
    if last_out and now - last_ts < 20 * 60 and last_out.strip() != bot_text.strip():
        return False, "вы сейчас сами ведёте этот диалог"
    return True, ""


def _auto_buttons(row_id: str) -> dict:
    return tg.keyboard_rows([
        [("✅ Отправить сейчас", f"now:{row_id}"), ("🚫 Не отправлять", f"noauto:{row_id}")],
        [("❌ Исправить", f"edit:{row_id}")],
    ])


def _schedule_auto(row_id: str, chat_id: str, msg_id: str, text: str, card_id: int | None) -> None:
    item = {"due": time.time() + AUTO_DELAY, "chat_id": chat_id, "msg_id": msg_id, "text": text, "card": card_id}
    _pending_auto[row_id] = item
    sheets.set_state(f"auto_{row_id}", json.dumps(item, ensure_ascii=False))  # переживёт перезапуск


def _cancel_auto(row_id: str) -> bool:
    if row_id not in _pending_auto:
        return False
    _pending_auto.pop(row_id, None)
    sheets.set_state(f"auto_{row_id}", "")
    return True


def _load_pending_auto() -> None:
    try:
        _auto_sent_log[:] = [tuple(x) for x in json.loads(sheets.get_state("auto_log", "") or "[]")]
    except ValueError:
        pass
    for key, (_, value) in list(sheets._load_state().items()):
        if key.startswith("auto_") and key not in ("auto_mode", "auto_log") and value:
            try:
                _pending_auto[key[5:]] = json.loads(value)
            except ValueError:
                pass


def _send_auto(user_id: int, row_id: str, item: dict) -> None:
    """Перед отправкой ещё раз смотрим в чат: если клиент дописал или Роман ответил сам — не отправляем."""
    _cancel_auto(row_id)
    contact = sheets.get_contact_by_id(row_id)
    if not contact or _is_sent(contact["values"]):
        return
    v = contact["values"]
    raw = avito_client.get_messages(user_id, item["chat_id"], limit=3).get("messages", [])
    if raw and raw[0].get("id") != item["msg_id"]:
        who = "клиент дописал — пришлю новую карточку" if raw[0].get("direction") == "in" else "вы уже ответили сами"
        if item.get("card"):
            tg.edit_message_reply_markup(ADMIN_ID, int(item["card"]), tg.inline_keyboard(
                [(f"⏸ Автоответ не отправлен: {who}", "noop")]))
        return
    if _stale_reason(item["text"], {**v, "Дата": today_str()}, row_id):
        tg.send_message(ADMIN_ID, f"⏸ Не отправил автоответ {v['Имя']}: время в нём устарело. Нажмите «❌ Исправить».")
        return
    ok, why = _auto_limits_ok(item["chat_id"], {}, None)
    if not ok:
        tg.send_message(ADMIN_ID, f"⏸ Не отправил автоответ {v['Имя']}: {why}.")
        return
    if _send_to_client(user_id, row_id, item["text"], "отправлено (бот сам)"):
        _auto_sent_log.append((time.time(), item["chat_id"]))
        _auto_sent_log[:] = [x for x in _auto_sent_log if time.time() - x[0] < 86400]
        sheets.set_state("auto_log", json.dumps(_auto_sent_log))
        print(f"AUTO_SENT: {v['Имя']}")


def process_due_autos(user_id: int) -> None:
    for row_id, item in list(_pending_auto.items()):
        if time.time() - item.get("due", 0) > 30 * 60:
            # назначен давно (бот был выключен) — такой ответ уже не к месту, решает Роман
            _cancel_auto(row_id)
            if item.get("card"):
                tg.edit_message_reply_markup(ADMIN_ID, int(item["card"]), _card_buttons(row_id))
            continue
        if time.time() >= item.get("due", 0):
            try:
                _send_auto(user_id, row_id, item)
            except Exception as e:
                _note_error("автоответ", e)


def _ai_enabled() -> bool:
    # включается командой /ai on (state ai_mode); работает, только если на хостинге есть DEEPSEEK_API_KEY
    return sheets.get_state("ai_mode", "off") == "on" and ai_reply.available()


# ---------- amoCRM (17.11): заявки → сделки в «Продажа курса», схема этапов — в amo_crm.py ----------

AVITO_CHAT_URL = "https://www.avito.ru/profile/messenger/channel/{}"


def _amo_on() -> bool:
    return sheets.get_state("amo_mode", "on") == "on" and amo_crm.ready()


def _amo_safe(what: str, fn, *args):
    """amoCRM — дополнение: если он недоступен, заявки, ответы и записи работают как раньше."""
    if not _amo_on():
        return None
    try:
        return fn(*args)
    except Exception as e:
        amo_crm._fail(e)
        _note_error(f"amoCRM ({what})", e)
        print("ERROR amo:", what, str(e)[:300])
        try:
            sheets.set_state("amo_last_error", f"{_now()} {what}: {str(e)[:300]}")
            if ("amoCRM 401" in str(e) or "amoCRM 402" in str(e)) and sheets.get_state("amo_alert_day", "") != today_str():
                sheets.set_state("amo_alert_day", today_str())
                tg.send_message(ADMIN_ID, "⚠️ amoCRM не пускает бота: " + ("токен не подходит или истёк — создайте новый "
                                "и замените AMO_TOKEN" if "401" in str(e) else "аккаунт amoCRM не оплачен")
                                + ". Заявки и ответы клиентам работают как обычно, только без amoCRM.")
        except Exception:
            pass
        return None


def _amo_lead(row_id: str) -> int | None:
    v = sheets.get_state(f"amo_{row_id}", "")
    return int(v) if v.isdigit() else None


def _amo_written(row_id: str) -> dict:
    try:
        return json.loads(sheets.get_state(f"amo_f_{row_id}", "") or "{}")
    except ValueError:
        return {}


def _amo_save_written(row_id: str, written: dict) -> None:
    if written:
        sheets.set_state(f"amo_f_{row_id}", json.dumps(written, ensure_ascii=False))


def _amo_comment(city: str, a: dict) -> str:
    """Коротко, что известно о запросе: для поля «Комментарий» в карточке сделки."""
    parts = [f"Avito, {city}" if city and city != "—" else "Avito"]
    if a.get("target"):
        parts.append(f"цель {a['target']}+")
    if a.get("freq"):
        parts.append(f"{a['freq']} раза в неделю")
    elif a.get("once_week_q"):
        parts.append("хочет 1 занятие в неделю")
    if a.get("offline_only"):
        parts.append("спрашивал(а) про очно")
    if a.get("phone"):
        parts.append(f"тел. {a['phone']}")
    return ", ".join(parts)


def _amo_save_log() -> None:
    sheets.set_state("amo_match_log", "\n".join(amo_crm.match_log))


def _amo_taken() -> set:
    """Сделки, уже привязанные к другим клиентам из таблицы, — их не берём для нового клиента."""
    return {int(v[1]) for k, v in sheets._load_state().items() if k.startswith("amo_") and str(v[1]).isdigit()}


def _amo_first_note(name: str, city: str, category: str, chat_id: str, client_text: str) -> str:
    return (f"Заявка с Avito\nКлиент: {name}, {city}\nКатегория: {category}\nЧат: {AVITO_CHAT_URL.format(chat_id)}"
            + (f"\n\nКлиент пишет:\n{client_text}" if client_text else ""))


def _amo_message(row_id: str, is_new: bool, name: str, city: str, category: str, chat_id: str, a: dict,
                 client_text: str, manual_why: str) -> None:
    def run():
        if sheets.get_state(f"amo_skip_{row_id}", ""):
            return  # Роман попросил этого клиента в amoCRM не добавлять
        lead = _amo_lead(row_id)
        if not lead:  # сделку создаёт интеграция Avito ↔ amoCRM — находим её (может появиться не сразу)
            lead = amo_crm.find_lead(name, a.get("phone"), _amo_first_note(name, city, category, chat_id, client_text),
                                     client_text=client_text, taken=_amo_taken())
            _amo_save_log()
            if not lead:
                return
            sheets.set_state(f"amo_{row_id}", str(lead))
        else:
            amo_crm.note(lead, f"Клиент пишет в Avito:\n{client_text}")
        written = _amo_written(row_id)
        booked = written.get("Комментарий", "").partition(" · пробный ")[2]
        comment = _amo_comment(city, a) + (f" · пробный {booked}" if booked else "")
        exam = a.get("category") if a.get("category") in ("ЕГЭ", "ОГЭ") else None
        _amo_save_written(row_id, amo_crm.fill_fields(lead, a.get("grade"), exam, comment, written))
        if a.get("declined"):
            if amo_crm.close_lost(lead) == "закрыл":
                amo_crm.note(lead, "Бот закрыл сделку: клиент отказался (см. сообщение выше).")
        elif a.get("category") in ("ЕГЭ", "ОГЭ") and (a.get("grade") or a.get("exam")) and a.get("has_our_msgs"):
            amo_crm.advance(lead, "qualified")
        # если Роман сам переписывается с клиентом, задача «ответить» ему не нужна
        if manual_why and "сами ведёте" not in manual_why and sheets.get_state(f"amo_task_{row_id}", "") != today_str():
            amo_crm.task(lead, f"Ответить клиенту {name} в Avito: {manual_why}"[:900], hours=1)
            sheets.set_state(f"amo_task_{row_id}", today_str())
    _amo_safe("заявка", run)


AMO_SYNC_VERSION = "bookings-v1"


def amo_sync_bookings_once() -> None:
    """17.11: Роман спросил, почему Анастасии (записана 16.11, до подключения amoCRM) нет на «Назначен пробный урок».
    Один раз переносим в amoCRM всех, у кого пробный сегодня или позже: находим их сделку (канал Avito / принятая /
    по телефону) или создаём, ставим на «Назначен пробный урок»."""
    if not _amo_on() or sheets.get_state("amo_synced", "") == AMO_SYNC_VERSION:
        return
    sheets.set_state("amo_synced", AMO_SYNC_VERSION)
    now = datetime.now(MSK)
    latest = {}
    for b in sheets.get_bookings():
        chat, dt = (b.get("chat_id") or "").strip(), _parse_msk(b.get("Когда пробный урок", ""))
        made = _parse_msk(b.get("Дата и время", "")) or now
        if chat and chat != "—" and dt and dt >= now - timedelta(hours=2) and (chat not in latest or made >= latest[chat][1]):
            latest[chat] = (dt, made, b)
    report = []
    for chat, (dt, _, b) in sorted(latest.items(), key=lambda x: x[1][0]):
        c = sheets.find_contact_by_chat(chat)
        if not c or _amo_lead(c["values"]["id"]):
            continue
        v, when = c["values"], b["Когда пробный урок"]
        phone = b.get("Телефон", "") if any(ch.isdigit() for ch in b.get("Телефон", "")) else None

        def run():
            lead = amo_crm.find_lead(v["Имя"], phone, _amo_first_note(v["Имя"], v["Город"], v["Категория"], chat, ""),
                                     stage="booked", client_text=v.get("Сообщение клиента", ""), taken=_amo_taken())
            if not lead:
                return "сделки от интеграции Avito не нашёл"
            sheets.set_state(f"amo_{v['id']}", str(lead))
            amo_crm.advance(lead, "booked")
            amo_crm.note(lead, f"Записан на пробный урок: {when} (мск). Ссылка Zoom отправлена.")
            return amo_crm.match_log[-1].split(": ", 1)[-1] if amo_crm.match_log else f"сделка #{lead}"
        res = _amo_safe("перенос записанных", run)
        report.append(f"• {v['Имя']} — пробный {when}: " + (res or "не получилось, см. /status"))
    _amo_save_log()
    if report:
        tg.send_message(ADMIN_ID, "📇 Перенёс в amoCRM уже записанных на пробный (они записались до подключения "
                                  "amoCRM), этап «Назначен пробный урок»:\n" + "\n".join(report))


def amo_manual_bookings(user_id: int) -> None:
    """19.11, «Да, сделай»: запись, которую Роман внёс в «Записаны на пробный» руками (без chat_id), тоже ставит сделку
    на «Назначен пробный урок». Клиента узнаём по имени, как для напоминаний; по каждой записи — один раз."""
    if not _amo_on():
        return
    now = datetime.now(MSK)
    for b in sheets.get_bookings():
        if (b.get("chat_id") or "").strip() not in ("", "—"):
            continue  # запись сделал бот — сделку он уже передвинул
        when = norm_when(b.get("Когда пробный урок") or "")
        dt = _parse_msk(when)
        if not dt or not -timedelta(hours=2) <= dt - now <= timedelta(days=14):
            continue
        name = re.sub(r"\s+", "_", (b.get("Имя") or "").strip().lower())[:30]
        done_key = f"amob_{dt:%d%m%H%M}_{name}"
        if not name or sheets.get_state(done_key, ""):
            continue
        chat = _booking_chat(user_id, b, dt, notify=False)
        c = sheets.find_contact_by_chat(chat) if chat else None
        if not c:
            continue  # ещё не узнали клиента — попробуем позже (за 3 часа до урока бот сам напишет, если не узнает)
        v = c["values"]
        cancel = _booking_cancel(user_id, chat, b, dt, _parse_msk(b.get("Дата и время", "")) or dt - timedelta(days=1))
        if cancel:  # после записи в чате отменили/перенесли — этап не трогаем (о напоминании Роману напишет отдельно)
            sheets.set_state(done_key, f"{_now()} не ставил: в чате «{cancel[:100]}»")
            continue
        phone = b.get("Телефон", "") if any(ch.isdigit() for ch in b.get("Телефон", "")) else None
        lead = _amo_safe("запись вручную", _amo_set_booked, v["id"], v, phone, when,
                         "Запись внесена вручную в лист «Записаны на пробный».")
        if lead is None:
            continue  # amoCRM недоступен или ошибка — повторим при следующей проверке
        sheets.set_state(done_key, f"{_now()} " + (f"#{lead}" if lead else "сделку не нашёл"))
        if lead:
            tg.send_message(ADMIN_ID, f"📇 {v['Имя']} ({v['Город']}): запись на пробный {when} из таблицы — "
                                      "сделку в amoCRM поставил на «Назначен пробный урок».")


def _amo_history_note(row_id: str, lead: int, dialog: list[dict], name: str) -> None:
    """20.11, Роман: «почему у этих клиентов не видно переписки в амо?» Чат Avito в карточке amoCRM показывает только
    у сделок от интеграции; в сделку, которую создал бот, кладём всю переписку до неё одной заметкой (один раз)."""
    if sheets.get_state(f"amo_hist_{row_id}", ""):
        return
    msgs = [m for m in dialog if m["text"].strip() and not m.get("system")][-40:]
    if not msgs:
        return
    lines = [f"{datetime.fromtimestamp(m['created'], MSK):%d.%m %H:%M} "
             f"{(name or 'Клиент') if m['direction'] == 'in' else 'Мы'}: {m['text'].strip()}" for m in msgs]
    text = "Переписка в Avito до создания сделки:\n\n" + "\n\n".join(lines)
    try:
        amo_crm.note(lead, text[-15000:])
    except RuntimeError as e:
        if "226" not in str(e):
            raise
        # amoCRM не принял какой-то символ — оставляем только буквы, цифры и обычную пунктуацию
        amo_crm.note(lead, re.sub(r"[^\w\s.,:;!?()«»\"'+\-–—/№%₽@#]", "·", text[-15000:]))
    sheets.set_state(f"amo_hist_{row_id}", _now())


AMO_RETRY = 600  # секунд между поисками сделки интеграции для одного клиента
_amo_searched = {}  # row_id -> когда последний раз искали


def amo_catch_up(user_id: int) -> None:
    """Клиент из «Обращений» за 3 дня, а его сделка от интеграции Avito ↔ amoCRM ещё не найдена: ищем её снова раз
    в 10 минут, не дожидаясь нового сообщения клиента. Нашли — заполняем поля и ставим этап по переписке и записи.
    Сами сделки не создаём (20.11, Роман: «не бот их создавал, а они автоматически шли с Avito»)."""
    if not _amo_on() or not amo_crm.ready():
        return
    now = datetime.now(MSK)
    done = 0
    for c in sheets.all_contacts():
        row_id, chat = c.get("id", ""), c.get("chat_id", "")
        seen = _parse_msk(f"{c.get('Дата', '')} {c.get('Время', '')}")
        if (not row_id or not chat or not seen or not timedelta(0) <= now - seen <= timedelta(days=3)
                or _amo_lead(row_id) or sheets.get_state(f"amo_skip_{row_id}", "")
                or time.time() - _amo_searched.get(row_id, 0) < AMO_RETRY):
            continue
        if done >= 5:  # не больше 5 за проверку: остальных — через минуту
            break
        done += 1
        _amo_searched[row_id] = time.time()
        dialog = _load_dialog(user_id, chat)
        a = analyze_dialog(dialog) if dialog else {}
        if a.get("spam"):
            sheets.set_state(f"amo_skip_{row_id}", "реклама")
            continue
        _amo_message(row_id, True, c.get("Имя", ""), c.get("Город", ""), c.get("Категория", ""), chat, a,
                     a.get("batch_text") or c.get("Сообщение клиента", ""), "")
        booking = next((b for b in reversed(sheets.get_bookings()) if (b.get("chat_id") or "").strip() == chat), None)
        dt = _parse_msk(norm_when(booking.get("Когда пробный урок", ""))) if booking else None
        if _amo_lead(row_id) and dt and dt >= now - timedelta(hours=2):
            _amo_safe("запись на пробный", _amo_set_booked, row_id, c, None, norm_when(booking["Когда пробный урок"]),
                      "Ссылка Zoom отправлена.")


AMO_REMOVE = {"b3858ece": 38637043, "7b120b3c": 38637047}  # 20.11, Роман: «Полину удали и больше не добавляй, ОПРАВУ тоже удали»


def amo_remove_once() -> None:
    if not _amo_on() or not amo_crm.ready() or sheets.get_state("amo_removed", "") == "v1":
        return
    sheets.set_state("amo_removed", "v1")
    report = []
    for row_id, lead in AMO_REMOVE.items():
        sheets.set_state(f"amo_skip_{row_id}", "Роман: удалить и больше не добавлять")
        sheets.set_state(f"amo_{row_id}", "")
        c = sheets.get_contact_by_id(row_id)
        res = _amo_safe("удаление сделки", amo_crm.delete_lead, lead) or "не удалось (см. /status)"
        report.append(f"• {c['values']['Имя'] if c else row_id} #{lead}: {res}")
    sheets.set_state("amo_removed_report", "\n".join(report))
    left = [r for r in report if not r.endswith(": удалил")]
    tg.send_message(ADMIN_ID, "📇 amoCRM: этих клиентов больше не веду и в amoCRM не добавляю:\n" + "\n".join(report)
                    + ("\n\nСделки, которые не удалились, удалите в amoCRM вручную." if left else ""))


BOT_MADE_LEADS = {38637043, 38637047, 38637049, 38637053}  # 20.11 15:32–15:33 сделки создал бот (Полина, ОПРАВА, Тетя-мотя, Наташа)
_hist_tries = {}


def amo_history_backfill_once(user_id: int) -> None:
    """Один раз: переписка из Avito в сделки, которые бот создал 20.11, пока интеграция не создавала сделок."""
    if not _amo_on() or not amo_crm.ready() or sheets.get_state("amo_hist_done", "") == "v2":
        return
    for c in sheets.all_contacts():
        lead = _amo_lead(c.get("id", ""))
        if lead not in BOT_MADE_LEADS or sheets.get_state(f"amo_hist_{c['id']}", "") or _hist_tries.get(c["id"], 0) >= 3:
            continue
        _hist_tries[c["id"]] = _hist_tries.get(c["id"], 0) + 1
        dialog = _load_dialog(user_id, c["chat_id"])
        if dialog:
            _amo_safe("переписка в сделку", _amo_history_note, c["id"], lead, dialog, c.get("Имя", ""))
        if not sheets.get_state(f"amo_hist_{c['id']}", "") and _hist_tries[c["id"]] < 3:
            return  # не получилось — повторим при следующей проверке (всего 3 попытки)
    sheets.set_state("amo_hist_done", "v2")


_user_id_for_amo = 0  # id аккаунта Avito — main() подставляет при старте (нужен, чтобы читать переписку)


AMO_FIELDS_VERSION = "cleanup-v1"


def amo_fields_once(user_id: int) -> None:
    """19.11, по «удали все 14» Романа: один раз удаляем лишние поля сделки и заполняем «Класс», «Экзамен»,
    «Комментарий», «Бюджет» у сделок, которые бот уже ведёт. Итог — Роману в Telegram."""
    if not _amo_on() or sheets.get_state("amo_fields_done", "") == AMO_FIELDS_VERSION:
        return
    sheets.set_state("amo_fields_done", AMO_FIELDS_VERSION)
    deleted = _amo_safe("удаление полей", amo_crm.delete_fields) or ["не получилось — подробности в /status"]
    filled = []
    for key, (_, value) in list(sheets._load_state().items()):
        if not (key.startswith("amo_") and str(value).isdigit()):
            continue
        row_id = key[len("amo_"):]
        c = sheets.get_contact_by_id(row_id)
        if not c:
            continue
        v = c["values"]
        dialog = _load_dialog(user_id, v["chat_id"])
        if not dialog:
            continue
        a = analyze_dialog(dialog)
        booked = next((norm_when(b.get("Когда пробный урок", "")) for b in reversed(sheets.get_bookings())
                       if (b.get("chat_id") or "").strip() == v["chat_id"]), "")
        comment = _amo_comment(v["Город"], a) + (f" · пробный {booked}" if booked else "")
        exam = a.get("category") if a.get("category") in ("ЕГЭ", "ОГЭ") else None
        done = _amo_safe("заполнение полей", amo_crm.fill_fields, int(value), a.get("grade"), exam, comment,
                         _amo_written(row_id))
        if done:
            _amo_save_written(row_id, done)
            filled.append(f"• {v['Имя']}: " + ", ".join(f"{k} {x}" for k, x in done.items() if k != "Комментарий"))
    sheets.set_state("amo_fields_report", f"{_now()}\n" + amo_crm.fields_report())
    tg.send_message(ADMIN_ID, "📇 amoCRM, поля сделки:\n" + "\n".join(deleted)
                    + "\n\nЗаполнил «Класс», «Экзамен», «Комментарий», «Бюджет» у сделок, которые веду:\n"
                    + ("\n".join(filled) or "• пока нечего заполнять"))


def _amo_trial_followups() -> None:
    """Через 35 минут после начала пробного спрашиваем Романа, прошёл ли он, — отмечаем в amoCRM."""
    if not _amo_on():
        return
    now = datetime.now(MSK)
    for b in sheets.get_bookings():
        when = norm_when(b.get("Когда пробный урок") or "")
        dt = _parse_msk(when)
        if not dt or not timedelta(minutes=35) <= now - dt <= timedelta(days=2):
            continue
        c = sheets.find_contact_by_chat(_booking_chat(_user_id_for_amo, b, dt, notify=False))
        row_id = c["values"]["id"] if c else ""
        if not row_id or not _amo_lead(row_id) or sheets.get_state(f"amo_trial_{row_id}", "") == when:
            continue
        sheets.set_state(f"amo_trial_{row_id}", when)
        tg.send_message(ADMIN_ID, f"🎓 Пробный с {b.get('Имя', '')} {when} — прошёл?\nОтмечу в amoCRM.",
                        tg.inline_keyboard([("✅ Прошёл", f"trok:{row_id}"), ("🚫 Не пришёл", f"trno:{row_id}")]))


def _hard_manual_reason(a: dict) -> str:
    """Где отвечает только Роман — и модель, и правила: урок идёт, реклама, задачи/жалобы, звонок, после записи."""
    if a.get("call_request"):
        return "клиент просит перезвонить"
    if a.get("connecting"):
        return "клиент на связи по уроку"
    if a.get("spam"):
        return "реклама"
    if a.get("manual_only"):
        return "нестандартная просьба (задача, вуз, жалоба)"
    if a.get("link_sent") and not a.get("ack_only"):
        return "клиент уже записан"
    return ""


def _notify_client(user_id: int, chat_id: str, name: str, city: str, avito_msg_id: str, last_text: str,
                   slots_text: str, title: str = "Новое сообщение на Avito", when: tuple | None = None,
                   allow_auto: bool = True) -> None:
    # смотрим всю переписку, а не одно последнее сообщение: класс, цель, о чём уже договорились
    dialog = _load_dialog(user_id, chat_id) or [{"direction": "in", "text": last_text, "created": time.time()}]
    a = analyze_dialog(dialog)
    client_text = a["batch_text"] or last_text
    category = category_text(a)
    variants = _build_variants(name, client_text, a)
    draft = variants[0]

    # один клиент — одна строка: если он уже писал, обновляем его строку, а старую карточку гасим
    existing = sheets.find_contact_by_chat(chat_id)
    row_id = existing["values"]["id"] if existing and existing["values"]["id"] else sheets.new_row_id()
    # 🧠 DeepSeek отвечает по смыслу всей переписки; правила — страховка и запасной вариант, если модель
    # недоступна или её ответ не прошёл проверку (ai_reply.validate)
    ai, ai_tag, hard = None, "", _hard_manual_reason(a)
    if allow_auto and _ai_enabled() and not hard:
        ai = ai_reply.ai_reply(name, dialog, a, schedule(), sheets.get_state("price_hint", ""), variants[0])
        err = ai_reply.usage().get("last_error", "")
        if not ai and ("402" in err or "401" in err) and sheets.get_state("ai_alert_day", "") != today_str():
            sheets.set_state("ai_alert_day", today_str())  # не чаще раза в день
            tg.send_message(ADMIN_ID, "⚠️ DeepSeek не отвечает: " + ("закончился баланс — пополните на "
                            "platform.deepseek.com/top_up" if "402" in err else "ключ не подходит — проверьте DEEPSEEK_API_KEY")
                            + ". Пока отвечаю клиентам по правилам.")
    if ai and ai["action"] in ("reply", "book"):
        if ai["action"] == "book":
            d, t = ai["date_obj"], ai["time"]
            draft = (confirm_block(sheets.get_state("zoom_link", ""))
                     + f"\n\nЖду вас {day_label(d, datetime.now(MSK).date())} в {t} мск!")
            a = {**a, "confirmed": (d, t)}  # чтобы _save_meta запомнил окно и после отправки бот записал в таблицу
        else:
            draft = ai["text"]
        variants = [draft] + [v for v in variants if v != draft][:2]
        ai_tag = "🧠 "
    card = {"Имя": name, "Город": city, "Категория": category, "Сообщение клиента": client_text,
            "Черновик ответа": draft}

    # отвечать ли самому: только в распознанных ситуациях, при включённом автоответе и в пределах лимитов
    auto_ok, why = (False, "")
    if allow_auto and _auto_enabled():
        if ai and ai["action"] == "manual":
            auto_ok, why = False, "DeepSeek считает, что лучше ответить вам: " + (ai.get("reason") or "нестандартный вопрос")
        elif ai:
            auto_ok, why = True, ""
        else:
            auto_ok, why = auto_decision(a, ", ".join(free_slots_today()), schedule())
        if auto_ok:
            auto_ok, why = _auto_limits_ok(chat_id, a, existing)
    auto_title = (f" — отвечу сам через {AUTO_DELAY // 60} мин" if AUTO_DELAY else " — отвечаю сам")
    card_text = _card_text(card, dialog, a.get("phone"), ai_tag + "🤖 " + title + auto_title if auto_ok
                           else ai_tag + title)
    if allow_auto and _auto_enabled() and not auto_ok and why:
        card_text += f"\n\n✋ Нужен ваш ответ: {why}"
    # без задержки отменять нечего — кнопки обычные (останутся, только если отправка вдруг не удалась)
    buttons = _auto_buttons(row_id) if auto_ok and AUTO_DELAY else _card_buttons(row_id)
    sent = tg.send_message(ADMIN_ID, card_text, reply_markup=buttons)
    if auto_ok:
        _schedule_auto(row_id, chat_id, avito_msg_id, draft, sent.get("message_id"))
    print(f"NEW_MESSAGE: {name} ({city}) -> notified")
    date, hhmm = when or (today_str(), _hhmm())
    if existing and existing["values"]["id"]:
        _retire_old_cards(row_id, existing["values"].get("telegram_msg_id"))
        sheets.update_contact(row_id, {
            "Дата": date, "Время": hhmm, "Имя": name, "Город": city, "Категория": category,
            "Сообщение клиента": client_text, "Черновик ответа": draft, "Статус": "ожидает",
            "avito_msg_id": avito_msg_id, "telegram_msg_id": sent.get("message_id"),
        })
    else:
        sheets.log_contact(chat_id, avito_msg_id, name, city, category, client_text, draft,
                           sent.get("message_id"), row_id=row_id)
    sheets.set_state(f"var_{row_id}", json.dumps(variants, ensure_ascii=False))
    sheets.set_state(f"card_{sent.get('message_id')}", row_id)
    sheets.set_state(f"vardate_{row_id}", today_str())
    _save_meta(row_id, a)
    # amoCRM — до автоответа, чтобы ответ бота тоже лёг в сделку примечанием
    _amo_message(row_id, not (existing and existing["values"]["id"]), name, city, category, chat_id, a, client_text,
                 why if (allow_auto and _auto_enabled() and not auto_ok) else "")
    if auto_ok and not AUTO_DELAY and row_id in _pending_auto:
        # отвечаем сразу — уже после того, как заявка и время записи сохранены в таблицу
        _send_auto(user_id, row_id, _pending_auto[row_id])


OLD_MESSAGE_SECONDS = 48 * 3600


def skip_old_pending_once(user_id: int) -> None:
    """Один раз: заявки «ожидает», где последнее сообщение клиента старше двух суток, — это не новые обращения.
    Помечаем «пропущено», гасим кнопки (чтобы не отправить им черновик с прошедшим временем), Роману — список."""
    if sheets.get_state("cleanup_old", "") == "v1":
        return
    sheets.set_state("cleanup_old", "v1")
    skipped = []
    for c in sheets.all_contacts():
        if c["Статус"] != "ожидает" or c["Имя"] == "—":
            continue
        last_in = [m for m in _load_dialog(user_id, c["chat_id"]) if m["direction"] == "in" and m["text"].strip()]
        if not last_in or time.time() - last_in[-1]["created"] <= OLD_MESSAGE_SECONDS:
            continue
        when = datetime.fromtimestamp(last_in[-1]["created"], MSK)
        sheets.update_contact(c["id"], {"Статус": f"пропущено (старое сообщение от {when:%d.%m})",
                                        "Дата": when.strftime("%d.%m.%Y"), "Время": when.strftime("%H:%M")})
        if c.get("telegram_msg_id"):
            tg.edit_message_reply_markup(ADMIN_ID, int(c["telegram_msg_id"]), tg.inline_keyboard(
                [(f"🕓 Старое сообщение от {when:%d.%m} — пропущено", "noop")]))
        _variant_msgs.pop(c["id"], None)
        skipped.append(f"• {c['Имя']} — последнее сообщение {when:%d.%m}")
    if skipped:
        tg.send_message(ADMIN_ID, "Разобрался с карточками, пришедшими в 18:27: это не новые заявки, а старые переписки "
                                  "(клиенты писали больше двух суток назад). Пометил их «пропущено» и отключил кнопки, "
                                  "чтобы случайно не отправить черновик с прошедшим временем:\n" + "\n".join(skipped)
                                  + "\n\nЕсли кому-то хотите написать — ответьте (reply) на его карточку.")


REFRESH_VERSION = "dialog-v2"  # v2 (08.11): «не хочу» = отказ, «напишу во вторник» = ждём, без вопроса о времени


def refresh_pending_once(user_id: int) -> None:
    """После перехода на разбор всей переписки: один раз пересчитать заявки, которые ждут ответа,
    и прислать по ним новые карточки (у старых были неверная категория и черновик)."""
    if sheets.get_state("refreshed", "") == REFRESH_VERSION:
        return
    sheets.set_state("refreshed", REFRESH_VERSION)  # сначала отметка — чтобы при сбое не слать карточки каждый час
    pending = [c for c in sheets.all_contacts() if c["Статус"] == "ожидает" and c["Имя"] != "—"]
    if not pending:
        return
    tg.send_message(ADMIN_ID, f"Пересчитал заявки, которые ждут ответа ({len(pending)}), с учётом всей переписки "
                              "в Avito — новые карточки ниже, старые кнопки отключены.")
    for c in pending:
        _notify_client(user_id, c["chat_id"], c["Имя"], c["Город"], c["avito_msg_id"], c["Сообщение клиента"],
                       today_slots(), title="Заявка ждёт ответа (обновлено по всей переписке)",
                       when=(c["Дата"], c["Время"]), allow_auto=False)


def _run_avito_check(user_id: int) -> None:
    t = time.time()
    try:
        maybe_ask_daily_slots()
        check_new_avito_messages(user_id)
        try:
            _trial_reminders(user_id)
        except Exception as e:
            _note_error("напоминания о пробном", e)
        try:
            amo_sync_bookings_once()
            amo_fields_once(user_id)
            amo_manual_bookings(user_id)
            amo_remove_once()
            amo_catch_up(user_id)
            amo_history_backfill_once(user_id)
            _amo_trial_followups()
        except Exception as e:
            _note_error("amoCRM (пробные)", e)
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
    sheets.set_state("sheets_diag", f"{_now()}: повторы при сбоях Google: "
                     + (", ".join(sheets.RETRY_PATCHED) or "НЕ ВКЛЮЧЕНЫ"))
    # 06.11 бот упал при запуске на коротком обрыве связи с Avito — даём ему три попытки, а не одну
    for attempt in range(3):
        try:
            user_id = avito_client.get_self_account()["id"]
            globals()["_user_id_for_amo"] = user_id
            break
        except Exception:
            if attempt == 2:
                raise
            time.sleep(10)
    if RUN_SECONDS:  # только на хостинге: склейка удаляет строки, второй процесс сбил бы номера строк
        removed = sheets.dedupe_contacts()
        if removed:
            sheets.set_state("last_dedupe", f"{_now()}: склеил повторы клиентов, убрал строк: {removed}")
    sheets.get_logged_msg_ids()  # прогреваем кэш таблицы до первых нажатий
    # уже отправленные карточки (есть в таблице) второй раз не шлём и после перезапуска
    _notified_msgs.update(sheets.get_logged_msg_ids())
    _load_pending_auto()  # автоответы, назначенные до перезапуска
    # видно в «Состоянии»: есть ли ключ DeepSeek и отвечает ли модель (сам ключ не пишем)
    test = ai_reply.ping() if _ai_enabled() else "не проверял (DeepSeek выключен)"
    sheets.set_state("ai_diag", f"{_now()}: HTTP API; ключ {'есть' if ai_reply.available() else 'нет'}; "
                                f"тестовый запрос: {test}")
    try:  # amoCRM: пока только проверка ключа и чтение воронок (сам токен не пишем)
        amo_short, amo_pipes = amo_crm.diag()
        sheets.set_state("amo_diag", f"{_now()}: {amo_short}")
        if amo_pipes:
            sheets.set_state("amo_pipelines", amo_pipes)
        q = sheets.get_state("amo_lookup_q", "")
        if q and amo_crm.ready():  # разовая проверка: запрос кладётся в «Состояние», ответ — туда же
            sheets.set_state("amo_lookup_result", f"{_now()} «{q}»:\n" + "\n\n".join(
                amo_crm.lookup(part.strip()) for part in q.split("|") if part.strip()))
            sheets.set_state("amo_lookup_q", "")
        if amo_crm.ready():
            sheets.set_state("amo_fields_diag", f"{_now()}: {amo_crm.ensure_fields()}")
            sheets.set_state("amo_fields_report", f"{_now()}\n" + amo_crm.fields_report())
        if amo_crm.ready():
            known = {c["Имя"].strip().lower() for c in sheets.all_contacts() if c.get("Имя")}
            sheets.set_state("amo_avito_diag", f"{_now()}\n" + amo_crm.avito_channel_diag(known))
    except Exception as e:
        _note_error("проверка amoCRM", e)
    if RUN_SECONDS and len(sheets._load_state()) > 1500:
        try:
            cleaned = sheets.compact_state({c["id"] for c in sheets.all_contacts() if c["id"]})
            sheets.set_state("last_compact", f"{_now()}: убрал служебных строк: {cleaned}")
        except Exception as e:
            _note_error("уборка «Состояния»", e)
    if RUN_SECONDS:
        try:
            refresh_pending_once(user_id)
            skip_old_pending_once(user_id)
        except Exception as e:
            _note_error("пересчёт заявок", e)
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
            process_due_autos(user_id)
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
