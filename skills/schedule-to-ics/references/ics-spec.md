# ICS 规范（RFC 5545）与检查清单

`.ics` 是纯文本，但它是有严格语法的格式：一处不规范，日历客户端要么整份拒绝导入，要么静默丢掉事件。
模型「凭记忆手写」的 .ics 几乎一定有问题，所以正常流程只有一条：**plan JSON → generate_ics.py → validate_ics.py**。

这份文件有两个用途：解释每条规则的来由，以及在环境没有 Python、只能手写时提供逐条自检依据。

## 一、文件级规则

| 规则 | 具体要求 | 违反后果 |
| --- | --- | --- |
| 编码 | UTF-8，**不带 BOM** | 带 BOM 时 Apple 日历、Outlook 直接报错 |
| 换行 | 每行以 CRLF（`\r\n`）结尾，**最后一行也要有** | 用 `\n`：部分客户端整份无法导入 |
| 行宽 | 每行不超过 75 字节（按 UTF-8 字节算，不是字符数） | 超长行被截断，中文描述尤其容易 |
| 折行 | 超长就折：插入 CRLF + **一个空格**，续行以空格开头 | 折错会把一个属性拆成两个 |
| 空行 | 文件内不允许空行 | 解析中断 |
| 属性格式 | `NAME;参数=值:值`，冒号前是名字和参数，冒号后是值 | 缺冒号、全角冒号都会解析失败 |
| 属性名 | 用大写（`DTSTART`、`SUMMARY`） | 大小写不敏感但部分客户端只认大写 |

## 二、VCALENDAR 层

- 整个文件只有一对 `BEGIN:VCALENDAR` / `END:VCALENDAR`，包住所有内容。
- 必须有 `VERSION:2.0` 和 `PRODID`（RFC 5545 强制）。
- 建议有 `CALSCALE:GREGORIAN`、`METHOD:PUBLISH`、`X-WR-CALNAME:课表`。
- `VERSION` / `PRODID` 要写在所有事件之前。

## 三、VTIMEZONE 层

- 只要事件里写了 `DTSTART;TZID=Asia/Shanghai:...`，文件里就必须有 `TZID:Asia/Shanghai` 的 VTIMEZONE 块，块内至少有一个 `STANDARD` 或 `DAYLIGHT` 子组件。
- 中国大陆没有夏令时，`TZOFFSETFROM` 和 `TZOFFSETTO` 都写 `+0800`。
- 顺手加 `X-LIC-LOCATION:Asia/Shanghai`，兼容性更好（非必需）。

## 四、VEVENT 层

| 属性 | 说明 |
| --- | --- |
| `UID` | 必需，全文件唯一。用「摘要+日期+节次」的哈希加域名，不要用 1、2、3 这种序号 |
| `DTSTAMP` | 必需，UTC 时间，**必须带 `Z`**，如 `20260911T040000Z` |
| `DTSTART` | 必需，基本格式 `20260907T080000`；带时区就写 `DTSTART;TZID=Asia/Shanghai:20260907T080000` |
| `DTEND` | 与 `DURATION` 二选一，且必须**晚于** `DTSTART` |
| `SUMMARY` | 事件标题；`LOCATION` 地点；`DESCRIPTION` 备注。三者都是 TEXT，要转义 |
| `SEQUENCE` | 非负整数，新建事件写 `0` |
| `TRANSP` | `OPAQUE`（占忙）比较符合上课场景 |

- 禁止 ISO 扩展格式：`2026-09-07 08:00:00`、`2026-09-07T08:00:00` 都是错的。
- `TZID=...` 和值末尾的 `Z` 不能同时出现。
- 时间段和重复规则冲突时，宁可拆成多个事件，也不要写不合法的 RRULE。

## 五、TEXT 值转义

`SUMMARY`、`LOCATION`、`DESCRIPTION`、`COMMENT`、`X-WR-CALNAME` 都是 TEXT 类型，值里的这些字符必须转义：

| 原字符 | 写法 |
| --- | --- |
| 反斜杠 `\` | `\\` |
| 分号 `;` | `\;` |
| 逗号 `,` | `\,` |
| 换行 | `\n`（字面反斜杠加 n，不是真的换行） |

标题里的逗号没转义，是课表里最常见的事故：`SUMMARY:A101 高数,习题` 会让事件名被切成两半。

## 六、RRULE（重复规则）

- `FREQ` 必须有，取值之一：`SECONDLY MINUTELY HOURLY DAILY WEEKLY MONTHLY YEARLY`。
- `COUNT` 和 `UNTIL` **不能同时出现**，客户端会直接拒绝解析。
- `COUNT`、`INTERVAL` 必须是 ≥ 1 的整数。
- `UNTIL` 如果是 DATE-TIME 形式，RFC 5545 要求必须是 UTC（`20261231T235959Z`）。
- `BYDAY` 里必须包含 `DTSTART` 所在星期，否则规则会被忽略。
- 单双周课表的标准写法：`RRULE:FREQ=WEEKLY;INTERVAL=2;COUNT=8`（每两周一次，共 8 次）。
- 周数不连续（1-8 周、10-16 周）不要硬塞进一条 RRULE，拆成两个事件更稳。

## 七、VALARM（提醒）

- 必须有 `ACTION` 和 `TRIGGER`。
- `ACTION:DISPLAY` 时必须有 `DESCRIPTION`。
- `TRIGGER` 是时长，形如 `-PT15M`（提前 15 分钟）、`-P1D`（提前一天），以 `P` 开头。
- `VALARM` 只能放在 `VEVENT` 里面。

## 八、高频翻车点对照表

| 常见错误写法 | 后果 | 正确写法 |
| --- | --- | --- |
| 全文用 `\n` 换行 | 部分客户端整份导入失败 | 全部 `\r\n` |
| `DTSTART:2026-09-07 08:00:00` | 事件丢失或时间错乱 | `DTSTART;TZID=Asia/Shanghai:20260907T080000` |
| 写了 `TZID` 却没有 `VTIMEZONE` | Outlook / Google 报错 | 补上同名 VTIMEZONE 块 |
| `SUMMARY:A101 高数,习题` | 标题被截断 | `SUMMARY:A101 高数\,习题` |
| 一行写几 KB 的描述 | 描述被截断 | 75 字节折行，续行以空格开头 |
| `UID:1`、`UID:2`，或每条事件都叫 `event@x` | 事件互相覆盖，课表只剩一半 | 用摘要+日期哈希，保证唯一 |
| `DTSTAMP:20260911T120000`（无 Z） | 导入报错或时间漂移 | `DTSTAMP:20260911T040000Z` |
| 保存成 UTF-8 BOM | 苹果日历 / Outlook 拒绝 | UTF-8 无 BOM |
| `RRULE:FREQ=WEEKLY;COUNT=8;UNTIL=...` | 解析失败 | COUNT、UNTIL 只留一个 |
| 手改 `.ics` 修问题 | 折行和转义被破坏，越改越乱 | 改 plan JSON 重新生成 |

## 九、最小合规模板

下面每一行都以 CRLF 结尾；`\n` 只出现在 DESCRIPTION 的值里（作为字面转义）。

```
BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//schedule-to-ics//CN
CALSCALE:GREGORIAN
METHOD:PUBLISH
X-WR-CALNAME:课表
BEGIN:VTIMEZONE
TZID:Asia/Shanghai
X-LIC-LOCATION:Asia/Shanghai
BEGIN:STANDARD
DTSTART:19700101T000000
TZOFFSETFROM:+0800
TZOFFSETTO:+0800
TZNAME:CST
END:STANDARD
END:VTIMEZONE
BEGIN:VEVENT
UID:<唯一哈希>@schedule-to-ics
DTSTAMP:20260911T040000Z
DTSTART;TZID=Asia/Shanghai:20260907T080000
DTEND;TZID=Asia/Shanghai:20260907T093500
RRULE:FREQ=WEEKLY;INTERVAL=1;COUNT=16
SUMMARY:A101 高等数学
LOCATION:教1 A101
DESCRIPTION:科目：高等数学\n教室：A101
SEQUENCE:0
TRANSP:OPAQUE
BEGIN:VALARM
ACTION:DISPLAY
DESCRIPTION:A101 高等数学
TRIGGER:-PT15M
END:VALARM
END:VEVENT
END:VCALENDAR
```

提示：`DTSTART;TZID=...` 这类行如果超过 75 字节才需要折行；模板里的都够短。

## 十、交付前自检（只能手写时逐条对照）

1. 文件开头没有 BOM，全文 CRLF，末尾有 CRLF。
2. 没有一行超过 75 字节；折行的续行以一个空格开头。
3. 有且只有一对 `BEGIN:VCALENDAR` / `END:VCALENDAR`，且含 `VERSION:2.0`、`PRODID`。
4. 每个 `VEVENT` 都有唯一 `UID`、带 `Z` 的 `DTSTAMP`、`DTSTART`，且 `DTEND` 晚于 `DTSTART`。
5. 所有时间都是 `YYYYMMDDTHHMMSS` 基本格式，没有 `-`、没有空格。
6. 每个用到的 `TZID` 都有同名 `VTIMEZONE`。
7. `SUMMARY` / `LOCATION` / `DESCRIPTION` 里的 `\` `;` `,` 换行都转义了。
8. 每条 `RRULE` 只有 `FREQ` 必需项，`COUNT`/`UNTIL` 不同时出现。
9. 每条 `VALARM` 有 `ACTION` 和 `TRIGGER`，`DISPLAY` 还带 `DESCRIPTION`。
