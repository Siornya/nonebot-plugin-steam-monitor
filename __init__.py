from __future__ import annotations

import re
import shutil
from pathlib import Path
from typing import Annotated

from nonebot import get_driver, get_plugin_config, logger, on_command, require
from nonebot.adapters.onebot.v11 import Bot, GroupMessageEvent, Message, MessageEvent
from nonebot.matcher import Matcher
from nonebot.params import CommandArg
from nonebot.permission import SUPERUSER
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

steam_common_cmd = on_command("steam", aliases={"Steam"}, priority=5, block=False)
steam_superuser_cmd = on_command("steam", aliases={"Steam"}, permission=SUPERUSER, priority=5, block=True)
steamwho_cmd = on_command("steamwho", aliases={"在干嘛"}, priority=5, block=True)
clear_cache_cmd = on_command("steam清除缓存", permission=SUPERUSER, priority=5, block=True)

PUBLIC_COMMANDS = {"help", "帮助", "list", "openbox", "rank", "config"}
ADMIN_COMMANDS = {"on", "off", "add", "rm", "rank_on", "achievement_on", "achievement_off", "set", "rs", "clear", "clear_allgroup"}

def _group_id(event: MessageEvent) -> str:
    if isinstance(event, GroupMessageEvent):
        return str(event.group_id)
    return "default"


def _is_superuser(event: MessageEvent) -> bool:
    return str(event.get_user_id()) in {str(user) for user in get_driver().config.superusers}


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
/steam add [SteamID/链接/好友码] [@用户] [备注名] - 添加监控；若已在别群监控则自动加入联动推送
/steam rm [SteamID/链接/好友码] [群号] - 删除监控
/steam list - 查看本群玩家状态
/steam openbox [SteamID/链接/好友码] - 查看玩家详情
/steam rank [天数|week|month] - 本群排行榜
/steam rank_on [all|list|test|del 群号] - 每日排行榜推送
/steam achievement_on - 开启本群成就推送
/steam achievement_off - 关闭本群成就推送
/steam config - 查看配置
/steam set [配置项] [值] - 修改配置并持久化
/steam rs - 清空运行状态并重新初始化
/steam clear [群号] - 清空指定群监控 ID（不填则清空本群）
/steam clear_allgroup - 清空所有群监控 ID
/steam清除缓存 - 清除图片缓存目录
/steamwho @用户 / /在干嘛 @用户 - 查询绑定玩家状态"""

async def _handle_public_command(
    matcher: Matcher,
    event: MessageEvent,
    parts: list[str],
) -> None:
    sub = parts[0].lower() if parts else "help"
    group_id = _group_id(event)

    if sub in {"help", "帮助"}:
        await matcher.finish(HELP_TEXT)

    if sub == "list":
        await matcher.finish(await service.list_group_status(group_id))

    if sub == "openbox":
        if len(parts) < 2:
            await matcher.finish("用法：/steam openbox [SteamID/链接/好友码]")
        await matcher.finish(await service.openbox(parts[1]))

    if sub == "rank":
        days, label = _parse_period(parts[1] if len(parts) >= 2 else "")
        await matcher.finish(await service.rank_text(days, group_id, label))

    if sub == "config":
        await matcher.finish(service.config_text())


async def _handle_admin_command(
    matcher: Matcher,
    bot: Bot,
    event: MessageEvent,
    args: Message,
    parts: list[str],
) -> None:
    sub = parts[0].lower()
    group_id = _group_id(event)

    if sub == "on":
        await matcher.finish(await service.start_group(group_id, bot))

    if sub == "off":
        await matcher.finish(service.stop_group(group_id))

    if sub == "add":
        if len(parts) < 2:
            await matcher.finish("用法：/steam add [SteamID/链接/好友码] [@用户] [备注名]")
        raw_ids = [x.strip() for x in re.split(r"[,，]+", parts[1]) if x.strip()]
        qq = _extract_at_qq(args)
        nickname = None
        if len(parts) >= 3:
            tail = re.sub(r"\[CQ:at,qq=\d+\]", "", " ".join(parts[2:])).strip()
            nickname = tail or None
        added, linked, already, invalid = await service.add_steam_ids(group_id, raw_ids, qq, nickname)
        if linked:
            service.remember_bot(group_id, bot)

        lines: list[str] = []
        if added:
            lines.append("已添加：" + ", ".join(added))
        if linked:
            lines.append("已存在于其他群主监控，本群自动加入联动推送：" + ", ".join(linked))
        if already:
            lines.append("已存在：" + ", ".join(already))
        if invalid:
            lines.append("无法解析：" + ", ".join(invalid))
        if qq and (added or linked):
            lines.append(f"已绑定 QQ {qq}。")
        await matcher.finish("\n".join(lines) if lines else "未添加任何 SteamID。")

    if sub == "rm":
        if len(parts) < 2:
            await matcher.finish("用法：/steam rm [SteamID/链接/好友码] [群号]")
        target_group = parts[2] if len(parts) >= 3 else group_id
        _, msg = await service.delete_steam_id(target_group, parts[1])
        await matcher.finish(msg)

    if sub == "rank_on":
        param = parts[1].lower() if len(parts) >= 2 else ""
        groups = service.rank_push.setdefault("groups", [])
        if param == "list":
            mode = "全局" if service.rank_push.get("all") else "分群"
            await matcher.finish(f"排行榜推送模式：{mode}\n推送群：{', '.join(groups) if groups else '未开启'}")
        if param == "test":
            await matcher.finish(await service.rank_text(1, None, "昨日", offset=-1))
        if param == "del":
            target = parts[2] if len(parts) >= 3 else group_id
            if target in groups:
                groups.remove(target)
                service.save_static()
                await matcher.finish(f"已关闭群 {target} 的排行榜推送。")
            await matcher.finish(f"群 {target} 未开启排行榜推送。")

        service.remember_bot(group_id, bot)
        service.rank_push["all"] = param == "all"
        if group_id not in groups:
            groups.append(group_id)
        service.save_static()
        await matcher.finish("已开启每日排行榜推送。" + ("（全局排行）" if param == "all" else ""))

    if sub == "achievement_on":
        service.set_achievement_enabled(group_id, True)
        await matcher.finish("已开启本群 Steam 成就推送。")

    if sub == "achievement_off":
        service.set_achievement_enabled(group_id, False)
        await matcher.finish("已关闭本群 Steam 成就推送。")

    if sub == "set":
        raw = str(args).strip()
        match = re.match(r"\S+\s+(\S+)\s+(.+)$", raw)
        if not match:
            await matcher.finish("用法：/steam set [配置项] [值]")
        await matcher.finish(service.set_config_value(match.group(1), match.group(2).strip()))

    if sub == "rs":
        service.reset_runtime()
        await matcher.finish("已清空 Steam 监控运行状态。")

    if sub == "clear_allgroup":
        service.group_steam_ids.clear()
        service.reset_runtime()
        service.save_static()
        await matcher.finish("已清空所有群的 SteamID。")

    if sub == "clear":
        target = parts[1] if len(parts) >= 2 else group_id
        service.group_steam_ids.pop(target, None)
        service.group_last_states.pop(target, None)
        service.group_start_play_times.pop(target, None)
        service.group_pending_quit.pop(target, None)
        service.save_static()
        service.save_runtime()
        await matcher.finish(f"已清空群 {target} 的 SteamID。")


@steam_common_cmd.handle()
async def _(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    parts = _text_args(args)
    sub = parts[0].lower() if parts else "help"

    if sub in ADMIN_COMMANDS:
        if _is_superuser(event):
            return
        await steam_common_cmd.finish("权限不足：此指令需要超级用户权限。")

    if sub in PUBLIC_COMMANDS:
        await _handle_public_command(steam_common_cmd, event, parts)
        return

    await steam_common_cmd.finish("未知指令，发送 /steam help 查看帮助。")


@steam_superuser_cmd.handle()
async def _(bot: Bot, event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    parts = _text_args(args)
    if not parts:
        return
    if parts[0].lower() not in ADMIN_COMMANDS:
        return
    await _handle_admin_command(steam_superuser_cmd, bot, event, args, parts)


@steamwho_cmd.handle()
async def _(event: MessageEvent, args: Annotated[Message, CommandArg()]) -> None:
    qq = _extract_at_qq(args) or str(args).strip().lstrip("@")
    if not qq:
        await steamwho_cmd.finish("用法：/steamwho @用户")
    await steamwho_cmd.finish(await service.query_bound_user(qq, _group_id(event)))


@clear_cache_cmd.handle()
async def _() -> None:
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
