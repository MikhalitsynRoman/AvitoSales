"""Картинка недели «как в Google Календаре» для команды /schedule (23.11, Роман).

Зелёные блоки — свободные окна для пробного (несколько окон подряд склеены в один блок «12:00–16:00»),
оранжевые — уже записанные пробные из листа «Записаны на пробный». Прошедшее — бледнее, красная линия — сейчас.
Рисуем Pillow со шрифтом DejaVu из папки fonts (на хостинге системных шрифтов с кириллицей может не быть).
"""
import io
import os
from datetime import date, datetime, timedelta


S = 2  # рисуем в 2 раза крупнее — в Telegram картинка чёткая
W = 1000
GUTTER = 62
TOP = 128          # заголовок + строка дней
HOUR = 56          # высота часа
COL = (W - GUTTER - 14) / 7

WEEKDAYS = ["ПН", "ВТ", "СР", "ЧТ", "ПТ", "СБ", "ВС"]
MONTHS = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября",
          "ноября", "декабря"]

BG = (255, 255, 255)
GRID = (218, 220, 224)
MUTED = (112, 117, 122)
TEXT = (32, 33, 36)
BLUE = (26, 115, 232)
RED = (234, 67, 53)
FREE = (51, 182, 121)      # «шалфей» Google
BUSY = (128, 134, 139)     # «графит» Google — события из календаря Романа
TRIAL = (244, 81, 30)      # «мандарин» Google

_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fonts")
_fonts = {}


def _font(size: int, bold: bool = False):
    key = (size, bold)
    if key not in _fonts:
        from PIL import ImageFont
        name = "DejaVuSans-Bold.ttf" if bold else "DejaVuSans.ttf"
        _fonts[key] = ImageFont.truetype(os.path.join(_DIR, name), size * S)
    return _fonts[key]


def _fade(color: tuple, k: float = 0.55) -> tuple:
    return tuple(int(c + (255 - c) * k) for c in color)


def _fit(draw, text: str, font, width: float) -> str:
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return (text.rstrip() + "…") if text else ""


def _mins(hhmm: str) -> int:
    h, m = hhmm.split(":")
    return int(h) * 60 + int(m)


def _hm(mins: int) -> str:
    return f"{mins // 60:02d}:{mins % 60:02d}"


def free_blocks(times: list[str]) -> list[tuple[int, int]]:
    """Окна по 30 минут («12:00», «12:30», «13:00») → блоки (начало, конец) в минутах: [(720, 810)]."""
    blocks = []
    for t in sorted(set(times), key=_mins):
        s = _mins(t)
        if blocks and blocks[-1][1] == s:
            blocks[-1][1] = s + 30
        else:
            blocks.append([s, s + 30])
    return [tuple(b) for b in blocks]


def _layout(events: list[dict]) -> None:
    """Пересекающиеся события дня — рядом, как в Google: каждому колонка col из ncols."""
    events.sort(key=lambda e: (e["start"], -e["end"]))
    cluster, cluster_end = [], -1

    def close():
        n = max((e["col"] for e in cluster), default=-1) + 1
        for e in cluster:
            e["ncols"] = n

    for e in events:
        if cluster and e["start"] >= cluster_end:
            close()
            cluster, cluster_end = [], -1
        used = {c["col"] for c in cluster if c["end"] > e["start"]}
        e["col"] = next(i for i in range(len(cluster) + 1) if i not in used)
        cluster.append(e)
        cluster_end = max(cluster_end, e["end"])
    if cluster:
        close()


def week_title(monday: date) -> str:
    sunday = monday + timedelta(days=6)
    if monday.month == sunday.month:
        return f"{monday.day}–{sunday.day} {MONTHS[sunday.month - 1]}"
    return f"{monday.day} {MONTHS[monday.month - 1]} – {sunday.day} {MONTHS[sunday.month - 1]}"


def render_week(monday: date, now: datetime, free: dict, trials: dict, busy: dict | None = None) -> bytes:
    """free — {дата: ["12:00", "12:30", …]} свободные окна; trials — {дата: [(datetime, имя, класс)]};
    busy — {дата: [(начало, конец, название)]} события Google Календаря. Возвращает PNG."""
    from PIL import Image, ImageDraw  # здесь, а не наверху: пока pip ставит Pillow, бот всё равно запускается
    days = [monday + timedelta(days=i) for i in range(7)]
    events = {d: [] for d in days}
    for d in days:
        for s, e in free_blocks(free.get(d, [])):
            events[d].append({"start": s, "end": e, "kind": "free", "title": "Свободно",
                              "sub": f"{_hm(s)}–{_hm(e)}"})
        for s_dt, e_dt, title in (busy or {}).get(d, []):
            s = s_dt.hour * 60 + s_dt.minute
            e = max(s + 15, e_dt.hour * 60 + e_dt.minute if e_dt.date() == d else 24 * 60)
            events[d].append({"start": s, "end": e, "kind": "busy", "title": title,
                              "sub": f"{_hm(s)}–{_hm(min(e, 24 * 60 - 1))}"})
        for dt, name, note in trials.get(d, []):
            s = dt.hour * 60 + dt.minute
            events[d].append({"start": s, "end": s + 30, "kind": "trial", "title": name or "Пробный",
                              "sub": f"{_hm(s)}–{_hm(s + 30)}" + (f" · {note}" if note else "")})
    all_ev = [e for d in days for e in events[d]]
    first = min([e["start"] // 60 for e in all_ev] + [12])
    last = max([-(-e["end"] // 60) for e in all_ev] + [22])
    first, last = max(0, min(first, 12)), min(24, last)

    height = TOP + (last - first) * HOUR + 46
    img = Image.new("RGB", (W * S, int(height * S)), BG)
    dr = ImageDraw.Draw(img)

    def X(v):
        return int(v * S)

    def y_of(mins: int) -> float:
        return TOP + (mins - first * 60) / 60 * HOUR

    # заголовок и легенда
    dr.text((X(20), X(14)), "Пробные занятия · " + week_title(monday), font=_font(19, True), fill=TEXT)
    lx = W - 20
    legend = (("пробный записан", TRIAL), ("свободно для пробного", FREE))
    if any((busy or {}).values()):
        legend = (("ваш календарь", BUSY),) + legend
    for label, color in legend:
        tw = dr.textlength(label, font=_font(12)) / S
        lx -= tw
        dr.text((X(lx), X(19)), label, font=_font(12), fill=MUTED)
        lx -= 18
        dr.rounded_rectangle([X(lx), X(19), X(lx + 12), X(31)], radius=X(3), fill=color)
        lx -= 18

    # строка дней
    today = now.date()
    for i, d in enumerate(days):
        cx = GUTTER + COL * i + COL / 2
        is_today = d == today
        wd = WEEKDAYS[d.weekday()]
        f = _font(11, is_today)
        dr.text((X(cx), X(52)), wd, font=f, fill=BLUE if is_today else MUTED, anchor="mm")
        if is_today:
            dr.ellipse([X(cx - 22), X(64), X(cx + 22), X(108)], fill=BLUE)
        dr.text((X(cx), X(86)), str(d.day), font=_font(24), fill=(255, 255, 255) if is_today else
                (MUTED if d < today else TEXT), anchor="mm")
    dr.text((X(GUTTER - 8), X(TOP - 12)), "GMT+3", font=_font(10), fill=MUTED, anchor="rm")

    # сетка
    bottom = y_of(last * 60)
    dr.line([X(GUTTER), X(TOP), X(W - 14), X(TOP)], fill=GRID, width=S)
    for h in range(first, last + 1):
        y = y_of(h * 60)
        dr.line([X(GUTTER - 6), X(y), X(W - 14), X(y)], fill=GRID, width=S)
        if h < 24:
            dr.text((X(GUTTER - 10), X(y + (7 if h == first else 0))), f"{h:02d}:00", font=_font(11), fill=MUTED,
                    anchor="rm")
    for i in range(8):
        x = GUTTER + COL * i
        dr.line([X(x), X(TOP - 10), X(x), X(bottom)], fill=GRID, width=S)

    # события
    for i, d in enumerate(days):
        ev = events[d]
        _layout(ev)
        for e in ev:
            colw = (COL - 8) / e["ncols"]
            x0 = GUTTER + COL * i + 2 + colw * e["col"]
            x1 = x0 + colw - 2
            y0, y1 = y_of(e["start"]) + 1, y_of(e["end"]) - 1
            end_dt = datetime.combine(d, datetime.min.time()).replace(tzinfo=now.tzinfo) + timedelta(minutes=e["end"])
            past = end_dt <= now
            base = {"free": FREE, "trial": TRIAL, "busy": BUSY}[e["kind"]]
            fill = _fade(base) if past else base
            dr.rounded_rectangle([X(x0), X(y0), X(x1), X(y1)], radius=X(5), fill=fill,
                                 outline=(255, 255, 255), width=S)
            if e["kind"] == "trial":
                dr.rounded_rectangle([X(x0), X(y0), X(x0 + 4), X(y1)], radius=X(2),
                                     fill=_fade((121, 134, 203), 0.4 if past else 0))
            tx, tw = x0 + (9 if e["kind"] == "trial" else 7), x1 - x0 - (13 if e["kind"] == "trial" else 11)
            color = (255, 255, 255)
            if y1 - y0 >= 40:
                dr.text((X(tx), X(y0 + 5)), _fit(dr, e["title"], _font(12, True), tw * S), font=_font(12, True),
                        fill=color)
                dr.text((X(tx), X(y0 + 22)), _fit(dr, e["sub"], _font(11), tw * S), font=_font(11), fill=color)
            else:
                line = f"{e['title']}, {e['sub'].split('–')[0]}"
                f = _font(11, True) if dr.textlength(line, font=_font(11, True)) <= tw * S else _font(10, True)
                if dr.textlength(line, font=f) > tw * S:  # узкий блок (два пробных в одно время) — только имя
                    line = e["title"]
                dr.text((X(tx), X((y0 + y1) / 2)), _fit(dr, line, f, tw * S), font=f, fill=color, anchor="lm")

    # красная линия «сейчас»
    if monday <= today <= days[-1]:
        m = now.hour * 60 + now.minute
        if first * 60 <= m <= last * 60:
            i = today.weekday()
            y = y_of(m)
            x0, x1 = GUTTER + COL * i, GUTTER + COL * (i + 1)
            dr.line([X(x0), X(y), X(x1), X(y)], fill=RED, width=2 * S)
            dr.ellipse([X(x0 - 6), X(y - 6), X(x0 + 6), X(y + 6)], fill=RED)

    n_free = sum(len([t for t in free.get(d, [])]) for d in days)
    n_trial = sum(len(trials.get(d, [])) for d in days)
    dr.text((X(20), X(bottom + 16)), f"Свободных окон по 30 минут: {n_free} · пробных записано: {n_trial} · "
                                     f"обновлено {now:%d.%m %H:%M}", font=_font(11), fill=MUTED)

    out = io.BytesIO()
    img.save(out, "PNG", optimize=True)
    return out.getvalue()
