import re
from datetime import datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

PRICE_RE = re.compile(r"сто(ит|ят|имость)|\bцен[аыу]\b|сколько.*(сто|плат)|оплат|прайс|фикс", re.I)
FORMAT_RE = re.compile(r"\bочно\b|\bонлайн\b|удал[её]нн|как\s+проход", re.I)
CLASS_RE = re.compile(r"(\d{1,2})\s*-?\s*(?:й|ый|ой)?\s*кл", re.I)
BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,2})\s*[.!)]*\s*$")
EGE_RE = re.compile(r"\bе[гг]э\b|егэ|профил", re.I)
OGE_RE = re.compile(r"\bогэ\b|огэ", re.I)
NONTARGET_RE = re.compile(r"студент|\bвуз|универ|взросл|для себя|олимпиад", re.I)
TARGET_RE = re.compile(r"(\d{2,3})\s*\+|на\s*(\d{2,3})\s*балл|цель\s*(\d{2,3})", re.I)
FREQ_RE = re.compile(r"(\d)\s*раз[а]?\s*в\s*неделю", re.I)
PHONE_RE = re.compile(r"(?:\+?7|8)?[\s\-(]*9\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
OUR_PRICE_RE = re.compile(r"(\d{3,5})\s*(?:р\b|р/|₽|руб)", re.I)

RECORD_RE = re.compile(r"как.*запис|набира(ете|ют)|свобод.*мест|записат", re.I)
AGREE_RE = re.compile(r"^\s*(да|давайте|давай|хорошо|ок|окей|можно|согласн|подходит|отлично|конечно|го)\b"
                      r"|давайте|согласн|подходит|удобно|есть возможность|можно\s+(завтра|сегодня|послезавтра)", re.I)
POSTPONE_RE = re.compile(r"боле(е|ет|ю|ла|л)|заболе|температур|перенес|не смож|не получ|позже|потом напиш|подума", re.I)
WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")

PRICE_LINE_DEFAULT = "Стоимость зависит от частоты занятий и целей — точно сориентирую после пробного урока."
FORMAT_LINE = "Занятия онлайн, в Zoom, из любой точки."


# ---------- разбор всей переписки ----------

def analyze_dialog(messages: list[dict], now: datetime | None = None) -> dict:
    """messages — сообщения чата Avito от старых к новым: {"direction": "in"/"out", "text": str, "created": unix}.
    Возвращает всё, что известно о клиенте, и что он пишет сейчас (сообщения после нашего последнего ответа)."""
    now = now or datetime.now(MSK)
    msgs = [m for m in messages if (m.get("text") or "").strip()]
    client_msgs = [m for m in msgs if m["direction"] == "in"]
    our_msgs = [m for m in msgs if m["direction"] == "out" and not m.get("system")]

    last_out_idx = max((i for i, m in enumerate(msgs) if m["direction"] == "out" and not m.get("system")), default=-1)
    batch = [m for m in msgs[last_out_idx + 1:] if m["direction"] == "in"]
    batch_text = " / ".join(m["text"].strip() for m in batch)

    # клиенты часто дробят сообщение («953», «7118811») — склеиваем подряд идущие
    all_client = " ".join(m["text"] for m in client_msgs)

    grade = None
    for i, m in enumerate(msgs):
        if m["direction"] != "in":
            continue
        g = CLASS_RE.search(m["text"])
        if g:
            grade = g.group(1)
            continue
        b = BARE_NUMBER_RE.match(m["text"])
        prev_out = next((p for p in reversed(msgs[:i]) if p["direction"] == "out"), None)
        if b and prev_out and "класс" in prev_out["text"].lower():
            grade = b.group(1)

    exam = "ОГЭ" if OGE_RE.search(all_client) else ("ЕГЭ" if EGE_RE.search(all_client) else None)
    t = TARGET_RE.search(all_client)
    target = next((x for x in t.groups() if x), None) if t else None
    f = FREQ_RE.search(all_client)
    phone_m = PHONE_RE.search(all_client)
    our_price = None
    for m in our_msgs:
        p = OUR_PRICE_RE.search(m["text"])
        if p:
            our_price = p.group(0)

    if grade in ("10", "11") or (exam == "ЕГЭ" and grade not in ("9",)):
        category = "ЕГЭ"
    elif grade == "9" or exam == "ОГЭ":
        category = "ОГЭ"
    elif (grade and grade.isdigit() and int(grade) <= 8) or NONTARGET_RE.search(all_client):
        category = "нецелевой"
    else:
        category = "не ясно"

    last_in_time = datetime.fromtimestamp(batch[-1]["created"], MSK) if batch else now
    day = _requested_day(batch_text, last_in_time)
    last_out_time = datetime.fromtimestamp(our_msgs[-1]["created"], MSK) if our_msgs else None

    return {
        "grade": grade,
        "exam": exam,
        "target": target,
        "freq": f.group(1) if f else None,
        "phone": re.sub(r"[^\d+]", "", phone_m.group(0)) if phone_m else None,
        "category": category,
        "batch_text": batch_text,
        "invited": any("пробн" in m["text"].lower() for m in our_msgs),
        "price_told": our_price,
        "asked_grade": any("класс" in m["text"].lower() and "?" in m["text"] for m in our_msgs),
        "has_our_msgs": bool(our_msgs),
        # здороваемся, только если с нашего последнего сообщения прошло больше 12 часов
        "need_greeting": not last_out_time or (now - last_out_time) > timedelta(hours=12),
        "agrees": bool(AGREE_RE.search(batch_text)),
        "postpones": bool(POSTPONE_RE.search(batch_text)),
        "asks_price": bool(PRICE_RE.search(batch_text)),
        "asks_format": bool(FORMAT_RE.search(batch_text)),
        "wants_record": bool(RECORD_RE.search(batch_text)),
        "day": day,
    }


def _requested_day(text: str, base: datetime) -> str | None:
    low = text.lower()
    if "послезавтра" in low:
        d = base + timedelta(days=2)
    elif "завтра" in low:
        d = base + timedelta(days=1)
    elif "сегодня" in low:
        d = base
    else:
        d = None
        for i, w in enumerate(WEEKDAYS):
            if w in low:
                d = base + timedelta(days=(i - base.weekday()) % 7 or 7)
                break
    if not d:
        return None
    return f"{WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def profile_text(a: dict) -> str:
    parts = []
    if a["grade"]:
        parts.append(f"{a['grade']} класс")
    if a["exam"]:
        parts.append(a["exam"])
    if a["target"]:
        parts.append(f"цель {a['target']}+")
    if a["freq"]:
        parts.append(f"{a['freq']} раза в неделю")
    return ", ".join(parts)


def _request_summary(a: dict) -> str:
    if a["postpones"]:
        return "просит отложить / не может сейчас"
    if a["day"] and (a["agrees"] or a["invited"]):
        return f"готов(а) на пробный, хочет в {a['day']}"
    if a["agrees"] and a["invited"]:
        return "готов(а) на пробный урок"
    if a["asks_price"]:
        return "спрашивает цену"
    if a["asks_format"]:
        return "уточняет формат"
    if a["wants_record"]:
        return "хочет записаться"
    snippet = a["batch_text"].replace("\n", " ")
    return (snippet[:60] + "…") if len(snippet) > 60 else (snippet or "без текста")


def category_text(a: dict) -> str:
    """Для столбца «Категория»: ЕГЭ / ОГЭ / нецелевой / не ясно + (что известно, что хочет сейчас)."""
    profile = ", ".join(p for p in profile_text(a).split(", ") if p != a["category"])
    details = [p for p in [profile or "класс не указан", _request_summary(a)] if p]
    return f"{a['category']} ({', '.join(details)})"


def classify_request(client_text: str) -> str:
    """Классификация по одному сообщению — когда переписки нет под рукой."""
    return category_text(analyze_dialog([{"direction": "in", "text": client_text or "", "created": 0}]))


# ---------- варианты ответа ----------

def _parse_slots(slots_text: str) -> list[str]:
    return [t.replace(".", ":") for t in TIME_RE.findall(slots_text or "")][:2]


def _time_question(slots: list[str]) -> str:
    if len(slots) >= 2:
        return f"сегодня удобно в {slots[0]} или в {slots[1]}?"
    if len(slots) == 1:
        return f"сегодня в {slots[0]} удобно?"
    return "когда вам будет удобно сегодня или завтра?"


def _name(client_name: str) -> str:
    return client_name if client_name and client_name != "—" else ""


def _opening(client_name: str, a: dict, word: str = "здравствуйте") -> str:
    """«Полина, здравствуйте!» в начале диалога, просто «Полина,» — если уже переписываемся."""
    n = _name(client_name)
    if a["need_greeting"]:
        return f"{n}, {word}!" if n else f"{word.capitalize()}!"
    return f"{n}," if n else ""


def _say(opening: str, *sentences: str) -> str:
    """Склеивает обращение и фразы. После «Полина,» первая фраза идёт с маленькой буквы."""
    parts = [p for p in sentences if p]
    if parts and opening.endswith(","):
        parts[0] = parts[0][:1].lower() + parts[0][1:]
    return " ".join([opening, *parts] if opening else parts).strip()


def generate_variants(client_name: str, client_text: str, slots_text: str = "", dialog: dict | None = None) -> list[str]:
    a = dialog or analyze_dialog([{"direction": "in", "text": client_text or "", "created": 0}])
    slots = _parse_slots(slots_text)
    tq = _time_question(slots)
    o = _opening(client_name, a)
    facts = []
    if a["asks_format"]:
        facts.append(FORMAT_LINE)
    if a["asks_price"]:
        facts.append(f"Занятие стоит {a['price_told']}." if a["price_told"] else PRICE_LINE_DEFAULT)

    # 1. Не может сейчас (заболел, просит позже) — не давим, оставляем дверь открытой
    if a["postpones"]:
        return [
            _say(o, "Выздоравливайте! Как будет удобно — напишите, подберу время для бесплатного пробного урока."),
            _say(o, "Понимаю, здоровье важнее. Давайте перенесём пробный урок — когда примерно сможете?",
                 "Подстрою время под вас."),
            _say(o, "Скорейшего выздоровления! Пробный урок онлайн, всего 30–40 минут — можно провести из дома,",
                 "как только станет лучше. Напишите, когда будет удобно."),
        ]

    # 2. Уже приглашали, клиент согласен и/или назвал день — договариваемся о конкретном времени
    if a["invited"] and (a["agrees"] or a["day"]):
        if a["day"]:
            ask_time = f"{a['day'][0].upper() + a['day'][1:]}, подходит. Во сколько вам удобно?"
        else:
            ask_time = "Подскажите, в какой день и во сколько вам удобно?"
        today_offer = f"Если удобно сегодня — есть время в {' или в '.join(slots)}." if slots and not a["day"] else ""
        grade_q = "" if a["grade"] else "И подскажите, какой класс — подготовлю задания под уровень."
        return [
            _say(o, "Отлично!", ask_time, "Пришлю ссылку на Zoom."),
            _say(o, "Да, конечно!", ask_time, today_offer),
            _say(o, "Отлично, договорились!", ask_time, grade_q),
        ]

    grade_q = "" if (a["grade"] or a["asked_grade"]) else \
        "Подскажите, в каком классе и к какому экзамену готовитесь — ЕГЭ или ОГЭ?"

    # 3. Уже приглашали, клиент задал вопрос — отвечаем на него и снова предлагаем время, без повторного приглашения
    if a["invited"]:
        return [
            _say(o, *facts, f"Когда вам удобно провести пробный урок — {tq}"),
            _say(o, *facts, "Давайте подберём время для бесплатного пробного урока —", tq),
            _say(o, *facts, grade_q or "Остались ли вопросы перед пробным уроком?",
                 "" if grade_q else f"Можем провести его {tq[:-1] if tq.endswith('?') else tq}."),
        ]

    # 4. Начало разговора — приглашаем на пробный
    known = profile_text(a)
    invite = f"Приглашаю на бесплатный пробный урок онлайн (30–40 минут) — {tq} " \
             "Познакомимся, посмотрим уровень и сразу обсудим план подготовки."
    return [
        _say(o, *facts, f"Записал: {known}." if known else "", invite),
        _say(o, "Да, есть свободные места." if a["wants_record"] or not a["has_our_msgs"] else "", *facts,
             f"Давайте начнём с бесплатного пробного урока — {tq}"),
        _say(o, *facts, grade_q or "Подскажите, на какой балл готовитесь и что сейчас даётся сложнее всего?",
             f"А пробный урок бесплатный — {tq}"),
    ]


def generate_draft(client_name: str, client_text: str, slots_text: str = "") -> str:
    return generate_variants(client_name, client_text, slots_text)[0]
