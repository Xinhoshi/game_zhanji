from __future__ import annotations

import argparse
import asyncio
import copy
from difflib import SequenceMatcher
import json
import os
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from PIL import Image
from winsdk.windows.globalization import Language
from winsdk.windows.graphics.imaging import BitmapDecoder
from winsdk.windows.media.ocr import OcrEngine
from winsdk.windows.storage import FileAccessMode, StorageFile


MEMBER_IMAGE = "盟.png"
BOSS_IMAGE = "Boss.png"
DATA_DIR = "data"
CORRECTIONS_FILE = "corrections.json"
RECORDS_DIR = "records"
MEMBER_RECORDS_DIR = "members"
BOSS_RECORDS_DIR = "boss"
ARCHIVE_DIR = "archives"


def normalize_text(value: str) -> str:
    value = value.replace("．", ".").replace("。", ".").replace("，", ",").replace("、", ",").replace("：", ":")
    value = value.replace("＃", "#").replace("﹟", "#").replace("　", " ")
    value = re.sub(r"\s+", " ", value)
    return value.strip(" ,.'`，、")


def compact_text(value: str) -> str:
    value = normalize_text(value)
    value = re.sub(r"\s+", "", value)
    value = value.replace("31()#", "310#")
    value = value.replace("31（）#", "310#")
    value = value.replace("I#", "1#")
    return value.strip(" ,.'`，、")


def player_key(zone: int | None, name: str) -> str:
    if zone is None:
        return name
    return f"{zone}#{name}"


def normalized_identity_name(name: str | None) -> str:
    return compact_text(str(name or "")).casefold()


def ocr_loose_identity_name(name: str | None) -> str:
    normalized = normalized_identity_name(name)
    return normalized.translate(str.maketrans({"0": "o", "1": "l", "i": "l", "|": "l", "箫": "萧"}))


def identity_alias_key(zone: int | None, name: str | None) -> tuple[int, str] | None:
    normalized = ocr_loose_identity_name(name)
    if zone is None or not normalized:
        return None
    return (zone, normalized)


def parse_identity(line: str) -> dict[str, Any] | None:
    clean = compact_text(line)
    match = re.search(r"(\d+)\D{0,4}#\s*(.+)$", clean)
    if not match:
        return None
    zone = int(match.group(1))
    name = match.group(2).strip()
    if not name:
        return None
    return {"zone": zone, "name": name, "key": player_key(zone, name), "raw": line}


def parse_partial_identity_name(line: str) -> str:
    clean = compact_text(line)
    if "#" in clean:
        name = clean.split("#", 1)[1].strip()
        return name
    return clean


def parse_number(line: str) -> int | None:
    digits = "".join(re.findall(r"\d+", normalize_text(line)))
    if not digits:
        return None
    return int(digits)


def coerce_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    if isinstance(value, int):
        return value
    return parse_number(str(value))


def parse_rank(line: str) -> int | None:
    text = normalize_text(line).replace("№", "No")
    compact = compact_text(text).replace("№", "No").replace("0,N0", "No").replace("N0", "No")
    match = re.search(r"[Nn][Oo0O]\s*[\.,]?\s*(\d+)", text)
    if match:
        return int(match.group(1))
    match = re.search(r"[Nn][Oo0O]\s*[\.,]?\s*(\d+)", compact)
    if match:
        return int(match.group(1))
    digits = re.findall(r"\d+", line)
    return int(digits[-1]) if digits else None


def parse_datetime_line(line: str) -> str | None:
    text = normalize_text(line).replace("&", "8")
    groups = re.findall(r"\d+", text)
    if len(groups) < 6:
        return None
    y, mo, d, h, mi, s = groups[:6]
    try:
        dt = datetime(int(y), int(mo), int(d), int(h), int(mi), int(s))
        return dt.isoformat(timespec="seconds")
    except ValueError:
        return None


def parse_folder_time(folder: Path) -> str:
    match = re.search(r"(\d{4})年(\d{1,2})月(\d{1,2})日(\d{2})(\d{2})(\d{2})", folder.name)
    if match:
        y, mo, d, h, mi, s = map(int, match.groups())
        return datetime(y, mo, d, h, mi, s).isoformat(timespec="seconds")
    candidates = [folder / MEMBER_IMAGE, folder / BOSS_IMAGE, folder / "snapshot.json", *folder.glob("*.pcapng")]
    mtimes = [path.stat().st_mtime for path in candidates if path.exists()]
    if not mtimes:
        raise FileNotFoundError(f"No snapshot files in {folder}")
    ts = max(mtimes)
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def week_id(value: str) -> str:
    dt = datetime.fromisoformat(value)
    start = dt.date() - timedelta(days=dt.weekday())
    return start.isoformat()


def boss_effective_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value) - timedelta(hours=8)


def boss_week_id(value: str) -> str:
    return week_id(boss_effective_datetime(value).isoformat(timespec="seconds"))


def boss_day_index(value: str) -> int:
    return boss_effective_datetime(value).weekday()


def relative_folder(root: Path | None, folder: Path) -> str:
    if root:
        try:
            return folder.resolve().relative_to(root.resolve()).as_posix()
        except ValueError:
            pass
    return folder.name


def folder_kind(folder: Path) -> str:
    parent = folder.parent.name
    if parent == MEMBER_RECORDS_DIR:
        return "members"
    if parent == BOSS_RECORDS_DIR:
        return "boss"
    if (folder / MEMBER_IMAGE).exists() and not (folder / BOSS_IMAGE).exists():
        return "members"
    if (folder / BOSS_IMAGE).exists() and not (folder / MEMBER_IMAGE).exists():
        return "boss"
    return "combined"


def snapshot_sort_key(folder: Path) -> tuple[str, int, str]:
    priority = {"members": 0, "combined": 1, "boss": 2}.get(folder_kind(folder), 1)
    return (parse_folder_time(folder), priority, folder.as_posix())


async def ocr_lines(image_path: Path, crop_box: tuple[int, int, int, int] | None = None) -> list[str]:
    source = image_path
    temp_path: Path | None = None
    if crop_box:
        image = Image.open(image_path)
        crop = image.crop(crop_box)
        temp_path = image_path.parent / f".ocr_{image_path.stem}_{crop_box[0]}_{crop_box[2]}_{os.getpid()}_{time.time_ns()}.png"
        crop.save(temp_path)
        source = temp_path

    try:
        storage_file = await StorageFile.get_file_from_path_async(str(source))
        stream = await storage_file.open_async(FileAccessMode.READ)
        decoder = await BitmapDecoder.create_async(stream)
        bitmap = await decoder.get_software_bitmap_async()
        engine = OcrEngine.try_create_from_language(Language("zh-Hans-CN"))
        result = await engine.recognize_async(bitmap)
        return [line.text for line in result.lines if normalize_text(line.text)]
    finally:
        if temp_path and temp_path.exists():
            try:
                temp_path.unlink()
            except PermissionError:
                pass


def looks_like_identity(line: str) -> bool:
    text = compact_text(line)
    if "#" not in text:
        return False
    if "伤害" in line or "职务" in line or "战斗力" in line:
        return False
    return True


def parse_members(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    for line in lines:
        normalized = normalize_text(line)
        if looks_like_identity(normalized):
            identity = parse_identity(normalized)
            if not identity:
                continue
            current = {
                "zone": identity["zone"],
                "name": identity["name"],
                "key": identity["key"],
                "role": "",
                "power": None,
                "last_online": None,
                "in_group": False,
                "raw": {"identity": identity["raw"], "role": "", "power": "", "last_online": ""},
                "needs_review": [],
            }
            rows.append(current)
            continue

        if current is None:
            continue

        if "职" in normalized:
            current["role"] = compact_text(normalized.replace("职务", "").replace(":", ""))
            current["raw"]["role"] = line
        elif "战" in normalized or "力" in normalized:
            value = parse_number(normalized)
            current["power"] = value
            current["raw"]["power"] = line
            if value is None or value < 1_000_000:
                current["needs_review"].append("power")
        else:
            parsed_dt = parse_datetime_line(normalized)
            if parsed_dt:
                current["last_online"] = parsed_dt
                current["raw"]["last_online"] = line
            elif re.search(r"\d{4}", normalized):
                current["raw"]["last_online"] = line
                current["needs_review"].append("last_online")

    for row in rows:
        if row["power"] is None:
            row["needs_review"].append("power")
        if re.search(r"[()&]|区冫", row["name"]):
            row["needs_review"].append("name")
    for index, row in enumerate(rows, start=1):
        row["row_id"] = f"m{index:03d}"
        row["ocr"] = {
            "zone": row.get("zone"),
            "name": row.get("name"),
            "key": row.get("key"),
            "power": row.get("power"),
            "last_online": row.get("last_online"),
        }
    return rows


def parse_boss(lines: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    pending_identity_line = ""

    def new_row(identity: dict[str, Any] | None = None) -> dict[str, Any]:
        if identity:
            row = {
                "zone": identity["zone"],
                "name": identity["name"],
                "key": identity["key"],
                "rank": None,
                "damage_k": None,
                "damage": None,
                "raw": {"identity": identity["raw"], "rank": "", "damage": ""},
                "needs_review": [],
            }
        else:
            row = {
                "zone": None,
                "name": "",
                "key": "",
                "rank": None,
                "damage_k": None,
                "damage": None,
                "raw": {"identity": "", "rank": "", "damage": ""},
                "needs_review": ["name"],
                "missing_identity": True,
            }
        rows.append(row)
        return row

    def backfill_missing_ranks() -> None:
        used = {int(row["rank"]) for row in rows if row.get("rank") is not None}
        ordered = sorted(rows, key=lambda item: item.get("damage_k") if item.get("damage_k") is not None else -1, reverse=True)
        next_rank = 1
        for row in ordered:
            if row.get("rank") is not None or row.get("damage_k") is None:
                continue
            while next_rank in used:
                next_rank += 1
            row["rank"] = next_rank
            row.setdefault("raw", {})["rank"] = "auto-filled by damage order"
            row["rank_inferred"] = True
            if "rank" not in row.setdefault("needs_review", []):
                row["needs_review"].append("rank")
            used.add(next_rank)

    for line in lines:
        normalized = normalize_text(line)
        if looks_like_identity(normalized):
            identity = parse_identity(normalized)
            current = new_row(identity) if identity else new_row()
            current["raw"]["identity"] = line
            if not identity:
                partial_name = parse_partial_identity_name(normalized)
                if partial_name:
                    current["name"] = partial_name
                    current["key"] = partial_name
            continue

        if parse_rank(normalized) is not None and re.search(r"[Nn][Oo0O]|№", normalized.replace(" ", "")):
            pending_identity_line = ""
            if current is None:
                current = new_row()
            if current.get("rank") is not None and current.get("damage_k") is not None:
                current = new_row()
            current["rank"] = parse_rank(normalized)
            current["raw"]["rank"] = line
            if current["rank"] is None:
                current["needs_review"].append("rank")
        elif "伤" in normalized or "害" in normalized:
            if current is not None and current.get("damage_k") is not None and current.get("rank") is None:
                current = new_row()
                current["raw"]["identity"] = pending_identity_line
                partial_name = parse_partial_identity_name(pending_identity_line)
                if partial_name:
                    current["name"] = partial_name
                    current["key"] = partial_name
            if current is None:
                current = new_row()
                current["raw"]["identity"] = pending_identity_line
                partial_name = parse_partial_identity_name(pending_identity_line)
                if partial_name:
                    current["name"] = partial_name
                    current["key"] = partial_name
            value = parse_number(normalized)
            current["damage_k"] = value
            current["damage"] = value * 1000 if value is not None else None
            current["raw"]["damage"] = line
            if value is None:
                current["needs_review"].append("damage")
            pending_identity_line = ""
        elif (current is None or (current.get("damage_k") is not None and current.get("rank") is None)) and re.search(r"[#A-Za-z\u4e00-\u9fff]", normalized):
            pending_identity_line = line

    backfill_missing_ranks()

    for row in rows:
        if row["rank"] is None:
            row["needs_review"].append("rank")
        if row["damage_k"] is None:
            row["needs_review"].append("damage")
        if re.search(r"[()&]|区冫", row["name"]):
            row["needs_review"].append("name")

    rows = sorted(rows, key=lambda item: item["rank"] or 9999)
    for index, row in enumerate(rows, start=1):
        row["row_id"] = f"b{index:03d}"
        if not row.get("key"):
            row["key"] = f"未知Boss#{row.get('rank') or index}"
        row["ocr"] = {
            "zone": row.get("zone"),
            "name": row.get("name"),
            "key": row.get("key"),
            "rank": row.get("rank"),
            "damage_k": row.get("damage_k"),
        }
    return rows


async def parse_snapshot(folder: Path, root: Path | None = None) -> dict[str, Any]:
    packet_files = list(folder.glob("*.pcapng"))
    if not (folder / MEMBER_IMAGE).exists() and not (folder / BOSS_IMAGE).exists() and ((folder / "snapshot.json").exists() or packet_files):
        snapshot = load_snapshot(folder) or {
            "id": folder.name,
            "folder": relative_folder(root, folder),
            "captured_at": parse_folder_time(folder),
            "week_id": week_id(parse_folder_time(folder)),
            "images": {"members": None, "boss": None},
            "members": [],
            "boss": [],
            "raw_ocr": {"members": [], "boss": []},
        }
        packet_import = snapshot.get("packet_import") or {}
        packet_path = packet_import.get("source_path") or packet_import.get("source_file")
        candidates = []
        if packet_path:
            candidates.append((root / packet_path) if root else (folder / packet_path))
            candidates.append(folder / Path(packet_path).name)
        candidates.extend(packet_files)
        source = next((path for path in candidates if path and path.exists()), None)
        if source:
            try:
                from tools.packet_importer import extract_packets
            except ModuleNotFoundError:
                import sys

                sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
                from tools.packet_importer import extract_packets

            extracted = extract_packets(source)
            kind = snapshot_record_kind(snapshot)
            if kind == "members" and extracted.get("members"):
                snapshot["members"] = extracted["members"]
                snapshot["boss"] = []
                (folder / "members.json").write_text(json.dumps(snapshot["members"], ensure_ascii=False, indent=2), encoding="utf-8")
            if kind == "boss" and extracted.get("boss"):
                snapshot["boss"] = extracted["boss"]
                snapshot["members"] = []
                captured_at = snapshot.get("captured_at") or parse_folder_time(folder)
                snapshot["boss_captured_at"] = boss_effective_datetime(captured_at).isoformat(timespec="seconds")
                snapshot["boss_week_id"] = boss_week_id(captured_at)
                snapshot["boss_day_index"] = boss_day_index(captured_at)
                (folder / "boss.json").write_text(json.dumps(snapshot["boss"], ensure_ascii=False, indent=2), encoding="utf-8")
            snapshot["packet_import"] = {
                "source_file": source.name,
                "source_path": f"{relative_folder(root, folder)}/{source.name}",
            }
            (folder / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
        return snapshot

    member_image = folder / MEMBER_IMAGE
    boss_image = folder / BOSS_IMAGE

    member_lines: list[str] = []
    boss_lines: list[str] = []
    if member_image.exists():
        member_size = Image.open(member_image).size
        member_lines = await ocr_lines(member_image, (112, 0, min(455, member_size[0]), member_size[1]))
    if boss_image.exists():
        boss_size = Image.open(boss_image).size
        boss_lines = await ocr_lines(boss_image, (120, 0, min(520, boss_size[0]), boss_size[1]))

    captured_at = parse_folder_time(folder)
    effective_boss_at = boss_effective_datetime(captured_at).isoformat(timespec="seconds") if boss_image.exists() else None
    snapshot = {
        "id": folder.name,
        "folder": relative_folder(root, folder),
        "captured_at": captured_at,
        "week_id": week_id(captured_at),
        "boss_captured_at": effective_boss_at,
        "boss_week_id": boss_week_id(captured_at) if boss_image.exists() else week_id(captured_at),
        "boss_day_index": boss_day_index(captured_at) if boss_image.exists() else None,
        "images": {"members": MEMBER_IMAGE if member_image.exists() else None, "boss": BOSS_IMAGE if boss_image.exists() else None},
        "members": parse_members(member_lines),
        "boss": parse_boss(boss_lines),
        "raw_ocr": {"members": member_lines, "boss": boss_lines},
    }

    if member_image.exists():
        (folder / "members.json").write_text(json.dumps(snapshot["members"], ensure_ascii=False, indent=2), encoding="utf-8")
    elif (folder / "members.json").exists():
        (folder / "members.json").unlink()
    if boss_image.exists():
        (folder / "boss.json").write_text(json.dumps(snapshot["boss"], ensure_ascii=False, indent=2), encoding="utf-8")
    elif (folder / "boss.json").exists():
        (folder / "boss.json").unlink()
    (folder / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def snapshot_folders(root: Path) -> list[Path]:
    def has_snapshot_source(path: Path) -> bool:
        return (path / MEMBER_IMAGE).exists() or (path / BOSS_IMAGE).exists() or (path / "snapshot.json").exists() or any(path.glob("*.pcapng"))

    folders = [
        path
        for path in root.iterdir()
        if path.is_dir() and has_snapshot_source(path)
    ]
    records_root = root / RECORDS_DIR
    for bucket in (MEMBER_RECORDS_DIR, BOSS_RECORDS_DIR):
        bucket_root = records_root / bucket
        if bucket_root.exists():
            folders.extend(
                path
                for path in bucket_root.iterdir()
                if path.is_dir() and has_snapshot_source(path)
            )
    return sorted(folders, key=snapshot_sort_key)


def load_snapshot(folder: Path) -> dict[str, Any] | None:
    path = folder / "snapshot.json"
    if not path.exists():
        return None
    snapshot = json.loads(path.read_text(encoding="utf-8"))
    if "/" not in str(snapshot.get("folder", "")) and folder.parent.name in (MEMBER_RECORDS_DIR, BOSS_RECORDS_DIR):
        snapshot["folder"] = relative_folder(folder.parents[2], folder)
    return snapshot


def load_snapshot_by_id(root: Path, snapshot_id: str, kind: str | None = None) -> dict[str, Any] | None:
    exact_matches: list[Path] = []
    prefix_matches: list[Path] = []
    for folder in snapshot_folders(root):
        if kind and folder_kind(folder) not in (kind, "combined"):
            continue
        if folder.name == snapshot_id:
            exact_matches.append(folder)
        elif folder.name.startswith(f"{snapshot_id}_"):
            prefix_matches.append(folder)

    for folder in exact_matches + prefix_matches:
        snapshot = load_snapshot(folder)
        if snapshot:
            return snapshot
    return None


def snapshot_archive_week(snapshot: dict[str, Any]) -> str:
    if snapshot_record_kind(snapshot) == "boss":
        return snapshot.get("boss_week_id") or snapshot.get("week_id", "")
    return snapshot.get("week_id", "")


def folder_archive_week(folder: Path) -> str:
    snapshot = load_snapshot(folder)
    if snapshot:
        return snapshot_archive_week(snapshot)
    captured_at = parse_folder_time(folder)
    return boss_week_id(captured_at) if folder_kind(folder) == "boss" else week_id(captured_at)


def correction_bucket_for_snapshot(corrections: dict[str, Any], snapshot: dict[str, Any]) -> dict[str, Any]:
    snapshots = corrections.get("snapshots", {})
    exact = copy.deepcopy(snapshots.get(snapshot.get("id"), {}))
    base_id = str(snapshot.get("id", "")).split("_", 1)[0]
    if base_id and base_id != snapshot.get("id") and base_id in snapshots:
        merged = copy.deepcopy(snapshots[base_id])
        for kind, values in exact.items():
            merged.setdefault(kind, {}).update(values)
        return merged
    return exact


def snapshot_record_kind(snapshot: dict[str, Any]) -> str:
    folder = str(snapshot.get("folder", "")).replace("\\", "/")
    if f"/{MEMBER_RECORDS_DIR}/" in f"/{folder}/" or snapshot.get("id", "").endswith("_联盟"):
        return "members"
    if f"/{BOSS_RECORDS_DIR}/" in f"/{folder}/" or snapshot.get("id", "").endswith("_Boss"):
        return "boss"
    has_members = bool(snapshot.get("images", {}).get("members"))
    has_boss = bool(snapshot.get("images", {}).get("boss"))
    if has_members and not has_boss:
        return "members"
    if has_boss and not has_members:
        return "boss"
    return "combined"


def write_state_files(root: Path, state: dict[str, Any]) -> None:
    data_dir = root / DATA_DIR
    data_dir.mkdir(exist_ok=True)
    (data_dir / "state.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    (data_dir / "state.js").write_text(
        "window.LOV_INITIAL_STATE = " + json.dumps(state, ensure_ascii=False, indent=2) + ";\n",
        encoding="utf-8",
    )


def load_corrections(root: Path) -> dict[str, Any]:
    path = root / DATA_DIR / CORRECTIONS_FILE
    if not path.exists():
        return {"snapshots": {}}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"snapshots": {}}


def save_corrections(root: Path, corrections: dict[str, Any]) -> None:
    data_dir = root / DATA_DIR
    data_dir.mkdir(exist_ok=True)
    (data_dir / CORRECTIONS_FILE).write_text(json.dumps(corrections, ensure_ascii=False, indent=2), encoding="utf-8")


def same_optional_text(left: Any, right: Any) -> bool:
    return (str(left or "").strip() or None) == (str(right or "").strip() or None)


def apply_member_values(row: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    old_zone = row.get("zone")
    old_name = row.get("name") or ""
    old_power = row.get("power")
    old_last_online = row.get("last_online")
    old_in_group = row.get("in_group", False)
    group_only = values.get("group_only") in (True, "true", "on", "1", 1)

    zone = old_zone if group_only else coerce_int(values.get("zone", old_zone))
    name = old_name if group_only else str(values.get("name", old_name) or "").strip()
    power = old_power if group_only else coerce_int(values.get("power", old_power))
    last_online = old_last_online if group_only else str(values.get("last_online", old_last_online or "")).strip() or None
    new_in_group = values.get("in_group", old_in_group) in (True, "true", "on", "1", 1)
    note = str(values.get("note", "") or "")

    data_changed = False if group_only else zone != old_zone or name != old_name or power != old_power or not same_optional_text(last_online, old_last_online)
    note_changed = False if group_only else bool(note) and note != str(row.get("raw", {}).get("correction_note", "") or "")

    row["zone"] = zone
    row["name"] = name
    row["key"] = player_key(zone, name)
    row["power"] = power
    row["last_online"] = last_online
    row["in_group"] = new_in_group
    if group_only and not row.get("manual"):
        row.pop("corrected", None)
    elif data_changed or note_changed or row.get("manual"):
        row["corrected"] = True
        row["needs_review"] = [] if values.get("reviewed", True) else row.get("needs_review", [])
    row.setdefault("raw", {})["correction_note"] = note
    return row


def apply_boss_values(row: dict[str, Any], values: dict[str, Any]) -> dict[str, Any]:
    zone = coerce_int(values.get("zone", row.get("zone")))
    name = str(values.get("name", row.get("name") or "")).strip()
    rank = coerce_int(values.get("rank", row.get("rank")))
    damage_k = coerce_int(values.get("damage_k", row.get("damage_k")))

    row["zone"] = zone
    row["name"] = name
    row["key"] = player_key(zone, name)
    row["rank"] = rank
    row["damage_k"] = damage_k
    row["damage"] = damage_k * 1000 if damage_k is not None else None
    row["corrected"] = True
    row["needs_review"] = [] if values.get("reviewed", True) else row.get("needs_review", [])
    row.setdefault("raw", {})["correction_note"] = values.get("note", "")
    return row


def manual_member(row_id: str, values: dict[str, Any]) -> dict[str, Any]:
    row = {
        "row_id": row_id,
        "zone": None,
        "name": "",
        "key": "",
        "role": "手动补录",
        "power": None,
        "last_online": None,
        "raw": {"identity": "manual", "role": "手动补录", "power": "", "last_online": ""},
        "needs_review": [],
        "ocr": {},
        "manual": True,
    }
    return apply_member_values(row, values)


def manual_boss(row_id: str, values: dict[str, Any]) -> dict[str, Any]:
    row = {
        "row_id": row_id,
        "zone": None,
        "name": "",
        "key": "",
        "rank": None,
        "damage_k": None,
        "damage": None,
        "raw": {"identity": "manual", "rank": "", "damage": ""},
        "needs_review": [],
        "ocr": {},
        "manual": True,
    }
    return apply_boss_values(row, values)


def make_identity_aliases() -> dict[str, dict[str, Any]]:
    return {
        "members": {"exact": {}, "by_zone": {}},
        "boss": {"exact": {}, "by_zone": {}},
    }


def add_exact_identity_alias(group: dict[str, Any], alias_name: str | None, candidate: dict[str, Any]) -> None:
    alias_key = identity_alias_key(candidate.get("zone"), alias_name)
    if not alias_key:
        return
    matches = group["exact"].setdefault(alias_key, [])
    if not any(item["key"] == candidate["key"] for item in matches):
        matches.append(candidate)


def register_identity_candidate(group: dict[str, Any], original: dict[str, Any] | None, values: dict[str, Any], kind: str) -> None:
    zone = coerce_int(values.get("zone", original.get("zone") if original else None))
    name = str(values.get("name", original.get("name") if original else "") or "").strip()
    if zone is None or not name:
        return

    key = player_key(zone, name)
    by_key = group["by_zone"].setdefault(zone, {})
    candidate = by_key.setdefault(
        key,
        {
            "zone": zone,
            "name": name,
            "key": key,
            "aliases": set(),
            "power": None,
            "rank": None,
            "damage_k": None,
        },
    )
    candidate["name"] = name
    if kind == "members":
        candidate["power"] = coerce_int(values.get("power", original.get("power") if original else None))
    else:
        candidate["rank"] = coerce_int(values.get("rank", original.get("rank") if original else None))
        candidate["damage_k"] = coerce_int(values.get("damage_k", original.get("damage_k") if original else None))

    aliases = [name]
    if original:
        aliases.extend([original.get("name"), original.get("ocr", {}).get("name")])
    for alias in aliases:
        for normalized in {normalized_identity_name(alias), ocr_loose_identity_name(alias)}:
            if normalized:
                candidate["aliases"].add(normalized)
        add_exact_identity_alias(group, alias, candidate)


def build_identity_aliases(root: Path, corrections: dict[str, Any]) -> dict[str, dict[str, Any]]:
    aliases = make_identity_aliases()
    for kind, entries in corrections.get("aliases", {}).items():
        if kind not in aliases:
            continue
        for entry in entries:
            original = {
                "zone": entry.get("source_zone"),
                "name": entry.get("source_name"),
                "power": entry.get("source_power"),
                "rank": entry.get("source_rank"),
                "damage_k": entry.get("source_damage_k"),
                "ocr": {"name": entry.get("source_name")},
            }
            values = {
                "zone": entry.get("zone"),
                "name": entry.get("name"),
                "power": entry.get("power"),
                "rank": entry.get("rank"),
                "damage_k": entry.get("damage_k"),
            }
            register_identity_candidate(aliases[kind], original, values, kind)

    for snapshot_id, snapshot_corrections in corrections.get("snapshots", {}).items():
        for kind in ("members", "boss"):
            original_snapshot = load_snapshot_by_id(root, snapshot_id, kind) or {}
            rows_by_id = {row.get("row_id"): row for row in original_snapshot.get(kind, [])}
            for row_id, values in snapshot_corrections.get(kind, {}).items():
                register_identity_candidate(aliases[kind], rows_by_id.get(row_id), values or {}, kind)
    return aliases


def remember_identity_alias(corrections: dict[str, Any], snapshot: dict[str, Any], kind: str, row_id: str, values: dict[str, Any]) -> None:
    row = next((item for item in snapshot.get(kind, []) if item.get("row_id") == row_id), None)
    if row is None:
        return

    zone = coerce_int(values.get("zone", row.get("zone")))
    name = str(values.get("name", row.get("name") or "")).strip()
    if zone is None or not name:
        return

    source_zone = coerce_int(row.get("zone"))
    source_name = str(row.get("name") or "").strip()
    if source_zone is None or not source_name:
        return

    entry = {
        "source_zone": source_zone,
        "source_name": source_name,
        "zone": zone,
        "name": name,
    }
    if kind == "members":
        entry["source_power"] = coerce_int(row.get("power"))
        entry["power"] = coerce_int(values.get("power", row.get("power")))
    else:
        entry["source_rank"] = coerce_int(row.get("rank"))
        entry["source_damage_k"] = coerce_int(row.get("damage_k"))
        entry["rank"] = coerce_int(values.get("rank", row.get("rank")))
        entry["damage_k"] = coerce_int(values.get("damage_k", row.get("damage_k")))

    entries = corrections.setdefault("aliases", {}).setdefault(kind, [])
    identity = (entry["source_zone"], normalized_identity_name(entry["source_name"]), entry["zone"], normalized_identity_name(entry["name"]), entry.get("rank"))
    for index, existing in enumerate(entries):
        existing_identity = (
            existing.get("source_zone"),
            normalized_identity_name(existing.get("source_name")),
            existing.get("zone"),
            normalized_identity_name(existing.get("name")),
            existing.get("rank"),
        )
        if existing_identity == identity:
            entries[index] = entry
            return
    entries.append(entry)


def numeric_close(left: int | None, right: int | None, minimum: int, ratio: float) -> bool:
    if left is None or right is None:
        return False
    return abs(left - right) <= max(minimum, int(max(left, right) * ratio))


def closest_numeric_candidate(candidates: list[dict[str, Any]], value: int | None, field: str, minimum: int, ratio: float) -> dict[str, Any] | None:
    if value is None:
        return None
    scored = [(abs(int(candidate[field]) - value), candidate) for candidate in candidates if candidate.get(field) is not None]
    if not scored:
        return None
    scored.sort(key=lambda item: item[0])
    best_delta, best = scored[0]
    if len(scored) > 1 and best_delta == scored[1][0]:
        return None
    return best if numeric_close(value, int(best[field]), minimum, ratio) else None


def identity_similarity(name: str, candidate: dict[str, Any]) -> float:
    normalized_names = {normalized_identity_name(name), ocr_loose_identity_name(name)}
    normalized_names = {item for item in normalized_names if item}
    if not normalized_names:
        return 0.0
    choices = set(candidate.get("aliases", set()))
    choices.add(normalized_identity_name(candidate.get("name")))
    choices.add(ocr_loose_identity_name(candidate.get("name")))
    return max((SequenceMatcher(None, normalized, choice).ratio() for normalized in normalized_names for choice in choices if choice), default=0.0)


def pick_identity_candidate(row: dict[str, Any], group: dict[str, Any], kind: str) -> dict[str, Any] | None:
    zone = row.get("zone")
    if zone is None:
        return None

    exact = group["exact"].get(identity_alias_key(zone, row.get("name")) or ())
    if exact:
        if len(exact) == 1:
            return exact[0]
        if kind == "members":
            return closest_numeric_candidate(exact, row.get("power"), "power", 8_000_000, 0.08)
        rank_match = closest_numeric_candidate(exact, row.get("rank"), "rank", 2, 0.0)
        if rank_match:
            return rank_match
        return closest_numeric_candidate(exact, row.get("damage_k"), "damage_k", 500_000, 0.35)

    candidates = list(group["by_zone"].get(zone, {}).values())
    if not candidates:
        return None
    scored = sorted(((identity_similarity(row.get("name") or "", candidate), candidate) for candidate in candidates), key=lambda item: item[0], reverse=True)
    best_score, best = scored[0]
    second_score = scored[1][0] if len(scored) > 1 else 0.0
    margin = best_score - second_score

    if kind == "members":
        row_power = row.get("power")
        candidate_power = best.get("power")
        if numeric_close(row_power, candidate_power, 2_000_000, 0.03) and (best_score >= 0.25 or len(candidates) == 1):
            return best
        if numeric_close(row_power, candidate_power, 8_000_000, 0.08) and best_score >= 0.55 and margin >= 0.08:
            return best
        if best_score >= 0.9 and margin >= 0.1:
            return best
        return None

    rank_close = row.get("rank") is not None and best.get("rank") is not None and abs(int(row["rank"]) - int(best["rank"])) <= 2
    if rank_close and best_score >= 0.65 and margin >= 0.08:
        return best
    return best if best_score >= 0.92 and margin >= 0.12 else None


def apply_identity_alias(row: dict[str, Any], group: dict[str, Any], kind: str) -> None:
    candidate = pick_identity_candidate(row, group, kind)
    if not candidate or candidate["key"] == row.get("key"):
        return

    original_key = row.get("key")
    row["zone"] = candidate["zone"]
    row["name"] = candidate["name"]
    row["key"] = candidate["key"]
    row["identity_matched"] = True
    row.setdefault("raw", {})["identity_matched_from"] = original_key
    row["needs_review"] = [item for item in row.get("needs_review", []) if item != "name"]


def apply_learned_member_power(row: dict[str, Any], group: dict[str, Any]) -> None:
    zone = row.get("zone")
    key = row.get("key")
    if zone is None or not key:
        return
    candidate = group["by_zone"].get(zone, {}).get(key)
    learned_power = candidate.get("power") if candidate else None
    current_power = row.get("power")
    if learned_power is None:
        return
    if current_power is None or current_power < learned_power * 0.5:
        row.setdefault("raw", {})["power_matched_from"] = current_power
        row["power"] = learned_power
        row["power_matched"] = True
        row["needs_review"] = [item for item in row.get("needs_review", []) if item != "power"]


def apply_identity_aliases_to_snapshot(snapshot: dict[str, Any], aliases: dict[str, dict[str, Any]]) -> None:
    for member in snapshot.get("members", []):
        apply_identity_alias(member, aliases["members"], "members")
        apply_learned_member_power(member, aliases["members"])
    for boss in snapshot.get("boss", []):
        apply_identity_alias(boss, aliases["boss"], "boss")


def latest_snapshots_by_day(snapshots: list[dict[str, Any]]) -> list[dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for snapshot in sorted(snapshots, key=lambda item: item["captured_at"]):
        member_key = snapshot["captured_at"].split("T", 1)[0]
        if snapshot_record_kind(snapshot) == "boss":
            member_key = f"boss:{(snapshot.get('boss_captured_at') or snapshot['captured_at']).split('T', 1)[0]}"
        latest[member_key] = snapshot
    return sorted(latest.values(), key=lambda item: item["captured_at"])


def repair_missing_boss_identities(snapshots: list[dict[str, Any]]) -> None:
    previous_by_week_rank: dict[str, dict[int, dict[str, Any]]] = {}
    previous_by_week: dict[str, list[dict[str, Any]]] = {}

    def replace_boss_identity(boss: dict[str, Any], previous: dict[str, Any], used_keys: set[str]) -> None:
        original_key = boss.get("key")
        boss["zone"] = previous.get("zone")
        boss["name"] = previous.get("name")
        boss["key"] = previous.get("key")
        boss["identity_matched"] = True
        boss["missing_identity"] = False
        boss.setdefault("raw", {})["identity_matched_from"] = original_key
        boss["needs_review"] = [item for item in boss.get("needs_review", []) if item != "name"]
        used_keys.add(boss["key"])

    def match_previous_by_name(boss: dict[str, Any], previous_rows: list[dict[str, Any]], used_keys: set[str]) -> dict[str, Any] | None:
        name = boss.get("name") or boss.get("key") or ""
        if not name:
            return None
        candidates = [row for row in previous_rows if row.get("key") not in used_keys]
        if not candidates:
            return None
        scored = sorted(((identity_similarity(name, candidate), candidate) for candidate in candidates), key=lambda item: item[0], reverse=True)
        best_score, best = scored[0]
        second_score = scored[1][0] if len(scored) > 1 else 0.0
        rank_close = boss.get("rank") is not None and best.get("rank") is not None and abs(int(boss["rank"]) - int(best["rank"])) <= 4
        damage_ok = boss.get("damage_k") is None or best.get("damage_k") is None or int(boss["damage_k"]) >= int(best["damage_k"])
        if damage_ok and ((rank_close and best_score >= 0.6) or best_score >= 0.68) and (best_score - second_score >= 0.08 or rank_close):
            return best
        return None

    for snapshot in sorted(snapshots, key=lambda item: item["captured_at"]):
        week = snapshot.get("boss_week_id") or snapshot["week_id"]
        previous_rank_map = previous_by_week_rank.get(week, {})
        previous_rows = previous_by_week.get(week, [])
        used_keys = {boss.get("key") for boss in snapshot.get("boss", []) if boss.get("key") and not boss.get("missing_identity")}

        for boss in snapshot.get("boss", []):
            previous = match_previous_by_name(boss, previous_rows, used_keys)
            if previous and previous.get("key") != boss.get("key"):
                replace_boss_identity(boss, previous, used_keys)
                continue

            if not boss.get("missing_identity") and boss.get("name"):
                continue
            rank = boss.get("rank")
            if rank is None:
                continue
            previous = previous_rank_map.get(int(rank))
            if not previous or previous.get("key") in used_keys:
                continue

            current_damage = boss.get("damage_k")
            previous_damage = previous.get("damage_k")
            if current_damage is not None and previous_damage is not None and current_damage < previous_damage:
                continue

            boss["missing_identity_repaired"] = True
            replace_boss_identity(boss, previous, used_keys)

        previous_by_week_rank[week] = {
            int(boss["rank"]): boss
            for boss in snapshot.get("boss", [])
            if boss.get("rank") is not None and boss.get("key") and not boss.get("missing_identity")
        }
        previous_by_week[week] = [
            boss
            for boss in snapshot.get("boss", [])
            if boss.get("key") and not boss.get("missing_identity")
        ]


def carry_forward_missing_data(snapshots: list[dict[str, Any]]) -> None:
    latest_members: list[dict[str, Any]] | None = None
    latest_boss: list[dict[str, Any]] | None = None
    latest_member_image: str | None = None
    latest_boss_image: str | None = None
    latest_member_source: str | None = None
    latest_boss_source: str | None = None

    def image_reference(snapshot: dict[str, Any], kind: str) -> str | None:
        file = snapshot.get("images", {}).get(kind)
        if not file:
            return None
        if "/" in str(file):
            return str(file)
        return f"{snapshot.get('folder', '')}/{file}".strip("/")

    for snapshot in sorted(snapshots, key=lambda item: (item["captured_at"], item.get("folder", ""))):
        if snapshot.get("members"):
            latest_members = copy.deepcopy(snapshot["members"])
            latest_member_image = image_reference(snapshot, "members")
            latest_member_source = snapshot["id"]
            snapshot["member_source_id"] = latest_member_source
        elif latest_members is not None:
            snapshot["members"] = copy.deepcopy(latest_members)
            snapshot.setdefault("images", {})["members"] = latest_member_image
            snapshot["member_source_id"] = latest_member_source
            snapshot["members_carried_forward"] = True

        if snapshot.get("boss"):
            latest_boss = copy.deepcopy(snapshot["boss"])
            latest_boss_image = image_reference(snapshot, "boss")
            latest_boss_source = snapshot["id"]
            snapshot["boss_source_id"] = latest_boss_source
        elif latest_boss is not None:
            snapshot["boss"] = copy.deepcopy(latest_boss)
            snapshot.setdefault("images", {})["boss"] = latest_boss_image
            snapshot["boss_source_id"] = latest_boss_source
            snapshot["boss_carried_forward"] = True


def archive_root(root: Path) -> Path:
    return root / DATA_DIR / ARCHIVE_DIR / BOSS_RECORDS_DIR


def load_boss_archives(root: Path) -> dict[str, dict[str, Any]]:
    base = archive_root(root)
    if not base.exists():
        return {}
    archives: dict[str, dict[str, Any]] = {}
    for path in base.glob("*.json"):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        week = payload.get("week_id") or path.stem
        archives[week] = payload
    return archives


def archive_closed_boss_weeks(root: Path, snapshots: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    archives = load_boss_archives(root)
    weeks = sorted({snapshot.get("boss_week_id") or snapshot["week_id"] for snapshot in snapshots})
    if len(weeks) <= 1:
        return archives

    current_week = weeks[-1]
    base = archive_root(root)
    base.mkdir(parents=True, exist_ok=True)

    for week in weeks:
        if week == current_week or week in archives:
            continue
        week_snapshots = [snapshot for snapshot in snapshots if (snapshot.get("boss_week_id") or snapshot["week_id"]) == week and snapshot.get("boss") and not snapshot.get("boss_carried_forward")]
        if not week_snapshots:
            continue
        final_snapshot = sorted(week_snapshots, key=lambda item: item["captured_at"])[-1]
        payload = {
            "week_id": week,
            "archived_at": datetime.now().isoformat(timespec="seconds"),
            "source_snapshot_id": final_snapshot["id"],
            "source_captured_at": final_snapshot["captured_at"],
            "locked": True,
            "boss": copy.deepcopy(final_snapshot.get("boss", [])),
            "total_damage_k": sum(item.get("damage_k") or 0 for item in final_snapshot.get("boss", [])),
        }
        (base / f"{week}.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        archives[week] = payload
    return archives


def apply_archived_boss_data(snapshots: list[dict[str, Any]], archives: dict[str, dict[str, Any]]) -> None:
    for week, archive in archives.items():
        week_snapshots = [snapshot for snapshot in snapshots if (snapshot.get("boss_week_id") or snapshot["week_id"]) == week]
        if not week_snapshots:
            continue
        target = next((snapshot for snapshot in week_snapshots if snapshot["id"] == archive.get("source_snapshot_id")), None)
        if target is None:
            target = sorted(week_snapshots, key=lambda item: item["captured_at"])[-1]
        target["boss"] = copy.deepcopy(archive.get("boss", []))
        target["boss_archived"] = True
        target["boss_archive_source"] = archive.get("source_snapshot_id")


def build_archive_stats(archives: dict[str, dict[str, Any]]) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    unique_members: set[str] = set()
    total_damage_k = 0
    for archive in sorted(archives.values(), key=lambda item: item.get("week_id", ""), reverse=True):
        boss_rows = archive.get("boss", [])
        members = [item for item in boss_rows if item.get("key")]
        for item in members:
            unique_members.add(item["key"])
        week_total = archive.get("total_damage_k")
        if week_total is None:
            week_total = sum(item.get("damage_k") or 0 for item in boss_rows)
        total_damage_k += week_total or 0
        leader = sorted(boss_rows, key=lambda item: item.get("rank") or 9999)[0] if boss_rows else {}
        rows.append(
            {
                "week_id": archive.get("week_id"),
                "archived_at": archive.get("archived_at"),
                "source_snapshot_id": archive.get("source_snapshot_id"),
                "source_captured_at": archive.get("source_captured_at"),
                "member_count": len(members),
                "total_damage_k": week_total or 0,
                "leader_key": leader.get("key"),
                "leader_damage_k": leader.get("damage_k"),
            }
        )
    return {
        "week_count": len(rows),
        "total_damage_k": total_damage_k,
        "member_count": len(unique_members),
        "rows": rows,
    }




def dedupe_members(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = row.get("key")
        if not key:
            merged.append(row)
            continue
        current = by_key.get(key)
        if current is None:
            by_key[key] = row
            merged.append(row)
            continue
        current["in_group"] = bool(current.get("in_group")) or bool(row.get("in_group"))
        current["corrected"] = bool(current.get("corrected")) or bool(row.get("corrected"))
        current["manual"] = bool(current.get("manual")) or bool(row.get("manual"))
        current["needs_review"] = sorted(set(current.get("needs_review", [])) | set(row.get("needs_review", [])))
        if row.get("last_online") and not current.get("last_online"):
            current["last_online"] = row.get("last_online")
            current.setdefault("raw", {})["deduped_last_online_from"] = row.get("row_id")
            if row.get("raw", {}).get("last_online"):
                current.setdefault("raw", {})["last_online"] = row["raw"]["last_online"]
        if (row.get("power") or 0) > (current.get("power") or 0):
            current["power"] = row.get("power")
            current.setdefault("raw", {})["deduped_power_from"] = row.get("row_id")
        current.setdefault("raw", {})["deduped_row_ids"] = sorted(set(current.get("raw", {}).get("deduped_row_ids", [])) | {str(row.get("row_id", ""))})
    return merged


def apply_corrections_to_snapshot(snapshot: dict[str, Any], corrections: dict[str, Any], aliases: dict[str, dict[str, Any]]) -> dict[str, Any]:
    snapshot = copy.deepcopy(snapshot)
    apply_identity_aliases_to_snapshot(snapshot, aliases)
    snapshot_corrections = correction_bucket_for_snapshot(corrections, snapshot)
    record_kind = snapshot_record_kind(snapshot)
    if record_kind == "members":
        snapshot_corrections.pop("boss", None)
    elif record_kind == "boss":
        snapshot_corrections.pop("members", None)

    member_corrections = snapshot_corrections.get("members", {})
    members_by_id = {row.get("row_id"): row for row in snapshot.get("members", [])}
    for row_id, values in member_corrections.items():
        if row_id in members_by_id:
            apply_member_values(members_by_id[row_id], values)
        else:
            snapshot.setdefault("members", []).append(manual_member(row_id, values))

    boss_corrections = snapshot_corrections.get("boss", {})
    boss_by_id = {row.get("row_id"): row for row in snapshot.get("boss", [])}
    boss_by_rank = {str(row.get("rank")): row for row in snapshot.get("boss", []) if row.get("rank") is not None}
    for row_id, values in boss_corrections.items():
        if row_id in boss_by_id:
            apply_boss_values(boss_by_id[row_id], values)
        elif str(coerce_int(values.get("rank"))) in boss_by_rank:
            apply_boss_values(boss_by_rank[str(coerce_int(values.get("rank")))], values)
        else:
            snapshot.setdefault("boss", []).append(manual_boss(row_id, values))

    snapshot["members"] = dedupe_members(sorted(snapshot.get("members", []), key=lambda item: item.get("row_id", "")))
    snapshot["boss"] = sorted(snapshot.get("boss", []), key=lambda item: item.get("rank") or 9999)
    snapshot["corrections"] = snapshot_corrections
    return snapshot


def build_state(root: Path, snapshots: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if snapshots is None:
        snapshots = [snapshot for folder in snapshot_folders(root) if (snapshot := load_snapshot(folder))]
    corrections = load_corrections(root)
    aliases = build_identity_aliases(root, corrections)
    snapshots = [apply_corrections_to_snapshot(snapshot, corrections, aliases) for snapshot in snapshots]
    snapshots = sorted(snapshots, key=lambda item: (item["captured_at"], item.get("folder", "")))
    carry_forward_missing_data(snapshots)
    snapshots = latest_snapshots_by_day(snapshots)
    repair_missing_boss_identities(snapshots)
    archives = archive_closed_boss_weeks(root, snapshots)
    apply_archived_boss_data(snapshots, archives)
    archived_weeks = set(archives)
    for snapshot in snapshots:
        snapshot["boss_archived"] = (snapshot.get("boss_week_id") or snapshot["week_id"]) in archived_weeks
        snapshot["members"] = dedupe_members(snapshot.get("members", []))
        for member in snapshot.get("members", []):
            member.setdefault("in_group", False)

    players: dict[str, dict[str, Any]] = {}
    for snapshot in snapshots:
        for member in snapshot.get("members", []):
            key = member["key"]
            record = players.setdefault(
                key,
                {"key": key, "zone": member.get("zone"), "name": member.get("name"), "first_seen": snapshot["captured_at"], "last_seen": snapshot["captured_at"], "snapshots": []},
            )
            record["zone"] = member.get("zone")
            record["name"] = member.get("name")
            record["last_seen"] = snapshot["captured_at"]
            record["snapshots"].append(snapshot["id"])

    boss_history: dict[str, list[dict[str, Any]]] = {}
    previous_by_week: dict[str, dict[str, int]] = {}
    for snapshot in snapshots:
        current_week = snapshot.get("boss_week_id") or snapshot["week_id"]
        previous = previous_by_week.setdefault(current_week, {})
        for boss in snapshot.get("boss", []):
            key = boss["key"]
            damage_k = boss.get("damage_k")
            delta_k = None
            if damage_k is not None and key in previous:
                delta_k = damage_k - previous[key]
                if delta_k < 0:
                    delta_k = None
            boss_history.setdefault(key, []).append(
                {
                    "snapshot_id": snapshot["id"],
                    "captured_at": snapshot["captured_at"],
                    "week_id": current_week,
                    "rank": boss.get("rank"),
                    "damage_k": damage_k,
                    "delta_k": delta_k,
                }
            )
        previous_by_week[current_week] = {item["key"]: item.get("damage_k") or 0 for item in snapshot.get("boss", [])}

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "snapshot_count": len(snapshots),
        "snapshots": snapshots,
        "players": sorted(players.values(), key=lambda item: item["key"]),
        "boss_history": boss_history,
        "boss_archives": sorted(archives.values(), key=lambda item: item["week_id"], reverse=True),
        "archive_stats": build_archive_stats(archives),
        "archived_boss_weeks": sorted(archives.keys(), reverse=True),
    }


async def parse_all(root: Path, active_only: bool = False) -> dict[str, Any]:
    parsed = []
    archived_weeks = set(load_boss_archives(root)) if active_only else set()
    reparse_summary = {
        "active_only": active_only,
        "reparsed_count": 0,
        "loaded_archived_count": 0,
        "packet_count": 0,
        "image_count": 0,
    }
    for folder in snapshot_folders(root):
        if active_only and folder_archive_week(folder) in archived_weeks:
            snapshot = load_snapshot(folder)
            if snapshot:
                parsed.append(snapshot)
                reparse_summary["loaded_archived_count"] += 1
            continue
        snapshot = await parse_snapshot(folder, root)
        parsed.append(snapshot)
        reparse_summary["reparsed_count"] += 1
        if snapshot.get("packet_import"):
            reparse_summary["packet_count"] += 1
        else:
            reparse_summary["image_count"] += 1
    state = build_state(root, parsed)
    if active_only:
        state["reparse_summary"] = reparse_summary
    write_state_files(root, state)
    return state


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".", help="战姬数据目录")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    state = asyncio.run(parse_all(root))
    print(f"parsed {state['snapshot_count']} snapshot(s)")


if __name__ == "__main__":
    main()
