from __future__ import annotations

import asyncio
import base64
import hashlib
import math
import time
from datetime import datetime, timedelta
from typing import Any

from nonebot import get_bots, logger
from nonebot.adapters.onebot.v11 import Bot, MessageSegment

from .config import CACHE_DIR, DATA_DIR, Config, dump_config
from .renderer import render_status_image
from .steam_api import PlayerStatus, SteamApi
from .storage import JsonStore

PRIVATE_SCOPE_PREFIX = "private:"
STAR_GROUP_SCOPE_PREFIX = "star-group:"


PERSONA_TEXT = {
    0: "离线",
    1: "在线",
    2: "忙碌",
    3: "离开",
    4: "打盹",
    5: "想交易",
    6: "想玩游戏",
}

STAR_PERSONA_TEXT = {
    0: "离线",
    1: "上线",
    2: "忙碌",
    3: "离开",
    4: "打盹",
    5: "想交易",
    6: "想玩游戏",
}


class SteamStatusService:
    def __init__(self, config: Config):
        self.store = JsonStore(DATA_DIR)
        self.config = dump_config(config)
        self.config.update(self.store.load("config_overrides.json", {}))

        self.group_steam_ids: dict[str, list[str]] = self.store.load("steam_groups.json", {})
        self.private_steam_ids: dict[str, list[str]] = self.store.load("private_subscriptions.json", {})
        self.starred_steam_ids: dict[str, list[str]] = self.store.load("starred_players.json", {})
        self.group_last_states: dict[str, dict[str, dict[str, Any]]] = self.store.load("group_states.json", {})
        self.group_start_play_times: dict[str, dict[str, dict[str, int]]] = self.store.load("start_play_times.json", {})
        self.group_pending_quit: dict[str, dict[str, dict[str, dict[str, Any]]]] = (self.store.load("pending_quit.json", {}))
        self.play_records: dict[str, dict[str, dict[str, dict[str, Any]]]] = (self.store.load("play_records.json", {}))
        self.bind_data: dict[str, dict[str, str]] = self.store.load("bind_data.json", {})
        self.push_groups: dict[str, list[str]] = self.store.load("push_groups.json", {})
        self.notify_bots: dict[str, str] = self.store.load("notify_bots.json", {})
        self.group_flags: dict[str, dict[str, bool]] = self.store.load("group_flags.json", {})
        self.rank_push: dict[str, Any] = self.store.load("rank_push_groups.json", {"groups": [], "all": False})

        self.next_poll_time: dict[str, dict[str, int]] = {}
        self.achievement_snapshots: dict[tuple[str, str, str], set[str]] = {}
        self.achievement_tasks: dict[tuple[str, str, str], asyncio.Task] = {}
        self._achievement_blacklist: set[str] = set(self.store.load("achievement_blacklist.json", []))
        self._recorded_quit_cache: dict[tuple[str, str], float] = {}
        self._last_rank_push_day: str | None = None
        self._poll_lock = asyncio.Lock()

        self.api = self._build_api()

    def _build_api(self) -> SteamApi:
        proxy = self.config.get("proxy_url") if self.config.get("enable_proxy") else None
        return SteamApi(
            api_key=self.config.get("steam_api_key", ""),
            api_base=self.config.get("steam_api_base", "https://api.steampowered.com"),
            store_base=self.config.get("steam_store_base", "https://store.steampowered.com"),
            retry_times=int(self.config.get("retry_times", 3)),
            proxy=proxy,
        )

    def _refresh_api_config(self) -> None:
        proxy = self.config.get("proxy_url") if self.config.get("enable_proxy") else None
        self.api.update(
            api_key=self.config.get("steam_api_key", ""),
            api_base=self.config.get("steam_api_base", "https://api.steampowered.com"),
            store_base=self.config.get("steam_store_base", "https://store.steampowered.com"),
            retry_times=int(self.config.get("retry_times", 3)),
            proxy=proxy,
        )

    def save_static(self) -> None:
        self.store.save("steam_groups.json", self.group_steam_ids)
        self.store.save("private_subscriptions.json", self.private_steam_ids)
        self.store.save("starred_players.json", self.starred_steam_ids)
        self.store.save("bind_data.json", self.bind_data)
        self.store.save("push_groups.json", self.push_groups)
        self.store.save("notify_bots.json", self.notify_bots)
        self.store.save("group_flags.json", self.group_flags)
        self.store.save("rank_push_groups.json", self.rank_push)

    def save_runtime(self) -> None:
        self.store.save("group_states.json", self.group_last_states)
        self.store.save("start_play_times.json", self.group_start_play_times)
        self.store.save("pending_quit.json", self.group_pending_quit)
        self.store.save("play_records.json", self._clean_play_records())
        self.store.save("achievement_blacklist.json", sorted(self._achievement_blacklist))

    def save_config_overrides(self) -> None:
        self.store.save("config_overrides.json", self.config)
        self._refresh_api_config()

    def _clean_play_records(self) -> dict[str, dict[str, dict[str, dict[str, Any]]]]:
        cutoff = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
        self.play_records = {
            day: data for day, data in self.play_records.items() if str(day) >= cutoff
        }
        return self.play_records

    def is_group_enabled(self, group_id: str) -> bool:
        return self.group_flags.get(group_id, {}).get("monitor", False)

    @staticmethod
    def private_scope(user_id: str) -> str:
        return f"{PRIVATE_SCOPE_PREFIX}{user_id}"

    @staticmethod
    def _private_user_id(scope_id: str) -> str | None:
        if scope_id.startswith(PRIVATE_SCOPE_PREFIX):
            return scope_id.removeprefix(PRIVATE_SCOPE_PREFIX)
        return None

    @staticmethod
    def star_group_scope(group_id: str) -> str:
        return f"{STAR_GROUP_SCOPE_PREFIX}{group_id}"

    @staticmethod
    def _star_group_id(scope_id: str) -> str | None:
        if scope_id.startswith(STAR_GROUP_SCOPE_PREFIX):
            return scope_id.removeprefix(STAR_GROUP_SCOPE_PREFIX)
        return None

    def _scope_label(self, scope_id: str) -> str:
        user_id = self._private_user_id(scope_id)
        if user_id:
            return f"私聊 {user_id}"
        star_group_id = self._star_group_id(scope_id)
        return f"群 {star_group_id} Star" if star_group_id else f"群 {scope_id}"

    def set_group_enabled(self, group_id: str, enabled: bool) -> None:
        self.group_flags.setdefault(group_id, {})["monitor"] = enabled
        self.save_static()

    def is_achievement_enabled(self, group_id: str) -> bool:
        return self.group_flags.get(group_id, {}).get("achievement", True)

    def set_achievement_enabled(self, group_id: str, enabled: bool) -> None:
        self.group_flags.setdefault(group_id, {})["achievement"] = enabled
        self.save_static()

    def is_star_enabled(self, group_id: str) -> bool:
        return self.group_flags.get(group_id, {}).get("star", False)

    def set_star_enabled(self, group_id: str, enabled: bool) -> None:
        self.group_flags.setdefault(group_id, {})["star"] = enabled
        if not enabled:
            scope_id = self.star_group_scope(group_id)
            self.next_poll_time.pop(scope_id, None)
            self.group_last_states.pop(scope_id, None)
            self.save_runtime()
        self.save_static()

    def _get_push_bot(self, group_id: str) -> Bot | None:
        bots = get_bots()
        bot_id = self.notify_bots.get(group_id)
        bot = bots.get(bot_id) if bot_id else None
        if not bot and bots:
            bot = next(iter(bots.values()))
        if not bot:
            logger.warning(f"[steam_status_monitor] 无可用 Bot，无法推送群 {group_id}")
        return bot

    async def send_group_text(self, group_id: str, text: str) -> bool:
        if not text:
            return False
        bot = self._get_push_bot(group_id)
        if not bot:
            return False
        try:
            await bot.send_group_msg(group_id=int(group_id), message=MessageSegment.text(text))
            return True
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 推送群 {group_id} 文本失败: {exc}")
            return False

    async def send_group_image(self, group_id: str, image: bytes) -> bool:
        if not image:
            return False
        bot = self._get_push_bot(group_id)
        if not bot:
            return False
        try:
            image_b64 = base64.b64encode(image).decode()
            await bot.send_group_msg(
                group_id=int(group_id),
                message=MessageSegment.image(f"base64://{image_b64}"),
            )
            return True
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 推送群 {group_id} 图片失败: {exc}")
            return False

    def remember_bot(self, group_id: str, bot: Bot) -> None:
        self.notify_bots[group_id] = bot.self_id
        self.save_static()

    def _get_private_push_bot(self, user_id: str) -> Bot | None:
        bots = get_bots()
        bot_id = self.notify_bots.get(self.private_scope(user_id))
        bot = bots.get(bot_id) if bot_id else None
        if not bot and bots:
            bot = next(iter(bots.values()))
        if not bot:
            logger.warning(f"[steam_status_monitor] 无可用 Bot，无法推送私聊 {user_id}")
        return bot

    async def send_private_text(self, user_id: str, text: str) -> bool:
        if not text:
            return False
        bot = self._get_private_push_bot(user_id)
        if not bot:
            return False
        try:
            await bot.send_private_msg(
                user_id=int(user_id), message=MessageSegment.text(text)
            )
            return True
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 推送私聊 {user_id} 文本失败: {exc}")
            return False

    async def send_private_image(self, user_id: str, image: bytes) -> bool:
        if not image:
            return False
        bot = self._get_private_push_bot(user_id)
        if not bot:
            return False
        try:
            image_b64 = base64.b64encode(image).decode()
            await bot.send_private_msg(
                user_id=int(user_id),
                message=MessageSegment.image(f"base64://{image_b64}"),
            )
            return True
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 推送私聊 {user_id} 图片失败: {exc}")
            return False

    def remember_private_bot(self, user_id: str, bot: Bot) -> None:
        self.notify_bots[self.private_scope(user_id)] = bot.self_id
        self.save_static()

    def _target_groups(self, group_id: str, sid: str) -> list[str]:
        groups = [group_id]
        for gid in self.push_groups.get(sid, []):
            if gid not in groups:
                groups.append(gid)
        return groups

    def _target_scopes(self, scope_id: str, sid: str) -> list[str]:
        if self._private_user_id(scope_id):
            return [scope_id]
        return self._target_groups(scope_id, sid)

    async def send_to_targets(self, scope_id: str, sid: str, text: str) -> None:
        for target in self._target_scopes(scope_id, sid):
            user_id = self._private_user_id(target)
            if user_id:
                await self.send_private_text(user_id, text)
            else:
                await self.send_group_text(target, text)

    async def send_image_to_targets(self, scope_id: str, sid: str, image: bytes) -> bool:
        ok = False
        for target in self._target_scopes(scope_id, sid):
            user_id = self._private_user_id(target)
            if user_id:
                ok = await self.send_private_image(user_id, image) or ok
            else:
                ok = await self.send_group_image(target, image) or ok
        return ok

    def display_name(self, sid: str, steam_name: str | None = None) -> str:
        for info in self.bind_data.values():
            if info.get("sid") == sid:
                nickname = info.get("nickname")
                if nickname and nickname != "*":
                    return nickname
        return steam_name or sid

    def bind_qq(self, qq: str, sid: str, nickname: str | None = None) -> None:
        self.bind_data[str(qq)] = {"sid": sid, "nickname": nickname or "*"}
        self.save_static()

    async def add_steam_ids(
        self, group_id: str, raw_values: list[str]
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        resolved: list[str] = []
        invalid: list[str] = []
        for raw in raw_values:
            sid = await self.api.resolve_steam_input(raw)
            if sid and sid.isdigit() and len(sid) == 17:
                if sid not in resolved:
                    resolved.append(sid)
            else:
                invalid.append(raw)

        ids = self.group_steam_ids.setdefault(group_id, [])
        added: list[str] = []
        linked: list[str] = []
        already: list[str] = []
        limit = int(self.config.get("max_group_size", 20))
        for sid in resolved:
            if sid in ids:
                already.append(sid)
                continue

            owner_groups = [
                gid for gid, group_ids in self.group_steam_ids.items()
                if gid != group_id and sid in group_ids
            ]
            if owner_groups:
                push_ids = self.push_groups.setdefault(sid, [])
                if group_id not in push_ids:
                    push_ids.append(group_id)
                    linked.append(sid)
                else:
                    already.append(sid)
                continue

            if len(ids) < limit:
                ids.append(sid)
                added.append(sid)

        self.save_static()
        return added, linked, already, invalid

    async def star_steam_ids(
        self, group_id: str, raw_values: list[str]
    ) -> tuple[list[str], list[str], list[str], list[str], list[str]]:
        added, linked, already, invalid = await self.add_steam_ids(group_id, raw_values)
        monitored = list(dict.fromkeys([*added, *linked, *already]))
        group_starred = self.starred_steam_ids.setdefault(group_id, [])
        starred: list[str] = []
        already_starred: list[str] = []
        for sid in monitored:
            if sid in group_starred:
                already_starred.append(sid)
            else:
                group_starred.append(sid)
                starred.append(sid)

        if not group_starred:
            self.starred_steam_ids.pop(group_id, None)
        self.save_static()
        return starred, already_starred, added, linked, invalid

    async def star_private_steam_ids(
        self, user_id: str, raw_values: list[str], bot: Bot
    ) -> tuple[list[str], list[str], list[str], list[str]]:
        added, already, invalid = await self.subscribe_private(user_id, raw_values, bot)
        scope_id = self.private_scope(str(user_id))
        monitored = list(dict.fromkeys([*added, *already]))
        scope_starred = self.starred_steam_ids.setdefault(scope_id, [])
        starred: list[str] = []
        already_starred: list[str] = []
        for sid in monitored:
            if sid in scope_starred:
                already_starred.append(sid)
            else:
                scope_starred.append(sid)
                starred.append(sid)

        if not scope_starred:
            self.starred_steam_ids.pop(scope_id, None)
        self.save_static()
        return starred, already_starred, added, invalid

    async def subscribe_private(
        self, user_id: str, raw_values: list[str], bot: Bot
    ) -> tuple[list[str], list[str], list[str]]:
        resolved: list[str] = []
        invalid: list[str] = []
        for raw in raw_values:
            sid = await self.api.resolve_steam_input(raw)
            if sid and sid.isdigit() and len(sid) == 17:
                if sid not in resolved:
                    resolved.append(sid)
            else:
                invalid.append(raw)

        user_id = str(user_id)
        ids = self.private_steam_ids.setdefault(user_id, [])
        added: list[str] = []
        already: list[str] = []
        limit = int(self.config.get("max_group_size", 20))
        for sid in resolved:
            if sid in ids:
                already.append(sid)
            elif len(ids) < limit:
                ids.append(sid)
                added.append(sid)

        scope_id = self.private_scope(user_id)
        if ids:
            self.remember_private_bot(user_id, bot)
            for sid in added:
                self.next_poll_time.setdefault(scope_id, {}).pop(sid, None)
        else:
            self.private_steam_ids.pop(user_id, None)
        return added, already, invalid

    async def unsubscribe_private(
        self, user_id: str, raw_values: list[str]
    ) -> tuple[list[str], list[str], list[str]]:
        resolved: list[str] = []
        invalid: list[str] = []
        for raw in raw_values:
            sid = await self.api.resolve_steam_input(raw)
            if sid and sid.isdigit() and len(sid) == 17:
                if sid not in resolved:
                    resolved.append(sid)
            else:
                invalid.append(raw)

        user_id = str(user_id)
        scope_id = self.private_scope(user_id)
        ids = self.private_steam_ids.get(user_id, [])
        removed: list[str] = []
        missing: list[str] = []
        for sid in resolved:
            if sid in ids:
                ids.remove(sid)
                removed.append(sid)
                self._remove_star(scope_id, sid)
                self._clear_scope_sid(scope_id, sid)
            else:
                missing.append(sid)

        if ids:
            self.private_steam_ids[user_id] = ids
        else:
            self.private_steam_ids.pop(user_id, None)
            self.notify_bots.pop(scope_id, None)
        self.save_static()
        self.save_runtime()
        return removed, missing, invalid

    def _clear_scope_sid(self, scope_id: str, sid: str) -> None:
        self.next_poll_time.get(scope_id, {}).pop(sid, None)
        self.group_last_states.get(scope_id, {}).pop(sid, None)
        self.group_start_play_times.get(scope_id, {}).pop(sid, None)
        self.group_pending_quit.get(scope_id, {}).pop(sid, None)
        for key, task in list(self.achievement_tasks.items()):
            if key[:2] == (scope_id, sid):
                task.cancel()
                self.achievement_tasks.pop(key, None)
                self.achievement_snapshots.pop(key, None)

    def _remove_star(self, scope_id: str, sid: str) -> None:
        starred = self.starred_steam_ids.get(scope_id, [])
        if sid in starred:
            starred.remove(sid)
        if not starred:
            self.starred_steam_ids.pop(scope_id, None)
        if self._private_user_id(scope_id) is None:
            self._clear_scope_sid(self.star_group_scope(scope_id), sid)

    def clear_group_star(self, group_id: str) -> None:
        self.starred_steam_ids.pop(group_id, None)
        scope_id = self.star_group_scope(group_id)
        self.next_poll_time.pop(scope_id, None)
        self.group_last_states.pop(scope_id, None)

    def clear_group_stars(self) -> None:
        for scope_id in list(self.starred_steam_ids):
            if self._private_user_id(scope_id) is None:
                self.clear_group_star(scope_id)

    async def delete_steam_id(self, group_id: str, raw_value: str) -> tuple[bool, str]:
        sid = await self.api.resolve_steam_input(raw_value)
        if not sid:
            return False, "无法解析为有效 SteamID。"
        ids = self.group_steam_ids.get(group_id, [])
        if sid in ids:
            ids.remove(sid)
            self.group_steam_ids[group_id] = ids
            self._remove_star(group_id, sid)
            for qq, info in list(self.bind_data.items()):
                if info.get("sid") == sid:
                    self.bind_data.pop(qq, None)
            self.save_static()
            return True, f"已删除本群主监控：{sid}"

        if sid in self.push_groups and group_id in self.push_groups[sid]:
            self.push_groups[sid].remove(group_id)
            self._remove_star(group_id, sid)
            if not self.push_groups[sid]:
                self.push_groups.pop(sid, None)
            self.save_static()
            return True, f"已取消本群联动推送：{sid}"

        return False, f"SteamID {sid} 不在群 {group_id} 的主监控或联动推送中。"

    async def start_group(self, group_id: str, bot: Bot) -> str:
        if not self.config.get("steam_api_key"):
            return "未配置 steam_api_key，请先在 .env 或 /steam set steam_api_key 中配置。"
        ids = self.group_steam_ids.get(group_id, [])
        if not ids:
            return "本群还没有监控任何 SteamID，请先使用 /steam add 添加。"

        self.remember_bot(group_id, bot)
        self.set_group_enabled(group_id, True)
        self.group_last_states.setdefault(group_id, {})
        self.group_start_play_times.setdefault(group_id, {})
        status_map = await self.api.fetch_player_statuses(ids)
        now = int(time.time())
        for sid, status in status_map.items():
            self.group_last_states[group_id][sid] = status.to_json()
            if status.gameid:
                self.group_start_play_times[group_id].setdefault(sid, {})[status.gameid] = now
                self.next_poll_time.setdefault(group_id, {})[sid] = now + 60
        self.save_runtime()
        return "本群 Steam 状态监控已启动。"

    def stop_group(self, group_id: str) -> str:
        self.set_group_enabled(group_id, False)
        self.next_poll_time.pop(group_id, None)
        self.group_pending_quit.pop(group_id, None)
        for key, task in list(self.achievement_tasks.items()):
            if key[0] == group_id:
                task.cancel()
                self.achievement_tasks.pop(key, None)
                self.achievement_snapshots.pop(key, None)
        self.save_runtime()
        return "本群 Steam 状态监控已关闭。"

    async def poll_due(self) -> None:
        if not self.config.get("steam_api_key"):
            return
        async with self._poll_lock:
            await self._maybe_push_daily_rank()
            now = int(time.time())
            scope_sids: dict[str, list[str]] = {}
            all_sids: set[str] = set()
            for group_id, ids in self.group_steam_ids.items():
                if not self.is_group_enabled(group_id):
                    continue
                due = [
                    sid
                    for sid in ids
                    if now >= self.next_poll_time.setdefault(group_id, {}).get(sid, 0)
                ]
                if due:
                    scope_sids[group_id] = due
                    all_sids.update(due)

            for user_id, ids in self.private_steam_ids.items():
                scope_id = self.private_scope(user_id)
                due = [
                    sid
                    for sid in ids
                    if now >= self.next_poll_time.setdefault(scope_id, {}).get(sid, 0)
                ]
                if due:
                    scope_sids[scope_id] = due
                    all_sids.update(due)

            for group_id, ids in self.starred_steam_ids.items():
                if self._private_user_id(group_id) or not self.is_star_enabled(group_id):
                    continue
                scope_id = self.star_group_scope(group_id)
                due = [
                    sid
                    for sid in ids
                    if now >= self.next_poll_time.setdefault(scope_id, {}).get(sid, 0)
                ]
                if due:
                    scope_sids[scope_id] = due
                    all_sids.update(due)

            if not scope_sids:
                await self._finalize_due_quits()
                return

            status_map = await self.api.fetch_player_statuses(list(all_sids))
            logs: list[str] = []
            for scope_id, ids in scope_sids.items():
                for sid in ids:
                    if self._star_group_id(scope_id):
                        line = await self._process_star_group_status(
                            scope_id, sid, status_map.get(sid)
                        )
                    else:
                        line = await self._process_status(scope_id, sid, status_map.get(sid))
                    if line:
                        logs.append(f"{self._scope_label(scope_id)}: {line}")
            await self._finalize_due_quits()
            self.save_runtime()
            if logs and self.config.get("detailed_poll_log", True):
                logger.info("[steam_status_monitor] 轮询完成\n" + "\n".join(logs))

    async def _process_star_group_status(
        self, scope_id: str, sid: str, status: PlayerStatus | None
    ) -> str | None:
        if not status:
            self._schedule_next_poll(scope_id, sid, None)
            return f"{sid} 状态获取失败"

        states = self.group_last_states.setdefault(scope_id, {})
        prev = states.get(sid)
        player_name = self.display_name(sid, status.name)
        if prev and int(prev.get("personastate") or 0) != status.personastate:
            await self._send_starred_persona_change(
                scope_id, sid, player_name, status.personastate
            )
        states[sid] = status.to_json()
        self._schedule_next_poll(scope_id, sid, status)
        return self._status_line(
            player_name, status, status.gameextrainfo or "未知游戏"
        )

    async def _process_status(
        self, group_id: str, sid: str, status: PlayerStatus | None
    ) -> str | None:
        now = int(time.time())
        if not status:
            self._schedule_next_poll(group_id, sid, None)
            return f"{sid} 状态获取失败"

        states = self.group_last_states.setdefault(group_id, {})
        start_times = self.group_start_play_times.setdefault(group_id, {})
        pending = self.group_pending_quit.setdefault(group_id, {}).setdefault(sid, {})
        prev = states.get(sid)
        prev_gameid = prev.get("gameid") if prev else None
        current_gameid = status.gameid
        player_name = self.display_name(sid, status.name)
        zh_game_name, _ = await self.api.get_game_names(current_gameid, status.gameextrainfo)

        if not prev:
            states[sid] = status.to_json()
            if current_gameid:
                start_times.setdefault(sid, {})[current_gameid] = now
            self._schedule_next_poll(group_id, sid, status)
            return self._status_line(player_name, status, zh_game_name)

        previous_persona = int(prev.get("personastate") or 0)
        if (
            self._private_user_id(group_id)
            and previous_persona != status.personastate
        ):
            await self._send_starred_persona_change(
                group_id, sid, player_name, status.personastate
            )

        ended_games: list[tuple[str, float]] = []
        if prev_gameid and prev_gameid != current_gameid:
            prev_name = prev.get("gameextrainfo") or "未知游戏"
            zh_prev_name, _ = await self.api.get_game_names(prev_gameid, prev_name)
            start_time = self._get_start_time(group_id, sid, prev_gameid) or now
            quit_info = {
                "quit_time": now,
                "name": player_name,
                "game_name": zh_prev_name,
                "duration_min": max(0, (now - start_time) / 60),
                "start_time": start_time,
                "avatar_url": prev.get("avatarfull") or prev.get("avatar"),
                "gameid": prev_gameid,
                "notified": False,
            }
            await self._stop_achievement_task(group_id, sid, prev_gameid, final_check=True)
            if current_gameid:
                ended_games.append(self._record_quit(sid, prev_gameid, quit_info))
            else:
                pending[prev_gameid] = quit_info

        if current_gameid and current_gameid != prev_gameid:
            wave = pending.get(current_gameid)
            if wave and now - int(wave.get("quit_time", 0)) <= 180 and not wave.get("notified"):
                pending.pop(current_gameid, None)
            else:
                for pending_gameid, info in list(pending.items()):
                    if not info.get("notified"):
                        ended_games.append(self._record_quit(sid, pending_gameid, info))
                    pending.pop(pending_gameid, None)
                start_times.setdefault(sid, {})[current_gameid] = now
                if not self._should_skip_game(current_gameid):
                    if ended_games and self.config.get("enable_game_end_notify", True):
                        await self._send_switch_message(
                            group_id,
                            sid,
                            player_name,
                            ended_games,
                            zh_game_name,
                            avatar_url=status.avatarfull or status.avatar,
                            new_gameid=current_gameid,
                        )
                    else:
                        await self._send_start_message(
                            group_id,
                            sid,
                            player_name,
                            zh_game_name,
                            avatar_url=status.avatarfull or status.avatar,
                            gameid=current_gameid,
                        )
                    await self._start_achievement_task(group_id, sid, current_gameid, player_name, zh_game_name)
                elif ended_games and self.config.get("enable_game_end_notify", True):
                    for game_name, duration in ended_games:
                        await self._send_end_message(
                            group_id,
                            sid,
                            player_name,
                            game_name,
                            duration,
                            avatar_url=status.avatarfull or status.avatar,
                        )

        states[sid] = status.to_json()
        self._schedule_next_poll(group_id, sid, status)
        return self._status_line(player_name, status, zh_game_name)

    def _get_start_time(self, group_id: str, sid: str, gameid: str) -> int | None:
        value = self.group_start_play_times.get(group_id, {}).get(sid)
        if isinstance(value, dict):
            found = value.get(gameid)
            return int(found) if found else None
        if value:
            return int(value)
        return None

    def _schedule_next_poll(
        self, group_id: str, sid: str, status: PlayerStatus | None
    ) -> None:
        interval = self._poll_interval(status)
        now = int(time.time())
        interval_min = max(1, interval // 60)
        next_time = ((now // 60) + math.ceil(interval_min)) * 60
        self.next_poll_time.setdefault(group_id, {})[sid] = next_time

    def _poll_interval(self, status: PlayerStatus | None) -> int:
        fixed = int(self.config.get("fixed_poll_interval") or 0)
        if fixed > 0:
            return fixed
        intervals = self._smart_intervals()
        if not status:
            return intervals[-1] * 60
        if status.gameid:
            return intervals[0] * 60
        if status.personastate > 0:
            return intervals[1] * 60
        if status.lastlogoff:
            minutes_ago = (int(time.time()) - status.lastlogoff) / 60
            if minutes_ago <= 12:
                return intervals[1] * 60
            if minutes_ago <= 180:
                return intervals[2] * 60
            if minutes_ago <= 1440:
                return intervals[3] * 60
            if minutes_ago <= 2880:
                return intervals[4] * 60
        return intervals[5] * 60

    def _smart_intervals(self) -> list[int]:
        raw = self.config.get("smart_poll_intervals", "1,3,5,10,20,30")
        try:
            values = [int(x.strip()) for x in str(raw).split(",") if x.strip()]
            if len(values) == 6 and all(v > 0 for v in values):
                return values
        except Exception:
            pass
        return [1, 3, 5, 10, 20, 30]

    def _status_line(self, name: str, status: PlayerStatus, game_name: str) -> str:
        if status.gameid:
            return f"{name} 正在玩 {game_name}"
        if status.personastate > 0:
            return f"{name} {PERSONA_TEXT.get(status.personastate, '在线')}"
        if status.lastlogoff:
            hours = (int(time.time()) - status.lastlogoff) / 3600
            return f"{name} 离线，上次在线 {hours:.1f} 小时前"
        return f"{name} 离线"

    async def _send_starred_persona_change(
        self, scope_id: str, sid: str, player_name: str, personastate: int
    ) -> None:
        state_text = STAR_PERSONA_TEXT.get(personastate, "状态发生变化")
        text = f"【Steam 状态】{player_name} {state_text}"
        user_id = self._private_user_id(scope_id)
        if user_id:
            if sid in self.starred_steam_ids.get(scope_id, []):
                await self.send_private_text(user_id, text)
            return

        group_id = self._star_group_id(scope_id)
        if (
            group_id
            and self.is_star_enabled(group_id)
            and sid in self.starred_steam_ids.get(group_id, [])
        ):
            await self.send_group_text(group_id, text)

    def _record_quit(self, sid: str, gameid: str, info: dict[str, Any]) -> tuple[str, float]:
        info["notified"] = True
        duration = float(info.get("duration_min") or 0)
        game_name = info.get("game_name") or "未知游戏"
        self._record_playtime(sid, gameid, game_name, duration)
        return game_name, duration

    async def _send_status_image(self, group_id: str, sid: str, **kwargs: Any) -> None:
        try:
            image = render_status_image(**kwargs)
        except Exception as exc:
            logger.warning(f"[steam_monitor] 渲染状态图片失败: {exc}")
            return
        if not await self.send_image_to_targets(group_id, sid, image):
            logger.warning(f"[steam_monitor] 状态图片未成功推送到任何目标 sid={sid}")

    async def _cached_image(self, cache_type: str, key: str, url: str | None) -> bytes | None:
        if not url:
            return None
        digest = hashlib.sha1(url.encode("utf-8")).hexdigest()[:12]
        suffix = url.rsplit(".", 1)[-1].split("?", 1)[0].lower()
        if suffix not in {"jpg", "jpeg", "png", "webp"}:
            suffix = "img"
        path = CACHE_DIR / "images" / cache_type / f"{key}_{digest}.{suffix}"
        if path.exists():
            return path.read_bytes()
        data = await self.api.download_image(url)
        if not data:
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return data

    async def _game_cover_image(self, gameid: str | None, game_name: str) -> bytes | None:
        cover_url = await self.api.get_sgdb_vertical_cover_url(
            game_name,
            self.config.get("sgdb_api_key"),
            appid=gameid,
        )
        return await self._cached_image("game_covers_v", gameid or "unknown", cover_url)

    async def _send_start_message(
        self,
        group_id: str,
        sid: str,
        player_name: str,
        game_name: str,
        *,
        avatar_url: str | None = None,
        gameid: str | None = None,
    ) -> None:
        avatar_image = await self._cached_image("avatars", sid, avatar_url)
        game_cover_image = await self._game_cover_image(gameid, game_name)
        await self._send_status_image(
            group_id,
            sid,
            kind="start",
            player_name=player_name,
            game_name=game_name,
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )

    async def _send_end_message(
        self,
        group_id: str,
        sid: str,
        player_name: str,
        game_name: str,
        duration: float,
        *,
        avatar_url: str | None = None,
        gameid: str | None = None,
    ) -> None:
        avatar_image = await self._cached_image("avatars", sid, avatar_url)
        game_cover_image = await self._game_cover_image(gameid, game_name)
        await self._send_status_image(
            group_id,
            sid,
            kind="end",
            player_name=player_name,
            game_name=game_name,
            duration_min=duration,
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )

    async def _send_switch_message(
        self,
        group_id: str,
        sid: str,
        player_name: str,
        ended_games: list[tuple[str, float]],
        new_game_name: str,
        *,
        avatar_url: str | None = None,
        new_gameid: str | None = None,
    ) -> None:
        avatar_image = await self._cached_image("avatars", sid, avatar_url)
        game_cover_image = await self._game_cover_image(new_gameid, new_game_name)
        await self._send_status_image(
            group_id,
            sid,
            kind="switch",
            player_name=player_name,
            new_game_name=new_game_name,
            ended_games=ended_games,
            avatar_image=avatar_image,
            game_cover_image=game_cover_image,
        )

    async def _finalize_quit(
        self, group_id: str, sid: str, gameid: str, info: dict[str, Any]
    ) -> None:
        player_name = info.get("name", sid)
        game_name, duration = self._record_quit(sid, gameid, info)
        if self.config.get("enable_game_end_notify", True):
            await self._send_end_message(
                group_id,
                sid,
                player_name,
                game_name,
                duration,
                avatar_url=info.get("avatar_url"),
                gameid=gameid,
            )

    async def _finalize_due_quits(self) -> None:
        now = int(time.time())
        changed = False
        for group_id, by_sid in list(self.group_pending_quit.items()):
            for sid, by_game in list(by_sid.items()):
                for gameid, info in list(by_game.items()):
                    if info.get("notified") or now - int(info.get("quit_time", 0)) < 180:
                        continue
                    await self._finalize_quit(group_id, sid, gameid, info)
                    by_game.pop(gameid, None)
                    changed = True
                if not by_game:
                    by_sid.pop(sid, None)
            if not by_sid:
                self.group_pending_quit.pop(group_id, None)
        if changed:
            self.save_runtime()

    def _record_playtime(self, sid: str, gameid: str, game_name: str, duration_min: float) -> None:
        if duration_min <= 0 or not gameid:
            return
        key = (sid, gameid)
        now = time.time()
        if now - self._recorded_quit_cache.get(key, 0) < 300:
            return
        self._recorded_quit_cache[key] = now
        day = self._day_key()
        self.play_records.setdefault(day, {}).setdefault(sid, {}).setdefault(
            gameid, {"name": game_name, "minutes": 0}
        )
        record = self.play_records[day][sid][gameid]
        record["name"] = game_name
        record["minutes"] = int(record.get("minutes") or 0) + int(duration_min)

    def _day_key(self, offset_days: int = 0) -> str:
        now = datetime.now()
        if now.hour < 4:
            now -= timedelta(days=1)
        now += timedelta(days=offset_days)
        return now.strftime("%Y-%m-%d")

    def _format_minutes(self, minutes: float) -> str:
        return f"{minutes:.1f} 分钟" if minutes < 60 else f"{minutes / 60:.1f} 小时"

    def _should_skip_game(self, gameid: str | None) -> bool:
        if not gameid:
            return False
        ids = [x.strip() for x in str(self.config.get("game_filter_ids", "")).split(",") if x.strip()]
        if not ids:
            return False
        mode = self.config.get("game_filter_mode", "全部游戏")
        if mode == "白名单":
            return str(gameid) not in ids
        if mode == "黑名单":
            return str(gameid) in ids
        return False

    async def _start_achievement_task(
        self, group_id: str, sid: str, gameid: str, player_name: str, game_name: str
    ) -> None:
        if not self.config.get("enable_achievement_poll", True):
            return
        if not self.is_achievement_enabled(group_id) or gameid in self._achievement_blacklist:
            return
        key = (group_id, sid, gameid)
        old = self.achievement_tasks.pop(key, None)
        if old:
            old.cancel()
        snapshot = await self.api.get_player_achievements(sid, gameid)
        if snapshot is None:
            self._achievement_blacklist.add(gameid)
            return
        self.achievement_snapshots[key] = snapshot
        task = asyncio.create_task(self._achievement_loop(group_id, sid, gameid, player_name, game_name))
        self.achievement_tasks[key] = task

    async def _stop_achievement_task(
        self, group_id: str, sid: str, gameid: str, *, final_check: bool
    ) -> None:
        key = (group_id, sid, gameid)
        task = self.achievement_tasks.pop(key, None)
        if task:
            task.cancel()
        if final_check and key in self.achievement_snapshots:
            asyncio.create_task(self._achievement_final_check(group_id, sid, gameid))
        else:
            self.achievement_snapshots.pop(key, None)

    async def _achievement_loop(
        self, group_id: str, sid: str, gameid: str, player_name: str, game_name: str
    ) -> None:
        try:
            while True:
                await asyncio.sleep(1200)
                await self._check_achievements(group_id, sid, gameid, player_name, game_name)
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 成就轮询异常: {exc}")

    async def _achievement_final_check(self, group_id: str, sid: str, gameid: str) -> None:
        await asyncio.sleep(300)
        key = (group_id, sid, gameid)
        state = self.group_last_states.get(group_id, {}).get(sid, {})
        player_name = self.display_name(sid, state.get("name") or sid)
        game_name, _ = await self.api.get_game_names(gameid, state.get("gameextrainfo"))
        await self._check_achievements(group_id, sid, gameid, player_name, game_name)
        self.achievement_snapshots.pop(key, None)

    async def _check_achievements(
        self, group_id: str, sid: str, gameid: str, player_name: str, game_name: str
    ) -> None:
        if gameid in self._achievement_blacklist or not self.is_achievement_enabled(group_id
        ):
            return
        key = (group_id, sid, gameid)
        before = self.achievement_snapshots.get(key)
        after = await self.api.get_player_achievements(sid, gameid)
        if after is None:
            return
        if before is None:
            self.achievement_snapshots[key] = after
            return
        new_items = after - before
        if not new_items:
            self.achievement_snapshots[key] = after
            return
        names = await self.api.get_achievement_names(gameid)
        limit = int(self.config.get("max_achievement_notifications", 5))
        shown = list(new_items)[:limit]
        lines = [f"【Steam 成就】{player_name} 在 {game_name} 解锁了新成就："]
        lines.extend(f"- {names.get(item, item)}" for item in shown)
        if len(new_items) > limit:
            lines.append(f"另有 {len(new_items) - limit} 个成就。")
        await self.send_to_targets(group_id, sid, "\n".join(lines))
        self.achievement_snapshots[key] = after

    async def list_group_status(self, group_id: str) -> str:
        ids = self.group_steam_ids.get(group_id, [])
        if not ids:
            return "本群还没有监控任何 SteamID。"
        status_map = await self.api.fetch_player_statuses(ids)
        lines = [f"Steam 状态列表（群 {group_id}）"]
        for sid in ids:
            status = status_map.get(sid)
            if not status:
                lines.append(f"- {sid}: 获取失败")
                continue
            name = self.display_name(sid, status.name)
            game_name, _ = await self.api.get_game_names(status.gameid, status.gameextrainfo)
            extra = ""
            if status.gameid:
                start = self._get_start_time(group_id, sid, status.gameid)
                if start:
                    extra = f"，已玩 {self._format_minutes((time.time() - start) / 60)}"
            lines.append(f"- {self._status_line(name, status, game_name)}{extra}")
        return "\n".join(lines)

    async def list_private_status(self, user_id: str) -> str:
        user_id = str(user_id)
        scope_id = self.private_scope(user_id)
        ids = self.private_steam_ids.get(user_id, [])
        if not ids:
            return "你还没有个人监控，请使用 /steam add 添加。"
        status_map = await self.api.fetch_player_statuses(ids)
        lines = ["Steam 状态列表（个人）"]
        for sid in ids:
            status = status_map.get(sid)
            if not status:
                lines.append(f"- {sid}: 获取失败")
                continue
            name = self.display_name(sid, status.name)
            game_name, _ = await self.api.get_game_names(status.gameid, status.gameextrainfo)
            extra = ""
            if status.gameid:
                start = self._get_start_time(scope_id, sid, status.gameid)
                if start:
                    extra = f"，已玩 {self._format_minutes((time.time() - start) / 60)}"
            lines.append(f"- {self._status_line(name, status, game_name)}{extra}")
        return "\n".join(lines)

    async def openbox(self, raw_value: str) -> str:
        sid = await self.api.resolve_steam_input(raw_value)
        if not sid:
            return "无法解析为有效 SteamID。"
        status = await self.api.fetch_player_status(sid)
        if not status:
            return f"无法获取 {sid} 的 Steam 状态。"
        game_name, _ = await self.api.get_game_names(status.gameid, status.gameextrainfo)
        lines = [
            f"SteamID: {sid}",
            f"昵称: {status.name or '-'}",
            f"状态: {self._status_line(status.name or sid, status, game_name)}",
            f"当前游戏ID: {status.gameid or '-'}",
            f"当前游戏: {game_name if status.gameid else '-'}",
        ]
        if status.lastlogoff:
            lines.append(datetime.fromtimestamp(status.lastlogoff).strftime("上次在线: %Y-%m-%d %H:%M:%S"))
        if status.avatarfull:
            lines.append(f"头像: {status.avatarfull}")
        return "\n".join(lines)

    def rank_data(self, days: int, group_id: str | None = None, offset: int = 0) -> list[dict[str, Any]]:
        base = datetime.strptime(self._day_key(offset), "%Y-%m-%d")
        date_keys = [(base - timedelta(days=i)).strftime("%Y-%m-%d") for i in range(days)]
        if group_id:
            target = set(self.group_steam_ids.get(group_id, []))
        else:
            target = {sid for ids in self.group_steam_ids.values() for sid in ids}
        merged: dict[str, dict[str, dict[str, Any]]] = {}
        for day in date_keys:
            for sid, games in self.play_records.get(day, {}).items():
                if sid not in target:
                    continue
                for gameid, info in games.items():
                    merged.setdefault(sid, {}).setdefault(
                        gameid, {"name": info.get("name") or "未知游戏", "minutes": 0}
                    )
                    merged[sid][gameid]["minutes"] += int(info.get("minutes") or 0)
        rows: list[dict[str, Any]] = []
        for sid, games in merged.items():
            game_rows = sorted(games.values(), key=lambda x: x["minutes"], reverse=True)
            total = sum(item["minutes"] for item in game_rows)
            if total > 0:
                rows.append({"sid": sid, "total_minutes": total, "games": game_rows})
        rows.sort(key=lambda x: x["total_minutes"], reverse=True)
        return rows

    async def rank_text(self, days: int, group_id: str | None = None, label: str = "今日", offset: int = 0) -> str:
        rows = self.rank_data(days, group_id, offset)
        if not rows:
            return f"暂无{label}游玩记录，游戏结束后才会计入排行榜。"
        status_map = await self.api.fetch_player_statuses([row["sid"] for row in rows[:20]])
        lines = [f"Steam 游戏时长排行榜（{label}）"]
        for idx, row in enumerate(rows[:20], start=1):
            status = status_map.get(row["sid"])
            name = self.display_name(row["sid"], status.name if status else row["sid"][-8:])
            top_games = "、".join(
                f"{g['name']} {self._format_minutes(g['minutes'])}" for g in row["games"][:3]
            )
            lines.append(
                f"{idx}. {name} - {self._format_minutes(row['total_minutes'])}\n   {top_games}"
            )
        return "\n".join(lines)

    async def _maybe_push_daily_rank(self) -> None:
        now = datetime.now()
        hour = int(self.config.get("rank_push_hour", 8))
        minute = int(self.config.get("rank_push_minute", 30))
        if now.hour != hour or now.minute != minute:
            return
        day = self._day_key(-1)
        if self._last_rank_push_day == day:
            return
        groups = list(self.rank_push.get("groups") or [])
        if not groups:
            return
        self._last_rank_push_day = day
        if self.rank_push.get("all"):
            text = await self.rank_text(1, None, "昨日", offset=-1)
            for group_id in groups:
                await self.send_group_text(group_id, text)
            return
        for group_id in groups:
            text = await self.rank_text(1, group_id, "昨日", offset=-1)
            await self.send_group_text(group_id, text)

    def config_text(self) -> str:
        hidden = {"steam_api_key"}
        lines = ["Steam 状态监控配置："]
        for key in sorted(self.config):
            value = "******" if key in hidden and self.config[key] else self.config[key]
            lines.append(f"{key}: {value}")
        return "\n".join(lines)

    def set_config_value(self, key: str, value: str) -> str:
        if key not in self.config:
            return f"无效配置项：{key}"
        old = self.config[key]
        try:
            if isinstance(old, bool):
                lowered = value.lower()
                if lowered in {"true", "1", "yes", "on", "开启"}:
                    parsed: Any = True
                elif lowered in {"false", "0", "no", "off", "关闭"}:
                    parsed = False
                else:
                    return "类型错误，应为布尔值。"
            elif isinstance(old, int):
                parsed = int(value)
            elif isinstance(old, float):
                parsed = float(value)
            else:
                parsed = value
        except Exception:
            return "配置值类型错误。"
        self.config[key] = parsed
        self.save_config_overrides()
        return f"已设置 {key} = {parsed}"

    async def query_bound_user(self, qq: str, group_id: str) -> str:
        info = self.bind_data.get(str(qq))
        if not info:
            return f"QQ {qq} 未绑定 SteamID。"
        sid = info.get("sid")
        if not sid:
            return f"QQ {qq} 的绑定数据异常。"
        status = await self.api.fetch_player_status(sid)
        if not status:
            return f"无法获取 {sid} 的 Steam 状态。"
        name = self.display_name(sid, status.name)
        game_name, _ = await self.api.get_game_names(status.gameid, status.gameextrainfo)
        extra = ""
        if status.gameid:
            start = self._get_start_time(group_id, sid, status.gameid)
            if start:
                extra = f"\n已玩：{self._format_minutes((time.time() - start) / 60)}"
        return self._status_line(name, status, game_name) + extra

    def reset_runtime(self) -> None:
        self.group_last_states.clear()
        self.group_start_play_times.clear()
        self.group_pending_quit.clear()
        self.next_poll_time.clear()
        for task in self.achievement_tasks.values():
            task.cancel()
        self.achievement_tasks.clear()
        self.achievement_snapshots.clear()
        self.save_runtime()

    async def shutdown(self) -> None:
        for task in self.achievement_tasks.values():
            task.cancel()
        self.achievement_tasks.clear()
        self.save_runtime()
