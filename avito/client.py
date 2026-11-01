import os
import requests
from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

CLIENT_ID = os.getenv("AVITO_CLIENT_ID")
CLIENT_SECRET = os.getenv("AVITO_CLIENT_SECRET")
BASE_URL = "https://api.avito.ru"

_token_cache = {"access_token": None}


def get_token() -> str:
    if _token_cache["access_token"]:
        return _token_cache["access_token"]
    resp = requests.post(
        f"{BASE_URL}/token",
        data={
            "grant_type": "client_credentials",
            "client_id": CLIENT_ID,
            "client_secret": CLIENT_SECRET,
        },
    )
    resp.raise_for_status()
    data = resp.json()
    _token_cache["access_token"] = data["access_token"]
    return data["access_token"]


def _headers() -> dict:
    return {"Authorization": f"Bearer {get_token()}"}


def get_self_account() -> dict:
    resp = requests.get(f"{BASE_URL}/core/v1/accounts/self", headers=_headers())
    resp.raise_for_status()
    return resp.json()


def get_chats(user_id: int, limit: int = 100, offset: int = 0) -> dict:
    resp = requests.get(
        f"{BASE_URL}/messenger/v2/accounts/{user_id}/chats",
        headers=_headers(),
        params={"limit": limit, "offset": offset},
    )
    resp.raise_for_status()
    return resp.json()


def get_messages(user_id: int, chat_id: str, limit: int = 100, offset: int = 0) -> dict:
    resp = requests.get(
        f"{BASE_URL}/messenger/v3/accounts/{user_id}/chats/{chat_id}/messages",
        headers=_headers(),
        params={"limit": limit, "offset": offset},
    )
    resp.raise_for_status()
    return resp.json()


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
    resp = requests.post(
        f"{BASE_URL}/messenger/v1/accounts/{user_id}/chats/{chat_id}/messages",
        headers=_headers(),
        json={"message": {"text": text}, "type": "text"},
    )
    resp.raise_for_status()
    return resp.json()


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
