from __future__ import annotations

import asyncio
import cgi
import json
import mimetypes
import shutil
from datetime import datetime
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

from tools.lov_parser import (
    BOSS_IMAGE,
    BOSS_RECORDS_DIR,
    MEMBER_IMAGE,
    MEMBER_RECORDS_DIR,
    RECORDS_DIR,
    archive_closed_boss_weeks,
    build_state,
    load_corrections,
    load_boss_archives,
    load_snapshot,
    parse_all,
    parse_folder_time,
    parse_snapshot,
    folder_kind,
    remember_identity_alias,
    save_corrections,
    snapshot_folders,
    write_state_files,
)
from tools.packet_importer import import_pcapng


ROOT = Path(__file__).resolve().parent
HOST = "127.0.0.1"
PORT = 8765


def timestamp_folder() -> str:
    now = datetime.now()
    return f"{now.year}年{now.month}月{now.day}日{now:%H%M%S}"


def unique_timestamp_folder(root: Path) -> Path:
    base = timestamp_folder()
    folder = root / base
    suffix = 1
    while folder.exists():
        folder = root / f"{base}-{suffix}"
        suffix += 1
    return folder


def unique_record_folder(root: Path, bucket: str, suffix: str) -> Path:
    base = f"{timestamp_folder()}_{suffix}"
    parent = root / RECORDS_DIR / bucket
    parent.mkdir(parents=True, exist_ok=True)
    folder = parent / base
    index = 1
    while folder.exists():
        folder = parent / f"{base}-{index}"
        index += 1
    return folder


def folder_day(folder: Path) -> str:
    return parse_folder_time(folder).split("T", 1)[0]


def same_day_folders(root: Path, day: str, kind: str | None = None) -> list[Path]:
    folders = [folder for folder in snapshot_folders(root) if folder_day(folder) == day]
    if kind:
        folders = [folder for folder in folders if folder_kind(folder) == kind]
    return folders


def latest_file_source(root: Path, filename: str, preferred: list[Path] | None = None) -> Path | None:
    candidates = [folder for folder in (preferred or []) if (folder / filename).exists()]
    if not candidates:
        candidates = [folder for folder in snapshot_folders(root) if (folder / filename).exists()]
    if not candidates:
        return None
    return candidates[-1] / filename


def valid_upload(field: cgi.FieldStorage | None) -> bool:
    return field is not None and bool(getattr(field, "filename", "")) and getattr(field, "file", None) is not None


def find_snapshot(snapshot_id: str) -> dict | None:
    for folder in snapshot_folders(ROOT):
        snapshot = load_snapshot(folder)
        if snapshot and snapshot.get("id") == snapshot_id:
            return snapshot
    return None


def read_state() -> dict:
    snapshots = []
    for folder in snapshot_folders(ROOT):
        snapshot = load_snapshot(folder)
        if snapshot:
            snapshots.append(snapshot)
    return build_state(ROOT, snapshots)


class Handler(SimpleHTTPRequestHandler):
    server_version = "LOVDashboard/1.0"

    def log_message(self, format: str, *args: object) -> None:
        return

    def translate_path(self, path: str) -> str:
        clean_path = unquote(urlparse(path).path).lstrip("/")
        if clean_path == "":
            return str(ROOT / "index.html")

        parts = [part for part in clean_path.replace("\\", "/").split("/") if part and part not in (".", "..")]
        return str(ROOT.joinpath(*parts))

    def send_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/state":
                self.send_json(read_state())
                return
            return super().do_GET()
        except Exception as exc:
            if path.startswith("/api/"):
                self.send_json({"error": str(exc)}, 500)
            else:
                self.send_error(500, str(exc))

    def do_POST(self) -> None:
        path = urlparse(self.path).path
        try:
            if path == "/api/reparse":
                state = asyncio.run(parse_all(ROOT))
                self.send_json(state)
                return
            if path == "/api/archive-boss-weeks":
                state = read_state()
                self.send_json({"state": state, "archived_boss_weeks": state.get("archived_boss_weeks", [])})
                return
            if path == "/api/corrections":
                self.handle_correction()
                return
            if path == "/api/upload":
                self.handle_upload()
                return
            if path == "/api/import-pcap":
                self.handle_pcap_import()
                return
            self.send_json({"error": "not_found"}, 404)
        except Exception as exc:
            self.send_json({"error": str(exc)}, 500)

    def handle_upload(self) -> None:
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")})
        member_field = form["member"] if "member" in form else None
        boss_field = form["boss"] if "boss" in form else None
        has_member = valid_upload(member_field)
        has_boss = valid_upload(boss_field)
        if not has_member and not has_boss:
            self.send_json({"error": "请至少上传联盟截图或 Boss 截图中的一张"}, 400)
            return

        parsed_snapshot = None
        now_day = datetime.now().date().isoformat()
        created_folders: list[Path] = []

        if has_member:
            member_folder = unique_record_folder(ROOT, MEMBER_RECORDS_DIR, "联盟")
            member_folder.mkdir(parents=True, exist_ok=True)
            created_folders.append(member_folder)
            with (member_folder / MEMBER_IMAGE).open("wb") as target:
                shutil.copyfileobj(member_field.file, target)
            parsed_snapshot = asyncio.run(parse_snapshot(member_folder, ROOT))
            for old_folder in same_day_folders(ROOT, now_day, "members"):
                if old_folder.resolve() != member_folder.resolve() and old_folder.is_dir() and old_folder.parent.resolve() == (ROOT / RECORDS_DIR / MEMBER_RECORDS_DIR).resolve():
                    shutil.rmtree(old_folder)

        if has_boss:
            boss_folder = unique_record_folder(ROOT, BOSS_RECORDS_DIR, "Boss")
            boss_folder.mkdir(parents=True, exist_ok=True)
            created_folders.append(boss_folder)
            with (boss_folder / BOSS_IMAGE).open("wb") as target:
                shutil.copyfileobj(boss_field.file, target)
            parsed_snapshot = asyncio.run(parse_snapshot(boss_folder, ROOT))
            for old_folder in same_day_folders(ROOT, now_day, "boss"):
                if old_folder.resolve() != boss_folder.resolve() and old_folder.is_dir() and old_folder.parent.resolve() == (ROOT / RECORDS_DIR / BOSS_RECORDS_DIR).resolve():
                    shutil.rmtree(old_folder)

        state = read_state()
        write_state_files(ROOT, state)
        selected_snapshot = state.get("snapshots", [])[-1] if state.get("snapshots") else parsed_snapshot
        self.send_json({"snapshot": selected_snapshot, "uploaded": [str(folder.relative_to(ROOT)) for folder in created_folders], "state": state})

    def handle_pcap_import(self) -> None:
        form = cgi.FieldStorage(fp=self.rfile, headers=self.headers, environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": self.headers.get("Content-Type")})
        pcap_field = form["pcap"] if "pcap" in form else None
        if not valid_upload(pcap_field):
            self.send_json({"error": "请选择 .pcapng 抓包文件"}, 400)
            return

        imports_root = ROOT / RECORDS_DIR / "imports"
        imports_root.mkdir(parents=True, exist_ok=True)
        safe_name = Path(getattr(pcap_field, "filename", "capture.pcapng")).name or "capture.pcapng"
        upload_path = imports_root / f"{datetime.now():%Y%m%d%H%M%S}_{safe_name}"
        with upload_path.open("wb") as target:
            shutil.copyfileobj(pcap_field.file, target)

        imported = import_pcapng(ROOT, upload_path)
        if not imported.get("snapshots"):
            self.send_json({"error": "没有在抓包中找到联盟或 Boss 排行数据", "diagnostics": imported.get("diagnostics", [])}, 422)
            return

        state = read_state()
        write_state_files(ROOT, state)
        selected_snapshot = state.get("snapshots", [])[-1] if state.get("snapshots") else imported["snapshots"][-1]
        summary = {
            "members": sum(len(snapshot.get("members", [])) for snapshot in imported["snapshots"]),
            "boss": sum(len(snapshot.get("boss", [])) for snapshot in imported["snapshots"]),
        }
        self.send_json(
            {
                "snapshot": selected_snapshot,
                "imported": [snapshot["folder"] for snapshot in imported["snapshots"]],
                "summary": summary,
                "diagnostics": imported.get("diagnostics", []),
                "state": state,
            }
        )

    def read_json_body(self) -> dict:
        content_length = int(self.headers.get("Content-Length", "0") or "0")
        if content_length <= 0:
            return {}
        body = self.rfile.read(content_length).decode("utf-8")
        return json.loads(body)

    def handle_correction(self) -> None:
        payload = self.read_json_body()
        snapshot_id = payload.get("snapshot_id")
        kind = payload.get("kind")
        row_id = payload.get("row_id")
        values = payload.get("values") or {}
        if not snapshot_id or kind not in ("members", "boss") or not row_id:
            self.send_json({"error": "snapshot_id、kind、row_id 不能为空"}, 400)
            return
        original_snapshot = find_snapshot(snapshot_id)
        if kind == "boss" and original_snapshot and original_snapshot.get("week_id") in load_boss_archives(ROOT):
            self.send_json({"error": "该周 Boss 数据已归档锁定，不能再修改"}, 423)
            return

        corrections = load_corrections(ROOT)
        snapshot_bucket = corrections.setdefault("snapshots", {}).setdefault(snapshot_id, {})
        kind_bucket = snapshot_bucket.setdefault(kind, {})

        if payload.get("delete"):
            kind_bucket.pop(row_id, None)
        else:
            values["reviewed"] = bool(values.get("reviewed", True))
            kind_bucket[row_id] = values
            if original_snapshot:
                remember_identity_alias(corrections, original_snapshot, kind, row_id, values)

        save_corrections(ROOT, corrections)
        state = build_state(ROOT)
        write_state_files(ROOT, state)
        self.send_json({"state": state, "corrections": corrections})

    def guess_type(self, path: str) -> str:
        if path.endswith(".json"):
            return "application/json; charset=utf-8"
        if path.endswith(".js"):
            return "application/javascript; charset=utf-8"
        if path.endswith(".css"):
            return "text/css; charset=utf-8"
        if path.endswith(".html"):
            return "text/html; charset=utf-8"
        return mimetypes.guess_type(path)[0] or "application/octet-stream"


def main() -> None:
    asyncio.run(parse_all(ROOT))
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    print(f"战姬 dashboard: http://{HOST}:{PORT}/")
    server.serve_forever()


if __name__ == "__main__":
    main()
