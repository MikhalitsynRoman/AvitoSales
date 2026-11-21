"""amoCRM (17.11, по просьбе Романа): заявки с Avito → сделки в воронке «Продажа курса».

Ключи — в credentials/.env на хостинге (кладёт Роман): AMO_DOMAIN=поддомен.amocrm.ru, AMO_TOKEN=долгосрочный токен.

Схема этапов (Роман подтвердил 17.11):
  новый клиент на Avito            → «Новая заявка» (контакт + сделка + примечание со ссылкой на чат)
  ответил, понятно, что нужно      → «Квалифицирован»
  записан на пробный (ушла ссылка) → «Назначен пробный урок»
  Роман нажал «Пробный прошёл»     → «Проведен пробный урок»
  отказ клиента                    → «Закрыто и не реализовано»
  разговор передан Роману          → задача «Ответить клиенту»
Сделку двигаем только вперёд и только внутри «Продажа курса»: если Роман сам перенёс её дальше, в другую
воронку или в «Успешно реализовано», бот её не трогает. Из «Закрыто и не реализовано» возвращаем только записью
на пробный (клиент передумал и записался).
"""
import os
import re
import time

import requests

REQUEST_TIMEOUT = 20
PIPELINE_NAME = "Продажа курса"
STAGE_NAMES = {"new": "Новая заявка", "qualified": "Квалифицирован", "booked": "Назначен пробный урок",
               "trial_done": "Проведен пробный урок"}
WON, LOST = 142, 143
TAG = "Avito-бот"

_session = requests.Session()
_pipe = None  # {"id": воронка, "order": [id этапов по порядку], "stage": {"new": id, ...}}
stats = {"created": 0, "made": 0, "moved": 0, "notes": 0, "tasks": 0, "errors": 0, "last_error": ""}


def _domain() -> str:
    d = (os.getenv("AMO_DOMAIN") or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    if d and "." not in d:  # написали только поддомен
        d += ".amocrm.ru"
    return d


def available() -> bool:
    return bool(_domain() and (os.getenv("AMO_TOKEN") or "").strip())


def _req(method: str, path: str, params: dict | None = None, body=None) -> dict | list:
    url = f"https://{_domain()}/api/v4/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {os.environ['AMO_TOKEN'].strip()}"}
    try:
        resp = _session.request(method, url, headers=headers, params=params or {}, json=body, timeout=REQUEST_TIMEOUT)
    except requests.RequestException as e:
        raise RuntimeError(f"amoCRM: нет связи ({type(e).__name__})") from None
    if resp.status_code == 204:
        return {}
    if resp.status_code >= 400:
        hint = {401: "токен не подходит или истёк", 402: "аккаунт amoCRM не оплачен",
                403: "у токена нет прав или доступ закрыт", 404: "нет такого адреса — проверьте AMO_DOMAIN"}
        raise RuntimeError(f"amoCRM {resp.status_code} {hint.get(resp.status_code, '')}: {resp.text[:200]}")
    return resp.json()


def _get(path: str, params: dict | None = None) -> dict:
    return _req("GET", path, params)


def pipelines() -> list[dict]:
    """[{id, name, statuses: [{id, name}]}] — воронки и этапы в порядке amoCRM."""
    data = _get("leads/pipelines")
    out = []
    for p in (data.get("_embedded") or {}).get("pipelines", []):
        sts = sorted((p.get("_embedded") or {}).get("statuses", []), key=lambda s: s.get("sort", 0))
        out.append({"id": p["id"], "name": p.get("name", ""), "main": p.get("is_main", False),
                    "statuses": [{"id": s["id"], "name": s.get("name", "")} for s in sts]})
    return out


def setup() -> str:
    """Находит воронку и этапы по названиям. '' — всё готово, иначе — что не так."""
    global _pipe
    _pipe = None
    pls = [p for p in pipelines() if p["name"].strip().lower() == PIPELINE_NAME.lower()]
    if not pls:
        return f"нет воронки «{PIPELINE_NAME}»"
    p = pls[0]
    by_name = {s["name"].strip().lower(): s["id"] for s in p["statuses"]}
    stage, missing = {}, []
    for key, name in STAGE_NAMES.items():
        if name.lower() in by_name:
            stage[key] = by_name[name.lower()]
        else:
            missing.append(name)
    if missing:
        return "в воронке нет этапов: " + ", ".join(missing)
    _pipe = {"id": p["id"], "order": [s["id"] for s in p["statuses"]], "stage": stage,
             "unsorted": by_name.get("неразобранное")}
    return ""


def ready() -> bool:
    return available() and _pipe is not None


def _fail(e: Exception) -> None:
    stats["errors"] += 1
    stats["last_error"] = f"{time.strftime('%d.%m %H:%M')} {str(e)[:200]}"


def note(lead_id: int, text: str) -> None:
    # amoCRM отвечает «Error 226» на эмодзи и другие символы вне BMP в тексте заметки (20.11, переписка ОПРАВА)
    text = "".join(ch if ord(ch) <= 0xFFFF else "·" for ch in text)
    _req("POST", f"leads/{lead_id}/notes", body=[{"note_type": "common", "params": {"text": text[:20000]}}])
    stats["notes"] += 1


def task(lead_id: int, text: str, hours: float = 1) -> None:
    _req("POST", "tasks", body=[{"text": text[:1000], "complete_till": int(time.time() + hours * 3600),
                                 "entity_id": lead_id, "entity_type": "leads"}])
    stats["tasks"] += 1


def _open_lead_by_phone(phone: str) -> tuple[int | None, int | None]:
    """(id открытой сделки в «Продажа курса», id контакта) для клиента с этим телефоном — чтобы не плодить дубли."""
    digits = re.sub(r"\D", "", phone or "")
    if len(digits) < 10:
        return None, None
    data = _get("contacts", {"query": digits[-10:], "with": "leads"})
    for c in (data.get("_embedded") or {}).get("contacts", []):
        for lead in (c.get("_embedded") or {}).get("leads", []):
            full = _get(f"leads/{lead['id']}")
            if full.get("pipeline_id") == _pipe["id"] and full.get("status_id") not in (WON, LOST, _pipe["unsorted"]):
                return full["id"], c["id"]
        return None, c["id"]
    return None, None


def _norm(s) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip().lower()


match_log = []  # последние решения «откуда взялась сделка» — main кладёт в state amo_match_log


def _log_match(text: str) -> None:
    match_log.append(f"{time.strftime('%d.%m %H:%M')} {text}")
    del match_log[:-20]


def _channel_lead(name: str, client_text: str, taken: set) -> tuple[int | None, str]:
    """Штатный канал Avito в amoCRM (подключён у Романа) кладёт каждое обращение в «Неразобранное» воронки
    «Продажа курса». Чтобы не было двух сделок на одного клиента, сначала ищем его там (и принимаем на «Новая заявка»),
    потом — среди уже принятых за 3 дня открытых сделок с контактом с тем же именем."""
    n = _norm(name)
    if not n or n == "—":
        return None, ""
    data = _get("leads/unsorted", {"filter[pipeline_id]": _pipe["id"], "filter[category][]": "chats", "limit": 50})
    cands = []
    for u in (data.get("_embedded") or {}).get("unsorted", []):
        if "avito" not in (_norm(u.get("source_name")) + _norm(u.get("source_uid"))):
            continue
        md = u.get("metadata") or {}
        emb = u.get("_embedded") or {}
        names = {_norm((md.get("client") or {}).get("name") if isinstance(md.get("client"), dict) else ""),
                 _norm(md.get("from"))} | {_norm(c.get("name")) for c in emb.get("contacts", [])}
        if n not in names:
            continue
        last = _norm(md.get("last_message_text"))
        same_text = bool(last) and last[:40] in _norm(client_text)
        cands.append(((same_text, md.get("received_at") or u.get("created_at") or 0), u))
    if cands:
        (same_text, _), best = max(cands, key=lambda x: x[0])
        # одно обращение с таким именем — оно; несколько — только если совпал и текст сообщения
        if len(cands) == 1 or same_text:
            lead_ids = [x["id"] for x in (best.get("_embedded") or {}).get("leads", [])]
            res = _req("POST", f"leads/unsorted/{best['uid']}/accept", body={"status_id": _pipe["stage"]["new"]})
            lead_ids = [x["id"] for x in ((res or {}).get("_embedded") or {}).get("leads", [])] or lead_ids
            if lead_ids:
                _log_match(f"«{name}»: принял обращение из «Неразобранного» (канал Avito) → сделка #{lead_ids[0]}")
                return lead_ids[0], "канал Avito"
        _log_match(f"«{name}»: в «Неразобранном» {len(cands)} обращения с таким именем, текст не совпал — не угадываю")
    # уже принятая (Романом) сделка с контактом с тем же именем
    week_ago = int(time.time()) - 3 * 86400
    data = _get("contacts", {"query": name, "with": "leads", "limit": 50})
    found = []
    for c in (data.get("_embedded") or {}).get("contacts", []):
        if _norm(c.get("name")) != n:
            continue
        for lead in (c.get("_embedded") or {}).get("leads", []):
            if lead["id"] in taken:
                continue
            full = _get(f"leads/{lead['id']}")
            if (full.get("pipeline_id") == _pipe["id"] and full.get("status_id") not in (WON, LOST, _pipe["unsorted"])
                    and (full.get("created_at") or 0) >= week_ago):
                found.append(full["id"])
    if len(found) == 1:
        _log_match(f"«{name}»: нашёл уже принятую сделку #{found[0]}")
        return found[0], "уже принятая сделка"
    if found:
        _log_match(f"«{name}»: открытых сделок с таким именем {len(found)} — не угадываю, создаю свою")
    return None, ""


def find_lead(name: str, phone: str | None, first_note: str, stage: str = "new",
              client_text: str = "", taken: set = frozenset(), create_city: str | None = None) -> int | None:
    """Сделку создаёт интеграция Avito ↔ amoCRM (19.11/20.11 Роман: «не бот их создавал, а они шли с Avito»). Сначала
    ищем её: по телефону, в «Неразобранном» (принимаем), среди принятых сделок. Не нашли: create_city is None — None
    (поищем снова); иначе создаём сами — 21.11 Роман: «они всегда должны попадать в амо», а интеграция с 17.11
    не создала ни одной сделки."""
    lead_id, contact_id = _open_lead_by_phone(phone) if phone else (None, None)
    if lead_id and lead_id not in taken:
        _log_match(f"«{name}»: нашёл открытую сделку #{lead_id} по телефону")
    else:
        lead_id = _channel_lead(name, client_text, taken)[0]
    if not lead_id and create_city is not None:
        return _create_lead(name, create_city, phone, contact_id, first_note, stage)
    if not lead_id:
        _log_match(f"«{name}»: сделки от интеграции Avito пока нет — жду её, через 15 минут создам сам")
        return None
    stats["created"] += 1  # в /status: «нашёл сделок»
    note(lead_id, first_note)
    advance(lead_id, stage)
    return lead_id


def _create_lead(name: str, city: str, phone: str | None, contact_id: int | None, first_note: str, stage: str) -> int:
    contact = {"id": contact_id} if contact_id else {"first_name": name or "Клиент Avito"}
    if phone and not contact_id:
        contact["custom_fields_values"] = [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}]
    title = f"{name or 'Клиент'} — Avito" + (f" ({city})" if city and city != "—" else "")
    res = _req("POST", "leads/complex", body=[{
        "name": title[:250], "pipeline_id": _pipe["id"], "status_id": _pipe["stage"][stage],
        "_embedded": {"contacts": [contact], "tags": [{"name": TAG}]},
    }])
    lead_id = res[0]["id"]
    stats["made"] += 1
    _log_match(f"«{name}»: интеграция Avito сделку не создала — создал сам #{lead_id}")
    note(lead_id, first_note)
    return lead_id


# ---------- поля сделки: «Класс», «Экзамен», «Комментарий» + «Бюджет» (19.11, по просьбе Романа) ----------
# Бюджет: ЕГЭ 11 класс — 12000, ЕГЭ 10 класс — 6000, ОГЭ 9 класс — 12000 (остальное не ставим).
FIELD_DEFS = {
    "Класс": {"type": "select", "enums": [str(g) for g in range(5, 12)]},
    "Экзамен": {"type": "select", "enums": ["ЕГЭ", "ОГЭ"]},
    "Комментарий": {"type": "textarea"},
}
BUDGET = {("ЕГЭ", "11"): 12000, ("ЕГЭ", "10"): 6000, ("ОГЭ", "9"): 12000}
_fields = {}  # название → {"id", "type", "enums": {значение: enum_id}}


def lead_fields() -> list[dict]:
    out, page = [], 1
    while True:
        data = _get("leads/custom_fields", {"page": page, "limit": 50})
        out += (data.get("_embedded") or {}).get("custom_fields", [])
        if not (data.get("_links") or {}).get("next"):
            return out
        page += 1


def ensure_fields() -> str:
    """Находит поля «Класс», «Экзамен», «Комментарий»; каких нет — создаёт (существующие не трогает)."""
    _fields.clear()
    have = {f["name"].strip().lower(): f for f in lead_fields()}
    made = []
    for name, d in FIELD_DEFS.items():
        f = have.get(name.lower())
        if not f:
            body = {"name": name, "type": d["type"]}
            if d.get("enums"):
                body["enums"] = [{"value": v, "sort": i + 1} for i, v in enumerate(d["enums"])]
            res = _req("POST", "leads/custom_fields", body=[body])
            f = ((res or {}).get("_embedded") or {}).get("custom_fields", [{}])[0]
            made.append(name)
        enums = {e["value"]: e["id"] for e in (f.get("enums") or [])}
        if d.get("enums") and f.get("id"):  # в списке не хватает значения (например, «8» в «Класс») — добавляем
            missing = [v for v in d["enums"] if v not in enums]
            if missing:
                all_enums = [{"id": i, "value": v} for v, i in enums.items()] + [{"value": v} for v in missing]
                res = _req("PATCH", f"leads/custom_fields/{f['id']}", body={"enums": all_enums})
                enums = {e["value"]: e["id"] for e in (res or {}).get("enums") or []} or enums
        _fields[name] = {"id": f.get("id"), "type": f.get("type", d["type"]), "enums": enums}
    return ("создал поля: " + ", ".join(made)) if made else "поля на месте"


def _current_values(lead: dict) -> dict:
    by_id = {f["id"]: n for n, f in _fields.items()}
    out = {}
    for cf in lead.get("custom_fields_values") or []:
        n = by_id.get(cf.get("field_id"))
        if n:
            out[n] = str(((cf.get("values") or [{}])[0]).get("value", ""))
    out["Бюджет"] = str(lead.get("price") or "")
    return out


def fill_fields(lead_id: int, grade: str | None, exam: str | None, comment: str, written: dict) -> dict:
    """Заполняет Класс/Экзамен/Комментарий/Бюджет. Поле, которое Роман поправил руками (значение не пустое и не то,
    что бот писал раньше), не трогаем. Возвращает, что записано, — main хранит это в state amo_f_{row}."""
    if not _fields:
        return written
    want = {}
    if grade and grade in (_fields.get("Класс") or {}).get("enums", {}):
        want["Класс"] = grade
    if exam in ("ЕГЭ", "ОГЭ"):
        want["Экзамен"] = exam
    if comment:
        want["Комментарий"] = comment[:1000]
    budget = BUDGET.get((exam, grade))
    if budget:
        want["Бюджет"] = str(budget)
    if not want:
        return written
    cur = _current_values(_get(f"leads/{lead_id}"))
    body, cfs, done = {}, [], dict(written)
    for name, value in want.items():
        now = cur.get(name, "")
        if now == value or (now and now != written.get(name)):
            continue  # уже так или поправлено руками
        if name == "Бюджет":
            body["price"] = int(value)
        else:
            f = _fields[name]
            val = {"enum_id": f["enums"][value]} if f["type"] == "select" else {"value": value}
            cfs.append({"field_id": f["id"], "values": [val]})
        done[name] = value
    if cfs:
        body["custom_fields_values"] = cfs
    if body:
        _req("PATCH", f"leads/{lead_id}", body=body)
    return done


# 19.11 Роман: «удали все 14» — из боковой панели сделки остаются только «Класс», «Экзамен», «Комментарий» (+ «Бюджет»).
# id и названия — из отчёта amo_fields_report того же дня; удаляем, только если название по id совпадает.
FIELDS_TO_DELETE = {
    2173505: "Название курса", 2173507: "Преподаватель", 2173509: "Дата начала обучения",
    2173511: "Дата и время начала вебинара", 2173513: "Группа", 2173515: "Тип оплаты", 2173517: "Причина отказа",
    2173677: "Avito chat id (Radist.online)", 2173679: "Avito chat url (Radist.online)",
    2173681: "Avito item price (Radist.online)", 2173683: "Avito item name (Radist.online)",
    2173685: "Avito item url (Radist.online)", 2173691: "Объявление", 2173693: "URL объявления",
}


def delete_fields() -> list[str]:
    """Удаляет поля из FIELDS_TO_DELETE (данные в них пропадают во всех сделках). Возвращает строки отчёта."""
    have = {f["id"]: f for f in lead_fields()}
    out = []
    for fid, name in FIELDS_TO_DELETE.items():
        f = have.get(fid)
        if not f:
            out.append(f"«{name}» — уже нет")
            continue
        if f.get("name", "").strip() != name:
            out.append(f"«{name}» — НЕ удалил: под этим номером теперь поле «{f.get('name')}»")
            continue
        try:
            _req("DELETE", f"leads/custom_fields/{fid}")
            out.append(f"«{name}» — удалил")
        except Exception as e:
            out.append(f"«{name}» — не удалось: {str(e)[:120]}")
    return out


def fields_report() -> str:
    """Только чтение: какие поля есть в карточке сделки и в скольких из последних 250 сделок они заполнены."""
    fs = lead_fields()
    data = _get("leads", {"limit": 250, "order[created_at]": "desc"})
    filled = {}
    for lead in (data.get("_embedded") or {}).get("leads", []):
        for cf in lead.get("custom_fields_values") or []:
            filled[cf["field_id"]] = filled.get(cf["field_id"], 0) + 1
    lines = [f"#{f['id']} «{f['name']}» · {f.get('type')} · {'системное' if not f.get('is_deletable', True) else 'можно удалить'}"
             f"{' · только API' if f.get('is_api_only') else ''} · заполнено в {filled.get(f['id'], 0)} из последних сделок"
             for f in fs]
    return "\n".join(lines) or "своих полей нет"


def advance(lead_id: int, stage: str) -> str:
    """Переводит сделку на этап, только если это шаг вперёд. Возвращает, что сделано (для журнала)."""
    lead = _get(f"leads/{lead_id}")
    target = _pipe["stage"][stage]
    cur = lead.get("status_id")
    if lead.get("pipeline_id") != _pipe["id"]:
        return "в другой воронке — не трогаю"
    if cur == WON:
        return "уже успешно реализовано — не трогаю"
    if cur == _pipe["unsorted"]:
        return "в «Неразобранном» — его принимают, а не двигают"
    if cur == LOST and stage != "booked":
        return "закрыта — не трогаю"
    order = _pipe["order"]
    if cur != LOST and cur in order and order.index(cur) >= order.index(target):
        return "уже на этом этапе или дальше"
    _req("PATCH", f"leads/{lead_id}", body={"status_id": target, "pipeline_id": _pipe["id"]})
    stats["moved"] += 1
    return "перевёл"


def back_to_qualified(lead_id: int) -> str:
    """20.11, Роман: «если не проведён пробный урок — клиента переноси в Квалифицирован назад». Назад двигаем только
    с «Назначен пробный урок» / «Проведен пробный урок»: если Роман перенёс сделку дальше, в другую воронку или
    закрыл её — не трогаем."""
    lead = _get(f"leads/{lead_id}")
    cur = lead.get("status_id")
    if lead.get("pipeline_id") != _pipe["id"]:
        return "в другой воронке — не трогаю"
    if cur not in (_pipe["stage"]["booked"], _pipe["stage"]["trial_done"]):
        return "не на этапе пробного урока — не трогаю"
    _req("PATCH", f"leads/{lead_id}", body={"status_id": _pipe["stage"]["qualified"], "pipeline_id": _pipe["id"]})
    stats["moved"] += 1
    return "перевёл"


def delete_lead(lead_id: int) -> str:
    """Удаление сделки — только по прямой просьбе Романа (20.11: Полина и ОПРАВА)."""
    try:
        _req("DELETE", f"leads/{lead_id}")
        return "удалил"
    except RuntimeError as e:
        return f"не удалось через API ({str(e)[:80]})"


def close_lost(lead_id: int) -> str:
    lead = _get(f"leads/{lead_id}")
    if lead.get("pipeline_id") != _pipe["id"] or lead.get("status_id") in (WON, LOST):
        return "не трогаю"
    order = _pipe["order"]
    # клиент, который уже был на пробном, — это решение Романа, а не бота
    if lead.get("status_id") in order and order.index(lead["status_id"]) >= order.index(_pipe["stage"]["trial_done"]):
        return "после пробного — решает Роман"
    _req("PATCH", f"leads/{lead_id}", body={"status_id": LOST, "pipeline_id": _pipe["id"]})
    stats["moved"] += 1
    return "закрыл"


def avito_channel_diag(known_names: set) -> str:
    """Только чтение: как выглядят сделки, которые создаёт штатный канал Avito в amoCRM, — чтобы бот находил их,
    а не заводил вторые. Имён не выводим: только «совпадает ли имя контакта с клиентом из таблицы»."""
    if not ready():
        return "amoCRM не готов"
    names = {}
    for s in pipelines():
        for st in s["statuses"]:
            names[(s["id"], st["id"])] = f"{s['name']} / {st['name']}"
    out = []
    week = int(time.time()) - 3 * 86400
    data = _get("leads", {"filter[created_at][from]": week, "with": "contacts,source_id", "limit": 50, "order[created_at]": "desc"})
    for lead in (data.get("_embedded") or {}).get("leads", [])[:15]:
        emb = lead.get("_embedded") or {}
        tags = [t.get("name", "") for t in emb.get("tags", [])]
        cons = emb.get("contacts", [])
        match = "нет контакта"
        if cons:
            c = _get(f"contacts/{cons[0]['id']}")
            cname = (c.get("name") or "").strip().lower()
            match = "имя = клиент из таблицы" if cname and cname in known_names else "имя не из таблицы"
        out.append(f"#{lead['id']} {time.strftime('%d.%m %H:%M', time.localtime(lead.get('created_at', 0)))} · "
                   f"{names.get((lead.get('pipeline_id'), lead.get('status_id')), lead.get('status_id'))} · "
                   f"теги {tags} · source_id {lead.get('source_id')} · создал {lead.get('created_by')} · {match}")
    try:
        uns = _get("leads/unsorted", {"limit": 10})
        for u in (uns.get("_embedded") or {}).get("unsorted", []):
            md = u.get("metadata") or {}
            out.append(f"неразобранное: {u.get('category')} · источник {u.get('source_name')} · uid {u.get('source_uid', '')[:20]} · "
                       f"поля metadata {sorted(md)}")
    except Exception as e:
        out.append(f"неразобранное: {str(e)[:120]}")
    return "\n".join(out) or "за 3 дня сделок нет"


def lookup(query: str) -> str:
    """Только чтение, для проверок: контакты по запросу и их сделки (воронка / этап / когда создана)."""
    names = {}
    for p in pipelines():
        for s in p["statuses"]:
            names[(p["id"], s["id"])] = f"{p['name']} / {s['name']}"
    out = []
    data = _get("contacts", {"query": query, "with": "leads", "limit": 20})
    for c in (data.get("_embedded") or {}).get("contacts", []):
        out.append(f"контакт #{c['id']} «{c.get('name', '')}»")
        for lead in (c.get("_embedded") or {}).get("leads", []):
            full = _get(f"leads/{lead['id']}")
            out.append(f"   сделка #{full['id']} «{full.get('name', '')}» · "
                       f"{names.get((full.get('pipeline_id'), full.get('status_id')), full.get('status_id'))} · "
                       f"создана {time.strftime('%d.%m %H:%M', time.localtime(full.get('created_at', 0)))}")
    uns = _get("leads/unsorted", {"limit": 50})
    for u in (uns.get("_embedded") or {}).get("unsorted", []):
        md = u.get("metadata") or {}
        cname = (md.get("client") or {}).get("name", "") if isinstance(md.get("client"), dict) else ""
        if query.lower() in f"{cname} {md.get('from', '')}".lower():
            out.append(f"неразобранное {u.get('uid', '')[:12]} «{cname or md.get('from', '')}»")
    return "\n".join(out) or "ничего не нашёл"


def diag() -> tuple[str, str]:
    """(короткая строка для «Состояния», описание воронок). Только чтение, без данных клиентов."""
    if not available():
        return "ключа нет (AMO_DOMAIN / AMO_TOKEN в credentials/.env)", ""
    t = time.time()
    try:
        acc = _get("account")
        pls = pipelines()
        problem = setup()
    except Exception as e:
        return f"ошибка: {str(e)[:250]}", ""
    lines = []
    for p in pls:
        lines.append(f"Воронка «{p['name']}» (id {p['id']}){' — основная' if p['main'] else ''}:")
        lines += [f"  • {s['name']} (id {s['id']})" for s in p["statuses"]]
    short = (f"подключено к «{acc.get('name', '')}» за {time.time() - t:.1f} с; "
             + (f"НЕ ВЕДУ СДЕЛКИ: {problem}" if problem else f"веду сделки в «{PIPELINE_NAME}»"))
    return short, "\n".join(lines)
