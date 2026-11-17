"""amoCRM (17.11, по просьбе Романа). Пока только чтение: проверка ключа, воронки и этапы.

Ключи — в credentials/.env на хостинге (кладёт Роман): AMO_DOMAIN=поддомен.amocrm.ru, AMO_TOKEN=долгосрочный токен.
Двигать сделки бот начнёт только после того, как Роман подтвердит, какое событие на какой этап ставить.
"""
import os
import re
import time

import requests

REQUEST_TIMEOUT = 20
_session = requests.Session()


def _domain() -> str:
    d = (os.getenv("AMO_DOMAIN") or "").strip().lower()
    d = re.sub(r"^https?://", "", d).split("/")[0]
    if d and "." not in d:  # написали только поддомен
        d += ".amocrm.ru"
    return d


def available() -> bool:
    return bool(_domain() and (os.getenv("AMO_TOKEN") or "").strip())


def _get(path: str, params: dict | None = None) -> dict:
    url = f"https://{_domain()}/api/v4/{path.lstrip('/')}"
    headers = {"Authorization": f"Bearer {os.environ['AMO_TOKEN'].strip()}"}
    resp = _session.get(url, headers=headers, params=params or {}, timeout=REQUEST_TIMEOUT)
    if resp.status_code == 204:
        return {}
    if resp.status_code >= 400:
        hint = {401: "токен не подходит или истёк", 402: "аккаунт amoCRM не оплачен",
                403: "у токена нет прав или доступ закрыт", 404: "нет такого адреса — проверьте AMO_DOMAIN"}
        raise RuntimeError(f"amoCRM {resp.status_code} {hint.get(resp.status_code, '')}: {resp.text[:200]}")
    return resp.json()


def pipelines() -> list[dict]:
    """[{id, name, statuses: [{id, name}]}] — воронки и этапы в порядке amoCRM."""
    data = _get("leads/pipelines")
    out = []
    for p in (data.get("_embedded") or {}).get("pipelines", []):
        sts = sorted((p.get("_embedded") or {}).get("statuses", []), key=lambda s: s.get("sort", 0))
        out.append({"id": p["id"], "name": p.get("name", ""), "main": p.get("is_main", False),
                    "statuses": [{"id": s["id"], "name": s.get("name", "")} for s in sts]})
    return out


def diag() -> tuple[str, str]:
    """(короткая строка для «Состояния», описание воронок). Только чтение, без данных клиентов."""
    if not available():
        return "ключа нет (AMO_DOMAIN / AMO_TOKEN в credentials/.env)", ""
    t = time.time()
    try:
        acc = _get("account")
        pls = pipelines()
        week_ago = int(time.time()) - 7 * 86400
        leads = _get("leads", {"filter[created_at][from]": week_ago, "limit": 250})
        recent = (leads.get("_embedded") or {}).get("leads", [])
    except Exception as e:
        return f"ошибка: {str(e)[:250]}", ""
    by_status = {}
    for lead in recent:
        by_status[(lead.get("pipeline_id"), lead.get("status_id"))] = by_status.get(
            (lead.get("pipeline_id"), lead.get("status_id")), 0) + 1
    lines = []
    for p in pls:
        lines.append(f"Воронка «{p['name']}» (id {p['id']}){' — основная' if p['main'] else ''}:")
        for s in p["statuses"]:
            n = by_status.get((p["id"], s["id"]), 0)
            lines.append(f"  • {s['name']} (id {s['id']})" + (f" — новых за неделю: {n}" if n else ""))
    short = (f"подключено к «{acc.get('name', '')}» за {time.time() - t:.1f} с; воронок {len(pls)}, "
             f"сделок за неделю: {len(recent)}{'+' if len(recent) >= 250 else ''}")
    return short, "\n".join(lines)
