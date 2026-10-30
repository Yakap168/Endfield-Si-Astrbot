"""Reply Guard — selective, rate-limited replies for AstrBot.

Stops bot-to-bot loops, rate-limits per sender, suppresses repeated questions,
and optionally nudges the model with a one-line instruction.
"""

from __future__ import annotations

import random
import re
import time

from astrbot.api import logger, star
from astrbot.api.event import AstrMessageEvent, filter

# In-memory state is per conversation and never persisted. Idle entries are
# dropped once the table grows past STATE_MAX, so a long-running bot does not
# accumulate one record per group it has ever seen.
STATE_MAX = 512
STATE_TTL_SECONDS = 24 * 3600

DEFAULTS: dict = {
    "bot_ids": [],
    "bot_name_keywords": ["bot", "机器人", "助手"],
    "max_bot_turns": 1,
    "bot_reset_seconds": 180,
    "cooldown_sender_seconds": 8,
    "cooldown_group_seconds": 0,
    "repeat_similarity": 0.6,
    "repeat_window": 5,
    "repeat_min_len": 2,
    "repeat_skip_after": 2,
    "same_sender_limit": 4,
    "same_sender_window": 60,
    "group_require_mention": True,
    "name_keywords": [],
    "skip_prefixes": ["/", "!", "！"],
    "log_skips": True,
    "log_identifiers": False,
    "nudge": {
        "enabled": False,
        "probability": 0.15,
        "strong_probability": 0.5,
        "cooldown_seconds": 300,
        "keywords": [],
        "strong_keywords": [],
        "prompt_prefix": "",
    },
}


def _norm(text: str) -> str:
    return re.sub(r"[\s\W_]+", "", text or "")


def _bigrams(s: str) -> set[str]:
    if len(s) < 2:
        return {s} if s else set()
    return {s[i : i + 2] for i in range(len(s) - 1)}


def _similarity(a: str, b: str) -> float:
    left, right = _bigrams(a), _bigrams(b)
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


class Main(star.Star):
    """Selective reply guard for AstrBot."""

    def __init__(self, context: star.Context, config=None) -> None:
        super().__init__(context, config)
        self.config = dict(config) if config else {}
        self.state: dict[str, dict] = {}
        self.nudge_last: dict[str, float] = {}

    # ---------- config ----------

    def _cfg(self) -> dict:
        merged = dict(DEFAULTS)
        merged.update(dict(self.config or {}))
        nudge = dict(DEFAULTS["nudge"])
        nudge.update(dict((self.config or {}).get("nudge") or {}))
        merged["nudge"] = nudge
        return merged

    # ---------- state ----------

    def _st(self, umo: str) -> dict:
        st = self.state.setdefault(
            umo,
            {
                "last_reply": 0.0,
                "sender_last": {},
                "bot_turns": 0,
                "bot_last": 0.0,
                "seen": [],
                "sender": "",
                "sender_count": 0,
                "sender_first": 0.0,
                "touched": 0.0,
            },
        )
        st["touched"] = time.time()
        return st

    def _prune(self, now: float) -> None:
        """Drop idle conversations once the tables grow past their cap."""
        if len(self.state) > STATE_MAX:
            for key, st in list(self.state.items()):
                if now - st.get("touched", 0.0) > STATE_TTL_SECONDS:
                    self.state.pop(key, None)
            self._trim_oldest(self.state, lambda st: st.get("touched", 0.0))

        if len(self.nudge_last) > STATE_MAX:
            for key, ts in list(self.nudge_last.items()):
                if now - ts > STATE_TTL_SECONDS:
                    self.nudge_last.pop(key, None)
            self._trim_oldest(self.nudge_last, lambda ts: ts)

    @staticmethod
    def _trim_oldest(table: dict, stamp) -> None:
        if len(table) <= STATE_MAX:
            return
        ordered = sorted(table.items(), key=lambda kv: stamp(kv[1]))
        for key, _ in ordered[: len(ordered) - STATE_MAX]:
            table.pop(key, None)

    # ---------- helpers ----------

    @staticmethod
    def _is_bot(sender: str, name: str, cfg: dict) -> bool:
        # WebUI may store IDs as ints; compare as text so both forms match.
        bot_ids = {str(x).strip() for x in (cfg.get("bot_ids") or [])}
        if sender and sender in bot_ids:
            return True
        low = (name or "").lower()
        return any(k.lower() in low for k in cfg.get("bot_name_keywords", []))

    @staticmethod
    def _skip(
        event: AstrMessageEvent,
        reason: str,
        umo: str,
        sender: str,
        log: bool,
        identifiers: bool,
    ) -> None:
        if log:
            if identifiers:
                logger.info(
                    "[reply_guard] skip reason=%s umo=%s sender=%s", reason, umo, sender
                )
            else:
                logger.info("[reply_guard] skip reason=%s", reason)
        event.stop_event()

    # ---------- hooks ----------

    @filter.event_message_type(filter.EventMessageType.ALL, priority=1)
    async def guard(self, event: AstrMessageEvent) -> None:
        try:
            if getattr(event, "is_stopped", lambda: False)():
                return

            text = (event.get_message_str() or "").strip()
            if not text:
                return

            cfg = self._cfg()
            if any(text.startswith(p) for p in cfg.get("skip_prefixes", [])):
                return

            now = time.time()
            umo = event.unified_msg_origin
            st = self._st(umo)
            self._prune(now)
            sender = str(event.get_sender_id() or "")
            name = event.get_sender_name() or ""
            log = bool(cfg.get("log_skips", True))
            ids = bool(cfg.get("log_identifiers", False))

            is_bot = self._is_bot(sender, name, cfg)
            if not is_bot:
                st["bot_turns"] = 0

            # 1) bot-to-bot loop breaker
            if is_bot:
                if now - st["bot_last"] > cfg["bot_reset_seconds"]:
                    st["bot_turns"] = 0
                st["bot_turns"] += 1
                st["bot_last"] = now
                if st["bot_turns"] > cfg["max_bot_turns"]:
                    self._skip(event, "bot_loop", umo, sender, log, ids)
                    return

            # 2) cooldowns
            sender_cd = float(cfg.get("cooldown_sender_seconds", 8))
            group_cd = float(cfg.get("cooldown_group_seconds", 0))
            if group_cd > 0 and now - st["last_reply"] < group_cd:
                self._skip(event, "cooldown_group", umo, sender, log, ids)
                return
            if sender and now - st["sender_last"].get(sender, 0.0) < sender_cd:
                self._skip(event, "cooldown_sender", umo, sender, log, ids)
                return

            # 3) repeated questions
            norm = _norm(text)
            if len(norm) >= int(cfg.get("repeat_min_len", 2)):
                # max(1, ...) keeps window=0 from turning [-0:] into the whole history
                window = max(1, int(cfg.get("repeat_window", 5)))
                hits = sum(
                    1
                    for old in st["seen"][-window:]
                    if _similarity(norm, old) >= cfg["repeat_similarity"]
                )
                if hits >= int(cfg.get("repeat_skip_after", 2)):
                    self._skip(event, "repeat", umo, sender, log, ids)
                    return

            # 4) same sender flooding
            if sender == st["sender"]:
                if now - st["sender_first"] > cfg["same_sender_window"]:
                    st["sender_count"] = 1
                    st["sender_first"] = now
                else:
                    st["sender_count"] += 1
                    if st["sender_count"] > cfg["same_sender_limit"]:
                        self._skip(event, "sender_flood", umo, sender, log, ids)
                        return
            else:
                st["sender"] = sender
                st["sender_count"] = 1
                st["sender_first"] = now

            # 5) group mention requirement
            if (not event.is_private_chat()) and cfg.get("group_require_mention", True):
                mentioned = bool(getattr(event, "is_at_or_wake_command", False)) or any(
                    k in text for k in cfg.get("name_keywords", [])
                )
                if not mentioned:
                    self._skip(event, "not_mentioned", umo, sender, log, ids)
                    return

        except Exception:
            logger.exception("[reply_guard] failed while handling a message")

    @filter.on_llm_request()
    async def nudge(self, event: AstrMessageEvent, req) -> None:
        """Optionally prepend a one-line instruction to the user prompt."""
        try:
            cfg = self._cfg()
            nd = cfg.get("nudge") or {}
            if not nd.get("enabled") or not nd.get("prompt_prefix"):
                return

            text = event.get_message_str() or ""
            strong = any(k in text for k in nd.get("strong_keywords", []))
            if not strong and not any(k in text for k in nd.get("keywords", [])):
                return

            now = time.time()
            umo = event.unified_msg_origin
            if now - self.nudge_last.get(umo, 0.0) < float(nd.get("cooldown_seconds", 300)):
                return

            prob = float(
                nd.get("strong_probability", 0.5)
                if strong
                else nd.get("probability", 0.15)
            )
            if random.random() >= prob:
                return

            self.nudge_last[umo] = now
            req.prompt = "（" + nd["prompt_prefix"] + "）" + (req.prompt or "")
            if bool(cfg.get("log_identifiers", False)):
                logger.info("[reply_guard] nudge injected umo=%s strong=%s", umo, strong)
            else:
                logger.info("[reply_guard] nudge injected strong=%s", strong)
        except Exception:
            logger.exception("[reply_guard] failed to inject nudge")

    @filter.after_message_sent()
    async def after_sent(self, event: AstrMessageEvent) -> None:
        try:
            st = self._st(event.unified_msg_origin)
            now = time.time()
            st["last_reply"] = now
            sender = str(event.get_sender_id() or "")
            if sender:
                st["sender_last"][sender] = now
            norm = _norm(event.get_message_str() or "")
            if norm:
                st["seen"].append(norm)
                st["seen"] = st["seen"][-10:]
        except Exception:
            logger.exception("[reply_guard] failed in after_message_sent")
