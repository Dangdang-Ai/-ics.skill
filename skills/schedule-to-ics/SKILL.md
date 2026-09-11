---
name: schedule-to-ics
description: 把课表截图和学校作息时间表转换成可导入日历的标准 .ics 文件（默认输出 schedule.ics）。当用户发来课表图片并想生成课表日历、导入手机或电脑日历、生成课程提醒时使用。适用于豆包电脑版「工作模式」，macOS 与 Windows 通用。
---

# 课表转 ICS

固定流水线，不要跳步：

```
课表图片 → 视觉识别 → 按本文件解析 → 课程 JSON → Python 生成 .ics → 校验 → schedule.ics
```

## 0. ICS 规范铁律（最高优先级）

模型手写 .ics 一定会出错：换行符、折行、转义、时区、UID 只要有一处不规范，日历要么导入失败，要么课表错位。因此：

1. **绝不手写 .ics。** 所有 .ics 只能由 `scripts/generate_ics.py` 生成。
2. **生成后必须校验。** 必须运行 `scripts/validate_ics.py`，看到 `✅ 规范校验通过` 才允许交付；不通过就改 plan JSON 重新生成。
3. **不通过时禁止手工改 .ics 文件。** 折行、转义、CRLF 手改极易破坏结构，只能回到 plan JSON 改数据再重新生成。
4. **不要重排、不要美化、不要补内容。** 脚本输出的 CRLF 换行、UTF-8 无 BOM、75 字节折行、RFC 5545 转义就是最终形态，改动只在数据层。
5. **交付时必须贴出校验结果**，让用户看到文件确实合规。
6. 唯一例外：运行环境确实没有 Python（见文末「无 Python 兜底」）。此时必须显式声明「本次未经脚本校验」，并严格照 [references/ics-spec.md](references/ics-spec.md) 的模板与清单生成。

完整的规范条款、逐条检查清单和「常见翻车点」见 [references/ics-spec.md](references/ics-spec.md)，交付前对照一遍。

## 环境准备（macOS / Windows）

命令里的 `scripts/xxx` 都相对本 skill 目录；如果当前不在该目录，换成 skill 目录的绝对路径。

| 事项 | macOS | Windows |
| --- | --- | --- |
| Python 命令 | `python3` | `py -3`（没有就用 `python`） |
| 桌面路径 | `~/Desktop` | `%USERPROFILE%\Desktop` |
| 图片 OCR 兜底 | `sh scripts/ocr_macos.sh 课表.png` | `powershell -ExecutionPolicy Bypass -File scripts\ocr_windows.ps1 课表.png` |

两份 OCR 脚本只是兜底，豆包自己能看图时直接用视觉识别，不要多跑一遍。

先确认环境（只跑一次，失败再想办法，不要反复重试）：

```bash
python3 scripts/validate_ics.py --self-test   # macOS
py -3 scripts\validate_ics.py --self-test     # Windows
```

## 第 1 步：视觉识别课表照片

用户发来教务系统课表照片（网格状：一列是星期，一行是节次）。逐条提取每门课的时间段：

| 科目 | 教学楼 | 教室编号 | 上课周数 | 星期 | 节次 |
| --- | --- | --- | --- | --- | --- |

前四列是用户要求的固定列，后两列来自同一张网格、第 3 步算日期必须用到，所以一并列出。

- 一门课一周上多个时间段 → 一个时间段一行。
- 同一门课不同周数或不同教室（例如 1-8 周在 A101、9-16 周在 B202）→ 拆成多行。
- 同一门课在连续节次（如第 1-2 节）→ 写成一行，节次填 `1-2`。
- 图片模糊、字段看不清、课表是列表式而非网格 → 列出不确定的地方并追问，不要猜。

表格下方写一句：「请核对以上信息，确认无误回复 yes，我进入第 2 步。」

### 读图方式

按这个顺序决定怎么读图，不要因为图片读不到就反复要求用户重发：

1. 能直接看图 → 直接读网格。
2. 看不到图片（当前模式不支持图片输入、图片没传过来、或需要更精确的列坐标）→ 用附件路径跑 OCR：
   - macOS：`sh scripts/ocr_macos.sh /path/to/课表.png`
   - Windows：`powershell -ExecutionPolicy Bypass -File scripts\ocr_windows.ps1 "C:\path\to\课表.png"`
 拿不到附件路径时才向用户要路径，然后继续用 OCR，不要只回一句「请把课表发过来」。

OCR 输出形如 `行 y≈226: x=26 第1节 | x=150 高等数学 | x=512 大学物理`。x 是左边界（图片宽度千分比），x 相近的属于同一列；y 越小越靠上。相邻几行、同一列的多条文字（科目 / 教学楼-教室 / 周数）属于同一个单元格；连堂单元格跨两行，中间没有分隔线。

OCR 一定会认错字（例如「星期三」认成「星期=」），按列位置和上下文判断，拿不准就问用户，不要照抄错字。OCR 首次运行可能要装/编译组件，等十几秒是正常的；失败就退回让用户直接描述，不要卡在这里。

### 硬性停顿

输出表格后必须停下，等用户明确回复 yes（或「是/对/确认」这类明确肯定）。没有明确肯定之前，不得进入第 2 步，不得写任何文件。用户在这期间发来纠错，更新表格后继续等 yes。

遇到非标准的周数写法、节次写法、教室写法，读 [references/parsing-notes.md](references/parsing-notes.md)。

## 第 2 步：确定周次锚点

1. 自己取当天日期和星期（用系统时间或 Python），不要凭记忆、不要凭对话里的假设。
2. 请用户只回两个信息：一共上几周、当前是第几周（单周还是双周）。
3. 算锚点：
   - 第 1 周周一 = 本周周一 − (当前周 − 1) × 7 天
   - 最后一周结束日 = 第 1 周周一 + 总周数 × 7 − 1 天
   - 检查当前周的奇偶与用户说的单/双周是否一致，不一致就指出并请用户确认，不要默默按一种算法继续。
4. 回显校验信息：

   > 第 1 周周一 = 2026-09-07，第 16 周结束于 2026-12-27（周日）。如果有误请指出；确认无误请上传学校的作息时间表。

## 第 3 步：解析作息时间表 → 课程 JSON

收到作息时间表后：

1. 把作息时间表解析成「第 N 节 → 开始-结束时间」。缺哪一节就问用户，不要臆造时间。表格同样可以直接看图，看不到图时用 OCR，会得到 `x=118 第1节 | x=523 08:00-08:45` 这样的分组输出。
2. 组装 plan JSON（字段说明见 [references/plan-schema.md](references/plan-schema.md)），把上面表格里的每一行翻译成一个 `courses` 条目：

   ```json
   {
     "semester": {"current_week": 3, "total_weeks": 16, "week1_monday": "2026-08-24", "parity": "单"},
     "from_date": "2026-09-11",
     "reminder_minutes": 15,
     "periods": {"1": ["08:00", "08:45"], "2": ["08:50", "09:35"], "3": ["10:00", "10:45"], "4": ["10:50", "11:35"]},
     "courses": [
       {"course": "高等数学", "building": "教1", "room": "A101", "weekday": 1, "periods": [1, 2], "weeks": "1-16"},
       {"course": "大学物理", "building": "教2", "room": "B202", "weekday": 3, "periods": [3, 4], "weeks": "1-16(双)"}
     ]
   }
   ```

   - `weekday`：1=周一 … 7=周日。
   - `weeks`：支持 `1-16`、`1-16(单)`、`1-8,10-16`、`单周`、`3-15(双)`。
   - `week1_monday` 省略时按今天和 `current_week` 反推；`parity` 省略时不校验单双周。
   - `from_date` 省略时取今天；`reminder_minutes` 省略或为 null 时不加提醒。
   - 连堂不要自己合并成 `[1,2]` 之外的写法，如实填节次即可，脚本会处理。
3. 写 plan.json 时必须用 **UTF-8 无 BOM**（Windows 上 `Out-File` 默认写出 UTF-16，会让脚本读不动；用 `[System.IO.File]::WriteAllText($p, $s, [System.Text.UTF8Encoding]::new($false))` 或直接用编辑器另存为 UTF-8）。
4. 写文件前，把 JSON 结构在对话里过一遍（课程条数、覆盖星期、周数），确认没有漏行。

## 第 4 步：Python 生成 .ics

生成前问一次是否需要提前提醒（例如提前 15 分钟）。用户不需要就不传 `--reminder-minutes`。

```bash
# macOS
python3 scripts/generate_ics.py --plan plan.json --out ~/Desktop/schedule.ics

# Windows（PowerShell 或 CMD 都可以）
py -3 scripts/generate_ics.py --plan plan.json --out "%USERPROFILE%\Desktop\schedule.ics"
```

默认输出名就是 `schedule.ics`；桌面路径不存在或被拦（OneDrive 重定向、权限不足）时，直接输出到当前工作目录下的 `schedule.ics`，不要因为路径问题改用别的方式生成。

脚本自己完成：连堂合并、单双周 RRULE、跳过已过去的日期、RFC 5545 折行与转义、CRLF、VTIMEZONE。

## 第 5 步：校验并交付

```bash
# macOS
python3 scripts/validate_ics.py ~/Desktop/schedule.ics

# Windows
py -3 scripts\validate_ics.py "%USERPROFILE%\Desktop\schedule.ics"
```

- 输出 `✅ 规范校验通过` → 交付。
- 输出 `❌` → 按提示改 plan JSON，重新生成再校验。允许重复这三步，但不允许手改 .ics。
- 需要 `--strict` 时（用户要求零警告）再加参数。

交付时报告：文件路径、事件数量、覆盖的日期范围、校验结果，并列出几条明细让用户核对。用户要原始文本时用 `--print`。

## 生成规则（脚本已实现）

- 标题 = 教室编号 + 科目，例如 `A101 高等数学`；地点 = 教学楼 + 教室编号。
- 同一天连续节次的同一门课合并成一个事件（两节连堂 08:00–09:35，而不是两个事件）。
- 单双周用 `RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=n`；周数不连续（如 1-8、10-16 周）拆成多个事件。
- 只排 `from_date` 当天及以后的课，已过去的日期不出现。
- 时区固定 Asia/Shanghai，带 VTIMEZONE 块。
- 不处理节假日和调休，按课表原样排。

## 无 Python 兜底（最后手段）

如果环境里确实装不了 Python，也要守住规范，按 [references/ics-spec.md](references/ics-spec.md) 里的模板逐条生成，并逐项自检：

1. 行尾全部 CRLF，文件末尾要有 CRLF，UTF-8 无 BOM。
2. 每行不超过 75 字节，超长按 UTF-8 边界折行，续行行首一个空格。
3. 有 `BEGIN:VCALENDAR`/`VERSION:2.0`/`PRODID`，每个事件有唯一 `UID` 和 UTC 的 `DTSTAMP`。
4. `DTSTART;TZID=Asia/Shanghai:20260907T080000` 这种基本格式，不要带 `-`、`:` 以外的 ISO 写法，不要写成 `2026-09-07 08:00:00`。
5. `SUMMARY`/`LOCATION`/`DESCRIPTION` 里的 `\` `;` `,` 换行都要转义。
6. 声明了 `TZID` 就必须带同名的 `VTIMEZONE` 块。
7. 在对话里明确写：「本次环境没有 Python，.ics 由模型手写，未经脚本校验，导入前请留意。」

## 注意

- 生成前不要重复问已经问过的问题；用户没提提醒就默认不提醒。
- 不要把 `.ics` 内容直接当最终答复贴一大段，除非用户明确要文本。
- 用户要「导出给别人」时，把 `schedule.ics` 复制成带日期的名字（如 `课表_2026-09-11.ics`）再给，内容不变。
