import json
import os
import socket
import time

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import ADMIN_ID, BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# С хостинга reg.ru соединение с api.telegram.org часто не устанавливается — из-за этого кнопки «висели».
# Замер 03.11 с хостинга: IPv4 149.154.166.110 — 3 из 3 таймаут, IPv6 2001:67c:4e8:f004::9 — 2 из 3 за ~1 с. Поэтому:
# 1) для Telegram сначала пробуем IPv6, IPv4 — запасной (Avito и Google не трогаем);
# 2) одно постоянное соединение (keep-alive) вместо нового на каждый запрос;
# 3) короткий таймаут на установку соединения и несколько повторов. Повторяем только неудачное соединение
#    (read=0): если запрос уже ушёл в Telegram, повтор мог бы продублировать сообщение.
_TG_HOST = "api.telegram.org"
_orig_getaddrinfo = socket.getaddrinfo


def _getaddrinfo_ipv6_first(host, *args, **kwargs):
    res = _orig_getaddrinfo(host, *args, **kwargs)
    if host == _TG_HOST:
        res = sorted(res, key=lambda a: 0 if a[0] == socket.AF_INET6 else 1)
    return res


socket.getaddrinfo = _getaddrinfo_ipv6_first

_session = requests.Session()
_session.mount("https://", HTTPAdapter(max_retries=Retry(
    total=4, connect=4, read=0, status=0, backoff_factor=0.3, allowed_methods=None, raise_on_status=False,
)))
CONNECT_TIMEOUT = 4

# Качество связи с Telegram за время работы процесса — показывается в /status
stats = {"calls": 0, "fails": 0, "slow": 0, "max_secs": 0.0}


def clean_error(err) -> str:
    """Текст ошибки без токена бота (requests кладёт полный адрес в сообщение об ошибке)."""
    return str(err).replace(BOT_TOKEN, "***")


def _call(method: str, payload: dict | None = None, read_timeout: float = 15, raise_errors: bool = True) -> dict:
    t = time.time()
    stats["calls"] += 1
    try:
        resp = _session.post(f"{API}/{method}", json=payload or {}, timeout=(CONNECT_TIMEOUT, read_timeout))
    except requests.RequestException as e:
        stats["fails"] += 1
        if raise_errors:
            raise RuntimeError(f"нет связи с Telegram ({method}): {clean_error(e)[:150]}") from None
        return {}
    finally:
        took = time.time() - t
        if method != "getUpdates":  # long-poll ждёт по 25 с специально, это не медлительность
            stats["max_secs"] = max(stats["max_secs"], took)
            if took > 3:
                stats["slow"] += 1
    data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if not data.get("ok") and raise_errors:
        raise RuntimeError(f"Telegram {method}: {clean_error(resp.text)[:200]}")
    return data


def get_updates(offset: int | None = None, timeout: int = 2) -> list:
    payload = {"timeout": timeout}
    if offset is not None:
        payload["offset"] = offset
    return _call("getUpdates", payload, read_timeout=timeout + 10).get("result", [])


def send_direct(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    """Сообщение только этому человеку, без копий команде."""
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", payload).get("result", {})


def send_photo(chat_id: int, png: bytes, caption: str = "", reply_markup: dict | None = None) -> dict:
    """Картинка (PNG) с подписью — только этому человеку, без копий команде."""
    t = time.time()
    stats["calls"] += 1
    data = {"chat_id": chat_id, "caption": caption[:1024]}
    if reply_markup:
        data["reply_markup"] = json.dumps(reply_markup, ensure_ascii=False)
    try:
        resp = _session.post(f"{API}/sendPhoto", data=data, files={"photo": ("schedule.png", png, "image/png")},
                             timeout=(CONNECT_TIMEOUT, 40))
    except requests.RequestException as e:
        stats["fails"] += 1
        raise RuntimeError(f"нет связи с Telegram (sendPhoto): {clean_error(e)[:150]}") from None
    finally:
        stats["max_secs"] = max(stats["max_secs"], time.time() - t)
    res = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
    if not res.get("ok"):
        raise RuntimeError(f"Telegram sendPhoto: {clean_error(resp.text)[:200]}")
    return res.get("result", {})


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    res = send_direct(chat_id, text, reply_markup)
    if chat_id == ADMIN_ID and res.get("message_id"):
        _copy_to_team(res["message_id"], text, reply_markup)
    return res


# ---------- Команда: всё, что бот пишет Роману, копией уходит людям, которым он открыл доступ (/join) ----------
# У каждой копии свой номер сообщения в чате помощника. Связь «номер у Романа ↔ номер у помощника» хранится в файле
# рядом с токеном Avito: по ней правки карточки (✅ Отправлено, варианты) доходят до всех копий, а нажатие кнопки
# или reply в копии бот понимает как действие с карточкой Романа. Копии — не обязательны: если до помощника
# не дошло (заблокировал бота, нет связи), у Романа всё работает как раньше.
team_ids = lambda: []  # noqa: E731  main подставляет функцию, которая читает список из «Состояния»
_MAP_FILE = os.path.expanduser("~/.avito_team_map.json")
_MAP_KEEP = 5000


def _load_map() -> dict:
    try:
        with open(_MAP_FILE, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


_copies = _load_map()  # "номер у Романа" -> {"chat_id помощника": номер копии}
_origin = {(int(c), int(m)): int(a) for a, cs in _copies.items() for c, m in cs.items()}


def _save_map() -> None:
    if len(_copies) > _MAP_KEEP:
        for key in sorted(_copies, key=int)[:len(_copies) - _MAP_KEEP]:
            for c, m in _copies.pop(key).items():
                _origin.pop((int(c), int(m)), None)
    try:
        tmp = _MAP_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_copies, f)
        os.replace(tmp, _MAP_FILE)
    except OSError as e:
        print("team map not saved:", e)


def _copy_to_team(admin_msg_id: int, text: str, reply_markup: dict | None) -> None:
    made = {}
    for cid in team_ids():
        payload = {"chat_id": cid, "text": text}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        mid = (_call("sendMessage", payload, raise_errors=False).get("result") or {}).get("message_id")
        if mid:
            made[str(cid)] = mid
            _origin[(cid, mid)] = admin_msg_id
    if made:
        _copies[str(admin_msg_id)] = made
        _save_map()


def _edit_copies(method: str, admin_msg_id: int, payload: dict) -> None:
    members = set(team_ids())  # тем, у кого доступ закрыли, новое больше не показываем
    for cid, mid in (_copies.get(str(admin_msg_id)) or {}).items():
        if int(cid) in members:
            _call(method, {**payload, "chat_id": int(cid), "message_id": mid}, raise_errors=False)


def to_admin_message(chat_id: int, message_id: int) -> int | None:
    """Номер сообщения у Романа, копией которого является сообщение помощника (None — не копия)."""
    return _origin.get((chat_id, message_id))


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    # Только убирает «загрузку» на кнопке. Если не дошло — само действие всё равно выполняем.
    _call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}, raise_errors=False)


def edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {}}
    _call("editMessageReplyMarkup", payload, raise_errors=False)
    if chat_id == ADMIN_ID:
        _edit_copies("editMessageReplyMarkup", message_id, payload)


def edit_message_text(chat_id: int, message_id: int, text: str, reply_markup: dict | None = None) -> None:
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    try:
        _call("editMessageText", payload)
    except RuntimeError as e:
        # "message is not modified" — безобидно (тот же вариант нажат повторно), остальное показываем
        if "not modified" not in str(e):
            raise
    if chat_id == ADMIN_ID:
        _edit_copies("editMessageText", message_id, payload)


def inline_keyboard(buttons: list[tuple[str, str]]) -> dict:
    """buttons: list of (label, callback_data), all placed in a single row."""
    return keyboard_rows([buttons])


def keyboard_rows(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}
