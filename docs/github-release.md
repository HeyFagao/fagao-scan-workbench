# GitHub 发行回读记录

## v0.7.0-dev.17

发糕扫榜工作台的 GitHub 官方分发入口使用仓库编号 `1452172195` 核验。安装及更新时使用该仓库当前名称，投稿组件和榜单可识别更名前后的固定下载地址。

当前发行源码 `3b9dc0a979dbbd86d8c72414dd833299b9eb6788`。可继续按官网扫榜页的说明安装或更新。Windows 已验证全新安装、旧版电脑资料保留、投稿附件和五榜下载。[Windows 原生验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43211790281)

## v0.7.0-dev.16

工作台「长篇」增加飞卢小说网、七猫中文网、晋江文学城和纵横中文网。选择平台与该站提供的榜单后即可更新，按源站名次最多保存前 100 本，可收藏作品并打开原站详情。

已安装的用户可以继续把原安装说明交给 WorkBuddy 更新。个人收藏与本机设置保留。

[Windows 新设备安装和长篇榜单验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43192178092)

本次固定提交为 `a643a95d48f7c1b182f31e32f814d9a6091dbe37`。

## v0.7.0-dev.15

改善从网页打开「小说投稿」时的进度与错误提示。Windows 工作台可识别需要修复的投稿组件，沿用原 WorkBuddy 安装指令即可修复并保留草稿和已有设置。

Windows 验收覆盖空稿启动、再次从网页打开已最小化或收到托盘的投稿窗口，以及组件修复。使用原工作台安装说明即可获取此版。

[Windows 3.11 与 3.12 验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43174953818) · [Windows 匿名安装验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43174985045)

本次固定提交为 `9e6caa11090410bcca876d312b4efeed78738098`。

## v0.7.0-dev.14

首次打开红果热播榜等榜单时，如果主文件地址无法连接，工作台会自动通过备用地址读取同一份已校验的榜单。已连接账号可在「漫剧 → 红果热播榜」点「更新榜单」。

继续使用原 WorkBuddy 安装说明即可更新。小说投稿沿用 dev.13 的三份已校验组件。

[Windows 3.11 与 3.12 验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43156979456) · [Windows 匿名安装验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43157045712)

本次固定提交为 `31fe8d65effa0d36b04f353dfdfa3a9e7e80a4fa`。Windows 两套 Python 环境均在空缓存下实测主地址断开后从备用地址下载红果榜官方密文，10,148 字节且哈希与发布记录相符；已验收授权后校验、解密及缓存写入。

## v0.7.0-dev.13

发糕扫榜工作台 `0.7.0-dev.13` 将小说投稿并入原 WorkBuddy 安装流程。可从工作台顶部使用本机草稿，带入投稿助手后由作者选择收件人并核对自检邮件。[使用说明](local-submissions.md)

发行源码为 `676c3c827245376f54e069cf25d4f7d5eca88c29`。默认分支的[安装清单](../install-release.json)提供版本、固定提交、安装器、源码以及投稿组件的 SHA-256。Apple Silicon Mac、Intel Mac 和 Windows x64 分别使用该发行的对应组件。[GitHub 发行](https://github.com/ShiQi-Han03/fagao-scan-workbench/releases/tag/v0.7.0-dev.13)

在 macOS 及两套 Windows Python 环境，验收覆盖首次安装、从 dev.12 升级、重复安装、草稿保留以及从浏览器将作品名、笔名、邮件主题和正文交接给实际投稿窗口。初装的编辑名单有 226 个启用的联系渠道，本次投稿默认选中 0 个收件人。升级保留作者自己设置的启停状态。[Windows 原生验收](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43054370640)

通过公开的固定 GitHub 发行链接，在独立的 Windows 运行器使用原安装器匿名下载并校验组件，打开投稿窗口接受草稿。测试使用合成草稿，真实对外投递数量为零。[Windows 公开下载核验](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43055286886) · [源码凭据扫描](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/43054370550)
