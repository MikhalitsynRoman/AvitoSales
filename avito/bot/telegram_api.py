import socket
import time

import requests
import urllib3.util.connection as urllib3_connection
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# С хостинга reg.ru соединение с api.telegram.org иногда не устанавливается за 15 с — из-за этого кнопки «висели».
# 1) только IPv4: попытка через IPv6 на хостинге может съедать весь таймаут;
# 2) одно постоянное соединение (keep-alive) вместо нового на каждый запрос;
# 3) короткий таймаут на установку соединения и несколько повторов. Повторяем только неудачное соединение
#    (read=0): если запрос уже ушёл в Telegram, повтор мог бы продублировать сообщение.
urllib3_connection.allowed_gai_family = lambda: socket.AF_INET

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


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    return _call("sendMessage", payload).get("result", {})


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    # Только убирает «загрузку» на кнопке. Если не дошло — само действие всё равно выполняем.
    _call("answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}, raise_errors=False)


def edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: dict | None = None) -> None:
    _call("editMessageReplyMarkup", {"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {}},
          raise_errors=False)


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


def inline_keyboard(buttons: list[tuple[str, str]]) -> dict:
    """buttons: list of (label, callback_data), all placed in a single row."""
    return keyboard_rows([buttons])


def keyboard_rows(rows: list[list[tuple[str, str]]]) -> dict:
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in row] for row in rows]}
