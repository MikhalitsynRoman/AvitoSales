import requests

from config import BOT_TOKEN

API = f"https://api.telegram.org/bot{BOT_TOKEN}"


def get_updates(offset: int | None = None, timeout: int = 2) -> list:
    params = {"timeout": timeout}
    if offset is not None:
        params["offset"] = offset
    resp = requests.get(f"{API}/getUpdates", params=params, timeout=timeout + 10)
    resp.raise_for_status()
    return resp.json().get("result", [])


def send_message(chat_id: int, text: str, reply_markup: dict | None = None) -> dict:
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    resp = requests.post(f"{API}/sendMessage", json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json().get("result", {})


def answer_callback_query(callback_query_id: str, text: str = "") -> None:
    requests.post(
        f"{API}/answerCallbackQuery",
        json={"callback_query_id": callback_query_id, "text": text},
        timeout=15,
    )


def edit_message_reply_markup(chat_id: int, message_id: int, reply_markup: dict | None = None) -> None:
    requests.post(
        f"{API}/editMessageReplyMarkup",
        json={"chat_id": chat_id, "message_id": message_id, "reply_markup": reply_markup or {}},
        timeout=15,
    )


def inline_keyboard(buttons: list[tuple[str, str]]) -> dict:
    """buttons: list of (label, callback_data), all placed in a single row."""
    return {"inline_keyboard": [[{"text": label, "callback_data": data} for label, data in buttons]]}
