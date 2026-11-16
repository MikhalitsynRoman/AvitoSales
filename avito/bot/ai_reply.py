"""Ответ клиенту «по смыслу» через YandexGPT (Yandex AI Studio).

Модель читает всю переписку, правила Романа и свободные окна и решает: ответить, записать на окно или отдать
Роману. Бот не доверяет ей вслепую — каждый ответ проходит проверку (validate), и при любом сомнении бот
отвечает по правилам script_engine, как раньше. Ключ и каталог — в переменных YC_API_KEY и YC_FOLDER_ID
(файл credentials/.env на хостинге); без них модуль просто выключен.
"""
import json
import os
import re
import time
from datetime import date, datetime

from script_engine import MSK, TIME_RE, ZOOM_MARK, booking_key, category_text, day_label

MODEL_NAME = os.getenv("YC_MODEL", "yandexgpt")  # YandexGPT Pro; «yandexgpt-lite» — дешевле, но слабее
MAX_CALLS_PER_DAY = 300   # страховка от счёта: дальше — ответы по правилам
HISTORY_MESSAGES = 30
ALLOWED_PRICES = {"500", "660", "1000", "12000", "12 000"}

SYSTEM_PROMPT = """Ты — Роман, репетитор по профильной математике (подготовка к ЕГЭ и ОГЭ, онлайн в Zoom). Ты переписываешься \
с клиентом на Avito от своего имени. Пишут родители или сами ученики.

Главная цель — записать человека на БЕСПЛАТНЫЙ пробный урок (30 минут, онлайн в Zoom, с камерой). Но сначала всегда \
отвечай на то, что спросил клиент, — коротко и по делу, — и только потом веди к записи. Создавай ценность пробного под \
цель клиента: на пробном познакомимся, определимся с целями и трудностями, я выстрою стратегию подготовки и расскажу, как \
будет устроена работа.

Держи нить всего разговора: учитывай, что клиент уже писал и что ты уже отвечал. Не повторяй свои прошлые сообщения \
слово в слово. Если клиент возвращается к тому же возражению — отвечай глубже, другими доводами.

Стиль: вежливо, тепло, уверенно, без давления и без канцелярита. Обращайся на «вы» и по имени. Коротко: 2–5 предложений, \
можно смайлик 😊. Никогда не груби и не спорь — плохой отзыв на Avito очень вредит.

Факты, которые можно использовать (ничего другого о себе не выдумывай):
- Готовлю только онлайн в Zoom: это экономия времени на дорогу, наглядные авторские материалы и презентации, с камерой \
вижу, как ученик решает, и сразу поправляю — фактически не отличается от очных занятий. Очно не занимаюсь.
- Подключиться можно и с телефона. Пробное — индивидуально, около 30 минут, бесплатно и ни к чему не обязывает.
- Результаты: в 2024 году каждый второй мой ученик сдал ЕГЭ на 80+; в 2025 году ни один мой ученик не сдал ОГЭ ниже «4».
- Домашнее задание даю по каждой теме и проверяю детально.
- Оплата — раз в месяц абонементом в начале месяца.
- Цена ступенями (ступень указана в данных): 1 — «стоимость зависит от частоты, формы оплаты, уровня и целей, точнее \
сориентирую на бесплатном пробном»; 2 — назвать ориентир из данных; 3 — «абонемент 12000 ₽ в месяц, это примерно 660 ₽ \
в час». Никаких других сумм не называй.
- Связаться вне Avito можно в Телеграме: @roma_math.
- Беру в основном 9–11 классы (ОГЭ/ЕГЭ), но с младшими тоже могу помочь.

Время пробного: предлагай ТОЛЬКО окна из списка «Свободные окна» (день и время ровно как там), лучше два ближайших. \
Если клиент сам назвал день или время — проверь по списку; если окна нет — извинись и предложи ближайшие свободные. \
Если клиент согласился на конкретное свободное окно — верни action "book" с этой датой и временем (подтверждение со \
ссылкой бот отправит сам; сам не пиши «записал» и не присылай ссылок).

Если клиент отказался — вежливо попрощайся и оставь дверь открытой; не уговаривай больше одного раза. Если клиент болеет \
или просит перенести — посочувствуй и не дави.

Верни action "manual" (ответит сам Роман), если: просят решить задачу/контрольную или помочь на экзамене; вуз, высшая \
математика; жалоба или конфликт; вопрос, на который из фактов выше нельзя честно ответить; что-то странное или \
непонятное.

Ответь строго JSON без пояснений:
{"action": "reply" | "book" | "manual", "text": "сообщение клиенту (для reply)", "date": "ДД.ММ.ГГГГ (для book)", \
"time": "ЧЧ:ММ (для book)", "reason": "коротко, почему manual"}"""

_sdk = None
_usage = {"day": "", "calls": 0, "tokens_in": 0, "tokens_out": 0, "fails": 0}


def available() -> bool:
    return bool(os.getenv("YC_API_KEY") and os.getenv("YC_FOLDER_ID"))


def usage() -> dict:
    return dict(_usage)


def _model():
    global _sdk
    from yandex_ai_studio_sdk import AIStudio  # импорт здесь: без ключа и пакета бот работает как раньше
    if _sdk is None:
        _sdk = AIStudio(folder_id=os.environ["YC_FOLDER_ID"], auth=os.environ["YC_API_KEY"])
    return _sdk.models.completions(MODEL_NAME).configure(temperature=0.3, max_tokens=800, response_format="json")


def _schedule_text(schedule: dict | None, today: date) -> str:
    if not schedule:
        return "Свободные окна: расписание не задано — спроси, когда клиенту удобно, и не называй конкретное время."
    lines = []
    for d in sorted(schedule):
        times = schedule[d]
        lines.append(f"- {day_label(d, today)} ({d:%d.%m.%Y}): {', '.join(times) if times else 'нет окон'}")
    return "Свободные окна (по 30 минут, время московское):\n" + "\n".join(lines)


def build_user_message(name: str, dialog: list[dict], a: dict, schedule: dict | None, price_hint: str,
                       rule_draft: str) -> str:
    now = a["now"]
    history = []
    for m in [m for m in dialog if m["text"].strip() and not m.get("system")][-HISTORY_MESSAGES:]:
        who = "Клиент" if m["direction"] == "in" else "Роман"
        when = datetime.fromtimestamp(m["created"], MSK).strftime("%d.%m %H:%M")
        history.append(f"[{when}] {who}: {m['text'].strip()}")
    facts = [
        f"Сейчас: {now:%d.%m.%Y %H:%M} (Москва), {day_label(now.date(), now.date())}.",
        f"Имя клиента в Avito: {name}.",
        f"Что известно: {category_text(a)}.",
        f"Ступень по цене сейчас: {a.get('price_step', 1)}; ориентир для ступени 2: {price_hint or 'в среднем от 1000 ₽ в час'}.",
        "Ссылку Zoom уже отправляли — клиент записан." if a.get("link_sent") else "Клиент ещё не записан.",
        _schedule_text(schedule, now.date()),
    ]
    return ("Данные:\n" + "\n".join(facts) + "\n\nПереписка (от старых к новым):\n" + "\n".join(history)
            + f"\n\nЧерновик по шаблону (для ориентира, можно лучше):\n{rule_draft}"
            + "\n\nНапиши следующее сообщение Романа клиенту. Ответь JSON.")


def _all_free_times(schedule: dict | None) -> set:
    return {t for times in (schedule or {}).values() for t in times}


_WEEKDAY_STEMS = ["понедельник", "вторник", "сред", "четверг", "пятниц", "суббот", "воскресен"]


def _day_before(context: str, schedule: dict | None) -> date | None:
    """К какому дню относится время в тексте: ближайшее перед ним «сегодня/завтра/в среду/18.11»."""
    if not schedule:
        return None
    days = sorted(schedule)
    today = days[0]
    low = context.lower()
    found = []  # (позиция, дата)
    for word, d in (("сегодня", today), ("послезавтра", today.fromordinal(today.toordinal() + 2)),
                    ("завтра", today.fromordinal(today.toordinal() + 1))):
        i = low.rfind(word)
        if i >= 0 and not (word == "завтра" and low[max(0, i - 5):i].endswith("после")):
            found.append((i, d))
    for wd, stem in enumerate(_WEEKDAY_STEMS):
        i = low.rfind(stem)
        if i >= 0:
            found += [(i, d) for d in days if d.weekday() == wd][:1]
    for m in re.finditer(r"\b(\d{1,2})\.(\d{2})\b|\b(\d{1,2})\s+(ноябр|декабр|январ|феврал|март|апрел|ма[йя]|июн|июл|август|сентябр|октябр)", low):
        for d in days:
            if (m.group(1) and int(m.group(1)) == d.day and int(m.group(2)) == d.month) or \
                    (m.group(3) and int(m.group(3)) == d.day):
                found.append((m.start(), d))
    return max(found)[1] if found else None


def validate(result: dict, schedule: dict | None) -> tuple[bool, str]:
    """Проверка ответа модели. Любое сомнение — False, тогда бот ответит по правилам."""
    action = result.get("action")
    if action not in ("reply", "book", "manual"):
        return False, f"непонятное действие {action!r}"
    if action == "manual":
        return True, ""
    if action == "book":
        try:
            d = datetime.strptime(result.get("date", ""), "%d.%m.%Y").date()
        except ValueError:
            return False, "неверная дата записи"
        t = str(result.get("time", "")).strip().replace(".", ":").zfill(5)
        if not schedule or t not in schedule.get(d, []):
            return False, f"окна {d:%d.%m} {t} нет среди свободных"
        result["date_obj"], result["time"] = d, t
        return True, ""
    text = (result.get("text") or "").strip()
    if not text or len(text) > 1500:
        return False, "пустой или слишком длинный ответ"
    if ZOOM_MARK in text or "http" in text.lower() or "www." in text.lower():
        return False, "ссылка в ответе (ссылку присылает только бот)"
    if re.search(r"\bзаписал[аи]?\b", text, re.I):
        return False, "«записал» без записи"
    if re.search(r"(?:\+?7|8)[\s\-(]*9\d{2}", text):
        return False, "номер телефона в ответе"
    for m in TIME_RE.finditer(text):
        if "." in m.group(0) and any(f"{d.day}.{d.month:02d}" == m.group(0).lstrip("0") or
                                     f"{d.day:02d}.{d.month:02d}" == m.group(0) for d in (schedule or {})):
            continue  # «18.11» — это дата, а не время
        t = m.group(0).replace(".", ":").zfill(5)
        d = _day_before(text[max(0, m.start() - 60):m.start()], schedule)
        allowed = (schedule or {}).get(d, []) if d else _all_free_times(schedule)
        if t not in allowed:
            return False, f"время {t}" + (f" на {d:%d.%m}" if d else "") + " не из свободных окон"
    for amount in re.findall(r"(\d[\d\s]{2,6})\s*(?:₽|руб|р\b|р/)", text):
        if amount.strip() not in ALLOWED_PRICES:
            return False, f"сумма {amount.strip()} не из разрешённых"
    if re.search(r"[A-Za-z]{12,}", text):
        return False, "латиница в ответе"
    result["text"] = text
    return True, ""


def ping() -> str:
    """Короткий тестовый запрос без данных клиентов — проверить ключ и связь при запуске бота."""
    t0 = time.time()
    try:
        res = _model().run([
            {"role": "system", "text": 'Ответь строго JSON: {"ok": true}'},
            {"role": "user", "text": "проверка связи"},
        ])
        ok = "true" in res.text.lower()
        return (f"{'работает' if ok else 'ответ странный'}, {time.time() - t0:.1f} с, модель "
                f"{getattr(res, 'model_version', '?')}: {res.text.strip()[:60]}")
    except Exception as e:
        return f"ошибка за {time.time() - t0:.1f} с: {type(e).__name__}: {str(e)[:200]}"


def ai_reply(name: str, dialog: list[dict], a: dict, schedule: dict | None, price_hint: str,
             rule_draft: str) -> dict | None:
    """{"action": "reply"|"book"|"manual", "text"…} или None — тогда бот отвечает по правилам."""
    if not available():
        return None
    today = datetime.now(MSK).strftime("%d.%m.%Y")
    if _usage["day"] != today:
        _usage.update(day=today, calls=0, tokens_in=0, tokens_out=0, fails=0)
    if _usage["calls"] >= MAX_CALLS_PER_DAY:
        return None
    _usage["calls"] += 1
    t0 = time.time()
    try:
        res = _model().run([
            {"role": "system", "text": SYSTEM_PROMPT},
            {"role": "user", "text": build_user_message(name, dialog, a, schedule, price_hint, rule_draft)},
        ])
        u = getattr(res, "usage", None)
        _usage["tokens_in"] += getattr(u, "input_text_tokens", 0) or 0
        _usage["tokens_out"] += getattr(u, "completion_tokens", 0) or 0
        raw = res.text.strip()
        raw = raw[raw.find("{"): raw.rfind("}") + 1]
        result = json.loads(raw)
    except Exception as e:  # сеть, ключ, неверный JSON — всё это не повод молчать: ответим по правилам
        _usage["fails"] += 1
        print(f"AI_FAIL ({time.time() - t0:.1f}s): {type(e).__name__}: {str(e)[:200]}")
        return None
    ok, why = validate(result, schedule)
    if not ok:
        _usage["fails"] += 1
        print(f"AI_REJECTED: {why} | {str(result)[:200]}")
        return None
    result["secs"] = round(time.time() - t0, 1)
    return result
