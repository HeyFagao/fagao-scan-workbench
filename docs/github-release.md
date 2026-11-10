# GitHub 发行回读记录

版本：`0.7.0-dev.8`。发行仓库：[ShiQi-Han03/fagao-scan-workbench](https://github.com/ShiQi-Han03/fagao-scan-workbench)。原始来源为 hanyyyou/fagao-scan-workbench，原仓库未被本轮修改。

固定源码：`9b571475c67e34d0f4d001366b4436fee1a920d8`。macOS 下载核验时间：`2026-11-10T13:46:00.211145+00:00`；Windows CI 核验时间：`2026-11-10T13:46:30.689709+00:00`。安装时读取默认分支的 [install-release.json](../install-release.json)，源码归档中的旧指针不能替代当前发行指针。

安装器：[固定源码安装器](https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/9b571475c67e34d0f4d001366b4436fee1a920d8/scripts/bootstrap.py)，SHA-256：`f9289d2d4557f2a943823768d71f6a9273d1dc82aaeba3a65c76a663eec41a52`。
源码归档：[固定源码 ZIP](https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/9b571475c67e34d0f4d001366b4436fee1a920d8)，SHA-256：`c1f10f75dff527546b30f9799bc2fa1422e4ee879e4a0cc0b70f9087956d3d01`。此哈希是安装器必填的强制校验值：缺失或不匹配时拒绝解压和安装。

## 这一版的变化

- 工作台顶部外观支持浅色、护眼、深色、跟随系统，当前浏览器保存选择，封面不反色。
- 外观模块不增加运行依赖且不改变会员/榜单逻辑。

## 已验证

- macOS 匿名 raw 与 codeload 下载、固定提交全部 115 个文件核对、安装器归档强制校验、首次及重复安装、版本和目录身份、资料保留、未登录资料隔离与测试服务关闭，11 项通过。
- 本地 Python 完整回归 682 项、会员 UI 46 项、国内 UI 146 项和海外 UI 专项通过；UI 外部平台请求为 0。
- 主题专项 20 项通过；四模式与实际 Chrome 157.0.0.0 / macOS 的桌面、手机尺寸视觉检查通过，保留 6 张合成数据截图。截图检查不代表真实手机设备或 Windows 主题视觉实测。
- [Windows 原生 CI](https://github.com/ShiQi-Han03/fagao-scan-workbench/actions/runs/42827177006) 的 Python 3.11 和 3.12 两个 job 均成功，每个 job 的 14 项场景与冻结源码逐项一致，测试进程和隔离目录均已清理。
- 两个 Windows job 的公开开篇、内置解密组件与收藏解析三套专项测试均通过。
- Windows 场景实际覆盖中文及空格路径、独立命令后的存活、固定 `.bat` 入口、运行中升级及旧资料保留、允许与拒绝 Job Object 脱离、原生后台托管恢复。证书失败由夹具注入，随后真实调用系统 Schannel curl 下载并校验官方归档。

| Windows Python | 原生检查 | 安装器测试：运行 / 跳过 | 启动器测试：运行 / 跳过 | 清理 |
| --- | ---: | ---: | ---: | --- |
| 3.11.9 | 14 | 65 / 2 | 23 / 0 | 通过 |
| 3.12.10 | 14 | 65 / 2 | 23 / 0 | 通过 |

## 复制给 WorkBuddy

> 请帮我安装或更新「发糕扫榜工作台」：https://github.com/ShiQi-Han03/fagao-scan-workbench 。按项目说明使用最新已发布版本，保留已有资料和设置，自动完成安装、启动和打开页面。遇到问题先自行诊断、修复并重试，不把排查命令交给我，不反复询问是否继续；确认安装命令结束后仍能打开，再告知完成。只有本人登录、验证码、授权、资料归属或系统必须由我批准时，才提醒我并让我自己操作；不要绕过安全校验。最后简短告诉我结果和下一步。

## 验证边界

Windows 结果来自 GitHub Actions 的真实 Windows 运行器，不是用户那台电脑或真实 WorkBuddy 客户端实测。安装验收未使用真实账号、未付款或采集真实榜单；本轮主题视觉使用隔离的合成数据，未实测真实 Windows 上的主题外观。企业证书、代理和宿主策略仍以目标电脑为准；安装 ready 不代表账号已有使用资格。官网权益与支付单独维护，主站私有代码、配置、用户资料和诊断不进入此公共仓库。此前固定提交与发行标签保留。
