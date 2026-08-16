# Steam 状态监控

基于 NoneBot2 与 OneBot V11 的 Steam 状态监控插件。插件可监控玩家开始游戏、结束游戏和切换游戏，并支持 Star 玩家在线状态变化、成就通知、游玩时长排行、群聊监控和个人私聊监控。

## 功能

- 批量查询 Steam 玩家状态
- 游戏开始、结束和切换通知
- Star 玩家的上线、离开、离线等在线状态变化通知
- 带头像与竖版游戏封面的状态图片
- Steam 成就解锁通知
- 今日、最近 7 天、最近 30 天游玩时长排行
- 每日排行榜定时推送
- 群聊主监控与跨群联动推送
- QQ 与 SteamID 绑定及快捷状态查询
- 个人私聊监控
- 智能轮询间隔、代理和游戏黑白名单

## 运行要求

- Python 3.10 及以上
- NoneBot2
- OneBot V11 适配器
- `nonebot-plugin-apscheduler`
- `nonebot-plugin-localstore`
- `httpx`
- Pillow
- 可用的 OneBot V11 实现，例如 NapCat
- Steam Web API Key

项目根目录已经声明所需的 NoneBot 依赖。插件目录需包含在 NoneBot 的 `plugin_dirs` 中。

## 配置

至少需要配置 Steam Web API Key：

```env
STEAM_API_KEY=your_steam_web_api_key
```

如需显示 SteamGridDB 竖版游戏封面，可额外配置：

```env
SGDB_API_KEY=your_steamgriddb_api_key
```

支持的配置项：

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `steam_api_key` | 空 | Steam Web API Key，状态监控必需 |
| `steam_api_base` | `https://api.steampowered.com` | Steam Web API 地址 |
| `steam_store_base` | `https://store.steampowered.com` | Steam 商店 API 地址 |
| `sgdb_api_key` | 空 | SteamGridDB API Key；为空时使用默认封面背景 |
| `fixed_poll_interval` | `0` | 固定轮询间隔，单位为秒；`0` 表示启用智能轮询 |
| `smart_poll_intervals` | `1,3,5,10,20,30` | 智能轮询间隔，单位为分钟 |
| `retry_times` | `3` | Steam API 请求重试次数 |
| `max_group_size` | `20` | 单个群或个人监控列表的最大玩家数 |
| `detailed_poll_log` | `true` | 是否输出详细轮询日志 |
| `enable_achievement_poll` | `true` | 是否启用成就轮询 |
| `enable_game_end_notify` | `true` | 是否发送游戏结束通知 |
| `max_achievement_notifications` | `5` | 单次通知最多展示的成就数 |
| `enable_proxy` | `false` | 是否启用 HTTP 代理 |
| `proxy_url` | 空 | HTTP 代理地址 |
| `rank_push_hour` | `8` | 每日排行榜推送小时 |
| `rank_push_minute` | `30` | 每日排行榜推送分钟 |
| `permission_level` | `2` | 兼容配置项；管理命令权限由 NoneBot 超级用户控制 |
| `game_filter_mode` | `全部游戏` | 游戏过滤模式：`全部游戏`、`白名单` 或 `黑名单` |
| `game_filter_ids` | 空 | 逗号分隔的 Steam AppID 列表 |

超级用户也可以使用以下命令修改配置，修改结果会持久化：

```text
/steam set [配置项] [值]
```

## 命令

### 通用命令

| 命令 | 说明 |
| --- | --- |
| `/steam help` | 查看帮助 |
| `/steam list` | 群聊查看本群状态；私聊查看个人监控状态 |
| `/steam openbox [SteamID/链接/好友码]` | 查看玩家详情 |
| `/steam rank [天数\|week\|month]` | 查看本群游玩时长排行 |
| `/steam bind [SteamID/链接/好友码]` | 绑定自己的 QQ 与 SteamID |
| `/steam config` | 查看当前插件配置 |
| `/steamwho @用户` | 查询已绑定用户的 Steam 状态 |
| `/在干嘛 @用户` | `/steamwho` 的别名 |

### 添加与删除监控

`add`、`rm` 和 `list` 会根据消息场景自动选择群聊或个人监控：

```text
/steam add [SteamID/链接/好友码...]
/steam rm [SteamID/链接/好友码...]
/steam list
```

- 在群聊中，任何用户都可以使用 `add` 和 `rm` 管理本群监控。
- 在私聊中，任何用户都可以管理自己的个人监控。
- 个人监控添加后自动生效，不需要执行 `/steam on`。
- SteamID 支持 17 位 SteamID64、好友码、个人主页链接和自定义主页链接。

### Star 状态通知

```text
/steam star [SteamID/链接/好友码...]
```

- 私聊 Star 功能默认开启，所有用户都可以使用；Star 会自动加入个人监控并直接推送状态变化。
- 群聊 Star 功能默认关闭，需要超级用户先执行 `/steam star_on`；开启后所有群成员都可以使用。
- `/steam star_off` 会停止本群 Star 命令和 Star 状态通知，但保留已有 Star 列表。
- 执行 `/steam rm` 会同时移除对应场景的 Star 标记。

### 群管理命令

以下命令需要 NoneBot 超级用户权限：

| 命令 | 说明 |
| --- | --- |
| `/steam on` | 启动本群状态监控 |
| `/steam off` | 停止本群状态监控 |
| `/steam star_on` | 开启本群 Star 功能，开启后所有群成员可使用 `/steam star` |
| `/steam star_off` | 关闭本群 Star 功能和状态通知 |
| `/steam achievement_on` | 开启本群成就通知 |
| `/steam achievement_off` | 关闭本群成就通知 |
| `/steam rank_on [all\|list\|test\|del 群号]` | 管理每日排行榜推送 |
| `/steam set [配置项] [值]` | 修改配置并持久化 |
| `/steam rs` | 清空运行状态并重新初始化 |
| `/steam clear [群号]` | 清空指定群的监控列表；不填时清空本群 |
| `/steam clear_allgroup` | 清空所有群的监控列表 |
| `/steam清除缓存` | 清除图片缓存 |

## 群监控与个人监控

群监控需要先添加玩家，再执行 `/steam on`。如果同一个 SteamID 已由其他群主监控，插件不会重复建立主监控，而是为当前群建立联动推送。

群聊 Star 功能由超级用户通过 `/steam star_on` 和 `/steam star_off` 控制。开启后，所有群成员都可以 Star 玩家；Star 会自动完成本群 `add`，玩家的上线、离开、离线等状态变化会推送到本群。群 Star 轮询独立生效，不需要再执行 `/steam on`；`on` 和 `off` 只控制普通群监控。

个人监控按 QQ 号独立保存。任何用户都可以在私聊中执行 `/steam add` 或 `/steam star`，插件会自动轮询玩家；普通个人监控推送游戏状态图片和成就通知，Star 玩家还会额外推送上线、离开、离线等状态变化。个人监控不需要执行开关命令，也不会加入群排行榜。

## 数据与缓存

插件通过 `nonebot-plugin-localstore` 获取数据和缓存目录。常见持久化文件包括：

- `steam_groups.json`：群主监控列表
- `private_subscriptions.json`：个人监控列表
- `starred_players.json`：各群及个人 Star 玩家列表
- `group_states.json`：最近一次玩家状态
- `start_play_times.json`：游戏开始时间
- `pending_quit.json`：等待确认的退出状态
- `play_records.json`：最近 30 天游玩记录
- `bind_data.json`：QQ 与 SteamID 绑定
- `push_groups.json`：跨群联动推送
- `group_flags.json`：群监控、Star 功能与成就开关
- `rank_push_groups.json`：排行榜推送设置
- `config_overrides.json`：通过 `/steam set` 修改的配置

头像和游戏封面会缓存在 localstore 提供的缓存目录中。

## 轮询说明

未设置固定间隔时，插件会根据玩家状态调整轮询频率：游戏中轮询最频繁，在线、刚离线和长期离线状态依次降低频率。同一轮到期的 SteamID 会合并请求，Steam API 每批最多查询 100 个玩家。

游戏退出后会等待约 3 分钟再发送结束通知，以减少游戏短暂断开或快速重连产生的误报。游玩记录在游戏结束后写入，并保留最近 30 天。

## 注意事项

- Steam 隐私设置会影响当前游戏、成就和游玩数据的可见性。
- NapCat 必须保持 QQ 登录，并启用可用的 OneBot V11 连接。
- 私聊主动推送要求机器人能够向该 QQ 用户发送私聊消息。
- 修改 Python 代码或模板布局后需要重启 Bot 服务。
- 未配置 `sgdb_api_key` 时不会下载 SteamGridDB 竖版封面，但状态图片仍可正常生成。

## 故障排查

查看 Bot 服务日志：

```bash
journalctl -u feather-bot -n 100 --no-pager
journalctl -u feather-bot -f
```

常见问题：

- 插件加载但没有轮询：检查 `steam_api_key`，群聊还需确认已执行 `/steam on`。
- 状态或成就为空：检查目标 Steam 账号的隐私设置。
- 图片发送超时：检查 NapCat、QQ 登录状态和 OneBot 连接日志。
- 修改未生效：重启 Bot 服务后再次检查启动日志。
