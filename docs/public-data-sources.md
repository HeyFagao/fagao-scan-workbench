# 公开榜单数据接入

核查日期：2026-11-09。工作台在本机独立读取公开 JSON，不运行第三方仓库代码，不调用其 AI，不上传用户数据。此适配器由本项目独立实现。

## 已接入：FanqieRankTracker

来源仓库：[scoful/FanqieRankTracker](https://github.com/scoful/FanqieRankTracker)，上游为 [wen1701/FanqieRankTracker](https://github.com/wen1701/FanqieRankTracker)。来源 README 的“最新数据接口”明确公开按频道、榜型拆分的静态 JSON。当前固定读取 `scoful/FanqieRankTracker` 的 `main` 分支，不自动发现、切换或执行其他仓库。

| 数据 | 固定路径（相对于仓库） |
| --- | --- |
| 女频新书榜日期和覆盖索引 | `api/female/new/index.json` |
| 男频新书榜日期和覆盖索引 | `api/male/new/index.json` |
| 女频新书榜实际日期快照 | `data/female/new/YYYYMMDD.json` |
| 男频新书榜实际日期快照 | `data/male/new/YYYYMMDD.json` |

下载地址前缀为 `https://raw.githubusercontent.com/scoful/FanqieRankTracker/main/`。先读取两个索引的来源日期，再分别读取该日与恰好七天前的文件；不请求阅读榜，不跟随 JSON 中的地址，也不追随 HTTP 重定向。最多六次请求，最多四个并发。单响应限制为 3 MiB，连接/套接字超时为 12 秒，同时逐块检查总读取截止时间，超时、结构改变或索引不一致时返回明确失败；已有数据由调用方保留。

### 实际覆盖

2026-11-09 核查实际 JSON：女频新书榜 18 个分类、360 条榜单条目；男频新书榜 19 个分类、380 条。每类 20 条，合计 740 条是本源当日样本条目数，不是平台全部书目数，也不是去重后的跨分类作品数。上游 README 的介绍写 Top30，但本次数据实际为 Top20；本适配器按已验证的 Top20 上限校验，未来变更需重新核查，不能悄悄扩大口径。

历史目录核查：女频有 213 份日期快照（2026-04-10 至 2026-11-09），男频有 79 份（2026-08-23 至 2026-11-09）。并非保证每天都有；本次两频道 2026-11-02 至 2026-11-09 文件均存在。程序只按实际取得并校验的日期计算变化，缺少七天前文件时保留缺失状态，不填零、不借用另一频道或另一个来源的日期。

### 字段与来源记录

- 实际字段为书名、作者、在读展示值、简介、封面 URL、原书 URL。书目 ID 必须从番茄原书 URL 解析，不按书名猜身份；同名不同 ID 保留为不同作品。
- 上游列表没有经核验的原榜名次。`rank` 保持空值，另存 `source_position` 表示来源列表位置，不用于“原榜第 N 名”的定位。
- 上游不提供字数；不推算或补造字数、效率指数、平台评分。封面 URL 可能过期，缺失时按应用既有占位方式显示。
- 在读值保留来源字符串及其精度，例如 `9.4万`。后续换算得到 94000 仍是来源四舍五入的展示数，不意味着已取得精确到个位的读者人数。
- 每个分类独立快照携带 `source_id`、来源日期、来源文件 URL、读取时间、范围和覆盖标记。`source_revision` 使用实际文件字节的 Git blob SHA-1，注明 `git-blob-sha1:`；它是文件内容标识，不是提交 SHA、签名或真实性保证。
- `complete=false` 表示只取得公开 Top20 样本，不代表完整分类榜。索引分类名作为导航元数据保留。来源的 AI 趋势摘要不会导入为工作台事实。
- 公共样本与直接采集的覆盖范围不同；比较历史需要同来源、同频道、同榜型、同分类和同作品 ID。不能拿一份公开 Top20 和直接采集的完整分类直接比较“新上榜/掉榜”，也不能将两者样本数量相加当作覆盖提升。

### 许可说明

核查时上游和该 fork 的 README 均声明 `MIT`，但当前根目录没有独立 `LICENSE` 文件。本项目仅独立编写公开 JSON 适配器并保留来源，不复制其程序、界面或 AI 摘要。仓库许可声明不改变第三方小说正文、简介和封面的既有权利；本项目不随代码发布用户采集所得的正文或数据快照。

## 已核查但未作为此自动源

| 来源 | 核查结果 | 当前使用边界 |
| --- | --- | --- |
| [wen1701/FanqieRankTracker](https://github.com/wen1701/FanqieRankTracker) | 女频上游公开 JSON 更新至 2026-11-09，README 声明 MIT | 作为来源追溯；适配器不自动混用其历史 |
| [uu201/FanqieRankTracker](https://github.com/uu201/FanqieRankTracker) | 男女新书榜与历史接口明确，但本次 `api/boards.json` 最新为 2026-10-15 | 不冒充实时数据 |
| [WindXRan/fanqie-radar](https://github.com/WindXRan/fanqie-radar) | 有独立 MIT 许可证，内置四榜样本实际为 2026-09-06 | 可参考结构；未把旧样本称为当天数据 |
| [QiuNova/Qbook](https://github.com/QiuNova/Qbook) | MIT；提供采集与图表代码，未见公开历史数据库 | 参考功能组织，不作为自动历史源 |
| [网文情报站官方 MCP](https://novelcatch.com/mcp) | 官方提供 `https://novelcatch.com/api/mcp`，需会员 token 并受个人额度约束 | 本版未接入；不把会员接口当作匿名公共全量接口 |

这些核查记录说明数据来源和实测边界，不代表其他服务持续可用。数据新鲜度以每次实际 JSON 的来源日期为准。
