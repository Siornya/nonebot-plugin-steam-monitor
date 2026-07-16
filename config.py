from __future__ import annotations

from pydantic import BaseModel


class Config(BaseModel):
    steam_api_key: str = ""
    steam_api_base: str = "https://api.steampowered.com"
    steam_store_base: str = "https://store.steampowered.com"

    fixed_poll_interval: int = 0
    smart_poll_intervals: str = "1,3,5,10,20,30"
    retry_times: int = 3
    max_group_size: int = 20
    detailed_poll_log: bool = True

    enable_achievement_poll: bool = True
    enable_game_end_notify: bool = True
    max_achievement_notifications: int = 5

    enable_proxy: bool = False
    proxy_url: str = ""

    rank_push_hour: int = 8
    rank_push_minute: int = 30

    permission_level: int = 2
    game_filter_mode: str = "全部游戏"
    game_filter_ids: str = ""


def dump_config(config: Config) -> dict:
    if hasattr(config, "model_dump"):
        return config.model_dump()
    return config.dict()

