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
    first = sheet.row_values(1)
    if not first:
        sheet.update(values=[headers], range_name="A1", value_input_option=RAW)
    elif first != headers:
        # Раньше здесь лист очищался целиком — так можно потерять все заявки. Теперь только останавливаемся.
        raise RuntimeError(f"В листе «{name}» изменена первая строка (заголовки) — данные не трогаю. "
                           f"Нужно: {', '.join(headers)}")
    _sheet_cache[name] = sheet
    return sheet


def _find_row(sheet, value: str, col: int) -> int | None:
    """Где строка с этим значением в колонке сейчас на самом деле (её могли сдвинуть, удалив строки вручную)."""
    cell = sheet.find(value, in_column=col)  # gspread 5.x и 6.x: None, если не нашлось
    return cell.row if cell else None


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


def find_contact_by_chat(chat_id: str) -> dict | None:
    idx = _IDX["chat_id"]
    for i, r in sorted(_load_contacts().items(), reverse=True):
        if r[idx] == chat_id:
            return {"row": i, "values": dict(zip(CONTACTS_HEADERS, r))}
    return None


def _reload_contacts() -> None:
    global _contacts
    _contacts = None
    _load_contacts()


def update_contact(row_id: str, fields: dict) -> None:
    """Переписывает строку клиента одним запросом (новое сообщение того же клиента — та же строка).
    Перед записью проверяем, что по запомненному номеру всё ещё этот клиент: если строки удаляли или
    двигали руками, номер сдвигается, и без проверки данные одного клиента легли бы поверх другого
    (03.11 так пропала строка Веры)."""
    found = get_contact_by_id(row_id)
    if not found:
        return
    sheet = _contacts_sheet()
    row_num = found["row"]
    width = len(CONTACTS_HEADERS)
    current = (sheet.row_values(row_num) + [""] * width)[:width]
    if current[_IDX["id"]] != row_id:
        _reload_contacts()
        again = get_contact_by_id(row_id)
        if not again:  # строку удалили вручную — не воскрешаем и, главное, не пишем в чужую
            print(f"WARN: строки {row_id} больше нет в таблице — запись пропущена")
            return
        row_num = again["row"]
        current = (sheet.row_values(row_num) + [""] * width)[:width]
    # за основу — то, что сейчас в таблице: ручные правки Романа в других ячейках сохраняются
    values = list(current)
    for k, v in fields.items():
        values[_IDX[k]] = "" if v is None else str(v)
    _load_contacts()[row_num] = values
    end_col = gspread.utils.rowcol_to_a1(row_num, len(CONTACTS_HEADERS))
    sheet.update(values=[values], range_name=f"A{row_num}:{end_col}", value_input_option=RAW)


def dedupe_contacts() -> int:
    """Один клиент — одна строка. Склеивает строки с одинаковым chat_id: остаётся первая по месту,
    в ней — данные самой свежей (имя и город берутся из любой, где они есть). Возвращает, сколько строк удалено.
    Вызывать только при старте бота: номера строк в памяти после удаления пересчитываются."""
    global _contacts
    sheet = _contacts_sheet()
    rows = sheet.get_all_values()[1:]
    groups = {}
    for i, r in enumerate(rows, start=2):
        r = (r + [""] * len(CONTACTS_HEADERS))[:len(CONTACTS_HEADERS)]
        if r[_IDX["chat_id"]]:
            groups.setdefault(r[_IDX["chat_id"]], []).append((i, r))
    to_delete = []
    dup_rows = sum(len(items) - 1 for items in groups.values() if len(items) > 1)
    if dup_rows > 5:
        # столько повторов разом — признак сбоя, а не обычных дублей; удалять строки в таком случае опасно
        raise RuntimeError(f"В «Обращениях» {dup_rows} повторов клиентов — склейку не делаю, нужна проверка")
    for items in groups.values():
        if len(items) < 2:
            continue
        keep_row = items[0][0]
        merged = list(items[-1][1])
        for col in ("Имя", "Город"):
            if merged[_IDX[col]] in ("", "—"):
                merged[_IDX[col]] = next((r[_IDX[col]] for _, r in reversed(items) if r[_IDX[col]] not in ("", "—")), "—")
        end_col = gspread.utils.rowcol_to_a1(keep_row, len(CONTACTS_HEADERS))
        sheet.update(values=[merged], range_name=f"A{keep_row}:{end_col}", value_input_option=RAW)
        to_delete += [i for i, _ in items[1:]]
    for i in sorted(to_delete, reverse=True):
        sheet.delete_rows(i)
    _contacts = None  # перечитать с новыми номерами строк
    return len(to_delete)


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
    fields = {"Статус": status}
    if new_draft is not None:
        fields["Черновик ответа"] = new_draft
    update_contact(row_id, fields)


_bookings = None  # строки листа «Записаны на пробный» (в памяти, как и остальные листы)


def get_bookings() -> list[dict]:
    global _bookings
    if _bookings is None:
        sheet = _get_or_create_sheet(_spreadsheet(), BOOKINGS_SHEET, BOOKINGS_HEADERS)
        _bookings = [dict(zip(BOOKINGS_HEADERS, r)) for r in sheet.get_all_values()[1:]]
    return _bookings


def log_booking(name: str, phone: str, chat_id: str, when: str, goal: str) -> None:
    """when — «05.11.2026 16:30»: по нему бот понимает, что это окно занято."""
    bookings = get_bookings()
    row = [datetime.now().strftime("%d.%m.%Y %H:%M"), name or "—", phone or "—", chat_id, when or "—", goal or "—"]
    _get_or_create_sheet(_spreadsheet(), BOOKINGS_SHEET, BOOKINGS_HEADERS).append_row(row, value_input_option=RAW)
    bookings.append(dict(zip(BOOKINGS_HEADERS, row)))

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
    global _state
    state = _load_state()
    found = state.get(key)
    if found:
        if found[1] == value:
            return
        sheet = _state_sheet()
        if sheet.cell(found[0], 1).value != key:  # строки сдвинули руками — перечитываем, а не пишем мимо
            _state = None
            set_state(key, value)
            return
        _set_cell(sheet, found[0], 2, value)
        found[1] = value
        return
    row_num = _append(_state_sheet(), [key, value]) or (max((v[0] for v in state.values()), default=1) + 1)
    state[key] = [row_num, value]
