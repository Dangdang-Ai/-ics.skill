#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把课表 plan 转换成 .ics 日历文件（macOS / Windows 通用，只用标准库）。

用法::

    python3 generate_ics.py --plan plan.json --out ~/Desktop/schedule.ics
    python3 generate_ics.py --plan plan.json --print

plan JSON 结构::

    {
      "semester": {
        "current_week": 3,
        "total_weeks": 16,
        "week1_monday": "2026-08-24",   # 可选，缺省按今天反推
        "parity": "单"                  # 可选，校验当前周奇偶
      },
      "from_date": "2026-09-11",        # 可选，默认今天
      "reminder_minutes": 15,           # 可选，默认不加提醒
      "timezone": "Asia/Shanghai",      # 可选
      "tz_offset": "+08:00",            # 可选
      "periods": {"1": ["08:00", "08:45"], "2": ["08:50", "09:35"]},
      "courses": [
        {"course": "高等数学", "building": "教1", "room": "A101",
         "weekday": 1, "periods": [1, 2], "weeks": "1-16"}
      ]
    }

``weekday`` 用 1=周一 … 7=周日。``weeks`` 支持 ``1-16``、``1-16(单)``、
``1-8,10-16``、``单周``、``3-15(双)`` 等写法。

脚本自己完成：连堂合并、单双周 RRULE、跳过已过去的日期、RFC 5545 折行与转义，
生成后自动调用同目录的 validate_ics.py 做规范校验，不通过就报错退出（退出码 3），
避免把不规范的 .ics 交给用户。加 --no-validate 可跳过。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
try:  # 校验器就在同目录，导入失败时降级为「不校验但不报错」
    import validate_ics
except Exception:  # pragma: no cover
    validate_ics = None

WEEKDAY_CN = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
PARITY = {"单": 1, "双": 0}
NORMALIZE = [
    ("（", "("), ("）", ")"), ("，", ","), ("、", ","), ("；", ";"),
    ("–", "-"), ("—", "-"), ("~", "-"), ("～", "-"), ("至", "-"), ("—", "-"),
]


def norm_text(value) -> str:
    text = str(value)
    for src, dst in NORMALIZE:
        text = text.replace(src, dst)
    return text


def parse_date(value) -> date:
    text = norm_text(value).strip().replace("/", "-").replace(".", "-")
    match = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})", text)
    if not match:
        raise SystemExit(f"日期格式无法解析：{value!r}（应为 YYYY-MM-DD）")
    return date(int(match.group(1)), int(match.group(2)), int(match.group(3)))


def parse_time(value) -> tuple[int, int]:
    text = norm_text(value).strip().replace("：", ":")
    match = re.fullmatch(r"(\d{1,2}):(\d{2})", text) or re.fullmatch(r"(\d{2})(\d{2})", text)
    if not match:
        raise SystemExit(f"时间格式无法解析：{value!r}（应为 HH:MM）")
    hour, minute = int(match.group(1)), int(match.group(2))
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise SystemExit(f"时间超出范围：{value!r}")
    return hour, minute


def parse_weeks(spec, total_weeks: int) -> set[int]:
    """把周数写法解析成周数集合，支持单双周。"""
    if spec is None or str(spec).strip() == "":
        raise ValueError("课程缺少 weeks（上课周数）")
    text = norm_text(spec).replace("周", "").replace("第", "").replace(" ", "")

    global_parity = None
    match = re.search(r"\((单|双)\)", text)
    if match:
        global_parity = PARITY[match.group(1)]
        text = text.replace(match.group(0), "")
    match = re.search(r"(单|双)$", text)
    if match:
        if global_parity is None:
            global_parity = PARITY[match.group(1)]
        text = text[: match.start()]

    tokens = [token for token in text.split(",") if token]
    if not tokens:
        if global_parity is None:
            raise ValueError(f"无法解析上课周数：{spec!r}")
        return {week for week in range(1, total_weeks + 1) if week % 2 == global_parity}

    weeks: set[int] = set()
    for token in tokens:
        match = re.fullmatch(r"(\d+)(?:-(\d+))?", token)
        if not match:
            raise ValueError(f"无法解析上课周数：{spec!r}（片段 {token!r}）")
        start = int(match.group(1))
        end = int(match.group(2)) if match.group(2) else start
        if end < start:
            start, end = end, start
        token_weeks = set(range(start, end + 1))
        if global_parity is not None:
            token_weeks = {week for week in token_weeks if week % 2 == global_parity}
        weeks |= token_weeks

    if not weeks:
        raise ValueError(f"上课周数解析结果为空：{spec!r}")
    out_of_range = sorted(week for week in weeks if week < 1 or week > total_weeks)
    if out_of_range:
        print(f"警告：周数 {out_of_range} 超出 1-{total_weeks}，已忽略", file=sys.stderr)
        weeks = {week for week in weeks if 1 <= week <= total_weeks}
    if not weeks:
        raise ValueError(f"上课周数全部超出范围：{spec!r}")
    return weeks


def build_periods(raw) -> dict[int, tuple[int, int]]:
    if not isinstance(raw, dict) or not raw:
        raise SystemExit("plan.periods 需要提供每节的开始和结束时间")
    periods: dict[int, tuple[int, int]] = {}
    for key, value in raw.items():
        try:
            index = int(str(key).strip().lstrip("第").replace("节", ""))
        except ValueError:
            raise SystemExit(f"节次编号无法解析：{key!r}")
        if isinstance(value, (list, tuple)):
            if len(value) < 2:
                raise SystemExit(f"第 {index} 节的作息格式无法解析：{value!r}")
            start, end = value[0], value[1]
        elif isinstance(value, dict):
            start, end = value.get("start"), value.get("end")
        else:
            parts = re.split(r"[-~]", norm_text(value))
            if len(parts) < 2:
                raise SystemExit(f"第 {index} 节的作息格式无法解析：{value!r}")
            start, end = parts[0], parts[1]
        start_h, start_m = parse_time(start)
        end_h, end_m = parse_time(end)
        start_min = start_h * 60 + start_m
        end_min = end_h * 60 + end_m
        if end_min <= start_min:
            raise SystemExit(f"第 {index} 节结束时间不晚于开始时间：{value!r}")
        periods[index] = (start_min, end_min)
    return periods


def consecutive_runs(numbers) -> list[list[int]]:
    runs: list[list[int]] = []
    for number in sorted(set(numbers)):
        if runs and number == runs[-1][-1] + 1:
            runs[-1].append(number)
        else:
            runs.append([number])
    return runs


def merge_courses(courses, total_weeks: int) -> list[dict]:
    """同一门课同一天连续节次的记录合并成一条（两节连堂）。"""
    if not isinstance(courses, list) or not courses:
        raise SystemExit("plan.courses 需要提供课程列表")
    groups: dict[tuple, list[dict]] = {}
    order: list[tuple] = []
    for index, raw in enumerate(courses, start=1):
        if not isinstance(raw, dict) or not str(raw.get("course", "")).strip():
            raise SystemExit(f"第 {index} 条课程缺少 course（科目）")
        try:
            weekday = int(raw.get("weekday", 0))
        except (TypeError, ValueError):
            raise SystemExit(f"课程 {raw.get('course')!r} 的 weekday 不是数字")
        if not 1 <= weekday <= 7:
            raise SystemExit(f"课程 {raw['course']!r} 的 weekday 必须是 1-7")
        if not raw.get("periods"):
            raise SystemExit(f"课程 {raw['course']!r} 缺少 periods（节次）")
        try:
            period_list = sorted({int(period) for period in raw["periods"]})
        except (TypeError, ValueError):
            raise SystemExit(f"课程 {raw['course']!r} 的 periods 不是节次数字")
        try:
            weeks = parse_weeks(raw.get("weeks"), total_weeks)
        except ValueError as exc:
            raise SystemExit(f"课程 {raw['course']!r}：{exc}")
        item = {
            "course": str(raw["course"]).strip(),
            "building": str(raw.get("building") or "").strip(),
            "room": str(raw.get("room") or "").strip(),
            "weekday": weekday,
            "periods": period_list,
            "weeks": weeks,
            "weeks_raw": str(raw.get("weeks")).strip(),
        }
        key = (item["course"], item["building"], item["room"], weekday, tuple(sorted(weeks)))
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(item)

    merged: list[dict] = []
    for key in order:
        items = groups[key]
        periods = sorted({period for item in items for period in item["periods"]})
        for run in consecutive_runs(periods):
            merged.append({**items[0], "periods": run})
    return merged


def week_runs(weeks) -> list[tuple[list[int], int]]:
    """把周数集合切成等差片段，步长 1 或 2，便于生成 RRULE。"""
    ordered = sorted(set(weeks))
    runs: list[tuple[list[int], int]] = []
    index = 0
    while index < len(ordered):
        if index + 1 < len(ordered) and ordered[index + 1] - ordered[index] in (1, 2):
            step = ordered[index + 1] - ordered[index]
        else:
            step = 1
        run = [ordered[index]]
        cursor = index + 1
        while cursor < len(ordered) and ordered[cursor] - run[-1] == step:
            run.append(ordered[cursor])
            cursor += 1
        runs.append((run, step))
        index = cursor
    return runs


def esc(value: str) -> str:
    return (
        str(value)
        .replace("\\", "\\\\")
        .replace(";", "\\;")
        .replace(",", "\\,")
        .replace("\r\n", "\\n")
        .replace("\n", "\\n")
        .replace("\r", "\\n")
    )


def fold(line: str) -> str:
    """按 RFC 5545 折行：首行 75 字节，续行从空格开始。"""
    raw = line.encode("utf-8")
    if len(raw) <= 75:
        return line
    chunks = []
    start = 0
    limit = 75
    while start < len(raw):
        end = min(start + limit, len(raw))
        if end < len(raw):
            while end > start and (raw[end] & 0xC0) == 0x80:
                end -= 1
        chunks.append(raw[start:end].decode("utf-8"))
        start = end
        limit = 74
    return chunks[0] + "".join("\r\n " + chunk for chunk in chunks[1:])


def vtimezone(tzid: str, offset: str) -> list[str]:
    compact = offset.replace(":", "")
    tzname = {"+0800": "CST", "+0900": "JST", "+0000": "UTC"}.get(compact, compact)
    return [
        "BEGIN:VTIMEZONE",
        f"TZID:{tzid}",
        f"X-LIC-LOCATION:{tzid}",
        "BEGIN:STANDARD",
        "DTSTART:19700101T000000",
        f"TZOFFSETFROM:{compact}",
        f"TZOFFSETTO:{compact}",
        f"TZNAME:{tzname}",
        "END:STANDARD",
        "END:VTIMEZONE",
    ]


def duration_from_minutes(minutes: int) -> str:
    hours, rest = divmod(minutes, 60)
    parts = ["PT"]
    if hours:
        parts.append(f"{hours}H")
    if rest or not hours:
        parts.append(f"{rest}M")
    return "".join(parts)


def build_events(plan) -> tuple[list[dict], dict]:
    semester = plan.get("semester") or {}
    try:
        current_week = int(semester["current_week"])
        total_weeks = int(semester["total_weeks"])
    except (KeyError, TypeError, ValueError):
        raise SystemExit("plan.semester 需要 current_week（当前第几周）和 total_weeks（总周数）")
    if not 1 <= current_week <= total_weeks:
        raise SystemExit(f"当前周 {current_week} 不在 1-{total_weeks} 范围内")

    parity = semester.get("parity")
    if parity in (None, ""):
        parity = None
    elif norm_text(parity).strip() in ("单", "双"):
        expected = PARITY[norm_text(parity).strip()]
        if current_week % 2 != expected:
            raise SystemExit(f"当前周 {current_week} 与声明的{norm_text(parity).strip()}周不一致，请核对锚点")
    else:
        raise SystemExit(f"parity 只能是 单 或 双：{parity!r}")

    week1_raw = semester.get("week1_monday")
    if week1_raw:
        week1_monday = parse_date(week1_raw)
    else:
        today = date.today()
        week1_monday = today - timedelta(days=today.weekday()) - timedelta(weeks=current_week - 1)

    from_date = parse_date(plan["from_date"]) if plan.get("from_date") else date.today()
    tzid = str(plan.get("timezone") or "Asia/Shanghai")
    tz_offset = str(plan.get("tz_offset") or "+08:00")
    reminder = plan.get("reminder_minutes")
    if reminder in ("", None):
        reminder = None
    else:
        try:
            reminder = int(reminder)
        except (TypeError, ValueError):
            raise SystemExit(f"reminder_minutes 不是数字：{plan.get('reminder_minutes')!r}")
        if reminder <= 0:
            reminder = None

    periods = build_periods(plan.get("periods"))
    courses = merge_courses(plan.get("courses"), total_weeks)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")

    events: list[dict] = []
    for item in courses:
        missing = [period for period in item["periods"] if period not in periods]
        if missing:
            raise SystemExit(
                f"课程 {item['course']!r} 用到的节次 {missing} 不在作息时间表里，请先补全"
            )
        start_min = periods[item["periods"][0]][0]
        end_min = periods[item["periods"][-1]][1]
        start_clock = time(start_min // 60, start_min % 60)
        end_clock = time(end_min // 60, end_min % 60)

        for run, step in week_runs(item["weeks"]):
            dates = [
                week1_monday + timedelta(weeks=week - 1, days=item["weekday"] - 1)
                for week in run
            ]
            upcoming = [day for day in dates if day >= from_date]
            if not upcoming:
                continue
            first_day = upcoming[0]
            count = len(upcoming)
            start_dt = datetime.combine(first_day, start_clock)
            end_dt = datetime.combine(first_day, end_clock)
            period_text = (
                f"第{item['periods'][0]}节"
                if len(item["periods"]) == 1
                else f"第{item['periods'][0]}-{item['periods'][-1]}节"
            )
            summary = " ".join(part for part in (item["room"], item["course"]) if part)
            if item["building"] and item["room"] and not item["room"].startswith(item["building"]):
                location = f"{item['building']} {item['room']}"
            else:
                location = item["room"] or item["building"]
            description = "\\n".join(
                esc(part)
                for part in (
                    f"科目：{item['course']}",
                    f"教学楼：{item['building']}" if item["building"] else None,
                    f"教室：{item['room']}" if item["room"] else None,
                    f"时间：{WEEKDAY_CN[item['weekday'] - 1]} {period_text}",
                    f"周数：{item['weeks_raw']}",
                )
                if part
            )
            uid_key = "|".join(
                [
                    summary,
                    str(item["weekday"]),
                    str(item["periods"][0]),
                    str(item["periods"][-1]),
                    first_day.isoformat(),
                    str(step),
                    str(count),
                ]
            )
            uid = hashlib.md5(uid_key.encode("utf-8")).hexdigest() + "@schedule-to-ics"
            events.append(
                {
                    "uid": uid,
                    "dtstamp": stamp,
                    "tzid": tzid,
                    "start": start_dt.strftime("%Y%m%dT%H%M%S"),
                    "end": end_dt.strftime("%Y%m%dT%H%M%S"),
                    "rrule": f"FREQ=WEEKLY;INTERVAL={step};COUNT={count}" if count > 1 else None,
                    "summary": summary,
                    "location": location,
                    "description": description,
                    "alarm": f"-{duration_from_minutes(reminder)}" if reminder else None,
                    "sort_key": start_dt,
                    "meta": {
                        "weekday": item["weekday"],
                        "periods": item["periods"],
                        "start_clock": start_clock.strftime("%H:%M"),
                        "end_clock": end_clock.strftime("%H:%M"),
                        "count": count,
                        "step": step,
                        "first_day": first_day,
                        "last_day": upcoming[-1],
                        "weeks_raw": item["weeks_raw"],
                    },
                }
            )

    events.sort(key=lambda event: event["sort_key"])
    info = {
        "tzid": tzid,
        "tz_offset": tz_offset,
        "from_date": from_date,
        "current_week": current_week,
        "total_weeks": total_weeks,
        "week1_monday": week1_monday,
    }
    return events, info


def event_lines(event: dict) -> list[str]:
    lines = [
        "BEGIN:VEVENT",
        f"UID:{event['uid']}",
        f"DTSTAMP:{event['dtstamp']}",
        f"DTSTART;TZID={event['tzid']}:{event['start']}",
        f"DTEND;TZID={event['tzid']}:{event['end']}",
    ]
    if event["rrule"]:
        lines.append(f"RRULE:{event['rrule']}")
    lines.append(f"SUMMARY:{esc(event['summary'])}")
    if event["location"]:
        lines.append(f"LOCATION:{esc(event['location'])}")
    if event["description"]:
        lines.append(f"DESCRIPTION:{event['description']}")
    lines.extend(["SEQUENCE:0", "TRANSP:OPAQUE"])
    if event["alarm"]:
        lines.extend(
            [
                "BEGIN:VALARM",
                "ACTION:DISPLAY",
                f"DESCRIPTION:{esc(event['summary'])}",
                f"TRIGGER:{event['alarm']}",
                "END:VALARM",
            ]
        )
    lines.append("END:VEVENT")
    return lines


def build_calendar(events, info, calname: str) -> str:
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//schedule-to-ics//CN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        f"X-WR-CALNAME:{esc(calname)}",
        f"X-WR-TIMEZONE:{info['tzid']}",
    ]
    lines.extend(vtimezone(info["tzid"], info["tz_offset"]))
    for event in events:
        lines.extend(event_lines(event))
    lines.append("END:VCALENDAR")
    return "\r\n".join(fold(line) for line in lines) + "\r\n"


def summarize(events, info, out_path) -> str:
    if not events:
        return "没有可生成的事件：从 from_date 到今天学期结束，课表里没有可排的课程。"
    first = min(event["meta"]["first_day"] for event in events)
    last = max(event["meta"]["last_day"] for event in events)
    lines = [
        f"文件：{out_path}" if out_path else "输出：标准输出",
        f"事件：{len(events)} 个",
        f"覆盖：{first.isoformat()} ~ {last.isoformat()}",
        f"锚点：第 1 周周一 = {info['week1_monday'].isoformat()}，当前第 {info['current_week']} 周 / 共 {info['total_weeks']} 周",
        "明细：",
    ]
    for event in events:
        meta = event["meta"]
        lines.append(
            "  {summary}  {weekday} {periods}  {weeks}  {start}-{end}  共 {count} 次（{first} 起）".format(
                summary=event["summary"],
                weekday=WEEKDAY_CN[meta["weekday"] - 1],
                periods=(
                    f"第{meta['periods'][0]}节"
                    if len(meta["periods"]) == 1
                    else f"第{meta['periods'][0]}-{meta['periods'][-1]}节"
                ),
                weeks=meta["weeks_raw"],
                start=meta["start_clock"],
                end=meta["end_clock"],
                count=meta["count"],
                first=meta["first_day"].isoformat(),
            )
        )
    return "\n".join(lines)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="把课表 plan 转换成 .ics 日历文件")
    parser.add_argument("--plan", required=True, help="plan JSON 文件路径")
    parser.add_argument("--out", help="输出 .ics 路径，默认桌面上的 schedule.ics")
    parser.add_argument("--print", dest="print_only", action="store_true", help="只打印 ICS 内容，不写文件")
    parser.add_argument("--reminder-minutes", type=int, help="提前提醒分钟数，覆盖 plan 里的设置")
    parser.add_argument("--from-date", help="起始日期 YYYY-MM-DD，默认今天")
    parser.add_argument("--calname", default="课表", help="日历名称")
    parser.add_argument("--no-validate", dest="validate", action="store_false", help="跳过生成后的规范校验")
    args = parser.parse_args(argv)

    plan_path = Path(args.plan).expanduser()
    if not plan_path.is_file():
        raise SystemExit(f"找不到 plan 文件：{plan_path}")
    try:
        # utf-8-sig：容忍 Windows 编辑器写出的 BOM，避免「JSON 解析失败」这种无头案
        plan = json.loads(plan_path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"plan JSON 解析失败：{exc}")
    except UnicodeDecodeError as exc:
        raise SystemExit(
            f"plan 文件不是 UTF-8 编码（{exc.reason}）；Windows 上用 Out-File 默认会写成 UTF-16，"
            f"请另存为 UTF-8 或改用 [System.IO.File]::WriteAllText 写入"
        )
    if not isinstance(plan, dict):
        raise SystemExit("plan JSON 顶层必须是对象")
    if args.from_date:
        plan["from_date"] = args.from_date
    if args.reminder_minutes is not None:
        plan["reminder_minutes"] = args.reminder_minutes

    events, info = build_events(plan)
    content = build_calendar(events, info, args.calname)

    if args.validate:
        if validate_ics is None:
            print("提示：没找到 validate_ics.py，跳过规范校验", file=sys.stderr)
        else:
            report, _stats = validate_ics.validate_bytes(content.encode("utf-8"))
            if report.errors:
                print("❌ 生成的 ICS 不符合 RFC 5545，已停止交付：", file=sys.stderr)
                for message in report.errors:
                    print(f"  • {message}", file=sys.stderr)
                print("请检查 plan JSON（时间、周数、课程名）后重新生成。", file=sys.stderr)
                return 3
            print(f"✅ 规范校验通过（RFC 5545）：{len(events)} 个事件")

    if args.print_only:
        sys.stdout.write(content)
        return 0

    if args.out:
        out_path = Path(args.out).expanduser()
    else:
        desktop = Path.home() / "Desktop"
        base = desktop if desktop.is_dir() else Path.cwd()
        out_path = base / "schedule.ics"
    try:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(content, encoding="utf-8", newline="")
    except OSError as exc:
        raise SystemExit(f"写入失败：{out_path}（{exc}）")
    print(summarize(events, info, out_path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
