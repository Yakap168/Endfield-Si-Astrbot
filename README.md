# astrbot_plugin_reply_guard

让 Bot 学会**不回复**。

一个 AstrBot 插件，解决群聊里最常见的三种噪音：

1. **机器人互聊死循环** —— 两个 bot 互相 @，无限对答
2. **通知轰炸** —— 同一个人连发几条，bot 每条都接
3. **重复问题刷屏** —— 「你是谁」被十个人各问一遍，bot 答十遍

它在消息进入 LLM 之前做判断，命中规则就 `stop_event()`，不产生模型调用，也不发消息。

> 本仓库根目录是这个插件。另附两份数据：
> [persona/](persona/)——祀的人格数据包（事实卡、语气样本、知识库、术语卡，逐条标注出处），
> [model-comparison/](model-comparison/)——同一人格在不同模型下的输出对照。
> 两者与本插件没有代码依赖，许可单独说明。

---

## 功能

| 能力 | 说明 |
|---|---|
| 机器人互聊止损 | 按账号 ID 或昵称关键词识别 bot，超过 N 轮静默一段时间 |
| 按人冷却 | 同一个人 X 秒内不重复触发，**其他人不受影响** |
| 群级冷却（可选） | 防止瞬时连发，默认关闭 |
| 重复问题抑制 | 同类问题答过 N 次后，后续静默 |
| 刷屏限制 | 同一人短时间内超过 N 条则静默 |
| 群聊点名 | 必须 @ 或含指定关键词才回 |
| 结构化日志 | 每次拦截都记 `reason=...`，便于排查 |
| 提示注入（可选） | 命中关键词时按概率在用户消息前加一句提示，用于角色口头禅、态度切换等 |

---

## 安装

要求 AstrBot **4.29.0+**。三种方式任选一种，装完重启（或在插件页点重载），
插件列表里会出现 **Reply Guard**。

### 方式一：压缩包（最快）

1. 下载 [astrbot_plugin_reply_guard-0.1.0.zip](https://github.com/Yakap168/Endfield-Si-Astrbot/releases/download/v0.1.0/astrbot_plugin_reply_guard-0.1.0.zip)
   （9.2 KB，其他版本见 [Releases](https://github.com/Yakap168/Endfield-Si-Astrbot/releases)）
2. WebUI → 插件 → 右下角 `+` → **从文件安装** → 选这个 zip → 安装

包里是一个 `astrbot_plugin_reply_guard/` 目录，只有 `main.py`、`_conf_schema.json`、
`metadata.yaml`、`README.md`、`LICENSE` 五个文件。AstrBot 按 metadata 里的 `name`
安装，最终落在 `data/plugins/astrbot_plugin_reply_guard/`。

> 直接下仓库的「Download ZIP」也能装——只要压缩包顶层能找到 `metadata.yaml` 就认。
> 但那样会把 `persona/` 和 `model-comparison/` 一起带进插件目录，不介意也无妨。

### 方式二：从链接安装

WebUI → 插件 → `+` → **从链接安装** → 填：

```
https://github.com/Yakap168/Endfield-Si-Astrbot
```

输入框接受 `https://github.com/owner/repo` 或 `git@host:owner/repo.git`，
AstrBot 会 git clone 整个仓库再按 metadata 安装。之后在插件页点更新可以拉取新提交。

### 方式三：手动放文件

把这三个文件复制到 `AstrBot/data/plugins/astrbot_plugin_reply_guard/`：

```
main.py
_conf_schema.json
metadata.yaml
```

`README.md`、`LICENSE` 等附属文件放不放都行。然后重启 AstrBot。

---

## 配置

所有参数都有默认值，不配置也能用。常用项：

| 键 | 默认 | 说明 |
|---|---|---|
| `bot_ids` | `[]` | 机器人账号 ID，优先于昵称匹配 |
| `bot_name_keywords` | `["bot","机器人","助手"]` | 昵称包含即视为 bot |
| `max_bot_turns` | `1` | 机器人最多触发几次，之后静默 |
| `cooldown_sender_seconds` | `8` | 同人冷却 |
| `cooldown_group_seconds` | `0` | 全群冷却，0 关闭 |
| `repeat_similarity` | `0.6` | 重复判定阈值（字符二元组 Jaccard） |
| `repeat_skip_after` | `2` | 同类问题答过几次后静默 |
| `group_require_mention` | `true` | 群聊必须点名 |
| `name_keywords` | `[]` | 视为点名的关键词，如角色名 |
| `log_skips` | `true` | 打印拦截原因 |
| `log_identifiers` | `false` | 是否在日志里带完整会话/发送者 ID；默认只打原因 |

完整说明见 `_conf_schema.json`。

### 提示注入（nudge）

用于「让角色偶尔做出某种反应」这类需求，例如固定口头禅、特定语气。命中词后按概率在**用户消息前**追加一句括号提示：

```json
"nudge": {
  "enabled": true,
  "probability": 0.15,
  "strong_probability": 0.5,
  "cooldown_seconds": 300,
  "keywords": ["住哪", "多大"],
  "strong_keywords": ["老婆", "宝贝", "他妈的"],
  "prompt_prefix": "这次用「搞莫子」开头挡回去，别正面回答"
}
```

实测把提示加在 system prompt 末尾经常被忽略，加在用户消息前更有效。概率与冷却用来避免变成口头禅复读机。

---

## 判定顺序

```
指令前缀放行
  → 机器人连聊限制
  → 同人冷却 / 群冷却
  → 重复问题
  → 同人刷屏
  → 群聊点名
  → 进入 LLM
```

任何一步命中都会 `stop_event()`，不会产生模型调用。

---

## 日志

默认只打印拦截原因，不带任何会话或账号标识：

```
[reply_guard] skip reason=bot_loop
[reply_guard] skip reason=cooldown_sender
[reply_guard] skip reason=repeat
[reply_guard] skip reason=not_mentioned
[reply_guard] nudge injected strong=False
```

`reason` 取值：`bot_loop`、`cooldown_sender`、`cooldown_group`、`repeat`、`sender_flood`、`not_mentioned`。

需要排查具体是谁被拦时，把 `log_identifiers` 设为 `true`，日志会追加 `umo=` 与 `sender=`：

```
[reply_guard] skip reason=cooldown_sender umo=... sender=...
```

**注意**：开启后日志会包含群号与账号 ID，属于个人信息。排查完请改回 `false`，也不要把带 ID 的日志贴到公开 issue 里。

---

## 注意

- 插件以 `priority=1` 注册消息处理器，命中规则会 `stop_event()`，因此会先于其他消息处理器生效。如果你的环境里有其他插件也依赖普通消息事件，请确认这个顺序符合预期
- `group_require_mention` 依赖 AstrBot 的 `is_at_or_wake_command`；如果平台适配器不设置这个字段，需要把角色名放进 `name_keywords`
- 冷却按 `unified_msg_origin`（会话）分组，同一个人在不同群的冷却互不影响
- 重复判定会把**已回复**的消息放进窗口；被拦截的消息不参与，所以不会因为拦截而让判断越来越松
- 插件不修改模型输出，只决定「这条要不要进模型」，以及可选地加一句前缀

---

## 角色资料（`persona/`）

`persona/` 是《明日方舟：终末地》角色**祀**的人物数据包，和本插件没有代码依赖，
可以单独拿去接任何 LLM。内容全部是整理与转述，引用的游戏台词逐条标了出处。

| 目录 | 内容 | 量 |
|---|---|---|
| `system_prompt.md` | 人格提示词，7 节 | 2,094 字 |
| `begin_dialogs.json` | 开场一问一答 | 13 组 |
| `corpus/facts.json` | 事实卡，带 `source` 与 `spoiler` | 31 条 |
| `corpus/style_examples.jsonl` | 语气样本，带 `citation` | 46 条 |
| `corpus/banned_patterns.json` | 禁用模式、回复政策、兜底句 | 28 条 |
| `kb/*.md` | 知识库长文，分块带元数据头 | 6 篇 / 55 块 |
| `kb/terms/*.md` | 术语卡 | 61 张 / 103 个别名 |

任务码含义、剧透分级、引文编号口径见 [`persona/README.md`](persona/README.md)。

### 按用途取用

**只想让模型说话像她**——用 `system_prompt.md` + `begin_dialogs.json`，
整段粘进人格配置即可。提示词已经把性格、语气、边界写完了，不要再往后面堆材料，
堆得越多越容易退化成「设定复读机」。

**要回答设定问题**——`facts.json` 每条有 `use` 字段：`both`（23 条）适合放进对话上下文，
`kb`（8 条）只适合检索，塞进 prompt 会显得在背百科。

**要回答名词**——`kb/terms/` 61 张卡，配 `terms_index.json` 做别名匹配，
命中哪张注入哪张。一次注入十张以上，回复就会变成词典。

**要回答剧情**——`kb/*.md` 按块检索，每块开头的 `[类别:… | 剧透:… | 来源:…]`
就是过滤条件。先按剧透等级筛，再取内容。

**上线前加一道闸**——`banned_patterns.json` 里有 28 条正则、
`policies`（单条 ≤120 字、≤4 句、无 markdown、无 emoji）和
`postprocess`（命中后重写一次，仍不合格就走兜底句）。这部分可以直接落到
本插件的 `nudge` 或你自己的 `on_llm_request` 里。

### 三条边界

1. **剧透**：S2（十年前旧约、丹青渡结局、身世）默认不主动展开。给用户做产品时，
   建议把 `spoiler` 字段接成一个开关，而不是删掉这些内容。
2. **引用**：`citation` 字段不要删。它不是装饰，是这些内容能公开发布的前提。
3. **版权**：整理部分是 CC BY-NC-SA 4.0，游戏引文版权归鹰角网络。
   禁止商用，也禁止拿去训练或微调模型，详见 [`persona/NOTICE.md`](persona/NOTICE.md)。

### 几个容易做错的地方

- 把 61 张术语卡、55 块知识库一次性塞进 system prompt——上下文被占满，人设反而变淡
- 只取台词不取 `banned_patterns`——样本会把她教成一个只会阴阳怪气的角色
- 删掉 `spoiler` 分级直接全量上线——等于替官方剧透
- 把 `citation` 当噪声清掉——再分发时就没法说明出处了

## 开发

```
main.py            # 全部逻辑，单文件
_conf_schema.json  # WebUI 配置 schema
metadata.yaml      # 插件元数据
```

欢迎 issue / PR。改动请附上复现步骤和日志。

## License

MIT。见 [LICENSE](LICENSE)。

---

## English

A small AstrBot plugin that decides whether an incoming message should reach the LLM at all: it stops bot-to-bot loops, rate-limits per sender, suppresses repeated questions, and logs every skip with a reason. Optional `nudge` injects a one-line prompt prefix with configurable probability. Written against the AstrBot 4.29.x plugin API and checked statically; not yet verified on a live install.
