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
stats = {"created": 0, "moved": 0, "notes": 0, "tasks": 0, "errors": 0, "last_error": ""}


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
    _pipe = {"id": p["id"], "order": [s["id"] for s in p["statuses"]], "stage": stage}
    return ""


def ready() -> bool:
    return available() and _pipe is not None


def _fail(e: Exception) -> None:
    stats["errors"] += 1
    stats["last_error"] = f"{time.strftime('%d.%m %H:%M')} {str(e)[:200]}"


def note(lead_id: int, text: str) -> None:
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
            if full.get("pipeline_id") == _pipe["id"] and full.get("status_id") not in (WON, LOST):
                return full["id"], c["id"]
        return None, c["id"]
    return None, None


def create_lead(name: str, city: str, phone: str | None, first_note: str, stage: str = "new") -> int:
    """Сделка в «Продажа курса» с контактом. Если клиент с этим телефоном уже есть — берём его открытую сделку."""
    lead_id, contact_id = _open_lead_by_phone(phone) if phone else (None, None)
    if lead_id:
        note(lead_id, first_note)
        advance(lead_id, stage)
        return lead_id
    contact = {"id": contact_id} if contact_id else {"first_name": name or "Клиент Avito"}
    if phone and not contact_id:
        contact["custom_fields_values"] = [{"field_code": "PHONE", "values": [{"value": phone, "enum_code": "WORK"}]}]
    title = f"{name or 'Клиент'} — Avito" + (f" ({city})" if city and city != "—" else "")
    res = _req("POST", "leads/complex", body=[{
        "name": title[:250], "pipeline_id": _pipe["id"], "status_id": _pipe["stage"][stage],
        "_embedded": {"contacts": [contact], "tags": [{"name": TAG}]},
    }])
    lead_id = res[0]["id"]
    stats["created"] += 1
    note(lead_id, first_note)
    return lead_id


def advance(lead_id: int, stage: str) -> str:
    """Переводит сделку на этап, только если это шаг вперёд. Возвращает, что сделано (для журнала)."""
    lead = _get(f"leads/{lead_id}")
    target = _pipe["stage"][stage]
    cur = lead.get("status_id")
    if lead.get("pipeline_id") != _pipe["id"]:
        return "в другой воронке — не трогаю"
    if cur == WON:
        return "уже успешно реализовано — не трогаю"
    if cur == LOST and stage != "booked":
        return "закрыта — не трогаю"
    order = _pipe["order"]
    if cur != LOST and cur in order and order.index(cur) >= order.index(target):
        return "уже на этом этапе или дальше"
    _req("PATCH", f"leads/{lead_id}", body={"status_id": target, "pipeline_id": _pipe["id"]})
    stats["moved"] += 1
    return "перевёл"


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
