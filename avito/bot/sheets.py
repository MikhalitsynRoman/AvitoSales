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


def _client():
    creds = Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return gspread.authorize(creds)


_sheet_cache = {}

# RAW: иначе Google превращает "02.11.2026" в дату и бот перестаёт узнавать сегодняшний день
RAW = "RAW"


def _set_cell(sheet, row: int, col: int, value: str) -> None:
    sheet.update(values=[[value]], range_name=gspread.utils.rowcol_to_a1(row, col), value_input_option=RAW)


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


def get_logged_msg_ids() -> set:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, CONTACTS_SHEET, CONTACTS_HEADERS)
    rows = sheet.get_all_values()[1:]
    i = _IDX["avito_msg_id"]
    return {r[i] for r in rows if len(r) > i and r[i]}


def log_contact(chat_id: str, avito_msg_id: str, name: str, city: str, category: str, client_text: str, draft: str,
                 telegram_msg_id: int | None, status: str = "ожидает") -> str:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, CONTACTS_SHEET, CONTACTS_HEADERS)
    row_id = str(uuid.uuid4())[:8]
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
    sheet.append_row(row, value_input_option=RAW)
    return row_id


def get_contact_by_id(row_id: str) -> dict | None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, CONTACTS_SHEET, CONTACTS_HEADERS)
    rows = sheet.get_all_values()
    idx = _IDX["id"]
    for i, r in enumerate(rows[1:], start=2):
        if len(r) > idx and r[idx] == row_id:
            return {"row": i, "values": dict(zip(CONTACTS_HEADERS, r))}
    return None


def find_row_id_by_telegram_msg_id(telegram_msg_id: int) -> str | None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, CONTACTS_SHEET, CONTACTS_HEADERS)
    rows = sheet.get_all_values()[1:]
    idx = _IDX["telegram_msg_id"]
    id_idx = _IDX["id"]
    for r in rows:
        if len(r) > idx and r[idx] == str(telegram_msg_id):
            return r[id_idx]
    return None


def update_contact_status(row_id: str, status: str, new_draft: str | None = None) -> None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, CONTACTS_SHEET, CONTACTS_HEADERS)
    found = get_contact_by_id(row_id)
    if not found:
        return
    row = found["row"]
    _set_cell(sheet, row, CONTACTS_HEADERS.index("Статус") + 1, status)
    if new_draft is not None:
        _set_cell(sheet, row, CONTACTS_HEADERS.index("Черновик ответа") + 1, new_draft)


def log_booking(name: str, phone: str, chat_id: str, when: str, goal: str) -> None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, BOOKINGS_SHEET, BOOKINGS_HEADERS)
    sheet.append_row(value_input_option=RAW, values=[
        datetime.now().strftime("%d.%m.%Y %H:%M"), name or "—", phone or "—", chat_id, when or "—", goal or "—",
    ])


def get_state(key: str, default: str = "") -> str:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, STATE_SHEET, STATE_HEADERS)
    rows = sheet.get_all_values()[1:]
    for r in rows:
        if len(r) >= 2 and r[0] == key:
            return r[1]
    return default


def set_state(key: str, value: str) -> None:
    ss = _spreadsheet()
    sheet = _get_or_create_sheet(ss, STATE_SHEET, STATE_HEADERS)
    rows = sheet.get_all_values()
    for i, r in enumerate(rows[1:], start=2):
        if r and r[0] == key:
            _set_cell(sheet, i, 2, value)
            return
    sheet.append_row([key, value], value_input_option=RAW)
