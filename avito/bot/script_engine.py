import re
from datetime import date, datetime, timedelta, timezone

MSK = timezone(timedelta(hours=3))

PRICE_RE = re.compile(r"сто(ит|ят|имость)|\bцен[аыу]\b|сколько.*(сто|плат)|оплат|прайс|фикс", re.I)
FORMAT_RE = re.compile(r"\bочно\b|\bонлайн\b|удал[её]нн|как\s+(проход|занима)|где\s+занят|формат", re.I)
CLASS_RE = re.compile(r"(\d{1,2})\s*-?\s*(?:й|ый|ой|х|ых|ти|ом)?\s*кл", re.I)
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
# Клиент отказался — не уговариваем и не зовём на пробный, вежливо прощаемся и оставляем дверь открытой
REFUSE_RE = re.compile(r"уже\s+(нашл|наш[её]л|записал|занима|есть\s+репет)|нашли\s+друг|к\s+другому|другого\s+репет"
                       r"|нашли\s+репет|не\s+нужн|уже\s+не\s+над|не\s+актуальн|неактуальн|передумал|не\s+интерес"
                       r"|не\s+хоч(у|ем)|не\s+хотим|не\s+будем|не\s+планир"
                       r"|откаж|^\s*(нет,?\s+спасибо|не надо)\s*[.!]*\s*$", re.I)
# Голое «нет» — отказ, только если мы не спрашивали про время; на «будет удобно завтра в 12:00?» это «время не подходит»
BARE_NO_RE = re.compile(r"^\s*(нет|неь|не|нит|неа)\s*[.!]*\s*$", re.I)
ILL_RE = re.compile(r"боле(е|ет|ю|ла|л)|заболе|нездоров|температур|простыл|в больниц", re.I)
WILL_WRITE_RE = re.compile(r"напиш(у|ем)|сообщ(у|им)|отпиш(у|емся)|дам знать|свяжусь|свяжемся", re.I)
# Реклама/спам от сервисов (ссылки, «попробуйте наш…») — отвечать не нужно
SPAM_RE = re.compile(r"https?://|www\.|попробуйте\s+(провести|наш|сервис)|наш[аи]?\s+(платформ|сервис)", re.I)
POSTPONE_RE = re.compile(r"боле(е|ет|ю|ла|л)|заболе|температур|перенес|не смож|не получ|позже|потом напиш|подума", re.I)
# «в 17», «в 17:30», «к 5 вечера» — время, которое назвал клиент (не «в 11 классе», не «на 70 баллов»)
CLIENT_HOUR_RE = re.compile(r"\b(?:в|к|на)\s*(\d{1,2})(?:[:.](\d{2}))?\b(?!\s*(?:кл|бал|раз|%|\+))", re.I)
WEEKDAYS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]
WEEKDAY_NAMES = ["понедельник", "вторник", "среду", "четверг", "пятницу", "субботу", "воскресенье"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")

# Частые вопросы клиентов (по переписке Романа и методичке)
PHONE_Q_RE = re.compile(r"с\s+телефон|через\s+телефон|телефона\s+можно|ноутбук|компьютер\w*\s+(нет|слома)", re.I)
DURATION_Q_RE = re.compile(r"сколько\s+(длит|продолж|идет|идёт|по\s+времени)|продолжительн|сколько\s+минут", re.I)
GROUP_Q_RE = re.compile(r"индивидуальн|в\s+групп|группов|мини-?групп|один\s+на\s+один|1\s+на\s+1|несколькими", re.I)
HOMEWORK_Q_RE = re.compile(r"домашн|д/з|\bдз\b", re.I)
PAY_Q_RE = re.compile(r"оплат\w*\s+(за\s+кажд|раз\s+в\s+мес|помесячн)|оплачива\w*\s+(отдельно|кажд|за\s+кажд)|абонемент|как\s+(оплат|платит)|раз\s+в\s+м[еяи]сяц\s+плат",
                      re.I)
MESSENGER_Q_RE = re.compile(r"\bмакс\b|\bmax\b|ватсап|whatsapp|вотсап|телеграм|телеграмм|\bтг\b|вайбер|viber|"
                            r"ваш\s+(номер|телефон)|дайте\s+(номер|телефон)|номер\s+телефона\?", re.I)
# «занимаетесь с 5 классом?», «берёте 7 класс?»
TAKE_GRADE_Q_RE = re.compile(r"занимаетесь|бер[её]те|работаете|преподаете|преподаёте|подготавливаете|возьм[её]те", re.I)
# клиент прислал только номер телефона (обычно в ответ на просьбу Avito оставить номер)
PHONE_ONLY_RE = re.compile(r"^[\s+()\-\d]{10,18}$")
THINK_RE = re.compile(r"подума(ю|ем|ть)|посовету(юсь|емся)|обсуд(им|им с)|решим\s+и\s+напиш|будем\s+думать", re.I)
OFFLINE_ONLY_RE = re.compile(r"только\s+очно|ищ(у|ем)\s+(оффлайн|офлайн|очно)|хоч\w*\s+очно|на\s+дому|к\s+вам\s+приход|"
                             r"приходить\s+к|адрес|в\s+посёлке|в\s+поселке", re.I)
# Клиент на связи по уроку (подключается, ждёт, не может зайти) — это момент урока, отвечает только Роман
CONNECT_RE = re.compile(r"не\s+получа|не\s+могу\s+(зайти|войти|подключ)|не\s+открыва|зарегистр|регистрац|мы\s+(вас\s+)?ждём|"
                        r"мы\s+(вас\s+)?ждем|я\s+(вас\s+)?жду|подключаемся|состоится|вы\s+где|где\s+вы|комнате\s+ожидан|"
                        r"ну\s+что\b|я\s+зашел|я\s+зашёл|не\s+пускает|не\s+слышно|не\s+видно", re.I)
# «12000 — это за сколько занятий?»
PRICE_DETAIL_RE = re.compile(r"за\s+сколько\s+(занят|урок)|сколько\s+(занятий|уроков)\s+в\s+(месяц|неделю)|это\s+в\s+месяц|"
                             r"за\s+месяц\?|за\s+час\?", re.I)
PRICE_DETAIL_ANSWER = ("Это средний расчёт на 8 занятий в месяц, может быть и меньше — всё зависит от количества занятий "
                       "в неделю, а это определим по текущим знаниям ученика на пробном уроке.")
# Короткое «ок/спасибо/до встречи» — на это бот может ответить сам и после записи
ACK_RE = re.compile(r"^\W*(ок|окей|хорошо|спасибо|спс|понял|поняла|понятно|до\s+встречи|отлично|супер|ладно|договорились)"
                    r"[\s!.,)👍🙏]*(спасибо|до\s+встречи)?[\s!.,)👍🙏]*$", re.I)
# С этим бот сам не справится — только карточка Роману, без автоответа
MANUAL_ONLY_RE = re.compile(r"реш(ить|ите|и|ать)\b.{0,25}(задач|контрольн|тест|вариант|домашк)|контрольн\w*\s+(работ|завтра)|"
                            r"сессия|высш\w+\s+мат|"
                            r"вуз|институт|универ|колледж|олимпиад|обман|жалоб|плох\w+\s+отзыв|верните|хамств|ужас|позор|"
                            r"мошенн|деньги\s+назад", re.I)

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
PLACES_ANSWER = "Да, есть свободные места."

# Цена — ступенями, как в методичке и переписке Романа: сначала без цифр, на повторный вопрос — ориентир,
# на настойчивый — абонемент. Ступень выбирается по тому, что уже говорили в этом чате.
PRICE_STEP_1 = ("Стоимость занятий зависит от многих факторов: частоты занятий, формы оплаты, начального уровня и целей "
                "подготовки. Более подробно смогу сориентировать после бесплатного пробного урока, куда вас и приглашаю 😊")
PRICE_STEP_3 = ("Стоимость абонемента моих занятий — 12000 ₽ в месяц; если учитывать дополнительные занятия с ответами "
                "на вопросы в индивидуальном формате, это примерно 660 ₽ в час.")
OFFLINE_ANSWER = ("Я готовлю к экзаменам только онлайн. Это и экономия времени, и более эффективные занятия благодаря "
                  "наглядности материала и презентациям. Так как мы занимаемся с камерой, мы видим друг друга через экран — "
                  "фактически ничем не отличается от очных занятий 😊")
FAQ_ANSWERS = {
    "phone_q": "Да, с телефона тоже можно подключиться.",
    "duration_q": "Пробное занятие займёт около 30 минут.",
    "group_q": "Пробное занятие проходит индивидуально.",
    "homework_q": ("Да, конечно, обязательно даю домашнее задание для закрепления каждой темы и детально его проверяю, "
                   "чтобы понимать, что нужно исправить и улучшить."),
    "pay_q": "Оплата — раз в месяц абонементом, в начале месяца; возможны и другие форматы, обсудим на пробном.",
    "messenger_q": "Занятия проходят в Zoom, а общаться можем здесь или в Телеграме: @roma_math",
}
THINK_ANSWER = "Скажите, пожалуйста, над чем нужно подумать? Пробное занятие бесплатное и ни к чему вас не обязывает 😊"
# Обещания по результатам — из методички Романа, дословно
RESULT_EGE = "Вполне реально! В 2024 году каждый второй мой ученик сдал ЕГЭ на 80+."
RESULT_OGE = "Вполне реально! В 2025 году ни один мой ученик не сдал ОГЭ ниже «4»."


# ---------- разбор всей переписки ----------

def _dt(ts) -> datetime:
    return datetime.fromtimestamp(ts or 0, MSK)


def analyze_dialog(messages: list[dict], now: datetime | None = None) -> dict:
    """messages — сообщения чата Avito от старых к новым: {"direction": "in"/"out", "text": str, "created": unix}.
    Возвращает всё, что известно о клиенте, на каком этапе разговор и что клиент пишет сейчас
    (сообщения после нашего последнего ответа)."""
    now = now or datetime.now(MSK)
    msgs = [m for m in messages if (m.get("text") or "").strip()]
    # служебные сообщения Avito («Пользователь попросил вас позвонить…») приходят как входящие — это не слова клиента
    sys_in = [m for m in msgs if m["direction"] == "in" and m["text"].lstrip().startswith("[Системное сообщение]")]
    msgs = [m for m in msgs if m not in sys_in]
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

    bare_no = bool(BARE_NO_RE.match(batch_text))
    asked_time = bool(our_last and (our_times or re.search(r"удобно|подключит|сможете", our_last["text"], re.I)))

    # прошедшее время никогда не подтверждаем и не предлагаем: «да» на наше вчерашнее «сегодня в 16:30»
    # или «давайте в 13:00», когда уже вечер, — это не запись, а повод предложить новое время
    time_passed = False
    if confirmed and is_past(confirmed[0] or now.date(), confirmed[1], now):
        confirmed, time_passed = None, True
    if choose_between:
        choose_between = [t for t in choose_between if not is_past(our_day or now.date(), t, now)]
        if len(choose_between) < 2:
            time_passed = time_passed or not choose_between
            choose_between = []

    return {
        "grade": grade,
        "exam": exam,
        "target": target,
        "freq": f.group(1) if f else None,
        "phone": re.sub(r"[^\d+]", "", phone_m.group(0)) if phone_m else None,
        "category": category,
        "batch_text": batch_text,
        "invited": any("пробн" in m["text"].lower() for m in our_msgs),
        # ссылку отправляли недавно — значит, человек записан на ближайшие дни; старая ссылка (пробный уже прошёл) не в счёт
        "link_sent": any(ZOOM_MARK in m["text"] and now - _dt(m["created"]) < timedelta(days=3) for m in our_msgs),
        "price_told": our_price,
        "asked_grade": any("класс" in m["text"].lower() and "?" in m["text"] for m in our_msgs),
        "has_our_msgs": bool(our_msgs),
        # здороваемся, только если с нашего последнего сообщения прошло больше 12 часов
        "need_greeting": not our_last or (now - _dt(our_last["created"])) > timedelta(hours=12),
        "agrees": agrees,
        "declines_time": bool(DECLINE_TIME_RE.search(batch_text)) or (asked_time and (bare_no or bool(
            re.match(r"^\W*нет\b", batch_text, re.I)) and not REFUSE_RE.search(batch_text))),
        # «не получится, только в понедельник» — это просьба о другом дне, а не «отложить»
        "postpones": bool(POSTPONE_RE.search(batch_text)) and not (day and not ILL_RE.search(batch_text)),
        "asks_price": bool(PRICE_RE.search(batch_text)),
        "asks_format": bool(FORMAT_RE.search(batch_text)),
        "wants_record": bool(RECORD_RE.search(batch_text)),
        "day": day,
        "confirmed": confirmed,
        "choose_between": choose_between,
        "time_passed": time_passed,
        "declined": bool(REFUSE_RE.search(batch_text)) or (bare_no and not asked_time),
        "spam": bool(SPAM_RE.search(batch_text)),
        "now": now,
        # вопросы из методички
        **{k: bool(rx.search(batch_text)) for k, rx in (
            ("phone_q", PHONE_Q_RE), ("duration_q", DURATION_Q_RE), ("group_q", GROUP_Q_RE),
            ("homework_q", HOMEWORK_Q_RE), ("pay_q", PAY_Q_RE), ("messenger_q", MESSENGER_Q_RE),
            ("thinks", THINK_RE), ("offline_only", OFFLINE_ONLY_RE), ("manual_only", MANUAL_ONLY_RE))},
        # что уже говорили — чтобы не повторяться и идти по ступеням
        # 1 — ещё ничего не говорили, 2 — уже сказали «зависит от…», 3 — уже называли цифру (дальше — абонемент)
        "price_step": 3 if (our_price or any(re.search(r"12\s?000|абонемент", m["text"]) for m in our_msgs))
        else 2 if any("зависит от" in m["text"].lower() for m in our_msgs) else 1,
        "results_said": any("вполне реально" in m["text"].lower() for m in our_msgs),
        "compare_tried": any("сравните" in m["text"].lower() for m in our_msgs),
        "our_times": our_times,
        "our_last_text": our_last["text"] if our_last else "",
        "our_last_ts": our_last["created"] if our_last else 0,
        "batch_len": len(batch_text),
        "call_request": bool(sys_in and "позвонить" in sys_in[-1]["text"]
                             and (not msgs or sys_in[-1]["created"] >= msgs[-1]["created"])),
        "connecting": bool(CONNECT_RE.search(batch_text)),
        "price_detail_q": bool(PRICE_DETAIL_RE.search(batch_text)),
        "ack_only": bool(ACK_RE.match(batch_text)),
        "gave_phone": bool(batch) and all(PHONE_ONLY_RE.match(m["text"].strip()) or not m["text"].strip() for m in batch[-1:]),
        "take_grade_q": bool(grade and grade.isdigit() and int(grade) <= 8 and TAKE_GRADE_Q_RE.search(batch_text)),
    }


def is_past(d: date, hhmm: str, now: datetime, margin_min: int = 15) -> bool:
    """Время уже прошло (или до него меньше 15 минут — клиент не успеет подключиться)."""
    h, m = map(int, hhmm.split(":"))
    return datetime(d.year, d.month, d.day, h, m, tzinfo=MSK) < now + timedelta(minutes=margin_min)


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
    prep = "во" if d.weekday() == 1 else "в"  # «во вторник»
    return f"{prep} {WEEKDAY_NAMES[d.weekday()]}, {d.day} {MONTHS[d.month - 1]}"


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
    if a["spam"]:
        return "реклама/спам — отвечать не нужно"
    if a["declined"]:
        return "отказ — уже не нужно"
    if a["time_passed"]:
        return "согласен(на), но предложенное время уже прошло"
    if a["declines_time"]:
        return "предложенное время не подошло"
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
        step = a.get("price_step", 1)
        price = price_hint or (f"около {a['price_told']}" if a["price_told"] else "")
        if step >= 3:
            out.append(PRICE_STEP_3)
        elif step == 2 and price:
            out.append(f"Мои занятия стоят {price}, всё зависит от формата, частоты и способа оплаты. После пробного урока "
                       "смогу сказать, какой формат подойдёт больше всего с учётом ситуации и пробелов 😊")
        else:
            out.append(PRICE_STEP_1)
    if a.get("offline_only"):
        out.append(OFFLINE_ANSWER)
    elif a["asks_format"]:
        out.append(FORMAT_ANSWER)
    if a.get("gave_phone"):
        out.insert(0, "Спасибо!")
    if a.get("take_grade_q"):
        out.append(f"Да, смогу вам помочь с {a['grade']} классом 😊")
    if a.get("price_detail_q"):
        out.append(PRICE_DETAIL_ANSWER)
    for key, text in FAQ_ANSWERS.items():
        if a.get(key):
            out.append(text)
    # цель уже известна, а «вполне реально» ещё не говорили — как в методичке, перед приглашением
    if a["target"] and not a.get("results_said") and not a["has_our_msgs"]:
        out.append(RESULT_OGE if a["category"] == "ОГЭ" else RESULT_EGE if a["category"] == "ЕГЭ" else "")
    return [x for x in out if x]


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
    if a["now"].hour >= 19:  # вечером «сегодня» уже не предлагаем
        return "Скажите, пожалуйста, во сколько вам будет удобно подключиться завтра? Подберу время."
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
    today = a["now"].date()
    # окна «сегодня» — только будущие, даже если кто-то передал список с прошедшими
    slots = [s for s in (x.replace(".", ":").zfill(5) for x in TIME_RE.findall(slots_text or ""))
             if not is_past(today, s, a["now"])]
    if a.get("declines_time"):  # от предложенного времени отказались — его больше не предлагаем
        slots = [s for s in slots if s not in a.get("our_times", [])]
    o = _greeting(client_name, a)
    answers = _answers(a, price_hint)

    # 0. Реклама или отказ — не продаём: на рекламу коротко, отказавшимся — вежливо, без приглашения
    if a["spam"]:
        return ["Спасибо, не интересует.", "Спасибо, но не актуально.", "Спасибо, нам не подходит."]
    found_other = bool(re.search(r"нашл|наш[её]л|друг\w*\s+репет|к\s+другому|записал", a["batch_text"], re.I))
    compare_tried = "сравните" in a.get("our_last_text", "").lower() or a.get("compare_tried", False)
    if a["declined"] and found_other and a["invited"] and not compare_tried:
        # как Роман: одна мягкая попытка — сравнить на бесплатном пробном; на повторный отказ уже прощаемся
        return [
            _say(o, "Понимаю! Приходите на бесплатный пробный урок — сравните и примете решение, с кем лучше "
                    "заниматься. Он ни к чему не обязывает 😊"),
            _say(o, "Рад, что нашли! Если захотите сравнить подходы — бесплатный пробный урок в силе, приходите 😊"),
            _say(o, "Понял вас, спасибо, что ответили! Если понадобится помощь — пишите, буду рад помочь. Удачи на экзамене!"),
        ]
    if a["declined"]:
        return [
            _say(o, "Понял вас, спасибо, что ответили! Если понадобится помощь с математикой — пишите, буду рад "
                    "помочь. Удачи на экзамене!"),
            _say(o, "Спасибо, что предупредили! Желаю успехов в подготовке. Если что-то не сложится — пишите, помогу."),
            _say(o, "Хорошо, понял. Если появятся вопросы по математике — пишите в любое время. Успехов!"),
        ]

    # 1. Уже записан (ссылку отправляли) — подтверждаем, при просьбе — переносим
    if a["link_sent"] and not a["confirmed"]:
        ill = bool(ILL_RE.search(a["batch_text"]))
        if a["postpones"] and WILL_WRITE_RE.search(a["batch_text"]):
            # клиент сам обещал написать позже — не спрашиваем время, а спокойно ждём
            when = f" {day_label(a['day'], today)}" if a["day"] else ""
            wish = "Выздоравливайте! " if ill else ""
            return [
                _say(o, f"Конечно, перенесём! {wish}Буду ждать вашего сообщения{when} — подберём удобное время."),
                _say(o, f"Хорошо, без проблем. {wish}Напишите{when}, как будет удобно, — договоримся о времени."),
                _say(o, f"Понял вас, перенесём. {wish}Жду вашего сообщения{when}!"),
            ]
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

    # 3а. «Подумаем» — ответ Романа на это возражение, и снова предлагаем время
    if a.get("thinks") and not ILL_RE.search(a["batch_text"]):
        return [
            _say(o, *answers, THINK_ANSWER, _time_question(slots, a)),
            _say(o, *answers, "Конечно! Пробный урок как раз поможет принять решение: он бесплатный, длится около "
                              "30 минут и ни к чему не обязывает.", _time_question(slots, a)),
            _say(o, *answers, "Хорошо! Если появятся вопросы — пишите, с радостью отвечу 😊"),
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
        starts_no = bool(re.match(r"^\W*(нет|не\s)", a["batch_text"], re.I))
        lead = "Отлично! Предложенное время, к сожалению, уже прошло — давайте подберём новое." if a["time_passed"] \
            else "Хорошо, подберём другое время." if (a["declines_time"] or starts_no) else "Отлично!"
        if a["declines_time"] and not slots and not a["day"]:
            # предложенное время не подошло, других окон на сегодня нет — спрашиваем, когда удобно клиенту
            return [
                _say(o, *answers, "Хорошо, подберём другое время. Подскажите, в какой день и во сколько вам будет удобно? "
                                  "Пробный идёт 30 минут."),
                _say(o, *answers, "Без проблем! Когда вам удобнее — в будни или в выходные, днём или вечером? Подстроюсь."),
                _say(o, *answers, "Понял. Напишите удобные день и время — я подберу окно для бесплатного пробного урока."),
            ]
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
    # класс ещё не знаем — уточняем сразу, как в методичке (пред-диагностика), чтобы подготовить пробный под уровень
    class_q = "" if (a["grade"] or a["asked_grade"]) else "И подскажите, пожалуйста, в каком классе ученик?"
    return [
        _say(o, *answers, INVITE_BLOCK + "\n" + _time_question(slots, a), class_q),
        _say(o, *answers, _value_line(a), "Приглашаю на бесплатный пробный урок онлайн в Zoom (30 минут) — "
                                          "познакомимся и я выстрою стратегию подготовки.", _time_question(slots, a)),
        _say(o, *answers, _grade_question(a) or _value_line(a), INVITE_BLOCK + "\n" + _time_question(slots, a)),
    ]


RECOGNIZED = ("asks_price", "asks_format", "wants_record", "agrees", "declines_time", "postpones", "declined",
              "phone_q", "duration_q", "group_q", "homework_q", "pay_q", "messenger_q", "thinks", "offline_only",
              "choose_between", "day", "time_passed", "confirmed", "gave_phone", "take_grade_q", "price_detail_q")
INFO_RE = re.compile(r"\d{1,2}\s*-?\s*(?:й|ый|ой)?\s*кл|^\s*\d{1,2}\s*$|егэ|огэ|профил|баз\w*\s+уров|\d{2,3}\s*\+|балл|"
                     r"^\s*(здравствуйте|добрый\s+(день|вечер)|доброе\s+утро|привет)\W*$", re.I)


def auto_decision(a: dict, slots_text: str = "") -> tuple[bool, str]:
    """Можно ли отправить ответ клиенту без Романа. Только там, где ситуация распознана уверенно;
    всё остальное — карточкой Роману. Возвращает (можно, почему нельзя)."""
    text = a["batch_text"]
    if a.get("call_request"):
        return False, "клиент через Avito просит перезвонить"
    if not text.strip():
        return False, "клиент ничего не написал"
    if a.get("connecting"):
        return False, "клиент на связи по уроку (подключается / ждёт / не может зайти) — ответьте сами"
    if a["link_sent"] and not (a.get("ack_only") or (a["postpones"] and WILL_WRITE_RE.search(text))):
        return False, "клиент уже записан (ссылку отправляли) — по записанным отвечаете вы"
    if a["spam"]:
        return False, "похоже на рекламу — отвечать не нужно"
    if a.get("manual_only"):
        return False, "нестандартная просьба (задача, вуз, жалоба) — нужен ваш ответ"
    if a.get("batch_len", 0) > 400:
        return False, "длинное сообщение — лучше ответить самому"
    if a["confirmed"]:
        d, hhmm = a["confirmed"]
        free = [x.replace(".", ":").zfill(5) for x in TIME_RE.findall(slots_text or "")]
        if (d or a["now"].date()) != a["now"].date() or hhmm not in free:
            return False, f"клиент выбрал {hhmm}" + ("" if (d or a["now"].date()) == a["now"].date() else
                                                    f" на {d:%d.%m}") + " — этого окна нет в ваших свободных, подтвердите сами"
        return True, ""
    known = any(a.get(k) for k in RECOGNIZED) or bool(INFO_RE.search(text))
    if "?" in text and not known:
        return False, "вопрос, на который нет готового ответа"
    if not known and a["has_our_msgs"]:
        return False, "не понял, что ответить"
    return True, ""


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
