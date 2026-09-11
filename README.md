# schedule-to-ics

把教务系统课表照片和学校作息时间表转换成可以导入日历的 `.ics` 文件，是一个 Codex skill。

仓库：https://github.com/Dangdang-Ai/-ics.skill

## 功能

- 从课表截图提取科目、教学楼、教室编号、上课周数、星期、节次
- 支持单双周（`1-16周(双)`）、不连续周数（`1-8,10-16周`）、两节连堂
- 按学校作息时间表推算每节课的具体日期和时间
- 输出 RFC 5545 标准的 `.ics`，时区 Asia/Shanghai，可直接导入 Apple 日历、Google 日历、Outlook
- 可选提前提醒，默认不添加

## 三步流程

1. 发课表截图 → 输出课程表格 → 等你回 `yes`
2. 报总周数与当前第几周（单/双）→ 回显"第 1 周周一"给你核对
3. 发作息时间表 → 生成 `.ics` 到桌面

## 安装

Codex 从 `$CODEX_HOME/skills`（默认 `~/.codex/skills`）加载技能，把 skill 目录拷进去即可：

```bash
mkdir -p ~/.codex/skills
cp -R skills/schedule-to-ics ~/.codex/skills/
```

或者用官方安装器：

```bash
python3 ~/.codex/skills/.system/skill-installer/scripts/install-skill-from-github.py \
  --repo Dangdang-Ai/-ics.skill --path skills/schedule-to-ics
```

## 目录结构

```
skills/schedule-to-ics/
├── SKILL.md                     流程说明
├── agents/openai.yaml           技能界面元数据
├── references/parsing-notes.md  周数、节次、教室写法解析细则
└── scripts/
    ├── generate_ics.py          由 plan JSON 生成 ICS
    ├── ocr.sh                   图片 OCR 兜底（macOS Vision）
    └── ocr_image.swift
```

## 生成规则

- 标题 = 教室编号 + 科目，地点 = 教学楼 + 教室编号
- 同一天连续节次的同一门课合并成一个事件（两节连堂 08:00–09:35）
- 单双周用 `RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=n`，不连续周数拆成多个事件
- 只排今天及以后的课，已过去的日期不出现
- 不处理节假日和调休，按课表原样排

## 依赖

- Python 3（只用标准库）
- OCR 兜底需要 macOS 和命令行工具里的 `swift`；模型本身能看图时不需要
