# GitHub 发行回读记录

版本：`0.7.0-dev.6`。发行仓库：[ShiQi-Han03/fagao-scan-workbench](https://github.com/ShiQi-Han03/fagao-scan-workbench)。原始来源为 hanyyyou/fagao-scan-workbench，原仓库未被本轮修改。

固定源码：`76279ffa67e860d425704d487cb0bbaf2b7d347e`。macOS 下载核验时间：`2026-11-10T10:59:06.860027+00:00`；Windows CI 核验时间：`2026-11-10T11:00:49.847425+00:00`。安装时读取默认分支的 [install-release.json](../install-release.json)，源码归档中的旧指针不能替代当前发行指针。

安装器：[固定源码安装器](https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/76279ffa67e860d425704d487cb0bbaf2b7d347e/scripts/bootstrap.py)，SHA-256：`f9289d2d4557f2a943823768d71f6a9273d1dc82aaeba3a65c76a663eec41a52`。
源码归档：[固定源码 ZIP](https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/76279ffa67e860d425704d487cb0bbaf2b7d347e)，SHA-256：`f798e40178448e49d3a03fc470cbfc37a20c3d2b89bba9bd1da0f7252645ca86`。此哈希是安装器必填的强制校验值：缺失或不匹配时拒绝解压和安装。

## 这一版的变化

- Windows 的 Python 证书或暂时连接错误可使用系统 Schannel curl 下载路径恢复，保留 TLS 证书校验、固定来源、大小限制和归档 SHA-256；不修改系统证书或代理，不关闭安全校验。
- 安装命令结束后重新核验服务与版本。操作系统允许时使用正常的分离启动；受限宿主拒绝后台存活时返回 `background_required`，由安装助手使用宿主原生后台任务托管前台服务，保留已核验归档以继续恢复。
- 重复安装、版本升级和后台恢复保留已有 SQLite 资料、设置与固定入口；保留前一版源码。个人登录、授权和旧资料归属由用户自己确认。
- README 和 INSTALL 提供同一套自动完成、有限重试和简短交付要求，不让普通用户执行排查命令。

## 已验证

- macOS 匿名 raw 与 codeload 下载、固定提交全部 100 个文件核对、安装器归档强制校验、首次及重复安装、版本和目录身份、资料保留、未登录资料隔离与测试服务关闭，11 项通过。
- 本地 Python 完整回归 633 项、会员 UI 46 项、国内 UI 109 项和海外 UI 专项通过；UI 外部平台请求为 0。
- [Windows 原生 CI](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/42803648013) 的 Python 3.11 和 3.12 两个 job 均成功，每个 job 的 14 项场景与冻结源码逐项一致，测试进程和隔离目录均已清理。
- Windows 场景实际覆盖中文及空格路径、独立命令后的存活、固定 `.bat` 入口、运行中升级及旧资料保留、允许与拒绝 Job Object 脱离、原生后台托管恢复。证书失败由夹具注入，随后真实调用系统 Schannel curl 下载并校验官方归档。

| Windows Python | 原生检查 | 安装器测试：运行 / 跳过 | 启动器测试：运行 / 跳过 | 清理 |
| --- | ---: | ---: | ---: | --- |
| 3.11.9 | 14 | 65 / 2 | 21 / 0 | 通过 |
| 3.12.10 | 14 | 65 / 2 | 21 / 0 | 通过 |

## 复制给 WorkBuddy

> 请帮我安装或更新「发糕扫榜工作台」：https://github.com/ShiQi-Han03/fagao-scan-workbench 。按项目说明使用最新已发布版本，保留已有资料和设置，自动完成安装、启动和打开页面。遇到问题先自行诊断、修复并重试，不把排查命令交给我，不反复询问是否继续；确认安装命令结束后仍能打开，再告知完成。只有本人登录、验证码、授权、资料归属或系统必须由我批准时，才提醒我并让我自己操作；不要绕过安全校验。最后简短告诉我结果和下一步。

## 验证边界

Windows 结果来自 GitHub Actions 的真实 Windows 运行器，不是用户那台电脑或真实 WorkBuddy 客户端实测。未登录真实账号、未进行真实付款或真实榜单采集。企业证书、代理和宿主策略仍以目标电脑为准；安装 ready 不代表账号已有使用资格。官网权益与支付单独维护，主站私有代码、配置、用户资料和诊断不进入此公共仓库。此前固定提交与发行标签保留。
