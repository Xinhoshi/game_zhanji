from __future__ import annotations

import json
import re
import shutil
import socket
import struct
import zlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.lov_parser import (
    BOSS_RECORDS_DIR,
    MEMBER_RECORDS_DIR,
    RECORDS_DIR,
    boss_day_index,
    boss_effective_datetime,
    boss_week_id,
    relative_folder,
    week_id,
)


RANK_PACKET = "SCLogic_RankInfoBack"
MEMBER_PACKET = "SCLogic_GetUnionMebInfoBack"
DAMAGE_LABEL = ("{" + "\u4f24\u5bb3" + "}" + "\uff1a").encode("utf-8")
MAX_INSPECT_TEXT = 1_000_000


@dataclass
class TcpSegment:
    number: int
    timestamp: float
    src: str
    sport: int
    dst: str
    dport: int
    seq: int
    payload: bytes


def _inet4(value: bytes) -> str:
    return socket.inet_ntop(socket.AF_INET, value)


def _inet6(value: bytes) -> str:
    return socket.inet_ntop(socket.AF_INET6, value)


def _align32(value: int) -> int:
    return ((value + 3) // 4) * 4


def read_pcapng_packets(path: Path) -> list[tuple[float, bytes]]:
    data = path.read_bytes()
    offset = 0
    endian = "<"
    tsresol: dict[int, float] = {}
    packets: list[tuple[float, bytes]] = []

    while offset + 12 <= len(data):
        block_type_le, block_len_le = struct.unpack_from("<II", data, offset)
        if block_type_le == 0x0A0D0D0A:
            magic = struct.unpack_from("<I", data, offset + 8)[0]
            endian = "<" if magic == 0x1A2B3C4D else ">"
            block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]
        else:
            block_type = struct.unpack_from(endian + "I", data, offset)[0]
            block_len = struct.unpack_from(endian + "I", data, offset + 4)[0]
            if block_len < 12 or offset + block_len > len(data):
                break
            body = data[offset + 8 : offset + block_len - 4]
            if block_type == 1 and len(body) >= 8:
                interface_id = len(tsresol)
                resolution = 1e-6
                option_offset = 8
                while option_offset + 4 <= len(body):
                    code, length = struct.unpack_from(endian + "HH", body, option_offset)
                    option_offset += 4
                    value = body[option_offset : option_offset + length]
                    option_offset += _align32(length)
                    if code == 0:
                        break
                    if code == 9 and value:
                        raw = value[0]
                        resolution = 2 ** -(raw & 0x7F) if raw & 0x80 else 10 ** -raw
                tsresol[interface_id] = resolution
            elif block_type == 6 and len(body) >= 20:
                interface_id, ts_high, ts_low, cap_len, _ = struct.unpack_from(endian + "IIIII", body, 0)
                resolution = tsresol.get(interface_id, 1e-6)
                timestamp = ((ts_high << 32) | ts_low) * resolution
                packets.append((timestamp, body[20 : 20 + cap_len]))

        if block_len_le < 12 and "block_len" not in locals():
            break
        offset += _align32(block_len)

    return packets


def _parse_tcp_segment(number: int, timestamp: float, frame: bytes) -> TcpSegment | None:
    if len(frame) < 14:
        return None
    ether_type = struct.unpack_from("!H", frame, 12)[0]
    offset = 14
    if ether_type == 0x8100 and len(frame) >= 18:
        ether_type = struct.unpack_from("!H", frame, 16)[0]
        offset = 18

    if ether_type == 0x0800 and len(frame) >= offset + 20:
        packet = frame[offset:]
        ihl = (packet[0] & 0x0F) * 4
        if packet[9] != 6:
            return None
        src = _inet4(packet[12:16])
        dst = _inet4(packet[16:20])
        l4 = packet[ihl:]
    elif ether_type == 0x86DD and len(frame) >= offset + 40:
        packet = frame[offset:]
        if packet[6] != 6:
            return None
        src = _inet6(packet[8:24])
        dst = _inet6(packet[24:40])
        l4 = packet[40:]
    else:
        return None

    if len(l4) < 20:
        return None
    sport, dport = struct.unpack_from("!HH", l4, 0)
    seq = struct.unpack_from("!I", l4, 4)[0]
    data_offset = (l4[12] >> 4) * 4
    return TcpSegment(number, timestamp, src, sport, dst, dport, seq, l4[data_offset:])


def _tcp_stream(segments: list[TcpSegment]) -> bytes:
    payloads = [(segment.seq, segment.payload) for segment in segments if segment.payload]
    if not payloads:
        return b""
    payloads.sort(key=lambda item: item[0])
    base = payloads[0][0]
    stream = bytearray()
    for seq, payload in payloads:
        if seq < base:
            continue
        relative = seq - base
        if relative > len(stream):
            stream.extend(b"\x00" * (relative - len(stream)))
        overlap = len(stream) - relative
        if overlap < len(payload):
            stream.extend(payload[max(overlap, 0) :])
    return bytes(stream)


def _length_prefixed_strings(payload: bytes) -> list[tuple[int, str]]:
    strings: list[tuple[int, str]] = []
    for offset in range(max(0, len(payload) - 5)):
        length = struct.unpack_from("<I", payload, offset)[0]
        if not 1 <= length <= 260 or offset + 4 + length > len(payload):
            continue
        raw = payload[offset + 4 : offset + 4 + length]
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if len(value.strip()) < 2:
            continue
        if all(ord(char) >= 32 and char != "\x7f" for char in value):
            strings.append((offset, value))
    return strings


def _clean_identity(value: str) -> str:
    return re.sub(r"</?color[^>]*>", "", value).strip()


def _split_identity(identity: str) -> tuple[int | None, str, str]:
    zone_text, _, name = identity.partition("#")
    zone = int(zone_text) if zone_text.isdigit() else None
    key = f"{zone}#{name}" if zone is not None else identity
    return zone, name or identity, key


def _parse_member_last_online(payload: bytes, offset: int) -> tuple[str | None, str | None]:
    if offset + 4 > len(payload):
        return None, None
    length = struct.unpack_from("<I", payload, offset)[0]
    if not 10 <= length <= 32 or offset + 4 + length > len(payload):
        return None, None
    raw = payload[offset + 4 : offset + 4 + length]
    try:
        value = raw.decode("utf-8").strip()
    except UnicodeDecodeError:
        return None, None
    if not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$", value):
        return None, value or None
    return datetime.strptime(value, "%Y-%m-%d %H:%M:%S").isoformat(timespec="seconds"), value


def _parse_rank_packet(payload: bytes) -> list[dict[str, Any]]:
    names: list[tuple[int, str]] = []
    for offset, value in _length_prefixed_strings(payload):
        if "#" not in value:
            continue
        identity = _clean_identity(value)
        if not identity or any(item[1] == identity for item in names):
            continue
        names.append((offset, identity))

    damages = [
        (match.start(), match.group(1).decode("ascii") + "k")
        for match in re.finditer(re.escape(DAMAGE_LABEL) + rb"([0-9,]+)k", payload)
    ]

    rows: list[dict[str, Any]] = []
    for rank, ((_, identity), (_, damage_text)) in enumerate(zip(names, damages), start=1):
        zone, name, key = _split_identity(identity)
        damage_k = int(damage_text[:-1].replace(",", ""))
        rows.append(
            {
                "zone": zone,
                "name": name,
                "key": key,
                "rank": rank,
                "damage_k": damage_k,
                "damage": damage_k * 1000,
                "raw": {"source": "pcapng", "identity": identity, "damage": damage_text},
                "needs_review": [],
                "row_id": f"b{rank:03d}",
                "ocr": {"zone": zone, "name": name, "key": key, "rank": rank, "damage_k": damage_k},
                "imported": True,
            }
        )
    return rows


def _parse_member_packet(payload: bytes) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for offset, identity in _length_prefixed_strings(payload):
        if "#" not in identity or "color=" in identity:
            continue
        identity = _clean_identity(identity)
        zone, name, key = _split_identity(identity)
        end = offset + 4 + struct.unpack_from("<I", payload, offset)[0]
        if end + 8 > len(payload):
            continue
        level, power = struct.unpack_from("<II", payload, end)
        extra1 = struct.unpack_from("<I", payload, end + 8)[0] if end + 12 <= len(payload) else None
        extra2 = struct.unpack_from("<I", payload, end + 12)[0] if end + 16 <= len(payload) else None
        flag = struct.unpack_from("<I", payload, end + 16)[0] if end + 20 <= len(payload) else 0
        last_online, last_online_raw = _parse_member_last_online(payload, end + 20) if flag == 0 else (None, None)
        row_index = len(rows) + 1
        rows.append(
            {
                "zone": zone,
                "name": name,
                "key": key,
                "role": "成员",
                "level": level,
                "power": power,
                "last_online": last_online,
                "in_group": bool(flag),
                "raw": {"source": "pcapng", "identity": identity, "level": level, "extra1": extra1, "extra2": extra2, "flag": flag, "last_online": last_online_raw},
                "needs_review": [],
                "row_id": f"m{row_index:03d}",
                "ocr": {"zone": zone, "name": name, "key": key, "power": power, "last_online": last_online},
                "imported": True,
            }
        )
    return rows


def _packet_label(text: str) -> str:
    if RANK_PACKET in text:
        return RANK_PACKET
    if MEMBER_PACKET in text:
        return MEMBER_PACKET
    match = re.search(r"\b(?:SC|CS|GC|CG|Logic)Logic_[A-Za-z0-9_]+|\bSC[A-Za-z0-9_]+Back\b", text)
    return match.group(0) if match else "gzip"


def _string_values(payload: bytes) -> list[dict[str, Any]]:
    seen: set[tuple[int, str]] = set()
    values: list[dict[str, Any]] = []
    for offset, value in _length_prefixed_strings(payload):
        key = (offset, value)
        if key in seen:
            continue
        seen.add(key)
        values.append({"offset": offset, "value": value})
    return values


def _ascii_strings(payload: bytes) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for match in re.finditer(rb"[ -~]{4,}", payload):
        values.append({"offset": match.start(), "value": match.group(0).decode("ascii", "ignore")})
    return values


def _readable_strings(payload: bytes) -> list[dict[str, Any]]:
    values = _string_values(payload)
    seen = {(item["offset"], item["value"]) for item in values}
    for item in _ascii_strings(payload):
        key = (item["offset"], item["value"])
        if key not in seen:
            seen.add(key)
            values.append(item)
    values.sort(key=lambda item: item["offset"])
    return values


def _bytes_as_text(payload: bytes) -> tuple[str, bool]:
    raw = payload[:MAX_INSPECT_TEXT]
    text = raw.decode("utf-8", "replace")
    printable = "".join(chr(byte) if 32 <= byte < 127 or byte in (9, 10, 13) else "." for byte in raw)
    hex_text = " ".join(f"{byte:02x}" for byte in raw)
    return f"UTF-8\n{text}\n\nASCII\n{printable}\n\nHEX\n{hex_text}", len(payload) > MAX_INSPECT_TEXT


def _binary_words(payload: bytes) -> list[dict[str, Any]]:
    words: list[dict[str, Any]] = []
    for offset in range(0, min(len(payload), MAX_INSPECT_TEXT) - 3, 4):
        u32 = struct.unpack_from("<I", payload, offset)[0]
        i32 = struct.unpack_from("<i", payload, offset)[0]
        f32 = struct.unpack_from("<f", payload, offset)[0]
        item: dict[str, Any] = {"offset": offset, "hex": payload[offset : offset + 4].hex(), "u32": u32, "i32": i32}
        if f32 == 1.0 or (0.000001 < abs(f32) < 1_000_000 and u32 > 1_000_000):
            item["float"] = round(f32, 6)
        words.append(item)
    return words


def _game_packet_records(stream: bytes) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    offset = 0
    while offset + 13 <= len(stream):
        total = struct.unpack_from("<I", stream, offset)[0]
        if total < 20 or offset + total > len(stream):
            offset += 1
            continue
        flag = stream[offset + 4]
        remaining = struct.unpack_from("<I", stream, offset + 5)[0]
        name_length = struct.unpack_from("<I", stream, offset + 9)[0]
        name_start = offset + 13
        name_end = name_start + name_length
        if name_length <= 0 or name_end > offset + total:
            offset += 1
            continue
        try:
            name = stream[name_start:name_end].decode("utf-8")
        except UnicodeDecodeError:
            offset += 1
            continue
        if not re.match(r"^(?:SC|CS|GC|CG|Logic)[A-Za-z0-9_]+", name):
            offset += 1
            continue
        records.append(
            {
                "offset": offset,
                "total": total,
                "flag": flag,
                "remaining": remaining,
                "name": name,
                "body_offset": name_end,
                "body": stream[name_end : offset + total],
            }
        )
        offset += total
    return records


def _inspect_game_record(record: dict[str, Any]) -> dict[str, Any]:
    body = record["body"]
    text, truncated = _bytes_as_text(body)
    words = _binary_words(body)
    return {
        "packet": record["name"],
        "bytes": len(body),
        "encoding": "game-binary",
        "text": text,
        "text_truncated": truncated,
        "strings": _readable_strings(body),
        "row_kind": None,
        "rows": [],
        "words": words,
        "insights": _game_packet_insights(record["name"], words),
        "game_header": {
            "stream_offset": record["offset"],
            "total": record["total"],
            "flag": record["flag"],
            "remaining": record["remaining"],
            "body_offset": record["body_offset"],
        },
    }


def _word_number(word: dict[str, Any]) -> float | int:
    return word["float"] if "float" in word else word["u32"]


def _game_packet_insights(name: str, words: list[dict[str, Any]]) -> list[dict[str, Any]]:
    values = [_word_number(word) for word in words]
    insights: list[dict[str, Any]] = []
    if values:
        insights.append({"label": "消息编号", "value": str(values[0])})

    if name == "SCLogic_GetPVEInfoBack" and len(values) >= 90:
        stage_count = int(values[10]) if isinstance(values[10], int) else 0
        stage_values = [value for value in values[11:72] if isinstance(value, int) and value != 10]
        tail = values[73:]
        current_id = values[73]
        hp_ratio = values[86]
        state_flag = values[87]
        state_type = values[88]
        hp_total = values[89]
        insights.extend(
            [
                {"label": "疑似基础字段", "value": ", ".join(str(value) for value in values[1:9])},
                {"label": "疑似关卡数量", "value": str(stage_count)},
                {"label": "疑似关卡 ID", "value": ", ".join(str(value) for value in stage_values)},
                {"label": "疑似当前 PVE/Boss ID", "value": str(current_id)},
                {"label": "末尾状态字段", "value": ", ".join(str(value) for value in tail)},
            ]
        )
        if isinstance(hp_ratio, float) and isinstance(hp_total, int):
            remaining = hp_total * hp_ratio
            lost = hp_total - remaining
            insights.extend(
                [
                    {"label": "疑似剩余血量比例", "value": f"{hp_ratio * 100:.2f}%"},
                    {"label": "归一化血条剩余", "value": f"{remaining:.0f} / {hp_total}"},
                    {"label": "归一化血条已损失", "value": f"{lost:.0f} / {hp_total}"},
                    {"label": "疑似状态标记", "value": f"flag={state_flag}, type={state_type}"},
                ]
            )

    elif name == "SCLogic_PVEAwarsPreview" and len(values) >= 2:
        item_ids = [int(value) for value in values[2:] if isinstance(value, int) and value >= 1000]
        insights.extend(
            [
                {"label": "疑似奖励组数", "value": str(values[1])},
                {"label": "疑似奖励/物品 ID", "value": ", ".join(str(value) for value in item_ids)},
            ]
        )

    return insights


def _inspect_gzip_payload(payload: bytes, text: str, packet: str) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []
    row_kind = None
    if packet == RANK_PACKET:
        rows = _parse_rank_packet(payload)
        row_kind = "boss"
    elif packet == MEMBER_PACKET:
        rows = _parse_member_packet(payload)
        row_kind = "members"

    visible_text = text[:MAX_INSPECT_TEXT]
    return {
        "packet": packet,
        "bytes": len(payload),
        "encoding": "gzip",
        "text": visible_text,
        "text_truncated": len(text) > MAX_INSPECT_TEXT,
        "strings": _readable_strings(payload),
        "row_kind": row_kind,
        "rows": rows,
        "words": _binary_words(payload),
    }


def _inspect_raw_stream(stream: bytes) -> dict[str, Any]:
    text, truncated = _bytes_as_text(stream)
    decoded = stream.decode("utf-8", "ignore")
    packet = _packet_label(decoded)
    if packet == "gzip":
        prefix = stream[:24].decode("ascii", "ignore").strip()
        if prefix.startswith(("GET ", "POST ", "HTTP/")):
            packet = prefix.splitlines()[0]
        else:
            packet = "tcp-stream"
    return {
        "packet": packet,
        "bytes": len(stream),
        "encoding": "raw",
        "text": text,
        "text_truncated": truncated,
        "strings": _readable_strings(stream),
        "row_kind": None,
        "rows": [],
        "words": _binary_words(stream),
    }


def inspect_pcapng(path: Path) -> dict[str, Any]:
    packets = read_pcapng_packets(path)
    flows: dict[tuple[str, int, str, int], list[TcpSegment]] = defaultdict(list)
    for number, (timestamp, frame) in enumerate(packets, start=1):
        segment = _parse_tcp_segment(number, timestamp, frame)
        if segment:
            flows[(segment.src, segment.sport, segment.dst, segment.dport)].append(segment)

    result: dict[str, Any] = {
        "file": {"name": path.name, "bytes": path.stat().st_size},
        "frame_count": len(packets),
        "tcp_flow_count": len(flows),
        "gzip_count": 0,
        "captured_at": None,
        "summary": {"members": 0, "boss": 0, "gzip": 0, "raw": 0},
        "flows": [],
        "payloads": [],
    }

    for flow_index, (flow, segments) in enumerate(flows.items(), start=1):
        payload_bytes = sum(len(segment.payload) for segment in segments)
        timestamps = [segment.timestamp for segment in segments if segment.timestamp]
        stream = _tcp_stream(segments)
        flow_label = f"{flow[0]}:{flow[1]} -> {flow[2]}:{flow[3]}"
        flow_info = {
            "index": flow_index,
            "flow": flow_label,
            "segment_count": len(segments),
            "payload_bytes": payload_bytes,
            "stream_bytes": len(stream),
            "first_at": datetime.fromtimestamp(min(timestamps)).isoformat(timespec="seconds") if timestamps else None,
            "last_at": datetime.fromtimestamp(max(timestamps)).isoformat(timespec="seconds") if timestamps else None,
            "gzip_count": 0,
        }

        has_gzip = b"\x1f\x8b\x08" in stream
        if has_gzip:
            flow_time = max((segment.timestamp for segment in segments if segment.payload), default=0)
            result["captured_at"] = result["captured_at"] or datetime.fromtimestamp(flow_time).isoformat(timespec="seconds")
            for match in re.finditer(b"\x1f\x8b\x08", stream):
                decompressor = zlib.decompressobj(31)
                payload = decompressor.decompress(stream[match.start() :]) + decompressor.flush()
                text = payload.decode("utf-8", "ignore")
                packet = _packet_label(text)
                details = _inspect_gzip_payload(payload, text, packet)
                details.update(
                    {
                        "id": f"payload-{len(result['payloads']) + 1}",
                        "flow_index": flow_index,
                        "flow": flow_label,
                        "stream_offset": match.start(),
                        "complete": decompressor.eof,
                    }
                )
                result["payloads"].append(details)
                flow_info["gzip_count"] += 1
                result["gzip_count"] += 1
                result["summary"]["gzip"] += 1
                if details["row_kind"] == "members":
                    result["summary"]["members"] = max(result["summary"]["members"], len(details["rows"]))
                elif details["row_kind"] == "boss":
                    result["summary"]["boss"] = max(result["summary"]["boss"], len(details["rows"]))

        if stream and not has_gzip:
            records = _game_packet_records(stream)
            if records:
                for record in records:
                    details = _inspect_game_record(record)
                    details.update(
                        {
                            "id": f"payload-{len(result['payloads']) + 1}",
                            "flow_index": flow_index,
                            "flow": flow_label,
                            "stream_offset": record["offset"],
                            "complete": True,
                        }
                    )
                    result["payloads"].append(details)
                    result["summary"]["raw"] += 1
            else:
                details = _inspect_raw_stream(stream)
                details.update(
                    {
                        "id": f"payload-{len(result['payloads']) + 1}",
                        "flow_index": flow_index,
                        "flow": flow_label,
                        "stream_offset": 0,
                        "complete": True,
                    }
                )
                result["payloads"].append(details)
                result["summary"]["raw"] += 1

        result["flows"].append(flow_info)

    return result


def extract_packets(path: Path) -> dict[str, Any]:
    packets = read_pcapng_packets(path)
    flows: dict[tuple[str, int, str, int], list[TcpSegment]] = defaultdict(list)
    for number, (timestamp, frame) in enumerate(packets, start=1):
        segment = _parse_tcp_segment(number, timestamp, frame)
        if segment:
            flows[(segment.src, segment.sport, segment.dst, segment.dport)].append(segment)

    result: dict[str, Any] = {
        "boss": [],
        "members": [],
        "diagnostics": [],
        "captured_at": None,
    }
    for flow, segments in flows.items():
        stream = _tcp_stream(segments)
        if b"\x1f\x8b\x08" not in stream:
            continue
        flow_time = max((segment.timestamp for segment in segments if segment.payload), default=0)
        for match in re.finditer(b"\x1f\x8b\x08", stream):
            decompressor = zlib.decompressobj(31)
            payload = decompressor.decompress(stream[match.start() :]) + decompressor.flush()
            text = payload.decode("utf-8", "ignore")
            diagnostic = {"flow": f"{flow[0]}:{flow[1]} -> {flow[2]}:{flow[3]}", "complete": decompressor.eof, "bytes": len(payload)}
            if RANK_PACKET in text:
                rows = _parse_rank_packet(payload)
                diagnostic.update({"packet": RANK_PACKET, "rows": len(rows)})
                if rows and (len(rows) > len(result["boss"]) or decompressor.eof):
                    result["boss"] = rows
                    result["captured_at"] = result["captured_at"] or datetime.fromtimestamp(flow_time).isoformat(timespec="seconds")
            elif MEMBER_PACKET in text:
                rows = _parse_member_packet(payload)
                diagnostic.update({"packet": MEMBER_PACKET, "rows": len(rows)})
                if rows and (len(rows) > len(result["members"]) or decompressor.eof):
                    result["members"] = rows
                    result["captured_at"] = result["captured_at"] or datetime.fromtimestamp(flow_time).isoformat(timespec="seconds")
            else:
                diagnostic["packet"] = "gzip"
            result["diagnostics"].append(diagnostic)

    return result


def _timestamp_folder(captured_at: str, suffix: str) -> str:
    dt = datetime.fromisoformat(captured_at)
    return f"{dt.year}年{dt.month}月{dt.day}日{dt:%H%M%S}_{suffix}"


def _unique_folder(root: Path, bucket: str, captured_at: str, suffix: str) -> Path:
    parent = root / RECORDS_DIR / bucket
    parent.mkdir(parents=True, exist_ok=True)
    base = _timestamp_folder(captured_at, suffix)
    folder = parent / base
    index = 1
    while folder.exists():
        folder = parent / f"{base}-{index}"
        index += 1
    return folder


def _copy_source_file(folder: Path, source_file: Path) -> str:
    target = folder / source_file.name
    if source_file.resolve() != target.resolve():
        shutil.copy2(source_file, target)
    return target.name


def _write_snapshot(root: Path, folder: Path, captured_at: str, members: list[dict[str, Any]], boss: list[dict[str, Any]], source_file: Path) -> dict[str, Any]:
    folder.mkdir(parents=True, exist_ok=True)
    effective_boss_at = boss_effective_datetime(captured_at).isoformat(timespec="seconds") if boss else None
    source_name = _copy_source_file(folder, source_file)
    snapshot = {
        "id": folder.name,
        "folder": relative_folder(root, folder),
        "captured_at": captured_at,
        "week_id": week_id(captured_at),
        "boss_captured_at": effective_boss_at,
        "boss_week_id": boss_week_id(captured_at) if boss else week_id(captured_at),
        "boss_day_index": boss_day_index(captured_at) if boss else None,
        "images": {"members": None, "boss": None},
        "members": members,
        "boss": boss,
        "raw_ocr": {"members": [], "boss": []},
        "packet_import": {"source_file": source_name, "source_path": f"{relative_folder(root, folder)}/{source_name}"},
    }
    if members:
        (folder / "members.json").write_text(json.dumps(members, ensure_ascii=False, indent=2), encoding="utf-8")
    if boss:
        (folder / "boss.json").write_text(json.dumps(boss, ensure_ascii=False, indent=2), encoding="utf-8")
    (folder / "snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2), encoding="utf-8")
    return snapshot


def import_pcapng(root: Path, source_file: Path) -> dict[str, Any]:
    extracted = extract_packets(source_file)
    captured_at = extracted.get("captured_at") or datetime.fromtimestamp(source_file.stat().st_mtime).isoformat(timespec="seconds")
    snapshots: list[dict[str, Any]] = []

    if extracted["members"]:
        member_folder = _unique_folder(root, MEMBER_RECORDS_DIR, captured_at, "联盟")
        snapshots.append(_write_snapshot(root, member_folder, captured_at, extracted["members"], [], source_file))

    if extracted["boss"]:
        boss_folder = _unique_folder(root, BOSS_RECORDS_DIR, captured_at, "Boss")
        snapshots.append(_write_snapshot(root, boss_folder, captured_at, [], extracted["boss"], source_file))

    return {"snapshots": snapshots, "diagnostics": extracted["diagnostics"], "captured_at": captured_at}
