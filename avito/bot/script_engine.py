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


def generate_draft(client_name: str, client_text: str, slots_text: str = "") -> str:
    text = client_text or ""
    parts = []

    greeting = f"{client_name}, добрый день!" if client_name and client_name != "—" else "Добрый день!"
    parts.append(greeting)

    if FORMAT_RE.search(text):
        parts.append(FORMAT_LINE)
    if PRICE_RE.search(text):
        parts.append(BASE_PRICE_LINE)

    m = CLASS_RE.search(text)
    if m:
        parts.append(f"Записал: {m.group(1)} класс.")

    if slots_text.strip():
        slots = [s.strip() for s in slots_text.split(",") if s.strip()][:2]
        if len(slots) == 2:
            parts.append(
                f"Приглашаю на бесплатный пробный урок онлайн (30–40 минут) — сегодня удобно в {slots[0]} "
                f"или в {slots[1]}? Познакомимся, посмотрим уровень и сразу обсудим план подготовки."
            )
        elif len(slots) == 1:
            parts.append(
                f"Приглашаю на бесплатный пробный урок онлайн (30–40 минут) — сегодня в {slots[0]} удобно?"
            )
    else:
        parts.append(
            "Приглашаю на бесплатный пробный урок онлайн (30–40 минут) — когда вам будет удобно сегодня или завтра?"
        )

    return " ".join(parts)
