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
