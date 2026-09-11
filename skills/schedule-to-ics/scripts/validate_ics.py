#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""严格校验 .ics 文件是否符合 RFC 5545（课表场景）。

用法::

    python3 validate_ics.py schedule.ics
    python3 validate_ics.py schedule.ics --strict      # 有警告也算失败
    python3 validate_ics.py --self-test                # 自检校验器本身

退出码：0 = 通过，1 = 有错误（或 --strict 下的警告），2 = 用法错误。

只做检查，不修改文件。发现问题是让你改 plan JSON 重新生成，而不是手改 .ics。
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PROPERTY_RE = re.compile(r"^([A-Za-z0-9-]+)((?:;[^:]*)?):(.*)$", re.DOTALL)
PARAM_RE = re.compile(r";([A-Za-z0-9-]+)=(\"[^\"]*\"|[^;:]*)")
DATE_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})$")
DATETIME_RE = re.compile(r"^(\d{4})(\d{2})(\d{2})T(\d{2})(\d{2})(\d{2})(Z?)$")
ISO_LIKE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}")
FREQ_VALUES = {"SECONDLY", "MINUTELY", "HOURLY", "DAILY", "WEEKLY", "MONTHLY", "YEARLY"}
BYDAY_RE = re.compile(r"^([+-]?\d{1,2})?(MO|TU|WE|TH|FR|SA|SU)$")
WEEKDAY_CODE = ["MO", "TU", "WE", "TH", "FR", "SA", "SU"]
TEXT_PROPS = {
    "SUMMARY", "LOCATION", "DESCRIPTION", "COMMENT", "X-WR-CALNAME", "X-WR-CALDESC",
    "TZNAME", "CONTACT", "CATEGORIES", "RESOURCES", "TZID",
}
ALLOWED_ESCAPES = set("\\;,nN")


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []

    def err(self, message: str, line=None) -> None:
        self.errors.append(f"第 {line} 行：{message}" if line else message)

    def warn(self, message: str, line=None) -> None:
        self.warnings.append(f"第 {line} 行：{message}" if line else message)

    @property
    def ok(self) -> bool:
        return not self.errors


def _check_line_endings(raw: bytes, rep: Report) -> list[bytes]:
    """按 RFC 5545 要求切分物理行，检查 CRLF、行宽、行尾空格。"""
    body = raw
    lookahead = raw.replace(b"\r\n", b"")
    bare_lf = lookahead.count(b"\n")
    bare_cr = lookahead.count(b"\r")
    if bare_lf or bare_cr:
        rep.err(
            f"有 {bare_lf} 个裸 LF、{bare_cr} 个裸 CR：ICS 必须统一用 CRLF。"
            f"已按行规范化后继续检查结构，下面的行号按规范化结果计"
        )
        body = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n").replace(b"\n", b"\r\n")

    segments = body.split(b"\r\n")
    if segments and segments[-1] == b"":
        segments = segments[:-1]
    else:
        rep.err("文件末尾缺少 CRLF：ICS 最后一行也必须以 CRLF 结束")

    for index, seg in enumerate(segments, start=1):
        if b"\n" in seg or b"\r" in seg:
            rep.err("使用裸 LF 或裸 CR 换行，ICS 必须统一用 CRLF（\\r\\n）", index)
        if len(seg) > 75:
            rep.err(f"物理行 {len(seg)} 字节，超过 RFC 5545 的 75 字节上限，需要折行", index)
        if seg.endswith(b" ") or seg.endswith(b"\t"):
            rep.warn("行尾有多余空白，部分日历客户端会吃掉它并破坏折行", index)
    return segments


def _unfold(segments: list[bytes], rep: Report) -> list[tuple[int, str]]:
    """把折行还原成逻辑行，返回 [(起始物理行号, 逻辑行)]。"""
    logical: list[tuple[int, str]] = []
    for index, seg in enumerate(segments, start=1):
        try:
            text = seg.decode("utf-8")
        except UnicodeDecodeError as exc:
            rep.err(f"不是合法 UTF-8：{exc.reason}", index)
            continue
        if "\x00" in text or any(ord(ch) < 0x20 and ch != "\t" for ch in text):
            rep.err("含有控制字符（NUL 等），ICS 正文不允许出现", index)
        if text.startswith(" "):
            if not logical:
                rep.err("第一行就是折行续行，文件被截断或结构错误", index)
                continue
            start, prev = logical[-1]
            logical[-1] = (start, prev + text[1:])
        else:
            logical.append((index, text))
    return logical


def _parse_property(line: str, rep: Report, lineno: int):
    match = PROPERTY_RE.match(line)
    if not match:
        rep.err(f"不是合法的内容行（应为 NAME;参数:值）：{line[:60]!r}", lineno)
        return None
    name, params_text, value = match.group(1), match.group(2), match.group(3)
    if name != name.upper():
        rep.warn(f"属性名 {name} 建议大写，部分客户端只认大写", lineno)
    params: dict[str, str] = {}
    if params_text:
        cursor = 0
        for param in PARAM_RE.finditer(params_text):
            if param.start() != cursor:
                rep.err(f"参数格式错误：{params_text!r}", lineno)
                return None
            cursor = param.end()
            raw_value = param.group(2)
            if raw_value.startswith('"') and raw_value.endswith('"') and len(raw_value) >= 2:
                raw_value = raw_value[1:-1]
            params[param.group(1).upper()] = raw_value
        if cursor != len(params_text):
            rep.err(f"参数格式错误：{params_text!r}", lineno)
            return None
    return name.upper(), params, value


def _check_text_value(value: str, prop: str, rep: Report, lineno: int) -> None:
    """TEXT 值里只允许 \\ \\; \\, \\n 这些转义。"""
    index = 0
    while index < len(value):
        char = value[index]
        if char == "\\":
            nxt = value[index + 1] if index + 1 < len(value) else ""
            if nxt not in ALLOWED_ESCAPES:
                rep.err(f"{prop} 里有非法转义 \\{nxt}（只允许 \\\\ \\; \\, \\n）", lineno)
                return
            index += 2
            continue
        if char in ";,":
            rep.err(
                f"{prop} 里的 {char!r} 没有转义；TEXT 值中的分号和逗号必须写成 \\{char}",
                lineno,
            )
        index += 1


def _parse_dt(value: str, rep: Report, lineno: int, prop: str):
    """返回 ('DATE', date) / ('DATE-TIME', datetime, is_utc)。"""
    from datetime import date, datetime

    value = value.strip()
    if ISO_LIKE_RE.match(value):
        rep.err(
            f"{prop} 用了 ISO 扩展格式 {value!r}；ICS 只接受基本格式 "
            f"20260907T080000（可带 Z）或 20260907",
            lineno,
        )
        return None
    date_match = DATE_RE.match(value)
    if date_match:
        year, month, day = (int(part) for part in date_match.groups())
        try:
            return ("DATE", date(year, month, day))
        except ValueError as exc:
            rep.err(f"{prop} 日期非法：{value!r}（{exc}）", lineno)
            return None
    dt_match = DATETIME_RE.match(value)
    if not dt_match:
        rep.err(
            f"{prop} 值非法：{value!r}；应为 20260907T080000、20260907T080000Z 或 20260907",
            lineno,
        )
        return None
    year, month, day, hour, minute, second = (int(part) for part in dt_match.groups()[:6])
    try:
        parsed = datetime(year, month, day, hour, minute, second)
    except ValueError as exc:
        rep.err(f"{prop} 时间非法：{value!r}（{exc}）", lineno)
        return None
    return ("DATE-TIME", parsed, dt_match.group(7) == "Z")


def _check_rrule(value: str, rep: Report, lineno: int, start_info) -> None:
    parts: dict[str, str] = {}
    for chunk in value.split(";"):
        if "=" not in chunk:
            rep.err(f"RRULE 片段缺少 '='：{chunk!r}", lineno)
            return
        key, _, val = chunk.partition("=")
        key = key.upper()
        if key in parts:
            rep.err(f"RRULE 里 {key} 重复出现", lineno)
            return
        parts[key] = val
    freq = parts.get("FREQ", "").upper()
    if freq not in FREQ_VALUES:
        rep.err(f"RRULE 的 FREQ 非法或缺失：{value!r}", lineno)
    for key in ("COUNT", "INTERVAL"):
        if key in parts:
            try:
                number = int(parts[key])
            except ValueError:
                rep.err(f"RRULE 的 {key} 不是整数：{parts[key]!r}", lineno)
                return
            if number < 1:
                rep.err(f"RRULE 的 {key} 必须 ≥ 1：{number}", lineno)
    if "COUNT" in parts and "UNTIL" in parts:
        rep.err("RRULE 不能同时有 COUNT 和 UNTIL，日历库会拒绝解析", lineno)
    if "UNTIL" in parts:
        until = parts["UNTIL"]
        if ISO_LIKE_RE.match(until) or not re.fullmatch(r"\d{8}(T\d{6}Z)?", until):
            rep.err(
                f"RRULE 的 UNTIL 非法：{until!r}；DATE-TIME 形式必须是 UTC，"
                f"如 20261231T235959Z（RFC 5545 要求）",
                lineno,
            )
    byday = parts.get("BYDAY")
    if byday:
        codes = []
        for token in byday.split(","):
            if not BYDAY_RE.match(token):
                rep.err(f"RRULE 的 BYDAY 片段非法：{token!r}", lineno)
                return
            codes.append(token[-2:])
        if freq == "WEEKLY" and start_info and start_info[0] == "DATE-TIME":
            weekday = WEEKDAY_CODE[start_info[1].weekday()]
            if weekday not in codes:
                rep.err(
                    f"RRULE 的 BYDAY={byday} 不包含 DTSTART 的星期 {weekday}，"
                    f"按 RFC 5545 这会被忽略或错排",
                    lineno,
                )


def validate_text(text: str, rep: Report) -> dict:
    """校验已解码的 ICS 文本（CRLF 换行）。返回统计信息。"""
    segments = _check_line_endings(text.encode("utf-8"), rep) if isinstance(text, str) else []
    logical = _unfold(segments, rep)
    stats = {"events": 0, "alarms": 0, "timezones": set(), "uids": [], "tzid_refs": {}}

    if not logical:
        rep.err("文件没有任何内容")
        return stats

    if logical[0][1].strip().upper() != "BEGIN:VCALENDAR":
        rep.err(f"第一行必须是 BEGIN:VCALENDAR，实际是 {logical[0][1][:40]!r}", logical[0][0])
    if logical[-1][1].strip().upper() != "END:VCALENDAR":
        rep.err(f"最后一行必须是 END:VCALENDAR，实际是 {logical[-1][1][:40]!r}", logical[-1][0])

    stack: list[tuple[str, int]] = []
    comp_stack: list[dict] = []
    component_order: list[str] = []
    version_seen = False
    prodid_seen = False
    calscale_seen = False
    calendars = 0

    for lineno, line in logical:
        if line.strip() == "":
            rep.err("空白行：ICS 不允许空行", lineno)
            continue
        parsed = _parse_property(line, rep, lineno)
        if not parsed:
            continue
        name, params, value = parsed

        if name == "BEGIN":
            component = value.strip().upper()
            if not component:
                rep.err("BEGIN 后面没有组件名", lineno)
                continue
            if not stack and component != "VCALENDAR":
                rep.err(f"最外层组件必须是 VCALENDAR，实际是 {component}", lineno)
            if component == "VCALENDAR":
                calendars += 1
                if calendars > 1:
                    rep.err("出现了多个 VCALENDAR，一个文件只能有一个", lineno)
            if component == "VEVENT":
                if any(item[0] == "VEVENT" for item in stack):
                    rep.err("VEVENT 里不能再嵌套 VEVENT", lineno)
                stats["events"] += 1
            elif component == "VALARM":
                if not any(item[0] == "VEVENT" for item in stack):
                    rep.err("VALARM 只能出现在 VEVENT 里", lineno)
                stats["alarms"] += 1
            elif component not in ("VCALENDAR", "VEVENT", "VALARM", "VTIMEZONE", "STANDARD", "DAYLIGHT"):
                rep.warn(f"不常见的组件 {component}，确认客户端支持", lineno)
            stack.append((component, lineno))
            if comp_stack:
                comp_stack[-1]["children"].add(component)
            comp_stack.append(
                {
                    "component": component,
                    "lineno": lineno,
                    "props": {},
                    "has_start": None,
                    "children": set(),
                }
            )
            if component not in ("VCALENDAR", "STANDARD", "DAYLIGHT"):
                component_order.append(component)
            continue

        if name == "END":
            component = value.strip().upper()
            if not stack:
                rep.err(f"多余的 END:{component}", lineno)
                continue
            open_name, open_line = stack.pop()
            if open_name != component:
                rep.err(f"END:{component} 与第 {open_line} 行的 BEGIN:{open_name} 不匹配", lineno)
            if comp_stack:
                finished = comp_stack.pop()
                if finished["component"] != component:
                    rep.err(f"END:{component} 与 BEGIN:{finished['component']} 顺序错乱", lineno)
                else:
                    _finish_component(finished, stack, rep)
            continue

        if not stack:
            rep.err(f"组件外的属性行：{name}", lineno)
            continue

        if name == "VERSION":
            version_seen = True
            if value.strip() != "2.0":
                rep.err(f"VERSION 必须是 2.0，实际是 {value.strip()!r}", lineno)
            if stack[-1][0] != "VCALENDAR":
                rep.err("VERSION 只能出现在 VCALENDAR 里", lineno)
            if component_order:
                rep.err("VERSION 必须写在所有事件之前（RFC 5545 §3.6）", lineno)
        elif name == "PRODID":
            prodid_seen = True
            if not value.strip():
                rep.err("PRODID 不能为空", lineno)
            if component_order:
                rep.warn("PRODID 建议写在所有事件之前", lineno)
        elif name == "CALSCALE":
            calscale_seen = True
            if value.strip().upper() != "GREGORIAN":
                rep.warn(f"CALSCALE 通常是 GREGORIAN，实际是 {value.strip()!r}", lineno)
        elif name == "METHOD":
            if value.strip().upper() not in ("PUBLISH", "REQUEST", "REPLY", "ADD", "CANCEL", "REFRESH", "COUNTER", "DECLINECOUNTER"):
                rep.err(f"METHOD 值非法：{value.strip()!r}", lineno)

        inner = comp_stack[-1] if comp_stack else None
        if inner is not None:
            inner["props"].setdefault(name, (value, params, lineno))

        if name in ("DTSTART", "DTEND", "DUE", "RECURRENCE-ID", "EXDATE"):
            tzid = params.get("TZID")
            if tzid:
                stats["tzid_refs"].setdefault(tzid, lineno)
                if value.strip().endswith("Z"):
                    rep.err(f"{name} 同时声明了 TZID 和 UTC 的 Z 后缀，二选一", lineno)
            if name in ("DTSTART", "DTEND"):
                parsed_dt = _parse_dt(value, rep, lineno, name)
                if name == "DTSTART" and inner is not None:
                    inner["has_start"] = parsed_dt
        elif name == "DTSTAMP":
            parsed_dt = _parse_dt(value, rep, lineno, name)
            if parsed_dt and parsed_dt[0] == "DATE-TIME" and not parsed_dt[2]:
                rep.err("DTSTAMP 必须是 UTC（以 Z 结尾，例如 20260911T040000Z）", lineno)
            if parsed_dt and parsed_dt[0] == "DATE":
                rep.err("DTSTAMP 必须是 DATE-TIME，不能只写日期", lineno)
        elif name == "UID":
            uid = value.strip()
            if not uid:
                rep.err("UID 不能为空", lineno)
            stats["uids"].append((uid, lineno))
        elif name == "RRULE":
            start_info = inner.get("has_start") if inner else None
            _check_rrule(value, rep, lineno, start_info)
        elif name == "TZID":
            if inner is not None and inner["component"] == "VTIMEZONE":
                stats["timezones"].add(value.strip())
            _check_text_value(value, name, rep, lineno)
        elif name in TEXT_PROPS:
            _check_text_value(value, name, rep, lineno)
            if name == "SUMMARY" and not value.strip():
                rep.err("SUMMARY 不能为空", lineno)

    if stack:
        for component, lineno in stack:
            rep.err(f"BEGIN:{component} 没有对应的 END", lineno)
    if not version_seen:
        rep.err("缺少 VERSION:2.0")
    if not prodid_seen:
        rep.err("缺少 PRODID（RFC 5545 要求 VCALENDAR 必须有 PRODID）")
    if not calscale_seen:
        rep.warn("建议写上 CALSCALE:GREGORIAN")
    if stats["events"] == 0:
        rep.warn("文件里没有任何 VEVENT，导入后日历会是空的")

    seen: dict[str, int] = {}
    for uid, lineno in stats["uids"]:
        if uid in seen:
            rep.err(f"UID 重复（第 {seen[uid]} 行已出现）：{uid}", lineno)
        else:
            seen[uid] = lineno
    for tzid, lineno in stats["tzid_refs"].items():
        if tzid not in stats["timezones"]:
            rep.err(
                f"TZID={tzid} 没有对应的 VTIMEZONE 块；RFC 5545 要求引用时区必须给出定义",
                lineno,
            )
    return stats


def _finish_component(component: dict, stack: list, rep: Report) -> None:
    name = component["component"]
    props = component["props"]
    lineno = component["lineno"]
    if name == "VALARM":
        for required in ("ACTION", "TRIGGER"):
            if required not in props:
                rep.err(f"VALARM 缺少必需的 {required}", lineno)
        action = props.get("ACTION", ("", {}, None))[0].strip().upper()
        if action and action not in ("DISPLAY", "AUDIO", "EMAIL"):
            rep.err(f"VALARM 的 ACTION 非法：{action}", lineno)
        if action == "DISPLAY" and "DESCRIPTION" not in props:
            rep.err("ACTION:DISPLAY 的 VALARM 必须有 DESCRIPTION", lineno)
        trigger = props.get("TRIGGER")
        if trigger and not re.fullmatch(
            r"[+-]?P(?:\d+W|(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?)", trigger[0].strip()
        ):
            rep.err(f"TRIGGER 时长格式非法：{trigger[0]!r}（如 -PT15M / -P1D）", trigger[2])
        return
    if name == "VTIMEZONE":
        tzid = props.get("TZID", ("", {}, None))[0].strip()
        if not tzid:
            rep.err("VTIMEZONE 缺少 TZID", lineno)
        if not ({"STANDARD", "DAYLIGHT"} & component.get("children", set())):
            rep.err("VTIMEZONE 里必须有至少一个 STANDARD 或 DAYLIGHT 子组件（RFC 5545 §3.6.5）", lineno)
        return
    if name != "VEVENT":
        return
    for required in ("UID", "DTSTAMP", "DTSTART"):
        if required not in props:
            rep.err(f"VEVENT 缺少必需的 {required}", lineno)
    if "DTEND" in props and "DURATION" in props:
        rep.err("VEVENT 不能同时有 DTEND 和 DURATION", props["DTEND"][2])
    if "DTEND" not in props and "DURATION" not in props and props.get("DTSTART") is not None:
        rep.warn("VEVENT 没有 DTEND/DURATION，客户端会按 0 分钟处理", lineno)
    start = props.get("DTSTART")
    end = props.get("DTEND")
    if start and end:
        start_tz = start[1].get("TZID")
        end_tz = end[1].get("TZID")
        if start_tz != end_tz:
            rep.err("DTSTART 与 DTEND 的 TZID 不一致", end[2])
        else:
            left = _parse_dt(start[0], Report(), start[2], "DTSTART")
            right = _parse_dt(end[0], Report(), end[2], "DTEND")
            if left and right and left[0] == right[0] == "DATE-TIME" and left[2] == right[2]:
                if right[1] <= left[1]:
                    rep.err("DTEND 不晚于 DTSTART", end[2])
    if "RRULE" not in props and "RDATE" not in props:
        pass
    for name_, (value_, params_, line_) in props.items():
        if name_ == "DURATION" and not re.fullmatch(
            r"[+-]?P(?:\d+W|(?:\d+D)?(?:T(?:\d+H)?(?:\d+M)?(?:\d+S)?)?)", value_.strip()
        ):
            rep.err(f"DURATION 值非法：{value_!r}", line_)
        if name_ == "SEQUENCE" and not re.fullmatch(r"\d+", value_.strip()):
            rep.err(f"SEQUENCE 必须是非负整数：{value_!r}", line_)


def validate_bytes(raw: bytes) -> tuple[Report, dict]:
    rep = Report()
    stats: dict = {"events": 0, "alarms": 0, "timezones": set(), "uids": [], "tzid_refs": {}}
    if not raw.strip():
        rep.err("文件为空")
        return rep, stats
    if raw.startswith(b"\xef\xbb\xbf"):
        rep.err("文件带 UTF-8 BOM，日历客户端会解析失败；请用 UTF-8 无 BOM 保存")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        rep.err(f"文件不是合法 UTF-8：{exc.reason}（偏移 {exc.start}）")
        return rep, stats
    stats = validate_text(text, rep)
    return rep, stats


def validate_file(path: Path, strict: bool) -> int:
    rep, stats = validate_bytes(path.read_bytes())
    events = stats.get("events", 0)
    timezones = stats.get("timezones", set())
    if rep.errors:
        print(f"❌ 规范校验不通过：{path}")
        for message in rep.errors:
            print(f"  • {message}")
        if rep.warnings:
            print(f"  警告 {len(rep.warnings)} 条：")
            for message in rep.warnings:
                print(f"  · {message}")
        print("处理方式：改 plan JSON → 重新跑 generate_ics.py → 再校验。不要手改 .ics。")
        return 1
    if rep.warnings and strict:
        print(f"❌ --strict 模式下存在 {len(rep.warnings)} 条警告：{path}")
        for message in rep.warnings:
            print(f"  · {message}")
        return 1
    print(f"✅ 规范校验通过：{path}（{events} 个事件，{len(timezones)} 个 VTIMEZONE）")
    if rep.warnings:
        print(f"警告 {len(rep.warnings)} 条（不影响导入）：")
        for message in rep.warnings:
            print(f"  · {message}")
    print("RFC 5545 检查项：CRLF / 75 字节折行 / UTF-8 无 BOM / 转义 / UID 唯一 / "
          "DTSTAMP UTC / TZID 有定义 / BEGIN-END 配对 / RRULE 合法")
    return 0


GOOD_SAMPLE = "\r\n".join(
    [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//schedule-to-ics//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:课表",
        "BEGIN:VTIMEZONE",
        "TZID:Asia/Shanghai",
        "X-LIC-LOCATION:Asia/Shanghai",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        "TZOFFSETFROM:+0800",
        "TZOFFSETTO:+0800",
        "TZNAME:CST",
        "END:STANDARD",
        "END:VTIMEZONE",
        "BEGIN:VEVENT",
        "UID:demo-1@schedule-to-ics",
        "DTSTAMP:20260911T040000Z",
        "DTSTART;TZID=Asia/Shanghai:20260907T080000",
        "DTEND;TZID=Asia/Shanghai:20260907T093500",
        "RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=16",
        "SUMMARY:A101 高等数学",
        "LOCATION:教1 A101",
        "DESCRIPTION:科目：高等数学\\n教室：A101",
        "SEQUENCE:0",
        "TRANSP:OPAQUE",
        "BEGIN:VALARM",
        "ACTION:DISPLAY",
        "DESCRIPTION:A101 高等数学",
        "TRIGGER:-PT15M",
        "END:VALARM",
        "END:VEVENT",
        "END:VCALENDAR",
        "",
    ]
)

BAD_SAMPLES = {
    "裸 LF 换行": GOOD_SAMPLE.replace("\r\n", "\n"),
    "UTF-8 BOM": "\ufeff" + GOOD_SAMPLE,
    "ISO 扩展时间格式": GOOD_SAMPLE.replace(
        "DTSTART;TZID=Asia/Shanghai:20260907T080000",
        "DTSTART;TZID=Asia/Shanghai:2026-09-07 08:00:00",
    ),
    "缺失 VTIMEZONE": GOOD_SAMPLE.split("BEGIN:VTIMEZONE")[0]
    + GOOD_SAMPLE.split("END:VTIMEZONE\r\n", 1)[1],
    "SUMMARY 里的逗号没转义": GOOD_SAMPLE.replace(
        "SUMMARY:A101 高等数学", "SUMMARY:A101 高等数学,物理"
    ),
    "DTSTAMP 不是 UTC": GOOD_SAMPLE.replace(
        "DTSTAMP:20260911T040000Z", "DTSTAMP:20260911T120000"
    ),
    "UID 重复": GOOD_SAMPLE.replace("END:VEVENT", "END:VEVENT\r\nBEGIN:VEVENT\r\nUID:demo-1@schedule-to-ics\r\nDTSTAMP:20260911T040000Z\r\nDTSTART;TZID=Asia/Shanghai:20260908T080000\r\nDTEND;TZID=Asia/Shanghai:20260908T093500\r\nSUMMARY:B202 大学物理"),
    "BEGIN/END 不配对": GOOD_SAMPLE.replace("END:VEVENT", "END:VTIMEZONE", 1),
    "超过 75 字节不折行": GOOD_SAMPLE.replace(
        "SUMMARY:A101 高等数学", "SUMMARY:" + "很长的课程名称" * 12
    ),
    "缺少 PRODID": GOOD_SAMPLE.replace("PRODID:-//schedule-to-ics//CN\r\n", ""),
    "缺少 CALSCALE": GOOD_SAMPLE.replace("CALSCALE:GREGORIAN\r\n", ""),
    "DTEND 早于 DTSTART": GOOD_SAMPLE.replace(
        "DTEND;TZID=Asia/Shanghai:20260907T093500",
        "DTEND;TZID=Asia/Shanghai:20260907T075500",
    ),
    "VTIMEZONE 没有 STANDARD 子组件": GOOD_SAMPLE.replace(
        "BEGIN:STANDARD\r\nDTSTART:19700101T000000\r\nTZOFFSETFROM:+0800\r\n"
        "TZOFFSETTO:+0800\r\nTZNAME:CST\r\nEND:STANDARD\r\n",
        "",
    ),
}


def self_test() -> int:
    failures: list[str] = []

    good, _ = validate_bytes(GOOD_SAMPLE.encode("utf-8"))
    if good.errors:
        failures.append("样板文件被误判为不合规：" + "；".join(good.errors))

    for title, sample in BAD_SAMPLES.items():
        result, _ = validate_bytes(sample.encode("utf-8"))
        if title == "缺少 CALSCALE":
            hit = bool(result.errors) or bool(result.warnings)
        else:
            hit = bool(result.errors)
        if not hit:
            failures.append(f"漏检：{title}")

    if failures:
        print("❌ 校验器自检失败：")
        for item in failures:
            print(f"  • {item}")
        return 1
    print(f"✅ 校验器自检通过（样板 1 例，反例 {len(BAD_SAMPLES)} 例全部命中）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="严格校验 .ics 是否符合 RFC 5545")
    parser.add_argument("path", nargs="?", help="要校验的 .ics 文件")
    parser.add_argument("--strict", action="store_true", help="有警告也返回失败")
    parser.add_argument("--self-test", action="store_true", help="自检校验器本身")
    args = parser.parse_args(argv)

    if args.self_test:
        return self_test()
    if not args.path:
        parser.error("需要给出 .ics 文件路径，或用 --self-test")
    path = Path(args.path).expanduser()
    if not path.is_file():
        print(f"❌ 找不到文件：{path}")
        return 2
    return validate_file(path, args.strict)


if __name__ == "__main__":
    sys.exit(main())
