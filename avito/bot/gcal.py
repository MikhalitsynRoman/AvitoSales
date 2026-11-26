"""Google Календарь Романа (24.11): каждая запись из листа «Записаны на пробный» — событие в его календаре.

Ходим тем же сервисным аккаунтом, что и в таблицу (credentials/service_account.json). Роман дал этому аккаунту
доступ к своему календарю («Вносить изменения в мероприятия») и включил Google Calendar API в проекте аккаунта.
Бот меняет только события, которые создал сам (их номера — в state gcal_map); остальные события не меняет.
26.11, Роман: «бот мог видеть мои события в календаре» — бот читает события календаря (только чтение) и не
предлагает клиентам время, когда Роман занят. События на весь день и помеченные «Свободен» время не занимают.
"""
from datetime import datetime, timedelta

from google.auth.transport.requests import AuthorizedSession
from google.oauth2.service_account import Credentials

from config import CREDENTIALS_FILE

API = "https://www.googleapis.com/calendar/v3"
TZ = "Europe/Moscow"
TRIAL_MINUTES = 30
COLOR_TANGERINE = "6"  # тот же оранжевый, что у пробных в /schedule

_session = None


def _s() -> AuthorizedSession:
    global _session
    if _session is None:
        creds = Credentials.from_service_account_file(
            CREDENTIALS_FILE, scopes=["https://www.googleapis.com/auth/calendar.events",
                                      "https://www.googleapis.com/auth/calendar.readonly"])
        _session = AuthorizedSession(creds)
    return _session


def service_email() -> str:
    try:
        return Credentials.from_service_account_file(CREDENTIALS_FILE).service_account_email
    except Exception:
        return ""


def _check(resp, what: str) -> dict:
    if resp.status_code >= 400:
        try:
            msg = resp.json().get("error", {}).get("message", "")
        except ValueError:
            msg = resp.text
        hint = ""
        if "has not been used" in msg or "is disabled" in msg:
            hint = "не включён Google Calendar API в проекте сервисного аккаунта"
        elif resp.status_code == 404:
            hint = "нет доступа к календарю (не открыт сервисному аккаунту) или неверный ID"
        elif resp.status_code == 403:
            hint = "у сервисного аккаунта доступ только на просмотр — нужно «Вносить изменения в мероприятия»"
        raise RuntimeError(f"Google Календарь, {what}: {resp.status_code} {hint} {msg[:150]}".strip())
    return resp.json() if resp.content else {}


def check_access(cal_id: str) -> str:
    """Название календаря, если доступ есть; иначе исключение с понятной причиной."""
    data = _check(_s().get(f"{API}/calendars/{cal_id}", timeout=20), "проверка доступа")
    return data.get("summary", cal_id)


def _body(start, title: str, description: str) -> dict:
    end = start + timedelta(minutes=TRIAL_MINUTES)
    return {
        "summary": title,
        "description": description,
        "start": {"dateTime": start.isoformat(), "timeZone": TZ},
        "end": {"dateTime": end.isoformat(), "timeZone": TZ},
        "colorId": COLOR_TANGERINE,
        "reminders": {"useDefault": True},
    }


def create_event(cal_id: str, start, title: str, description: str) -> str:
    data = _check(_s().post(f"{API}/calendars/{cal_id}/events", json=_body(start, title, description), timeout=20),
                  "создание события")
    return data["id"]


def _parse_dt(v: dict) -> datetime | None:
    s = (v or {}).get("dateTime")
    if not s:
        return None  # событие на весь день (только date) — время не занимает
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def busy_events(cal_id: str, start: datetime, end: datetime, skip_ids: set = frozenset()) -> list[dict]:
    """События календаря, которые занимают время: [{"start", "end", "title"}] (aware datetime).
    Без событий на весь день, отменённых, помеченных «Свободен» и событий самого бота (skip_ids)."""
    out, token = [], None
    for _ in range(10):
        params = {"timeMin": start.isoformat(), "timeMax": end.isoformat(), "singleEvents": "true",
                  "orderBy": "startTime", "maxResults": 250, "timeZone": TZ,
                  "fields": "items(id,status,summary,start,end,transparency),nextPageToken"}
        if token:
            params["pageToken"] = token
        data = _check(_s().get(f"{API}/calendars/{cal_id}/events", params=params, timeout=20), "чтение событий")
        for ev in data.get("items", []):
            if ev.get("status") == "cancelled" or ev.get("transparency") == "transparent" or ev.get("id") in skip_ids:
                continue
            s, e = _parse_dt(ev.get("start")), _parse_dt(ev.get("end"))
            if s and e and e > s:
                out.append({"start": s, "end": e, "title": (ev.get("summary") or "Занято").strip()[:60]})
        token = data.get("nextPageToken")
        if not token:
            break
    return out


def delete_event(cal_id: str, event_id: str) -> None:
    resp = _s().delete(f"{API}/calendars/{cal_id}/events/{event_id}", timeout=20)
    if resp.status_code in (404, 410):  # уже удалено руками — это нормально
        return
    _check(resp, "удаление события")
