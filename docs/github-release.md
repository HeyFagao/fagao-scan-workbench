# GitHub 发行回读记录

版本：`0.7.0-dev.3`。发行仓库：[ShiQi-Han03/fagao-scan-workbench](https://github.com/ShiQi-Han03/fagao-scan-workbench)。原始来源为 hanyyyou/fagao-scan-workbench，原仓库没有被本轮修改。

源码固定提交：`d5e7a4d7fa2f9a462af23dcfa19b1111aa734e90`。公开下载核验时间：`2026-11-10T03:55:04.335378+00:00`。当前安装指针见默认分支的 [install-release.json](../install-release.json)。固定源码提交先于该指针发布；源码包缺少指针时，按 INSTALL.md 从默认分支读取。

安装器：[https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/d5e7a4d7fa2f9a462af23dcfa19b1111aa734e90/scripts/bootstrap.py](https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/d5e7a4d7fa2f9a462af23dcfa19b1111aa734e90/scripts/bootstrap.py)。SHA-256：`41d3f3de190ee27086f2b86d6c4c56a92f668330829b2fd01fd35cc83c9b501d`。

源码归档：[https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/d5e7a4d7fa2f9a462af23dcfa19b1111aa734e90](https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/d5e7a4d7fa2f9a462af23dcfa19b1111aa734e90)。本次下载 SHA-256：`912c1c37538440bcf81cc2e024972501edc2dc5f7abca229e41f9b6f3d0122f7`。归档校验值是发行方本次匿名下载的回读证据；现有 bootstrap 固定 Git 提交并检查解压安全，未新增读取该哈希字段的执行逻辑。

## 实际完成

- 未使用 GitHub 登录凭据，实际通过公开 raw 与 codeload 下载；下载的安装器与提交内容一致，归档 95 个文件与该提交文件清单一致。
- 在隔离 macOS 目录实际执行下载后的安装器，通过 `--repo` 和完整 `--commit` 首次安装及重复安装，均返回 ready。
- 本机健康接口核对应用版本、资料目录和源码目录；重复安装复用同一个服务及固定入口，预置的合成旧资料原样保留。
- 固定 WorkBuddy 项目说明和源码维护文档存在；未登录状态不能读取受保护资料。测试服务完成后已关闭，未修改现有用户安装。
- Python 测试 574 项、会员 UI 37 项、国内 UI 109 项及海外 UI 专项通过。并发资料初始化修复覆盖新旧库、真实锁冲突、迁移回滚及损坏库保护。

## 本次验证边界

没有操作真实 WorkBuddy 客户端界面、Windows 电脑或真实会员登录；没有调用真实榜单采集。源码下载不需要 GitHub 登录，工作台会员资格仍须由用户在官网自行登录并确认。安装 ready 只表示对应本机服务和依赖就绪，不代表所有第三方平台都已可访问。

官网会员、订单、支付和实验规则未因本轮客户端发行改动。主站私有代码、账号名单、服务器配置和用户资料不在这个仓库中。
