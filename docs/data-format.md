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
      "rank": 14,
      "url": "",
      "tags": ["古言脑洞"],
      "metrics": [{"name": "在读", "value": 12345, "unit": "人"}]
    }
  ]
}
```

`platform` 支持 `fanqie`、`qidian`、`ciweimao`、`heiyan`、`fanqie_short`。`id` 是平台内的稳定作品 ID，不能用当前显示序号替代。书库的 `rank` 使用 `null`；黑岩导入时会强制清空名次。数据日期未知时留空，不把下载日期填成源榜日期。`collected_at` 可选，提供时使用 ISO 日期时间。

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
