# GitHub 发行回读记录

版本：`0.7.0-dev.5`。发行仓库：[ShiQi-Han03/fagao-scan-workbench](https://github.com/ShiQi-Han03/fagao-scan-workbench)。原始来源为 hanyyyou/fagao-scan-workbench，原仓库未被本轮修改。

固定源码：`8bc355c7e64c8a8099d159af59136da39a2cd690`。公开下载核验时间：`2026-11-10T06:52:54.923581+00:00`。安装时始终读取默认分支的 [install-release.json](../install-release.json)，源码归档里的旧指针不能替代当前发行指针。

安装器：[固定源码安装器](https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/8bc355c7e64c8a8099d159af59136da39a2cd690/scripts/bootstrap.py)，SHA-256：`41d3f3de190ee27086f2b86d6c4c56a92f668330829b2fd01fd35cc83c9b501d`。
源码归档：[固定源码 ZIP](https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/8bc355c7e64c8a8099d159af59136da39a2cd690)，SHA-256：`04e184763b2488206f53cab5cbd4cfd088e6b3e37363d42fa7ed7fed87cccbca`。归档校验值为发行方匿名下载回读证据；安装器固定 Git 提交并检查解压安全。

## 已验证

- 匿名 raw 与 codeload 下载成功，归档 98 个文件与固定 Git 提交一致。
- 隔离 macOS 目录的首次、重复安装均返回 ready；健康接口版本、源码目录和资料目录一致。
- 重复安装复用原服务与固定入口，合成旧资料保留，未连接账号不能读取受保护资料。
- 测试服务已关闭，未改动用户原安装。
- Python 586 项、会员 UI 46 项、国内 UI 109 项及海外 UI 专项通过。

本版接收官网免费体验、字数包续期和会员使用权益，保持真实会员期限、设备限制与到期历史只读。到期页面使用现有发糕娘素材，付款返回后刷新资格。资格和期限由官网决定，客户端安装成功不代表已领取体验或获得使用权。

## 验证边界

未操作真实 WorkBuddy 界面、Windows 电脑、真实账号付款或真实榜单采集。官网权益、支付与发布另行维护；主站私有源码、配置、账号和数据不在本仓库。此前固定提交与标签保留。
