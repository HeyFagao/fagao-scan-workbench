# 上游参考与许可

采集适配的字段、页面结构和黑岩接口参考 [Oh Story](https://github.com/zenstory-ai/oh-story-claudecode)，固定参考提交 `7e26aa0708984ce81a41cd77ebfbbdf467c500b0`，2026-11-09 核对。

- `skills/story-long-scan/scripts/fanqie-rank-scraper.js`
- `skills/story-long-scan/scripts/qidian-rank-scraper.js`
- `skills/story-long-scan/scripts/ciweimao-rank-scraper.js`
- `skills/story-short-scan/scripts/heiyan-booklist-scraper.js`

此工程重新实现了 Python 采集和隔离浏览器流程，没有打包上游完整 Skill 产品；保留上游许可如下。

```
MIT License

Copyright (c) 2025-2026 oh-story-claudecode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

```

浏览器依赖 Playwright 采用 Apache-2.0 许可；通过 npm 按锁定版本安装，依赖包含自己的许可。平台内容及访问权限不因代码许可而改变。

## 用户提供的番茄榜单工具包

2026-11-09 按用户要求融合 `番茄榜单工具包-2026-11-09.zip` 的能力。源包 SHA-256：`11d8693276eab5f1dd6776dfcdb41d9fa7a5b1bfd20b298f3c53985ae28d80cd`。

`fagao/assets/fanqie-charmap.json` 保留源包 `02-抓取工具/fanqie_tool/assets/charmap.json` 的 362 条字符映射，文件 SHA-256：`6f607ae25910ca912104d97cf2a90c7b5a5f867712bcf5a6eb5bf4f8122ae533`。源包抓取模块的项目元数据声明 MIT，未提供独立的版权人署名或 LICENSE 文件；本说明保留该来源与许可声明，不据此扩大其他内容的许可范围。

官方公开目录／阅读页访问方式及分类采集思路参考源包；本工程重新实现与本地快照、任务、缓存和权限状态的连接。未引入其复刻网页的 HTML、JS、CSS、Chart.js、部署脚本、服务端地址、数据样本或小说正文。榜单统计由本工程独立实现，不接入源包的外部分析服务。


## FanqieRankTracker 公开榜单数据

v0.5 独立编写 JSON 适配器，读取 [scoful/FanqieRankTracker](https://github.com/scoful/FanqieRankTracker) 主动公布的静态新书榜接口与历史快照。该仓库 README 声明 MIT，核查时未见独立 LICENSE 文件。本项目不复制该仓库程序、样式、AI 摘要或小说正文；记录来源仓库、实际文件日期、每分类样本范围和缺失字段。公开榜单元数据不意味着取得小说正文权利。详细记录见 `docs/public-data-sources.md`。
