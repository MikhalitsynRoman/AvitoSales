import re

PRICE_RE = re.compile(r"сто(ит|имость)|\bцена\b|сколько.*(сто|плат)", re.I)
FORMAT_RE = re.compile(r"\bочно\b|\bонлайн\b|удал[её]нн", re.I)
CLASS_RE = re.compile(r"(\d{1,2})\s*кл", re.I)

RECORD_RE = re.compile(r"как.*запис|набира(ете|ют)|свобод.*мест", re.I)


def classify_request(client_text: str) -> str:
    """Грубая категоризация обращения: ЕГЭ (10-11 кл) / ОГЭ (9 кл) / нецелевой + (класс, короткий запрос)."""
    text = client_text or ""

    m = CLASS_RE.search(text)
    grade = m.group(1) if m else None

    if grade in ("10", "11"):
        category = "ЕГЭ"
    elif grade == "9":
        category = "ОГЭ"
    else:
        category = "нецелевой"

    if PRICE_RE.search(text):
        query = "спрашивает цену"
    elif FORMAT_RE.search(text):
        query = "уточняет формат (очно/онлайн)"
    elif RECORD_RE.search(text):
        query = "хочет записаться"
    else:
        snippet = text.strip().replace("\n", " ")
        query = (snippet[:60] + "…") if len(snippet) > 60 else (snippet or "без текста")

    grade_label = f"{grade} класс" if grade else "класс не указан"
    return f"{category} ({grade_label}, {query})"

BASE_PRICE_LINE = "Занятие стоит от 500₽, точную сумму подскажу после пробного урока — зависит от уровня и частоты занятий."
FORMAT_LINE = "Занятия онлайн, в Zoom, из любой точки."


TIME_RE = re.compile(r"\b\d{1,2}[:.]\d{2}\b")


def _parse_slots(slots_text: str) -> list[str]:
    return [t.replace(".", ":") for t in TIME_RE.findall(slots_text or "")][:2]


def _time_question(slots: list[str]) -> str:
    if len(slots) >= 2:
        return f"сегодня удобно в {slots[0]} или в {slots[1]}?"
    if len(slots) == 1:
        return f"сегодня в {slots[0]} удобно?"
    return "когда вам будет удобно сегодня или завтра?"


def _facts(text: str) -> list[str]:
    facts = []
    if FORMAT_RE.search(text):
        facts.append(FORMAT_LINE)
    if PRICE_RE.search(text):
        facts.append(BASE_PRICE_LINE)
    return facts


def _greeting(client_name: str, word: str = "добрый день") -> str:
    if client_name and client_name != "—":
        return f"{client_name}, {word}!"
    return f"{word.capitalize()}!"


def generate_variants(client_name: str, client_text: str, slots_text: str = "") -> list[str]:
    text = client_text or ""
    slots = _parse_slots(slots_text)
    facts = _facts(text)
    m = CLASS_RE.search(text)
    grade = m.group(1) if m else None

    v1 = [_greeting(client_name), *facts]
    if grade:
        v1.append(f"Записал: {grade} класс.")
    v1.append(
        f"Приглашаю на бесплатный пробный урок онлайн (30–40 минут) — {_time_question(slots)} "
        f"Познакомимся, посмотрим уровень и сразу обсудим план подготовки."
    )

    v2 = [_greeting(client_name, "здравствуйте"), "Да, есть свободные места.", *facts]
    v2.append(f"Давайте начнём с бесплатного пробного урока — {_time_question(slots)}")

    v3 = [_greeting(client_name), *facts]
    if grade:
        v3.append(f"Подскажите, на какой балл готовитесь в {grade} классе и что сейчас даётся сложнее всего?")
    else:
        v3.append("Подскажите, в каком классе и к какому экзамену готовитесь — ЕГЭ или ОГЭ?")
    v3.append(f"А пробный урок бесплатный — {_time_question(slots)}")

    return [" ".join(v1), " ".join(v2), " ".join(v3)]


def generate_draft(client_name: str, client_text: str, slots_text: str = "") -> str:
    return generate_variants(client_name, client_text, slots_text)[0]
