import json
import os
import time

import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
BASE_URL = "https://api.avito.ru"
# Токен живёт ~24 ч; храним его между запусками, Avito не даёт выпускать новый каждую минуту.
TOKEN_FILE = os.getenv("AVITO_TOKEN_FILE") or os.path.join(os.path.expanduser("~"), ".avito_token.json")

# Срок храним и в памяти: бот на хостинге работает часами, и токен, взятый при запуске, истекает посреди работы
# (03.11 в 18:20 так и случилось — Avito начал отвечать 403 каждую минуту).
_token_cache = {"access_token": None, "expires_at": 0.0}


def _load_token_file() -> tuple[str, float] | None:
    try:
        with open(TOKEN_FILE, encoding="utf-8") as f:
            data = json.load(f)
        if data.get("expires_at", 0) - 300 > time.time():
            return data["access_token"], float(data["expires_at"])
    except (OSError, ValueError, KeyError):
        pass
    return None


def get_token(force_new: bool = False) -> str:
    if _token_cache["access_token"] and not force_new and _token_cache["expires_at"] - 300 > time.time():
        return _token_cache["access_token"]
    if not force_new:
        cached = _load_token_file()
        if cached:
            _token_cache["access_token"], _token_cache["expires_at"] = cached
            return cached[0]
    resp = requests.post(
        f"{BASE_URL}/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
        timeout=20,
    )
    resp.raise_for_status()
    data = resp.json()
    if "access_token" not in data:
        raise RuntimeError(f"Avito не выдал токен: {str(data)[:200]}")
    expires_at = time.time() + int(data.get("expires_in", 3600))
    _token_cache["access_token"], _token_cache["expires_at"] = data["access_token"], expires_at
    try:
        with open(TOKEN_FILE, "w", encoding="utf-8") as f:
            json.dump({"access_token": data["access_token"], "expires_at": expires_at}, f)
        os.chmod(TOKEN_FILE, 0o600)
    except OSError:
        pass
    return data["access_token"]


def _request(method: str, path: str, **kwargs) -> dict:
    """Запрос к Avito. На 401/403 (токен истёк или отозван) один раз берём новый токен и повторяем —
    Avito такой запрос не выполнил, так что повтор безопасен и для отправки сообщения."""
    for attempt in (1, 2):
        resp = requests.request(method, f"{BASE_URL}{path}", headers={"Authorization": f"Bearer {get_token(force_new=attempt == 2)}"},
                                timeout=20, **kwargs)
        if resp.status_code in (401, 403) and attempt == 1:
            continue
        if not resp.ok:
            raise RuntimeError(f"Avito {resp.status_code} на {path.split('?')[0]}: {resp.text[:200]}")
        return resp.json()


def get_self_account() -> dict:
    return _request("GET", "/core/v1/accounts/self")


def get_chats(user_id: int, limit: int = 100, offset: int = 0) -> dict:
    return _request("GET", f"/messenger/v2/accounts/{user_id}/chats", params={"limit": limit, "offset": offset})


def get_messages(user_id: int, chat_id: str, limit: int = 100, offset: int = 0) -> dict:
    return _request("GET", f"/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages",
                    params={"limit": limit, "offset": offset})


def get_all_chats(user_id: int, max_chats: int = None) -> list:
    all_chats = []
    offset = 0
    limit = 100
    while True:
        page = get_chats(user_id, limit=limit, offset=offset)
        batch = page.get("chats", [])
        all_chats.extend(batch)
        if max_chats and len(all_chats) >= max_chats:
            return all_chats[:max_chats]
        if len(batch) < limit or offset + limit >= 1000:
            break
        offset += limit
    return all_chats


def send_message(user_id: int, chat_id: str, text: str) -> dict:
    return _request("POST", f"/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages",
                    json={"message": {"text": text}, "type": "text"})


def get_all_messages(user_id: int, chat_id: str) -> list:
    all_messages = []
    offset = 0
    limit = 100
    while True:
        page = get_messages(user_id, chat_id, limit=limit, offset=offset)
        batch = page.get("messages", [])
        all_messages.extend(batch)
        if len(batch) < limit:
            break
        offset += limit
    return all_messages


if __name__ == "__main__":
    token = get_token()
    print("Token OK, length:", len(token))

    account = get_self_account()
    print("Account:", account)
