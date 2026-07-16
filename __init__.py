from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Annotated

from nonebot import get_driver, get_plugin_config, logger, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.params import CommandArg
from nonebot.plugin import PluginMetadata

from .config import Config
from .service import SteamStatusService

require("nonebot_plugin_apscheduler")
from nonebot_plugin_apscheduler import scheduler  # noqa: E402


__plugin_meta__ = PluginMetadata(
    name="Steam 状态监控",
    description="监控群内 Steam 玩家在线、游戏、成就与游玩时长排行。",
    usage="/steam help",
    type="application",
    homepage="https://github.com/Maoer233/astrbot_plugin_steam_status_monitor",
    supported_adapters={"~onebot.v11"},
    config=Config,
)


plugin_config = get_plugin_config(Config)
service = SteamStatusService(plugin_config)

steam_cmd = on_command("steam", aliases={"Steam"}, priority=5, block=True)
steamwho_cmd = on_command("steamwho", aliases={"在干嘛"}, priority=5, block=True)
clear_cache_cmd = on_command("steam清除缓存", priority=5, block=True)


def _group_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return "default"


def _is_admin(event: MessageEvent) -> bool:
    user_id = str(event.get_user_id())
    superusers = {str(x) for x in get_driver().config.superusers}
    if user_id in superusers:
        return True
    if isinstance(event, GroupMessageEvent):
        return event.sender.role in {"admin", "owner"}
    return True


def _check_perm(event: MessageEvent, min_level: int) -> bool:
    level = int(service.config.get("permission_level", 2) or 2)
    needs_admin = True
    if level >= 2 and min_level <= 2:
        needs_admin = False
    if level >= 3 and min_level <= 3:
        needs_admin = False
    return not needs_admin or _is_admin(event)


def _text_args(args: Message) -> list[str]:
    text = str(args).strip()
    return text.split() if text else []


def _extract_at_qq(args: Message) -> str | None:
    for seg in args:
        if seg.type == "at":
            qq = seg.data.get("qq")
            if qq and qq != "all":
                return str(qq)
    text = str(args)
    match = re.search(r"\[CQ:at,qq=(\d+)\]|@(\d+)", text)
    if match:
        return match.group(1) or match.group(2)
    return None


def _parse_period(raw: str | None) -> tuple[int, str]:
    value = (raw or "").strip().lower()
    if value == "week":
        return 7, "最近7天"
    if value == "month":
        return 30, "最近30天"
    if value.isdigit():
        days = max(1, min(30, int(value)))
        return days, f"最近{days}天"
    return 1, "今日"


HELP_TEXT = """Steam 状态监控指令：
/steam on - 启动本群监控
/steam off - 停止本群监控
/steam addid [SteamID/链接/好友码] [@用户] [备注名] - 添加并可绑定
/steam delid [SteamID/链接/好友码] [群号] - 删除监控
/steam list - 查看本群玩家状态
/steam alllist - 查看全群玩家状态
/steam openbox [SteamID/链接/好友码] - 查看玩家详情
/steam rank [天数|week|month] - 本群排行榜
/steam allrank [天数|week|month] - 全局排行榜
/steam rank_on [all|list|test|del 群号] - 每日排行榜推送
/steam achievement_on - 开启本群成就推送
/steam achievement_off - 关闭本群成就推送
/steam push_group [SteamID] - 本群接收该 ID 联动推送
/steam delpush_group [SteamID] [群号] - 移除联动推送
/steam config - 查看配置
/steam set [配置项] [值] - 修改配置并持久化
/steam rs - 清空运行状态并重新初始化
/steam clear_allids - 清空所有群监控 ID
/steam clear_groupids [群号] - 清空指定群监控 ID
/steam清除缓存 - 清除图片缓存目录
/steamwho @用户 / /在干嘛 @用户 - 查询绑定玩家状态"""


@steam_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    parts = _text_args(args)
    if not parts or parts[0].lower() in {"help", "帮助"}:
        await steam_cmd.finish(HELP_TEXT)

    sub = parts[0].lower()
    group_id = _group_id(event)

    if sub == "on":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        await steam_cmd.finish(await service.start_group(group_id, bot))

    if sub == "off":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        await steam_cmd.finish(service.stop_group(group_id))

    if sub == "addid":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        if len(parts) < 2:
            await steam_cmd.finish("用法：/steam addid [SteamID/链接/好友码] [@用户] [备注名]")
        raw_ids = [x.strip() for x in re.split(r"[,，]+", parts[1]) if x.strip()]
        qq = _extract_at_qq(args)
        nickname = None
        if len(parts) >= 3:
            tail = re.sub(r"\[CQ:at,qq=\d+\]", "", " ".join(parts[2:])).strip()
            nickname = tail or None
        added, already, invalid = await service.add_steam_ids(group_id, raw_ids, qq, nickname)
        lines: list[str] = []
        if added:
            lines.append("已添加：" + ", ".join(added))
        if already:
            lines.append("已存在：" + ", ".join(already))
        if invalid:
            lines.append("无法解析：" + ", ".join(invalid))
        if qq and added:
            lines.append(f"已绑定 QQ {qq}。")
        await steam_cmd.finish("\n".join(lines) if lines else "未添加任何 SteamID。")

    if sub == "delid":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        if len(parts) < 2:
            await steam_cmd.finish("用法：/steam delid [SteamID/链接/好友码] [群号]")
        target_group = parts[2] if len(parts) >= 3 else group_id
        ok, msg = await service.delete_steam_id(target_group, parts[1])
        await steam_cmd.finish(f"已删除：{msg}" if ok else msg)

    if sub == "list":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        await steam_cmd.finish(await service.list_group_status(group_id))

    if sub == "alllist":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        await steam_cmd.finish(await service.list_all_status())

    if sub == "openbox":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        if len(parts) < 2:
            await steam_cmd.finish("用法：/steam openbox [SteamID/链接/好友码]")
        await steam_cmd.finish(await service.openbox(parts[1]))

    if sub == "rank":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        days, label = _parse_period(parts[1] if len(parts) >= 2 else "")
        await steam_cmd.finish(await service.rank_text(days, group_id, label))

    if sub == "allrank":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        days, label = _parse_period(parts[1] if len(parts) >= 2 else "")
        await steam_cmd.finish(await service.rank_text(days, None, label))

    if sub == "rank_on":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        param = parts[1].lower() if len(parts) >= 2 else ""
        groups = service.rank_push.setdefault("groups", [])
        if param == "list":
            mode = "全局" if service.rank_push.get("all") else "分群"
            await steam_cmd.finish(
                f"排行榜推送模式：{mode}\n推送群：{', '.join(groups) if groups else '未开启'}"
            )
        if param == "test":
            await steam_cmd.finish(await service.rank_text(1, None, "昨日", offset=-1))
        if param == "del":
            target = parts[2] if len(parts) >= 3 else group_id
            if target in groups:
                groups.remove(target)
                service.save_static()
                await steam_cmd.finish(f"已关闭群 {target} 的排行榜推送。")
            await steam_cmd.finish(f"群 {target} 未开启排行榜推送。")
        service.remember_bot(group_id, bot)
        service.rank_push["all"] = param == "all"
        if group_id not in groups:
            groups.append(group_id)
        service.save_static()
        await steam_cmd.finish("已开启每日排行榜推送。" + ("（全局排行）" if param == "all" else ""))

    if sub == "achievement_on":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        service.set_achievement_enabled(group_id, True)
        await steam_cmd.finish("已开启本群 Steam 成就推送。")

    if sub == "achievement_off":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        service.set_achievement_enabled(group_id, False)
        await steam_cmd.finish("已关闭本群 Steam 成就推送。")

    if sub == "push_group":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        if len(parts) < 2:
            await steam_cmd.finish("用法：/steam push_group [SteamID]")
        sid = await service.api.resolve_steam_input(parts[1])
        if not sid:
            await steam_cmd.finish("无法解析为有效 SteamID。")
        found = any(sid in ids for ids in service.group_steam_ids.values())
        if not found:
            await steam_cmd.finish("未找到监控该 SteamID 的主群，请先在任一群添加并启动监控。")
        service.remember_bot(group_id, bot)
        service.push_groups.setdefault(sid, [])
        if group_id not in service.push_groups[sid]:
            service.push_groups[sid].append(group_id)
            service.save_static()
        await steam_cmd.finish(f"本群已加入 SteamID {sid} 的联动推送。")

    if sub == "delpush_group":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        if len(parts) < 2:
            await steam_cmd.finish("用法：/steam delpush_group [SteamID] [群号]")
        sid = await service.api.resolve_steam_input(parts[1])
        if not sid:
            await steam_cmd.finish("无法解析为有效 SteamID。")
        target = parts[2] if len(parts) >= 3 else group_id
        if sid in service.push_groups and target in service.push_groups[sid]:
            service.push_groups[sid].remove(target)
            if not service.push_groups[sid]:
                service.push_groups.pop(sid, None)
            service.save_static()
            await steam_cmd.finish(f"已移除群 {target} 的联动推送。")
        await steam_cmd.finish(f"群 {target} 未在 SteamID {sid} 的联动推送中。")

    if sub == "config":
        if not _check_perm(event, 2):
            await steam_cmd.finish("权限不足。")
        await steam_cmd.finish(service.config_text())

    if sub == "set":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        raw = str(args).strip()
        match = re.match(r"\S+\s+(\S+)\s+(.+)$", raw)
        if not match:
            await steam_cmd.finish("用法：/steam set [配置项] [值]")
        await steam_cmd.finish(service.set_config_value(match.group(1), match.group(2).strip()))

    if sub == "rs":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        service.reset_runtime()
        await steam_cmd.finish("已清空 Steam 监控运行状态。")

    if sub == "clear_allids":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        service.group_steam_ids.clear()
        service.reset_runtime()
        service.save_static()
        await steam_cmd.finish("已清空所有群的 SteamID。")

    if sub == "clear_groupids":
        if not _check_perm(event, 3):
            await steam_cmd.finish("权限不足：此指令需要管理员权限。")
        target = parts[1] if len(parts) >= 2 else group_id
        service.group_steam_ids.pop(target, None)
        service.group_last_states.pop(target, None)
        service.group_start_play_times.pop(target, None)
        service.group_pending_quit.pop(target, None)
        service.save_static()
        service.save_runtime()
        await steam_cmd.finish(f"已清空群 {target} 的 SteamID。")

    await steam_cmd.finish("未知指令，发送 /steam help 查看帮助。")


@steamwho_cmd.handle()
async def _(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    qq = _extract_at_qq(args) or str(args).strip().lstrip("@")
    if not qq:
        await steamwho_cmd.finish("用法：/steamwho @用户")
    await steamwho_cmd.finish(await service.query_bound_user(qq, _group_id(event)))


@clear_cache_cmd.handle()
async def _(event: MessageEvent) -> None:
    if not _check_perm(event, 3):
        await clear_cache_cmd.finish("权限不足：此指令需要管理员权限。")
    root = Path.cwd() / "data" / "steam_status_monitor"
    cleared: list[str] = []
    for name in ("avatars", "covers", "covers_v", "images"):
        path = root / name
        if path.exists():
            shutil.rmtree(path)
            cleared.append(str(path))
    await clear_cache_cmd.finish("已清除：\n" + "\n".join(cleared) if cleared else "没有可清除的图片缓存。")


@scheduler.scheduled_job("interval", seconds=60, id="steam_status_monitor_poll")
async def _poll_steam_status() -> None:
    try:
        await service.poll_due()
    except Exception as exc:
        logger.exception(f"[steam_status_monitor] 轮询任务异常: {exc}")


@get_driver().on_shutdown
async def _shutdown() -> None:
    await service.shutdown()

