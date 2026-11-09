# 书目导入与维护接口

工作台接受一个 JSON 对象。每个文件仅包含一个明确平台、频道、分类和榜型；不同分类即使同为“第 14 名”也应分别导入。以下是格式说明，不是真实作品，也不会自动加载到应用。

```json
{
  "platform": "fanqie",
  "channel": "女频",
  "category": "古言脑洞",
  "board": "新书榜",
  "source_url": "https://fanqienovel.com/rank/0_1_253",
  "data_date": null,
  "books": [
    {
      "id": "填入平台真实作品ID",
      "title": "填入真实书名",
      "author": "作者",
      "summary": "来源中的简介",
      "cover_url": "",
      "status": "连载中",
      "rank": 14,
      "url": "",
      "tags": ["古言脑洞"],
      "metrics": [{"name": "在读", "value": 12345, "unit": "人"}]
    }
  ]
}
```

`platform` 支持 `fanqie`、`qidian`、`ciweimao`、`heiyan`、`fanqie_short`。`id` 是平台内的稳定作品 ID，不能用当前显示序号替代。书库的 `rank` 使用 `null`；黑岩导入时会强制清空名次。数据日期未知时留空，不把下载日期填成源榜日期。`collected_at` 可选，提供时使用 ISO 日期时间。

`cover_url` 只填写来源提供的公开 HTTP(S) 图片地址；空值或失效时显示书名占位，不生成虚构封面。非网页地址、带凭据、localhost 和私网地址会被清空。`status` 为来源提供的连载状态，未知时留空。可选的 `categories` 是当前频道的真实分类名称数组，不能混入其他频道。

采集快照可包含 `pagination`：`pages_fetched` 是实际读取页数，`max_pages` 是本次上限，`stop_reason` 是停止原因，`source_total` 是平台声明值。平台声明值可能不准确，不能替代 `books.length` 或据此拼造缺失作品。

`POST /api/collect` 接受 `platform`、`options` 和可选布尔 `automatic`。默认最多 100 页，未指定 `limit` 时按源实际结束停止；番茄详情默认全部读取，并缓存可读元数据。自动请求在同一范围 15 分钟内复用成功结果，失败后等待至少 5 分钟；同范围正在采集时共用任务。手动更新仍会复用正在进行的任务。结果 `status` 为 `started`、`busy`、`fresh` 或 `cooldown`，分别包含 `job`/`job_id`、`snapshot_id` 或 `retry_after_seconds`。更改范围或采集上限不能误用另一范围的新鲜度。

每次保存生成新的快照 ID；界面顺序单独保存在 view。分析任务始终保存 `snapshot_id + book_id`，后续更新榜单不改变任务。收藏以 `platform + book_id` 区分同名或同 ID 的其他平台作品。

## 开发验证

基础运行只需要 Python 3.10+：

```sh
python3 -m unittest discover -s tests -v
```

可选的界面 DOM 与真实 HTTP 接口联调：

```sh
npm ci --ignore-scripts
python3 tests/run_ui_smoke.py
```

该测试自动创建隔离临时数据库、随机本机端口，验证选择、排序、筛选、收藏、重新打开、黑岩登录卡、历史和报告查看。它不声称完成像素布局或真实账号验收。运行黑岩真实浏览器还需 `npm run browser:install`。

所有 HTTP 接口只接受本机 Host；写入时需要 JSON Content-Type 及 `X-Fagao-Request: 1`。外部网站没有 CORS 权限。报告以纯文本呈现，不能在工作台执行报告中的 HTML 或脚本。

## 目录、正文和榜单观察

- `POST /api/catalog`：`{snapshot_id, book_id}`，先验证本机作品，再读取当前真实目录。
- `POST /api/fetch-text`：`{snapshot_id, book_id, start_chapter:1, chapters:3}`；明确全目录时用 `all:true`。返回后台任务，任务 `kind` 为 `text`，不能当作采集榜单任务处理。
- `GET /api/jobs/:id`：正文任务完成后返回 `text_id`、不含正文的 `text` 元数据和 `download_url`。暂停先变为 `cancelling`，有效部分保存后再成为 `cancelled`。
- `GET /api/texts/:id/download`：输出已保存的 UTF-8 TXT 附件；不接受文件系统路径，不临时抓取另一作品。
- `POST /api/insights`：`{snapshot_id}`，仅对本机快照计算规则统计和可比较的历史差值。

正文资产记录实际章节 ID、目录位置、字符数、缺章原因与请求范围；`coverage_complete` 和全书 `complete` 是不同概念。`Store.save_fetched_text` 统一验证身份和范围；保存不改旧分析任务，调用 `resume` 才为原作品关联新资产。所有取文操作固定 `snapshot_id + book_id`，不读取执行途中变化的选中对象。
