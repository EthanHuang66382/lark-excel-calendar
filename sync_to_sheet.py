#!/usr/bin/env python3
"""LarkExcelCalendar — 查询 APAC 管理层日历忙闲并写入 Lark 电子表格"""

import argparse
import json
import logging
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------

def load_env_file(path: Path) -> None:
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("'\"")
        if key and key not in os.environ:
            os.environ[key] = value


load_env_file(PROJECT_DIR / ".env")

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

TZ_UTC8 = timezone(timedelta(hours=8))

SPREADSHEET_TOKEN = "RJ1Ush4nmh2aeotgKL8l2QtGgcb"
SHEET_ID = "d8c36e"

AARON_USER_ID = "ou_a34ef34252262d466f5b7b5ede682293"
JACKSON_USER_ID = "ou_e6aa709de5c54635c209414d527eab1d"
ALVIN_USER_ID = os.getenv("OPEN_ID_ALVIN", "ou_8f0b0a9e14ba9f6a1f0f96566b413009")
THOMAS_USER_ID = os.getenv("OPEN_ID_THOMAS", "ou_6f6b6f442a67861762a7c2a4b2f909f6")
DERIC_USER_ID = os.getenv("OPEN_ID_DERIC", "ou_7c886ae31caba4f77f0e3369c033b8aa")

PEOPLE = [
    {"name": "Aaron", "user_ids": [AARON_USER_ID, JACKSON_USER_ID]},
    {"name": "Alvin", "user_ids": [ALVIN_USER_ID]},
    {"name": "Thomas", "user_ids": [THOMAS_USER_ID, DERIC_USER_ID]},
]

SYNC_DAYS = 7
LARK_CLI = os.getenv("LARK_CLI_BIN", "lark-cli")
IDENTITY = "bot" if os.getenv("LARK_APP_ID") else "user"

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s] %(levelname)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# lark-cli wrapper
# ---------------------------------------------------------------------------

def run_lark_cli(args: list[str]) -> dict | None:
    cmd = [LARK_CLI] + args + ["--as", IDENTITY]
    log.debug("Running: %s", " ".join(cmd))
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.error("lark-cli failed: %s", result.stderr.strip() or result.stdout.strip())
            return None
        return json.loads(result.stdout)
    except Exception as e:
        log.error("lark-cli exception: %s", e)
        return None

# ---------------------------------------------------------------------------
# Calendar: FreeBusy
# ---------------------------------------------------------------------------

def get_freebusy(user_id: str, start_date: str, end_date: str) -> list[tuple[datetime, datetime]]:
    time_min = f"{start_date}T00:00:00+08:00"
    time_max = f"{end_date}T23:59:59+08:00"

    data_payload = json.dumps({
        "user_id": user_id,
        "time_min": time_min,
        "time_max": time_max,
        "include_external_calendar": False,
        "only_busy": True,
    })

    cmd = [LARK_CLI, "calendar", "freebusys", "list",
           "--params", json.dumps({"user_id_type": "open_id"}),
           "--data", data_payload,
           "--as", IDENTITY]

    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.error("freebusy query failed for %s: %s", user_id, result.stderr.strip())
            return []
        data = json.loads(result.stdout)
        if data.get("code") == 0:
            raw_list = data.get("data", {}).get("freebusy_list", [])
        elif data.get("ok") and "data" in data:
            raw_list = data["data"]
        else:
            log.error("freebusy unexpected response for %s: %s", user_id, str(data)[:200])
            return []
    except Exception as e:
        log.error("freebusy exception for %s: %s", user_id, e)
        return []

    slots = []
    for item in raw_list:
        start_utc = item.get("start_time", "")
        end_utc = item.get("end_time", "")
        if not start_utc or not end_utc:
            continue
        start_dt = datetime.strptime(start_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ_UTC8)
        end_dt = datetime.strptime(end_utc, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc).astimezone(TZ_UTC8)
        slots.append((start_dt, end_dt))
    return slots


def merge_slots(slots: list[tuple[datetime, datetime]]) -> list[tuple[datetime, datetime]]:
    if not slots:
        return []
    slots.sort(key=lambda x: x[0])
    merged = [slots[0]]
    for start, end in slots[1:]:
        if start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return merged


def get_merged_freebusy(user_ids: list[str], start_date: str, end_date: str) -> list[tuple[datetime, datetime]]:
    all_slots = []
    for uid in user_ids:
        all_slots.extend(get_freebusy(uid, start_date, end_date))
    return merge_slots(all_slots)

# ---------------------------------------------------------------------------
# Format busy slots for a single day
# ---------------------------------------------------------------------------

def slots_for_day(slots: list[tuple[datetime, datetime]], day: datetime) -> list[tuple[datetime, datetime]]:
    day_start = day.replace(hour=0, minute=0, second=0, microsecond=0)
    day_end = day_start + timedelta(days=1)
    result = []
    for s, e in slots:
        if e <= day_start or s >= day_end:
            continue
        clipped_start = max(s, day_start)
        clipped_end = min(e, day_end)
        result.append((clipped_start, clipped_end))
    return result


def format_time_range(start: datetime, end: datetime) -> str:
    return f"{start.strftime('%H:%M')}-{end.strftime('%H:%M')}"


def format_day_slots(slots: list[tuple[datetime, datetime]], day: datetime) -> str:
    day_slots = slots_for_day(slots, day)
    if not day_slots:
        return ""
    return "\n".join(format_time_range(s, e) for s, e in day_slots)


WORK_START_HOUR = 9
WORK_END_HOUR = 19


def compute_common_free(all_person_slots: list[list[tuple[datetime, datetime]]], day: datetime) -> str:
    day_start = day.replace(hour=WORK_START_HOUR, minute=0, second=0, microsecond=0)
    day_end = day.replace(hour=WORK_END_HOUR, minute=0, second=0, microsecond=0)

    all_busy = []
    for person_slots in all_person_slots:
        all_busy.extend(slots_for_day(person_slots, day))
    combined_busy = merge_slots(all_busy)

    free_slots = []
    cursor = day_start
    for busy_start, busy_end in combined_busy:
        if busy_start > cursor:
            free_slots.append((cursor, min(busy_start, day_end)))
        cursor = max(cursor, busy_end)
    if cursor < day_end:
        free_slots.append((cursor, day_end))

    free_slots = [(s, e) for s, e in free_slots if s < day_end and e > day_start]
    if not free_slots:
        return ""
    return "\n".join(format_time_range(s, e) for s, e in free_slots)

# ---------------------------------------------------------------------------
# Spreadsheet write
# ---------------------------------------------------------------------------

def write_to_sheet(rows: list[list[str]]) -> bool:
    num_rows = len(rows)
    num_cols = max(len(r) for r in rows) if rows else 0
    end_col = chr(ord("A") + num_cols - 1)
    range_str = f"{SHEET_ID}!A1:{end_col}{num_rows}"

    padded_rows = []
    for row in rows:
        padded = row + [""] * (num_cols - len(row))
        padded_rows.append(padded)

    values_json = json.dumps(padded_rows, ensure_ascii=False)

    cmd = [LARK_CLI, "sheets", "+write",
           "--spreadsheet-token", SPREADSHEET_TOKEN,
           "--range", range_str,
           "--values", values_json,
           "--as", IDENTITY]

    log.info("Writing %d rows to range %s", num_rows, range_str)
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.returncode != 0:
            log.error("Sheet write failed: %s", result.stderr.strip() or result.stdout.strip())
            return False
        resp = json.loads(result.stdout)
        if resp.get("ok") or resp.get("code") == 0:
            log.info("Sheet write successful")
            return True
        log.error("Sheet write unexpected response: %s", str(resp)[:300])
        return False
    except Exception as e:
        log.error("Sheet write exception: %s", e)
        return False

def apply_styles(num_data_rows: int) -> None:
    last_row = 4 + num_data_rows
    sid = SHEET_ID

    style_data = json.dumps([
        {"ranges": [f"{sid}!A1:F1"], "style": {"font": {"bold": True}, "fontSize": 14, "hAlign": 0, "vAlign": 0}},
        {"ranges": [f"{sid}!A2:F2"], "style": {"font": {"italic": True}, "foreColor": "#666666", "fontSize": 9, "hAlign": 0}},
        {"ranges": [f"{sid}!A4:F4"], "style": {"font": {"bold": True}, "foreColor": "#FFFFFF", "fontSize": 11, "backColor": "#1F4E79", "hAlign": 1, "vAlign": 1}},
        {"ranges": [f"{sid}!A5:B{last_row}"], "style": {"font": {"bold": True}, "fontSize": 11, "hAlign": 1, "vAlign": 0}},
        {"ranges": [f"{sid}!C5:E{last_row}"], "style": {"fontSize": 9, "hAlign": 0, "vAlign": 0, "backColor": "#FFF2CC"}},
        {"ranges": [f"{sid}!F5:F{last_row}"], "style": {"foreColor": "#006100", "fontSize": 9, "hAlign": 0, "vAlign": 0, "backColor": "#C6EFCE"}},
    ], ensure_ascii=False)

    cmd = [LARK_CLI, "sheets", "+batch-set-style",
           "--spreadsheet-token", SPREADSHEET_TOKEN,
           "--data", style_data,
           "--as", IDENTITY]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        log.warning("Style apply failed: %s", result.stderr.strip() or result.stdout.strip())
    else:
        log.info("Styles applied")

    # Merge title and subtitle rows
    for row_range in [f"{sid}!A1:F1", f"{sid}!A2:F2"]:
        cmd = [LARK_CLI, "sheets", "+merge-cells",
               "--spreadsheet-token", SPREADSHEET_TOKEN,
               "--range", row_range,
               "--merge-type", "MERGE_ALL",
               "--as", IDENTITY]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    # Set column widths: A=70, B=50, C/D/E=160, F=160
    widths = [(1, 1, 70), (2, 2, 50), (3, 5, 160), (6, 6, 160)]
    for start, end, px in widths:
        cmd = [LARK_CLI, "sheets", "+update-dimension",
               "--spreadsheet-token", SPREADSHEET_TOKEN,
               "--sheet-id", SHEET_ID,
               "--dimension", "COLUMNS",
               "--start-index", str(start),
               "--end-index", str(end),
               "--fixed-size", str(px),
               "--as", IDENTITY]
        subprocess.run(cmd, capture_output=True, text=True, timeout=15)

    log.info("Merge and column widths applied")


# ---------------------------------------------------------------------------
# Build spreadsheet data
# ---------------------------------------------------------------------------

WEEKDAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def build_sheet_data(dry_run: bool = False) -> list[list[str]]:
    now = datetime.now(TZ_UTC8)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    start_date = today.strftime("%Y-%m-%d")
    end_date = (today + timedelta(days=SYNC_DAYS - 1)).strftime("%Y-%m-%d")

    log.info("Querying freebusy for %s to %s (%s)", start_date, end_date, IDENTITY)

    person_slots = {}
    for person in PEOPLE:
        name = person["name"]
        log.info("Querying %s (%d accounts)...", name, len(person["user_ids"]))
        slots = get_merged_freebusy(person["user_ids"], start_date, end_date)
        person_slots[name] = slots
        log.info("  %s: %d busy slots", name, len(slots))

    updated_str = now.strftime("%Y-%m-%d %H:%M") + " UTC+8"

    rows = [
        ["APAC Management Calendar (UTC+8)", "", "", "", "", ""],
        [f"Last Updated: {updated_str}", "", "", "", "", ""],
        ["", "", "", "", "", ""],
        ["Date", "Day", "Aaron busy time", "Alvin busy time", "Thomas busy time", "All available time"],
    ]

    all_person_slot_lists = [person_slots[p["name"]] for p in PEOPLE]

    for i in range(SYNC_DAYS):
        day = today + timedelta(days=i)
        if day.weekday() >= 5:  # skip Saturday(5) and Sunday(6)
            continue
        date_str = day.strftime("%m/%d")
        weekday_str = WEEKDAY_NAMES[day.weekday()]
        row = [date_str, weekday_str]
        for person in PEOPLE:
            row.append(format_day_slots(person_slots[person["name"]], day))
        row.append(compute_common_free(all_person_slot_lists, day))
        rows.append(row)

    if dry_run:
        log.info("=== DRY RUN — would write: ===")
        for i, row in enumerate(rows):
            log.info("  Row %2d: %s", i + 1, row)

    return rows

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync APAC calendar to Lark Sheet")
    parser.add_argument("--dry-run", action="store_true", help="Print data without writing")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    log.info("LarkExcelCalendar sync starting (identity=%s)", IDENTITY)

    rows = build_sheet_data(dry_run=args.dry_run)

    if args.dry_run:
        log.info("Dry run complete — no changes made")
        return

    success = write_to_sheet(rows)
    if not success:
        log.error("Failed to write to spreadsheet")
        sys.exit(1)

    num_data_rows = len(rows) - 4  # subtract header rows
    apply_styles(num_data_rows)

    log.info("Sync complete")


if __name__ == "__main__":
    main()
