import re
import uuid
from datetime import datetime

import gspread
from google.oauth2.service_account import Credentials

from config import CREDENTIALS_FILE, SPREADSHEET_ID

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CONTACTS_SHEET = "Обращения"
CONTACTS_HEADERS = [
    "Дата", "Время", "Имя", "Город", "Категория", "Сообщение клиента",
    "Черновик ответа", "Статус",
    "id", "chat_id", "avito_msg_id", "telegram_msg_id",  # технические, не трогать
]

BOOKINGS_SHEET = "Записаны на пробный"
BOOKINGS_HEADERS = [
    "Дата и время", "Имя", "Телефон", "chat_id", "Когда пробный урок", "Класс/цель",
]

STATE_SHEET = "Состояние"
STATE_HEADERS = ["key", "value"]

# Бот — единственный, кто пишет в таблицу, поэтому читаем листы один раз при запуске и дальше держим их в памяти.
# Одно чтение Google Таблицы с хостинга занимает секунды, а кнопки в Telegram должны отвечать сразу.
# Правки, сделанные в таблице руками, бот увидит после перезапуска (не реже раза в час).


def _client():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


_sheet_cache = {}

# RAW: иначе Google превращает "02.11.2026" в дату и бот перестаёт узнавать сегодняшний день
RAW = "RAW"


def _set_cell(sheet, row: int, col: int, value: str) -> None:
    sheet.update(values=[[value]], range_name=gspread.utils.rowcol_to_a1(row, col), value_input_option=RAW)


def _append(sheet, values: list) -> int:
    """Дописывает строку и возвращает её номер (берём из ответа Google, а не считаем сами)."""
    resp = sheet.append_row(values, value_input_option=RAW)
    rng = (resp or {}).get("updates", {}).get("updatedRange", "")
    m = re.search(r"![A-Z]+(\d+)", rng)
    return int(m.group(1)) if m else 0


def _get_or_create_sheet(spreadsheet, name: str, headers: list):
    if name in _sheet_cache:
        return _sheet_cache[name]
    try:
        sheet = spreadsheet.worksheet(name)
    except gspread.exceptions.WorksheetNotFound:
        sheet = spreadsheet.add_worksheet(title=name, rows=2000, cols=len(headers))
        sheet.append_row(headers)
        _sheet_cache[name] = sheet
        return sheet
    if sheet.row_values(1) != headers:
        sheet.clear()
        sheet.append_row(headers)
    _sheet_cache[name] = sheet
    return sheet


_ss_cache = None


def _spreadsheet():
    global _ss_cache
    if _ss_cache is None:
        _ss_cache = _client().open_by_key(SPREADSHEET_ID)
    return _ss_cache


_IDX = {h: i for i, h in enumerate(CONTACTS_HEADERS)}

# ---------- обращения ----------

_contacts = None  # номер строки в таблице -> список значений


def _contacts_sheet():
    return _get_or_create_sheet(_spreadsheet(), CONTACTS_SHEET, CONTACTS_HEADERS)


def _load_contacts() -> dict:
    global _contacts
    if _contacts is None:
        rows = _contacts_sheet().get_all_values()[1:]
        _contacts = {}
        for i, r in enumerate(rows, start=2):
            _contacts[i] = (r + [""] * len(CONTACTS_HEADERS))[:len(CONTACTS_HEADERS)]
    return _contacts


def new_row_id() -> str:
    return str(uuid.uuid4())[:8]


def get_logged_msg_ids() -> set:
    i = _IDX["avito_msg_id"]
    return {r[i] for r in _load_contacts().values() if r[i]}


def all_contacts() -> list[dict]:
    return [dict(zip(CONTACTS_HEADERS, r)) for _, r in sorted(_load_contacts().items())]


def log_contact(chat_id: str, avito_msg_id: str, name: str, city: str, category: str, client_text: str, draft: str,
                 telegram_msg_id: int | None, status: str = "ожидает", row_id: str | None = None) -> str:
    contacts = _load_contacts()
    row_id = row_id or new_row_id()
    now = datetime.now()
    row = [""] * len(CONTACTS_HEADERS)
    row[_IDX["Дата"]] = now.strftime("%d.%m.%Y")
    row[_IDX["Время"]] = now.strftime("%H:%M")
    row[_IDX["Имя"]] = name or "—"
    row[_IDX["Город"]] = city or "—"
    row[_IDX["Категория"]] = category or "—"
    row[_IDX["Сообщение клиента"]] = client_text or ""
    row[_IDX["Черновик ответа"]] = draft or ""
    row[_IDX["Статус"]] = status
    row[_IDX["id"]] = row_id
    row[_IDX["chat_id"]] = chat_id
    row[_IDX["avito_msg_id"]] = avito_msg_id
    row[_IDX["telegram_msg_id"]] = str(telegram_msg_id or "")
    row_num = _append(_contacts_sheet(), row) or (max(contacts, default=1) + 1)
    contacts[row_num] = row
    return row_id


def get_contact_by_id(row_id: str) -> dict | None:
    idx = _IDX["id"]
    for i, r in _load_contacts().items():
        if r[idx] == row_id:
            return {"row": i, "values": dict(zip(CONTACTS_HEADERS, r))}
    return None


def find_row_id_by_telegram_msg_id(telegram_msg_id: int) -> str | None:
    idx = _IDX["telegram_msg_id"]
    for r in _load_contacts().values():
        if r[idx] == str(telegram_msg_id):
            return r[_IDX["id"]]
    return None


def mark_status_cached(row_id: str, status: str, new_draft: str | None = None) -> None:
    """Только в памяти и мгновенно; в таблицу затем пишет update_contact_status."""
    found = get_contact_by_id(row_id)
    if found:
        cached = _load_contacts()[found["row"]]
        cached[_IDX["Статус"]] = status
        if new_draft is not None:
            cached[_IDX["Черновик ответа"]] = new_draft


def update_contact_status(row_id: str, status: str, new_draft: str | None = None) -> None:
    found = get_contact_by_id(row_id)
    if not found:
        return
    row = found["row"]
    cached = _load_contacts()[row]
    cached[_IDX["Статус"]] = status
    _set_cell(_contacts_sheet(), row, CONTACTS_HEADERS.index("Статус") + 1, status)
    if new_draft is not None:
        cached[_IDX["Черновик ответа"]] = new_draft
        _set_cell(_contacts_sheet(), row, CONTACTS_HEADERS.index("Черновик ответа") + 1, new_draft)


def log_booking(name: str, phone: str, chat_id: str, when: str, goal: str) -> None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, BOOKINGS_SHEET, BOOKINGS_HEADERS)
    sheet.append_row(value_input_option=RAW, values=[
        datetime.now().strftime("%d.%m.%Y %H:%M"), name or "—", phone or "—", chat_id, when or "—", goal or "—",
    ])

# ---------- состояние (ключ -> значение) ----------

_state = None  # key -> [номер строки, значение]


def _state_sheet():
    return _get_or_create_sheet(_spreadsheet(), STATE_SHEET, STATE_HEADERS)


def _load_state() -> dict:
    global _state
    if _state is None:
        rows = _state_sheet().get_all_values()[1:]
        _state = {}
        for i, r in enumerate(rows, start=2):
            if r and r[0] and r[0] not in _state:
                _state[r[0]] = [i, r[1] if len(r) > 1 else ""]
    return _state


def get_state(key: str, default: str = "") -> str:
    found = _load_state().get(key)
    return found[1] if found else default


def set_state(key: str, value: str) -> None:
    state = _load_state()
    found = state.get(key)
    if found:
        if found[1] == value:
            return
        _set_cell(_state_sheet(), found[0], 2, value)
        found[1] = value
        return
    row_num = _append(_state_sheet(), [key, value]) or (max((v[0] for v in state.values()), default=1) + 1)
    state[key] = [row_num, value]
