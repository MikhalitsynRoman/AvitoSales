import json
import time

import client

OUT_FILE = "dialogs.json"
MAX_CHATS = 80  # most recent chats; raise later if we need deeper history


def main():
    account = client.get_self_account()
    user_id = account["id"]
    print("Account:", account["name"], user_id)

    chats = client.get_all_chats(user_id, max_chats=MAX_CHATS)
    print("Fetched chats:", len(chats))

    dialogs = []
    for i, chat in enumerate(chats):
        chat_id = chat["id"]
        try:
            messages = client.get_all_messages(user_id, chat_id)
        except Exception as e:
            print(f"  [{i}] {chat_id}: error {e}")
            continue

        client_user = next(
            (u for u in chat.get("users", []) if u["id"] != user_id), None
        )
        item = chat.get("context", {}).get("value", {})

        dialogs.append(
            {
                "chat_id": chat_id,
                "item_title": item.get("title"),
                "item_url": item.get("url"),
                "location": (item.get("location") or {}).get("title"),
                "client_name": client_user["name"] if client_user else None,
                "created": chat.get("created"),
                "updated": chat.get("updated"),
                "messages": [
                    {
                        "author_id": m.get("author_id"),
                        "direction": m.get("direction"),
                        "created": m.get("created"),
                        "text": (m.get("content") or {}).get("text"),
                        "type": m.get("type"),
                    }
                    for m in sorted(messages, key=lambda m: m.get("created", 0))
                ],
            }
        )
        print(f"  [{i+1}/{len(chats)}] {chat_id}: {len(messages)} messages")
        time.sleep(0.2)

    with open(OUT_FILE, "w", encoding="utf-8") as f:
        json.dump(dialogs, f, ensure_ascii=False, indent=2)
    print(f"Saved {len(dialogs)} dialogs to {OUT_FILE}")


if __name__ == "__main__":
    main()
