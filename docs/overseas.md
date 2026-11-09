# 海外网文接入 · v0.6.0

“海外网文”位于“我的收藏”上方。模式 `overseas` 独立保存浏览条件与选中作品；收藏、历史、报告共用现有资料库。作品书名、作者、简介保留来源英文，分类及已知标签通过固定词典显示中文。原标签另存 `source_tags`，无需模型翻译。

## 来源与口径

| 平台 ID | 公开入口 | 已实现范围 | 排名与数据含义 |
| --- | --- | --- | --- |
| `goodnovel` | https://www.goodnovel.com/rankings | 7 个长篇分类 | 页面每页重置编号，保存 `rank=null` 与列表序号，不冒充跨页官方名次 |
| `goodnovel_short` | https://www.goodnovel.com/stories/short-novels | 热门短篇及分类 | 书库排序，无官方数字排名；阅读量保留 K/M 等原精度 |
| `dreame` | https://www.dreame.com/ranking/6115.html | 新书、上升、月票、畅销、完结、必读榜 | 按来源页码与每页条数保存官方名次；`pager.totalpage` 实为条数，换算后才是页数 |
| `meganovel` | https://www.meganovel.com/rankings/Fantasy | 5 个分类 | 保存列表序号；第二页参数为 `pageIndex` |
| `webnovel` | https://www.webnovel.com/ranking/novel/monthly/power_rank | 男女频新书能量榜 | `monthly` 表示发表不超过 30 天；周榜能量石票数，签约原创范围，不将票数写为章节数 |
| `royalroad` | https://www.royalroad.com/fictions/rising-stars | 当前新星列表 | 官网列表没有显式数字名次，保存列表序号；阅读、关注、页数、章数沿用各自单位 |
| `scribblehub` | https://www.scribblehub.com/series-ranking/?order=2&sort=2 | 周收藏榜 | 保存来源显式名次，阅读、收藏、读者、英文词数与章节分开 |

各平台独立采集、独立展示。`英文词数` 单位为“词”，与中文“字数”分开。缺少值时省略指标。来源没有真实榜单日期时 `data_date` 为空，显示本次采集时间；不把下载时刻伪作源站更新日。

## 分页与首屏

每个实际来源页面解析后立即发布可用快照。浏览任务默认在首批后等待滚动需求，由 `/api/jobs/more` 继续；最终结果保留真实页数、已取得数量、停止原因和警告。切换页面或停止浏览保留已有快照。旧快照引用与选书不会被后台更新换成另一作品。

GoodNovel 默认狼人使用实际支持分页的 `/rankings?page=2`。非默认分类 URL 在本次验证中忽略 `page` 参数，程序检测重复页后保留首屏并说明原因。来源结束时按实际数量停止，不用配置的 100 页上限填充或伪造书目。

## WorkBuddy 翻译交接

`python3 -m fagao --data-dir DATA_DIR --session SESSION translation-context "翻译这本"`

以上参数占位由 WorkBuddy 从固定项目配置读取。输出包含 `snapshot_id`、`book_id`、`platform`、英文 `original`、实际 `scope`、正文资料元数据 `available_texts` 和可复制 `prompt`。没有简介时明确只翻译书名。

复制按钮使用 `POST /api/translation-context`，请求 `{snapshot_id,book_id}`。两项 ID 必须同时提供，并核对该快照中存在该作品。CLI 支持 `translation-context --snapshot ID --book ID`；复制动作在请求前固定对象，因此后续切换选书不会改变已复制任务。`继续翻译它` 使用本对话的固定翻译目标。

由 WorkBuddy 当前对话模型读原文并输出译文。当前范围是已取得的书名、简介及用户另行指定的可用正文资料；工作台不发模型请求，不覆盖英文原文。详细指令见 `workbuddy.md`。

## 实现与参考

适配器按公开网页的数据结构独立实现，使用 Python 标准库解析 JSON/HTML，不运行来源页面脚本。调研参考包含 `Varun-Patkar/webnovel-filter-extension`、`fs-c/royalroad-api` 及 WebToEpub 的 GoodNovel 目录实现；本版未引入这些仓库的运行时依赖或全文下载代码。

Royal Road 的 RST 公开历史数据服务已调研，本版使用官方新星列表，尚未接入其历史增长 API。海外首页统计只显示来源实际作品指标，不套用中文标题句式或每万汉字公式。
