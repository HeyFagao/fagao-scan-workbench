# 上游参考与许可

采集适配的字段、页面结构和黑岩接口参考 [Oh Story](https://github.com/zenstory-ai/oh-story-claudecode)，固定参考提交 `7e26aa0708984ce81a41cd77ebfbbdf467c500b0`，2026-11-09 核对。

- `skills/story-long-scan/scripts/fanqie-rank-scraper.js`
- `skills/story-long-scan/scripts/qidian-rank-scraper.js`
- `skills/story-long-scan/scripts/ciweimao-rank-scraper.js`
- `skills/story-short-scan/scripts/heiyan-booklist-scraper.js`

新增七猫和晋江适配时另核对了 Oh Story 的[七猫脚本](https://github.com/zenstory-ai/oh-story-claudecode/blob/41d28fcde00bc60956f9fa432a569478c43a217e/skills/story-long-scan/scripts/qimao-rank-scraper.js)和[晋江脚本](https://github.com/zenstory-ai/oh-story-claudecode/blob/7e26aa0708984ce81a41cd77ebfbbdf467c500b0/skills/story-long-scan/scripts/jjwxc-rank-scraper.js)。工作台的 Python 适配器独立读取官网公开榜页，晋江榜型和分站以页面当时选中的栏目为准。飞卢与纵横同样按各自官网榜页独立实现。

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


## 2026-11-10 工具包中的 DSH 扫榜插件

2026-11-10 核对用户提供的更新包 `番茄榜单工具包-2026-11-10.zip`，源包 SHA-256：`d4da09b0173edf8de05c0948d78efafa7e7332dc33aa99f0ab18fc9b8f9c5116`。其中 `06-扫榜插件/dsh-rank-scan` 是 [kilo-s/dsh-rank-scan](https://github.com/kilo-s/dsh-rank-scan) 的 `0.6.4` 编译产物，声明 BSD-3-Clause 许可，版权人为 `kilo-s`。

本工程仅对照其榜单、书页元数据和缓存设计。该插件明确不登录、不抓正文，附件中没有起点或刺猬猫的正文下载实现。两站官方公开阅读内容的前 10 章适配由本工程自行实现，不引入完整 DSH bundle、DeepSeekHarness 宿主、第三方统计接口或源包中的安装、部署与探测脚本。源包的数据样本、小说正文、用户资料和日志不随本项目发布。

保留附件插件的完整许可与署名如下：

```text
BSD 3-Clause License

Copyright (c) 2026, kilo-s
All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions are met:

1. Redistributions of source code must retain the above copyright notice, this
   list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright notice,
   this list of conditions and the following disclaimer in the documentation
   and/or other materials provided with the distribution.

3. Neither the name of the copyright holder nor the names of its
   contributors may be used to endorse or promote products derived from
   this software without specific prior written permission.

THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
```

## pyaes 1.6.1

为读取官方允许匿名访问的公开章节格式，客户端随包包含 [Richard Moore 的 pyaes](https://github.com/ricmoo/pyaes)，采用 MIT 许可，Copyright (c) 2014 Richard Moore。完整原文许可保存在 [fagao/_vendor/pyaes/LICENSE.txt](fagao/_vendor/pyaes/LICENSE.txt)。平台的内容权利和访问资格不因本依赖的许可而改变。

固定来源为 [PyPI pyaes 1.6.1](https://pypi.org/project/pyaes/1.6.1/) 的官方 `pyaes-1.6.1.tar.gz`，归档 SHA-256：`02c1b1405c38d3c370b085fb952dd8bea3fadcee6411ad99f312cc129c536d8f`。`fagao/_vendor/pyaes/` 中四个 Python 运行文件与许可证均逐字保留；[UPSTREAM.json](fagao/_vendor/pyaes/UPSTREAM.json) 记录原始下载地址、逐文件大小和 SHA-256。发行版以 PyPI 归档的 `PKG-INFO` 为准；上游 `__init__.py` 中较旧的 `VERSION` 常量也原样保留，未据此伪造版本号。

本工程的 `fagao/reader_crypto.py` 只调用其 CBC 解密，并独立严格检查 PKCS#7 填充、密钥／IV 长度及输入大小。没有使用上游宽松的去填充函数。此依赖不用于凭据、本机资料加密或设计新密码协议，不包含安装钩子，不在用户电脑上运行 pip、Node、编译或依赖下载。验证覆盖 NIST SP 800-38A 的 AES-128/192/256 CBC 向量，以及独立生成的合法和非法填充样例。

## 本地小说投稿

[投稿组件第三方许可](docs/submission-third-party/NOTICE.txt)随组件提供。各平台发行归档另附对应 Electron、Chromium 和 JavaScript 依赖的许可文本。
