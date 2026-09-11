# schedule-to-ics（豆包电脑版工作模式版）

把**教务系统课表照片 + 学校作息时间表**变成可以导入日历的 `schedule.ics`，一个给**豆包电脑版「工作模式」**用的技能，macOS 和 Windows 都能跑。

```
课表图片
  ↓
豆包视觉识别
  ↓
SKILL.md 指导解析
  ↓
生成课程 JSON
  ↓
Python 生成 .ics
  ↓
schedule.ics
```

仓库：https://github.com/Dangdang-Ai/-ics.skill

## 这是什么

一个**技能包**（skill）：`SKILL.md` 告诉豆包怎么一步步读图、怎么追问、怎么组装 JSON；`scripts/` 里的 Python 脚本负责生成和校验 `.ics`。豆包工作模式能读写本地文件、能执行 Python，就能完整跑通这条流水线。

特点：

1. **macOS / Windows 双平台**：命令、桌面路径、OCR 兜底脚本各一套，两边都能跑。
2. **ICS 规范是硬约束**：模型不许手写 `.ics`，必须由脚本生成，生成后必须过 `validate_ics.py`，不过不许交付。
3. **看图靠豆包自己的视觉能力**：OCR 脚本只在看不了图时兜底，不依赖任何外部服务。

## 为什么必须有规范校验

模型直接手写 `.ics` 最常见的翻车点：用 `\n` 而不是 CRLF、超长行不折行、`SUMMARY` 里的逗号不转义、`DTSTART` 写成 `2026-09-07 08:00:00`、写了 `TZID` 却没有 `VTIMEZONE`、`DTSTAMP` 不带 `Z`、`UID` 重复导致事件互相覆盖。这些错误往往不报错，只会让日历静默丢事件或直接拒绝导入。

所以这个技能的做法是：

- `scripts/generate_ics.py` 负责生成，折行、转义、CRLF、时区全部由代码保证，生成后自动校验；
- `scripts/validate_ics.py` 独立做一遍 RFC 5545 检查（CRLF、75 字节折行、UTF-8 无 BOM、转义、UID 唯一、DTSTAMP UTC、TZID 有定义、BEGIN/END 配对、RRULE 合法性、VALARM 完整性），不通过就以非 0 退出码结束；
- `SKILL.md` 里写明：校验不通过时**改 JSON 重新生成**，不许手改 `.ics`。

## 目录结构

```
skills/schedule-to-ics/
├── SKILL.md                      五步流程 + ICS 规范铁律
├── references/
│   ├── parsing-notes.md          周数、节次、教室写法解析细则
│   ├── plan-schema.md            plan JSON 字段说明
│   └── ics-spec.md               RFC 5545 规范清单 + 常见翻车点
└── scripts/
    ├── generate_ics.py           由 plan JSON 生成 ICS（含自动校验）
    ├── validate_ics.py           严格校验 ICS 是否合规
    ├── ocr_macos.sh              macOS OCR 兜底（Vision）
    ├── ocr_macos.swift
    └── ocr_windows.ps1           Windows OCR 兜底（Windows.Media.Ocr）
```

## 安装到豆包电脑版

豆包电脑版各版本的技能入口不完全一样，按你能找到的入口选一种：

1. **导入技能包**：把 `skills/schedule-to-ics` 整个文件夹打包成 zip，在豆包「工作模式 → 技能 / 自定义技能」里导入。
2. **放到技能目录**：把 `skills/schedule-to-ics` 拷进豆包配置目录下的 skills 目录；`SKILL.md` 顶部带 `name` / `description` 字段，豆包靠它识别技能用途。
3. **临时使用**：让豆包工作模式打开这个文件夹，把 `SKILL.md` 的内容作为工作指令，同时让它按需执行 `scripts/` 里的脚本。

装好后，直接发课表图片，说「把这个课表导入日历」即可触发。

## 依赖

- **Python 3**：Mac 自带 `python3`；Windows 用 `py -3`（没有就用 `python`）。脚本只用标准库，不需要 pip 装任何东西。
- **OCR 兜底**（可选）：
  - macOS 需要 `swift`（装了 Xcode Command Line Tools 就有）；
  - Windows 用它自带的 `Windows.Media.Ocr`，需要中文语言包（设置 → 时间和语言 → 语言和区域 → 中文(简体) → 可选语言功能）。
  - 豆包自己能看图时**用不到** OCR。

## 三步流程（用户视角）

1. 发课表截图 → 豆包输出课程表格 → 你回 `yes`
2. 报总周数与当前第几周（单/双周）→ 豆包回显「第 1 周周一」给你核对
3. 发作息时间表 → 生成并校验 `schedule.ics`，落在桌面上

## 生成规则

- 标题 = 教室编号 + 科目；地点 = 教学楼 + 教室编号。
- 同一天连续节次的同一门课合并成一个事件（两节连堂 08:00–09:35）。
- 单双周用 `RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=n`；不连续周数拆成多个事件。
- 只排今天及以后的课，已过去的日期不出现。
- 时区固定 Asia/Shanghai，带 VTIMEZONE；不处理节假日和调休，按课表原样排。

## 自检与排错

```bash
# 校验器自检：1 个合规样板 + 12 个错误样板，确认检查项真的生效
python3 skills/schedule-to-ics/scripts/validate_ics.py --self-test

# 校验一个已有的 ics
python3 skills/schedule-to-ics/scripts/validate_ics.py ~/Desktop/schedule.ics
```

常见问题：

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `plan JSON 解析失败` | JSON 里有尾逗号，或键名少了引号 | 按提示检查 plan 文件 |
| 提示不是 UTF-8 编码 | Windows 的 `Out-File` 默认写 UTF-16 | 另存为 UTF-8 再用（脚本已容忍 BOM） |
| 导入日历后没有事件 | 文件被客户端拒绝了 | 跑 `validate_ics.py` 看具体哪条不合规 |
| `当前周与声明的单周不一致` | 周次锚点算错 | 核对「第 1 周周一」 |
| OCR 输出乱码 | 缺中文 OCR 语言包 | 装语言包，或直接让豆包看图 |

## 已知限制

- Windows 的 OCR 兜底脚本（`ocr_windows.ps1`）按 `Windows.Media.Ocr` 的接口编写，尚未在真机上验证；豆包自己能看图时走不到它，真遇到问题按脚本提示让用户描述课表内容即可。
- VTIMEZONE 用固定偏移（中国无夏令时，`+0800` 恒成立）；换成有夏令时的时区时需要自行确认偏移。
- 不识别节假日和调休，课表照原样排。
