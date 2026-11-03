import re
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

PRICE_RE = re.compile(r"сто(ит|ят|имость)|\bцен[аыу]\b|сколько.*(сто|плат)|оплат|прайс|фикс", re.I)
FORMAT_RE = re.compile(r"\bочно\b|\bонлайн\b|удал[её]нн|как\s+(проход|занима)|где\s+занят|формат", re.I)
CLASS_RE = re.compile(r"(\d{1,2})\s*-?\s*(?:й|ый|ой)?\s*кл", re.I)
BARE_NUMBER_RE = re.compile(r"^\s*(\d{1,2})\s*[.!)]*\s*$")
EGE_RE = re.compile(r"егэ|профил", re.I)
OGE_RE = re.compile(r"огэ", re.I)
NONTARGET_RE = re.compile(r"студент|\bвуз|универ|взросл|для себя|олимпиад", re.I)
TARGET_RE = re.compile(r"(\d{2,3})\s*\+|на\s*(\d{2,3})\s*балл|цель\s*(\d{2,3})", re.I)
FREQ_RE = re.compile(r"(\d)\s*раз[а]?\s*в\s*неделю", re.I)
PHONE_RE = re.compile(r"(?:\+?7|8)?[\s\-(]*9\d{2}[\s\-)]*\d{3}[\s\-]*\d{2}[\s\-]*\d{2}")
OUR_PRICE_RE = re.compile(r"(\d{3,5})\s*(?:р\b|р/|₽|руб)", re.I)

RECORD_RE = re.compile(r"как.*запис|набира(ете|ют)|свобод.*мест|есть\s+мест|записат", re.I)
AGREE_RE = re.compile(r"^\s*(да|давайте|давай|хорошо|ок|окей|можно|согласн|подходит|отлично|конечно|го|супер)\b"
                      r"|давайте|согласн|подходит|удобно|есть возможность|можно\s+(завтра|сегодня|послезавтра)", re.I)
DECLINE_TIME_RE = re.compile(r"не\s+удобно|неудобно|не\s+подходит|не\s+сможем|не\s+можем", re.I)
POSTPONE_RE = re.compile(r"боле(е|ет|ю|ла|л)|заболе|температур|перенес|не смож|не получ|позже|потом напиш|подума", re.I)
# «в 17», «в 17:30», «к 5 вечера» — время, которое назвал клиент (не «в 11 классе», не «на 70 баллов»)
CLIENT_HOUR_RE = re.compile(r"\b(?:в|к|на)\s*(\d{1,2})(?:[:.](\d{2}))?\b(?!\s*(?:кл|бал|раз|%|\+))", re.I)
WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")

# ---------- тексты Романа (дословно) ----------

# Ссылка Zoom с паролем — не в коде (репозиторий публичный): хранится в закрытой таблице, ключ zoom_link,
# меняется командой /zoom в боте. Здесь только признак, что ссылку уже отправляли.
ZOOM_MARK = "zoom.us/"

INVITE_BLOCK = (
    "Приглашаю вас на бесплатный пробный урок онлайн в ZOOM, на котором:\n"
    "📌 Познакомимся\n"
    "📈 Определимся с целями и трудностями\n"
    "📝 Я выстрою стратегию подготовки\n"
    "📚 Расскажу более конкретно, как будет устроена наша работа\n\n"
    "Попрошу быть с камерой."
)

def confirm_block(zoom_link: str) -> str:
    return (
        "Хорошо, записал!\n"
        "🔗Ссылка для подключения\n\n"
        f"{zoom_link or '[ССЫЛКА ZOOM НЕ ЗАДАНА — пришлите её боту командой /zoom]'}\n\n"
        "Попрошу быть с камерой\n"
        "По времени будет +-30 минут"
    )

FORMAT_ANSWER = "Занятия проходят онлайн в Zoom — можно заниматься из дома, из любого города."
PRICE_ANSWER_DEFAULT = ("Стоимость зависит от частоты занятий и целей подготовки — точно сориентирую на пробном уроке, "
                        "когда посмотрю уровень.")
PLACES_ANSWER = "Да, есть свободные места."


# ---------- разбор всей переписки ----------

def _dt(ts) -> datetime:
    return datetime.fromtimestamp(ts or 0, MSK)


def analyze_dialog(messages: list[dict], now: datetime | None = None) -> dict:
    """messages — сообщения чата Avito от старых к новым: {"direction": "in"/"out", "text": str, "created": unix}.
    Возвращает всё, что известно о клиенте, на каком этапе разговор и что клиент пишет сейчас
    (сообщения после нашего последнего ответа)."""
    now = now or datetime.now(MSK)
    msgs = [m for m in messages if (m.get("text") or "").strip()]
    client_msgs = [m for m in msgs if m["direction"] == "in"]
    our_msgs = [m for m in msgs if m["direction"] == "out" and not m.get("system")]

    last_out_idx = max((i for i, m in enumerate(msgs) if m["direction"] == "out" and not m.get("system")), default=-1)
    batch = [m for m in msgs[last_out_idx + 1:] if m["direction"] == "in"]
    batch_text = " / ".join(m["text"].strip() for m in batch)
    our_last = our_msgs[-1] if our_msgs else None

    # клиенты часто дробят сообщение («953», «7118811») — склеиваем
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

    last_in_time = _dt(batch[-1]["created"]) if batch else now
    day = _requested_date(batch_text, last_in_time)

    # время: что предлагали мы последним сообщением и что выбрал клиент
    our_times = [x.replace(".", ":").zfill(5) for x in TIME_RE.findall(our_last["text"])] if our_last else []
    our_day = None
    if our_last and our_times:
        our_day = _requested_date(our_last["text"], _dt(our_last["created"])) or _dt(our_last["created"]).date()
    client_time = _client_time(batch_text)
    agrees = bool(AGREE_RE.search(batch_text)) and not DECLINE_TIME_RE.search(batch_text)

    confirmed = None  # (дата, "HH:MM") — о чём договорились
    choose_between = []
    if client_time:
        confirmed = (day or (our_day if client_time in our_times else None) or last_in_time.date(), client_time)
    elif agrees and len(our_times) == 1:
        confirmed = (day or our_day, our_times[0])
    elif agrees and len(our_times) >= 2:
        choose_between = our_times[:2]

    return {
        "grade": grade,
        "exam": exam,
        "target": target,
        "freq": f.group(1) if f else None,
        "phone": re.sub(r"[^\d+]", "", phone_m.group(0)) if phone_m else None,
        "category": category,
        "batch_text": batch_text,
        "invited": any("пробн" in m["text"].lower() for m in our_msgs),
        "link_sent": any(ZOOM_MARK in m["text"] for m in our_msgs),
        "price_told": our_price,
        "asked_grade": any("класс" in m["text"].lower() and "?" in m["text"] for m in our_msgs),
        "has_our_msgs": bool(our_msgs),
        # здороваемся, только если с нашего последнего сообщения прошло больше 12 часов
        "need_greeting": not our_last or (now - _dt(our_last["created"])) > timedelta(hours=12),
        "agrees": agrees,
        "declines_time": bool(DECLINE_TIME_RE.search(batch_text)),
        "postpones": bool(POSTPONE_RE.search(batch_text)),
        "asks_price": bool(PRICE_RE.search(batch_text)),
        "asks_format": bool(FORMAT_RE.search(batch_text)),
        "wants_record": bool(RECORD_RE.search(batch_text)),
        "day": day,
        "confirmed": confirmed,
        "choose_between": choose_between,
        "now": now,
    }


def _client_time(text: str) -> str | None:
    m = TIME_RE.search(text)
    if m:
        h, mm = re.split(r"[:.]", m.group(0))
        return f"{int(h):02d}:{mm}"
    m = CLIENT_HOUR_RE.search(text)
    if not m:
        return None
    h = int(m.group(1))
    if "вечер" in text.lower() or "дня" in text.lower():
        h = h + 12 if h < 12 else h
    if not 8 <= h <= 22:
        return None
    return f"{h:02d}:{m.group(2) or '00'}"


def _requested_date(text: str, base: datetime) -> date | None:
    low = text.lower()
    if "послезавтра" in low:
        return (base + timedelta(days=2)).date()
    if "завтра" in low:
        return (base + timedelta(days=1)).date()
    if "сегодня" in low:
        return base.date()
    for i, w in enumerate(WEEKDAYS):
        if w in low:
            return (base + timedelta(days=(i - base.weekday()) % 7 or 7)).date()
    return None


def day_label(d: date, today: date) -> str:
    if d == today:
        return "сегодня"
    if d == today + timedelta(days=1):
        return "завтра"
    return f"в {WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


def booking_key(d: date, hhmm: str) -> str:
    return f"{d.strftime('%d.%m.%Y')} {hhmm}"


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
    today = a["now"].date()
    if a["confirmed"]:
        d, hhmm = a["confirmed"]
        return f"выбрал(а) время: {day_label(d or today, today)} в {hhmm}"
    if a["postpones"]:
        return "просит отложить / не может сейчас"
    if a["day"] and (a["agrees"] or a["invited"]):
        return f"готов(а) на пробный, хочет {day_label(a['day'], today)}"
    if a["agrees"] and a["invited"]:
        return "готов(а) на пробный урок"
    if a["asks_price"]:
        return "спрашивает цену"
    if a["asks_format"]:
        return "уточняет формат"
    if a["wants_record"]:
        return "хочет записаться"
    snippet = a["batch_text"].replace("\n", " ")
    return (snippet[:60] + "…") if len(snippet) > 60 else (snippet or "ждём ответа клиента")


def category_text(a: dict) -> str:
    """Для столбца «Категория»: ЕГЭ / ОГЭ / нецелевой / не ясно + (что известно, что хочет сейчас)."""
    profile = ", ".join(p for p in profile_text(a).split(", ") if p != a["category"])
    details = [p for p in [profile or "класс не указан", _request_summary(a)] if p]
    return f"{a['category']} ({', '.join(details)})"


def classify_request(client_text: str) -> str:
    """Классификация по одному сообщению — когда переписки нет под рукой."""
    return category_text(analyze_dialog([{"direction": "in", "text": client_text or "", "created": 0}]))


# ---------- варианты ответа ----------
# Порядок всегда один: (1) приветствие, если разговор только начался или давно молчали,
# (2) ответ на вопрос клиента, (3) приглашение на пробный с ценностью под его цель, (4) конкретное время.

def _greeting(client_name: str, a: dict) -> str:
    if not a["need_greeting"]:
        return f"{client_name}," if client_name and client_name != "—" else ""
    h = a["now"].hour
    word = "Доброе утро" if 5 <= h < 12 else "Добрый день" if 12 <= h < 18 else "Добрый вечер" if 18 <= h < 23 \
        else "Здравствуйте"
    return f"{client_name}, {word.lower()}!" if client_name and client_name != "—" else f"{word}!"


def _say(opening: str, *parts: str) -> str:
    """Склеивает обращение и абзацы. После «Полина,» первая фраза идёт с маленькой буквы."""
    parts = [p for p in parts if p]
    if parts and opening.endswith(","):
        parts[0] = parts[0][:1].lower() + parts[0][1:]
        return (opening + " " + "\n\n".join(parts)).strip()
    return "\n\n".join([opening, *parts] if opening else parts).strip()


def _answers(a: dict, price_hint: str) -> list[str]:
    """Сначала — ответ на то, о чём спросил клиент."""
    out = []
    if a["wants_record"]:
        out.append(PLACES_ANSWER)
    if a["asks_price"]:
        price = price_hint or (f"около {a['price_told']}" if a["price_told"] else "")
        out.append(f"По стоимости — {price}, точнее сориентирую на бесплатном пробном уроке, когда посмотрю уровень "
                   "и цели."
                   if price else PRICE_ANSWER_DEFAULT)
    if a["asks_format"]:
        out.append(FORMAT_ANSWER)
    return out


def _value_line(a: dict) -> str:
    """Чем пробный полезен именно этому клиенту."""
    exam = a["exam"] or ("ЕГЭ" if a["category"] == "ЕГЭ" else "ОГЭ" if a["category"] == "ОГЭ" else "")
    if a["target"] and exam:
        return f"На пробном разберём, где сейчас теряются баллы и что нужно, чтобы выйти на {a['target']}+ на {exam}."
    if exam:
        return f"На пробном посмотрим текущий уровень и составим понятный план подготовки к {exam}."
    return "На пробном посмотрим текущий уровень и составим понятный план подготовки."


def _time_question(slots: list[str], a: dict, polite: bool = True) -> str:
    today = a["now"].date()
    lead = "Скажите, пожалуйста, будет удобно подключиться" if polite else "Удобно подключиться"
    if a["day"] and a["day"] != today:
        return f"Во сколько вам будет удобно подключиться {day_label(a['day'], today)}? Пробный идёт 30 минут."
    if len(slots) >= 2:
        return f"{lead} сегодня в {slots[0]} или в {slots[1]} мск?"
    if len(slots) == 1:
        return f"{lead} сегодня в {slots[0]} мск?"
    return "Скажите, пожалуйста, когда вам будет удобно подключиться — сегодня или завтра? Подберу время."


def _grade_question(a: dict) -> str:
    if a["grade"] or a["asked_grade"]:
        return ""
    return "Подскажите, в каком классе и к какому экзамену готовитесь — ЕГЭ или ОГЭ?"


def generate_variants(client_name: str, client_text: str, slots_text: str = "", dialog: dict | None = None,
                      booked: set | None = None, price_hint: str = "", zoom_link: str = "") -> list[str]:
    """slots_text — свободные окна на сегодня (уже без занятых и прошедших), booked — занятые «дд.мм.гггг ЧЧ:ММ»."""
    a = dialog or analyze_dialog([{"direction": "in", "text": client_text or "", "created": 0}])
    booked = booked or set()
    slots = [x.replace(".", ":").zfill(5) for x in TIME_RE.findall(slots_text or "")]
    today = a["now"].date()
    o = _greeting(client_name, a)
    answers = _answers(a, price_hint)

    # 1. Уже записан (ссылку отправляли) — подтверждаем, при просьбе — переносим
    if a["link_sent"] and not a["confirmed"]:
        if a["postpones"] or a["declines_time"]:
            return [
                _say(o, "Конечно, перенесём!", _time_question(slots, a)),
                _say(o, "Без проблем, давайте подберём другое время.", _time_question(slots, a)),
                _say(o, "Хорошо, перенесём. Напишите, когда вам будет удобно — подстроюсь."),
            ]
        return [
            _say(o, *answers, "Отлично, жду вас на пробном уроке! Ссылка для подключения выше 🔗"),
            _say(o, *answers, f"Жду вас! Ссылка на всякий случай:\n{zoom_link}\n\nПопрошу быть с камерой."
                 if zoom_link else "Жду вас! Если планы изменятся — просто напишите."),
            _say(o, *answers, "Отлично! Если планы изменятся — просто напишите, перенесём."),
        ]

    # 2. Договорились о конкретном времени — подтверждение со ссылкой (если окно свободно)
    if a["confirmed"]:
        d, hhmm = a["confirmed"]
        d = d or today
        when = f"{day_label(d, today)} в {hhmm} мск"
        alt = [s for s in slots if s != hhmm][:2] if d == today else []
        offer = f"Могу предложить {' или '.join(alt)} мск — как вам удобнее?" if alt else \
            "Подскажите, какое ещё время вам удобно?"
        if d == today and slots and hhmm not in slots and booking_key(d, hhmm) not in booked:
            # клиент назвал время вне ваших свободных окон на сегодня — не подтверждаем, предлагаем свободные
            return [
                _say(o, f"В {hhmm} сегодня, к сожалению, не получится. {offer}"),
                _say(o, f"На {hhmm} сегодня уже нет мест. {offer}"),
                _say(o, f"{hhmm}, к сожалению, занято. {offer}"),
            ]
        if booking_key(d, hhmm) in booked:
            return [
                _say(o, f"К сожалению, {when} уже занято. {offer}"),
                _say(o, f"Ой, на {hhmm} только что записался другой ученик. {offer}"),
                _say(o, f"{hhmm} уже занято, извините! {offer}"),
            ]
        return [
            confirm_block(zoom_link),
            confirm_block(zoom_link) + f"\n\nЖду вас {when}!",
            confirm_block(zoom_link) + f"\n\nЖду вас {when}! Если планы изменятся — напишите, перенесём.",
        ]

    # 3. Не может сейчас (заболел, просит позже) — не давим, оставляем дверь открытой
    if a["postpones"]:
        return [
            _say(o, *answers, "Выздоравливайте! Как станет лучше — напишите, подберу удобное время для бесплатного "
                              "пробного урока. Он онлайн и займёт всего 30 минут."),
            _say(o, *answers, "Понимаю, здоровье важнее. Давайте предварительно запланируем пробный урок на "
                              "ближайшие дни — когда примерно будет удобно? Если не получится, просто перенесём."),
            _say(o, *answers, "Скорейшего выздоровления! Пробный урок онлайн, всего 30 минут — можно подключиться из "
                              "дома, как только станет лучше. Напишите, когда будет удобно."),
        ]

    # 4. Согласен, но не выбрал из двух предложенных времён
    if a["choose_between"]:
        t1, t2 = a["choose_between"]
        return [
            _say(o, f"Отлично! Какое время вам удобнее — {t1} или {t2} мск?"),
            _say(o, f"Супер! Записываю вас — на {t1} или на {t2}?"),
            _say(o, f"Отлично! Подскажите, {t1} или {t2} мск — что удобнее?"),
        ]

    # 5. Уже приглашали, клиент согласен / назвал день — сразу предлагаем конкретное время
    if a["invited"] and (a["agrees"] or a["day"] or a["declines_time"]):
        lead = "Хорошо, подберём другое время." if a["declines_time"] else "Отлично!"
        return [
            _say(o, *answers, lead, _time_question(slots, a)),
            _say(o, *answers, "Да, конечно!" if not a["declines_time"] else lead, _time_question(slots, a, polite=False)),
            _say(o, *answers, lead, _time_question(slots, a), _grade_question(a)),
        ]

    # 6. Уже приглашали, клиент задал вопрос — отвечаем и возвращаем к записи, без повторного длинного приглашения
    if a["invited"]:
        return [
            _say(o, *answers, _value_line(a), _time_question(slots, a)),
            _say(o, *answers, "Давайте начнём с бесплатного пробного урока — так будет понятнее всего.",
                 _time_question(slots, a)),
            _say(o, *answers, _grade_question(a) or "Остались ли вопросы? С радостью отвечу и подберу время "
                                                    "для бесплатного пробного урока.", _time_question(slots, a)),
        ]

    # 7. Начало разговора: ответ на вопрос → приглашение (текст Романа) → время
    return [
        _say(o, *answers, INVITE_BLOCK + "\n" + _time_question(slots, a)),
        _say(o, *answers, _value_line(a), "Приглашаю на бесплатный пробный урок онлайн в Zoom (30 минут) — "
                                          "познакомимся и я выстрою стратегию подготовки.", _time_question(slots, a)),
        _say(o, *answers, _grade_question(a) or _value_line(a), INVITE_BLOCK + "\n" + _time_question(slots, a)),
    ]


def generate_draft(client_name: str, client_text: str, slots_text: str = "") -> str:
    return generate_variants(client_name, client_text, slots_text)[0]


# ---------- свободные окна ----------

def expand_slots(slots_text: str) -> list[str]:
    """«15:00, 18:00» — свободные часы: в каждый пишем двоих (пробный 30 минут) → 15:00, 15:30, 18:00, 18:30.
    «15:00-18:00» — промежуток: каждые 30 минут до конца. «16:30» — с половины: 16:30 и 17:00."""
    text = (slots_text or "").replace("—", "-").replace("–", "-")
    result = []

    def add(h: int, m: int) -> None:
        if 0 <= h < 24:
            result.append(f"{h:02d}:{m:02d}")

    for rng in re.finditer(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", text):
        start = int(rng.group(1)) * 60 + int(rng.group(2))
        end = int(rng.group(3)) * 60 + int(rng.group(4))
        for t in range(start, end, 30):
            add(t // 60, t % 60)
    text = re.sub(r"(\d{1,2})[:.](\d{2})\s*-\s*(\d{1,2})[:.](\d{2})", " ", text)
    for m in TIME_RE.finditer(text):
        h, mm = (int(x) for x in re.split(r"[:.]", m.group(0)))
        add(h, mm)
        t = h * 60 + mm + 30
        add(t // 60, t % 60)
    return sorted(set(result))
