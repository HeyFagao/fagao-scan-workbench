# GitHub 发行回读记录

发糕扫榜工作台 `0.7.0-dev.13` 将小说投稿并入原 WorkBuddy 安装流程。可从工作台顶部使用本机草稿，带入投稿助手后由作者选择收件人并核对自检邮件。[使用说明](local-submissions.md)

发行源码为 `676c3c827245376f54e069cf25d4f7d5eca88c29`。默认分支的[安装清单](../install-release.json)提供版本、固定提交、安装器、源码以及投稿组件的 SHA-256。Apple Silicon Mac、Intel Mac 和 Windows x64 分别使用该发行的对应组件。[GitHub 发行](https://github.com/ShiQi-Han03/fagao-scan-workbench/releases/tag/v0.7.0-dev.13)

在 macOS 及两套 Windows Python 环境，验收覆盖首次安装、从 dev.12 升级、重复安装、草稿保留以及从浏览器将作品名、笔名、邮件主题和正文交接给实际投稿窗口。初装的编辑名单有 226 个启用的联系渠道，本次投稿默认选中 0 个收件人。升级保留作者自己设置的启停状态。[Windows 原生验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43054370640)

通过公开的固定 GitHub 发行链接，在独立的 Windows 运行器使用原安装器匿名下载并校验组件，打开投稿窗口接受草稿。测试使用合成草稿，真实对外投递数量为零。[Windows 公开下载核验](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43055286886) · [源码凭据扫描](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43054370550)
