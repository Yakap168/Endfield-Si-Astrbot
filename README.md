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

1. 把整个目录放到 `AstrBot/data/plugins/astrbot_plugin_reply_guard/`
2. 重启 AstrBot（或在 WebUI 插件页重载）
3. 在 WebUI 的插件配置里按需修改参数

要求 AstrBot **4.29.0+**。

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
