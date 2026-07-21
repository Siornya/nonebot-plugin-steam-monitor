from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlparse

import httpx
from nonebot import logger

STEAM_ID64_OFFSET = 76561197960265728


@dataclass
class PlayerStatus:
    steamid: str
    name: str
    gameid: str | None
    gameextrainfo: str | None
    personastate: int
    lastlogoff: int | None
    avatar: str | None
    avatarfull: str | None

    @classmethod
    def from_api(cls, player: dict[str, Any]) -> "PlayerStatus":
        gameid = player.get("gameid")
        return cls(
            steamid=str(player.get("steamid") or ""),
            name=player.get("personaname") or "",
            gameid=str(gameid) if gameid else None,
            gameextrainfo=player.get("gameextrainfo"),
            personastate=int(player.get("personastate") or 0),
            lastlogoff=int(player["lastlogoff"]) if player.get("lastlogoff") else None,
            avatar=player.get("avatar"),
            avatarfull=player.get("avatarfull"),
        )

    def to_json(self) -> dict[str, Any]:
        return {
            "steamid": self.steamid,
            "name": self.name,
            "gameid": self.gameid,
            "gameextrainfo": self.gameextrainfo,
            "personastate": self.personastate,
            "lastlogoff": self.lastlogoff,
            "avatar": self.avatar,
            "avatarfull": self.avatarfull,
        }


class SteamApi:
    def __init__(
        self,
        *,
        api_key: str,
        api_base: str,
        store_base: str,
        retry_times: int = 3,
        proxy: str | None = None,
    ):
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.store_base = store_base.rstrip("/")
        self.retry_times = max(1, retry_times)
        self.proxy = proxy
        self._game_name_cache: dict[str, tuple[str, str]] = {}
        self._sgdb_cover_cache: dict[tuple[str, str], str | None] = {}

    def update(
        self,
        *,
        api_key: str,
        api_base: str,
        store_base: str,
        retry_times: int,
        proxy: str | None,
    ) -> None:
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")
        self.store_base = store_base.rstrip("/")
        self.retry_times = max(1, retry_times)
        self.proxy = proxy

    def _client(self, timeout: int = 15) -> httpx.AsyncClient:
        return httpx.AsyncClient(timeout=timeout, proxy=self.proxy)

    async def fetch_player_status(self, steamid: str) -> PlayerStatus | None:
        result = await self.fetch_player_statuses([steamid])
        return result.get(steamid)

    async def fetch_player_statuses(self, steamids: list[str]) -> dict[str, PlayerStatus]:
        if not self.api_key or not steamids:
            return {}

        result: dict[str, PlayerStatus] = {}
        unique_ids = list(dict.fromkeys(str(sid) for sid in steamids if sid))
        for start in range(0, len(unique_ids), 100):
            batch = unique_ids[start : start + 100]
            batch_result = await self._fetch_player_batch(batch)
            result.update(batch_result)
        return result

    async def _fetch_player_batch(self, steamids: list[str]) -> dict[str, PlayerStatus]:
        params = {"key": self.api_key, "steamids": ",".join(steamids)}
        delay = 1
        url = f"{self.api_base}/ISteamUser/GetPlayerSummaries/v2/"
        for attempt in range(self.retry_times):
            try:
                async with self._client() as client:
                    resp = await client.get(url, params=params)
                    resp.raise_for_status()
                    data = resp.json()
                response = data.get("response")
                if not isinstance(response, dict):
                    raise RuntimeError(f"Steam 返回异常 response: {response!r}")
                players = response.get("players") or []
                found: dict[str, PlayerStatus] = {}
                for player in players:
                    status = PlayerStatus.from_api(player)
                    if status.steamid:
                        found[status.steamid] = status
                return found
            except Exception as exc:
                logger.warning(
                    f"[steam_status_monitor] 批量查询 Steam 状态失败 "
                    f"({len(steamids)} 个, 第 {attempt + 1} 次): {exc}"
                )
                if attempt < self.retry_times - 1:
                    await asyncio.sleep(delay)
                    delay *= 2
        return {}

    async def resolve_steam_input(self, raw: str) -> str | None:
        if not raw:
            return None
        text = raw.strip()
        if text.isdigit() and len(text) == 17:
            return text
        if text.isdigit() and len(text) <= 10:
            steamid = str(int(text) + STEAM_ID64_OFFSET)
            return steamid if len(steamid) == 17 else None

        lowered = text.lower()
        if "steamcommunity.com" in lowered or "s.team/p/" in lowered:
            parsed = urlparse(text if "://" in text else f"https://{text}")
            parts = [p for p in parsed.path.split("/") if p]
            if len(parts) >= 2 and parts[-2] == "profiles" and parts[-1].isdigit():
                return parts[-1] if len(parts[-1]) == 17 else None
            if len(parts) >= 2 and parts[-2] == "id" and parts[-1]:
                return await self.resolve_vanity(parts[-1])
            if parsed.netloc.endswith("s.team") and len(parts) >= 2 and parts[-2] == "p":
                return parts[-1] if parts[-1].isdigit() and len(parts[-1]) == 17 else None
        return None

    async def resolve_vanity(self, vanity: str) -> str | None:
        if not self.api_key or not vanity:
            return None
        url = f"{self.api_base}/ISteamUser/ResolveVanityURL/v1/"
        try:
            async with self._client() as client:
                resp = await client.get(url, params={"key": self.api_key, "vanityurl": vanity})
                resp.raise_for_status()
                data = resp.json()
            response = data.get("response") if isinstance(data, dict) else {}
            if isinstance(response, dict) and response.get("success") == 1:
                steamid = response.get("steamid")
                return str(steamid) if steamid else None
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 解析 Steam vanity URL 失败 {vanity}: {exc}")
        return None

    async def get_game_names(self, appid: str | int | None, fallback: str | None = None) -> tuple[str, str]:
        if not appid:
            name = fallback or "未知游戏"
            return name, name
        key = str(appid)
        if key in self._game_name_cache:
            return self._game_name_cache[key]

        zh = fallback or "未知游戏"
        en = fallback or "未知游戏"
        async with self._client(timeout=10) as client:
            for lang, marker in (("schinese", "zh"), ("en", "en")):
                try:
                    resp = await client.get(
                        f"{self.store_base}/api/appdetails",
                        params={"appids": key, "l": lang},
                    )
                    if resp.status_code != 200:
                        continue
                    data = resp.json()
                    name = (data.get(key) or {}).get("data", {}).get("name")
                    if name and marker == "zh":
                        zh = name
                    elif name:
                        en = name
                except Exception as exc:
                    logger.warning(f"[steam_status_monitor] 获取游戏名失败 appid={key}: {exc}")
        self._game_name_cache[key] = (zh, en)
        return zh, en

    async def download_image(self, url: str) -> bytes | None:
        if not url:
            return None
        try:
            async with self._client(timeout=15) as client:
                resp = await client.get(url)
                resp.raise_for_status()
                content_type = resp.headers.get("content-type", "")
                if "image" not in content_type.lower():
                    return None
                return resp.content
        except Exception as exc:
            logger.warning(f"[steam_status_monitor] 下载图片失败 {url}: {exc}")
            return None

    async def get_sgdb_vertical_cover_url(
        self,
        game_name: str,
        sgdb_api_key: str | None,
        *,
        appid: str | int | None = None,
        sgdb_game_name: str | None = None,
    ) -> str | None:
        if not sgdb_api_key:
            return None

        search_name = (sgdb_game_name or game_name or "").strip()
        cache_key = (str(appid or ""), search_name)
        if cache_key in self._sgdb_cover_cache:
            return self._sgdb_cover_cache[cache_key]

        headers = {"Authorization": f"Bearer {sgdb_api_key}"}
        async with self._client(timeout=10) as client:
            try:
                cover_url = await self._find_sgdb_cover_by_name(client, headers, search_name)
                if cover_url:
                    self._sgdb_cover_cache[cache_key] = cover_url
                    return cover_url

                if appid:
                    game_url = f"https://www.steamgriddb.com/api/v2/games/steam/{appid}"
                    resp = await client.get(game_url, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()
                    sgdb_name = (data.get("data") or {}).get("name") if data.get("success") else None
                    if sgdb_name:
                        cover_url = await self._find_sgdb_cover_by_name(client, headers, str(sgdb_name))
                        if cover_url:
                            self._sgdb_cover_cache[cache_key] = cover_url
                            return cover_url
            except Exception as exc:
                logger.warning(
                    f"[steam_status_monitor] 获取 SGDB 竖版封面失败 appid={appid} name={search_name}: {exc}"
                )

        self._sgdb_cover_cache[cache_key] = None
        return None

    async def _find_sgdb_cover_by_name(
        self, client: httpx.AsyncClient, headers: dict[str, str], game_name: str
    ) -> str | None:
        if not game_name:
            return None
        search_url = (
            "https://www.steamgriddb.com/api/v2/search/autocomplete/"
            f"{quote(game_name, safe='')}"
        )
        resp = await client.get(search_url, headers=headers)
        resp.raise_for_status()
        data = resp.json()
        if not data.get("success") or not data.get("data"):
            return None

        sgdb_game_id = data["data"][0].get("id")
        if not sgdb_game_id:
            return None
        grid_url = (
            f"https://www.steamgriddb.com/api/v2/grids/game/{sgdb_game_id}"
            "?dimensions=600x900&type=static&limit=1"
        )
        resp = await client.get(grid_url, headers=headers)
        resp.raise_for_status()
        grid_data = resp.json()
        if not grid_data.get("success") or not grid_data.get("data"):
            return None
        return grid_data["data"][0].get("url")

    async def get_player_achievements(self, steamid: str, appid: str | int) -> set[str] | None:
        if not self.api_key or not steamid or not appid:
            return None
        url = f"{self.api_base}/ISteamUserStats/GetPlayerAchievements/v1/"
        for lang in ("schinese", "english", "en"):
            try:
                async with self._client() as client:
                    resp = await client.get(
                        url,
                        params={
                            "key": self.api_key,
                            "steamid": steamid,
                            "appid": str(appid),
                            "l": lang,
                        },
                    )
                if resp.status_code in {400, 401, 403}:
                    return None
                if resp.status_code != 200:
                    continue
                data = resp.json()
                achievements = (data.get("playerstats") or {}).get("achievements") or []
                return {
                    str(item.get("apiname"))
                    for item in achievements
                    if item.get("apiname") and int(item.get("achieved") or 0) == 1
                }
            except Exception as exc:
                logger.warning(
                    f"[steam_status_monitor] 获取成就失败 steamid={steamid} appid={appid}: {exc}"
                )
        return None

    async def get_achievement_names(self, appid: str | int) -> dict[str, str]:
        if not self.api_key or not appid:
            return {}
        url = f"{self.api_base}/ISteamUserStats/GetSchemaForGame/v2/"
        for lang in ("schinese", "english", "en"):
            try:
                async with self._client() as client:
                    resp = await client.get(
                        url,
                        params={"key": self.api_key, "appid": str(appid), "l": lang},
                    )
                if resp.status_code != 200:
                    continue
                data = resp.json()
                items = (
                    (data.get("game") or {})
                    .get("availableGameStats", {})
                    .get("achievements", [])
                )
                names = {
                    str(item.get("name")): item.get("displayName") or str(item.get("name"))
                    for item in items
                    if item.get("name")
                }
                if names:
                    return names
            except Exception as exc:
                logger.warning(f"[steam_status_monitor] 获取成就名称失败 appid={appid}: {exc}")
        return {}

