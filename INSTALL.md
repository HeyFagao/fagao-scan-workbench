# WorkBuddy 自动安装入口

这是给执行安装的助手看的说明。用户的目标是“发来一句话，最后看到工作台”。自行完成可执行的步骤，不把下面的终端操作转交给普通用户。第三方平台登录需要用户本人完成。

## 1. 确认来源并固定版本

本项目的公开仓库是 [hanyyyou/fagao-scan-workbench](https://github.com/hanyyyou/fagao-scan-workbench)。用户复制 README 的安装话术时，`owner/repo` 为 `hanyyyou/fagao-scan-workbench`。若用户明确指定另一个来源，先核对该来源及用途；不要擅自切换到其他仓库，也不把参考项目 Oh Story 当成安装对象。公开仓库无需 GitHub 登录。

先读取仓库 `INSTALL.md`、`AGENTS.md` 与 `README.md`；通过 GitHub API 查询仓库的默认分支和该分支最新提交，取得完整 40 位 SHA。若用户指定版本，以指定版本对应的完整 SHA 为准。下载该 SHA 下的安装器：

```text
https://raw.githubusercontent.com/OWNER/REPO/FULL_SHA/scripts/bootstrap.py
```

这里的大写名称是执行时替换的参数，不是可直接访问的地址。将安装器保存到临时目录并阅读，确认与所选项目一致。**安装器和工程归档必须使用同一个 SHA**，不要混用浮动分支与旧版本脚本。工程归档由安装器从 GitHub codeload 下载，不要求 Git。

## 2. 准备 Python

优先使用本机已有的 Python 3.10+，验证版本并取得解释器绝对路径；Windows 检查 `py -3`，macOS/Linux 检查 `python3`。不要把商店占位程序或仅存在的命令名当成可用解释器。

若没有可用 Python，使用 Astral 官方 uv 的用户目录安装方式：

1. 在 Windows `%LOCALAPPDATA%/FagaoWorkbench/tools`，或 macOS/Linux `~/.fagao-workbench/tools` 中准备独立工具目录。已设置 `FAGAO_DATA_DIR` 时跟随该位置。
2. 从 `https://astral.sh/uv/install.ps1`（Windows）或 `https://astral.sh/uv/install.sh`（macOS/Linux）下载并检查安装脚本。设置 `UV_UNMANAGED_INSTALL` 为该工具目录下的 `uv`，再执行保存的脚本；不使用管理员权限、不改系统 Python 或全局 PATH、不永久放宽系统执行策略。
3. 当前进程设置 `UV_PYTHON_INSTALL_DIR` 为工具目录下的 `python`、`UV_PYTHON_INSTALL_BIN=0`、`UV_PYTHON_NO_REGISTRY=1`。通过 uv 的绝对路径执行 `python install 3.12`，随后执行 `python find --managed-python 3.12` 取得解释器路径。
4. 再次运行该解释器检查版本；后续命令及快捷入口都使用这个绝对路径。这个 Python 来自 Astral 的 python-build-standalone 分发，不称为 python.org 官方安装包。

具体参数以 [uv 安装器文档](https://docs.astral.sh/uv/reference/installer/) 与 [Python 安装文档](https://docs.astral.sh/uv/guides/install-python/) 为准。网络或设备策略阻止安装时，给出失败步骤和原因，不伪报完成，也不让用户尝试一串无关命令。

## 3. 安装、启动并打开

使用已经验证的 Python 执行下载的安装器（实际调用请使用参数数组或对应 shell 的正确引用，兼容中文和空格路径）：

```text
PYTHON_ABSOLUTE_PATH bootstrap.py --repo OWNER/REPO --commit FULL_SHA
```

普通安装不要传 `--no-browser`。不用启动第二个示例服务器，也不要改动线上发糕主站。默认服务只绑定 `127.0.0.1:18376`。

安装器会：

- 限制下载与解压大小，校验归档目录；拒绝路径穿越、链接和特殊文件。
- 将固定版本放进 `app/releases/版本号`，保留已有资料；不执行包安装钩子。
- 运行项目启动器，核对健康响应中的应用身份、资料目录、源码版本目录。
- 通过健康检查后写入 `current.json`、固定启动入口和供 WorkBuddy 使用的固定项目目录。

读取安装器的 JSON 返回值。只有 `status=ready` 才表示该版本服务已就绪。记录 `url`、`data_dir`、`release_dir`、`project_dir`、`interpreter`、`shortcut`。

`browser_opened=true` 只表示系统接受了打开请求；若助手拥有浏览器能力，还要检查窗口是否实际出现、页面是否是工作台。`browser_opened=false` 时，用宿主的打开网页能力访问返回的本机 URL。不要把远程服务器上的 localhost 地址当成用户电脑的工作台。

用户应直接看到长篇/短篇工作台；首次没有真实数据时显示明确的空状态。不要自动加载虚构榜单，不要强迫用户每次选择“本次任务类型”。初次完成后给用户固定启动入口；后续只说“打开发糕工作台”即可复用该入口。

## 4. 保留后续对话入口

用返回的 `project_dir` 作为 WorkBuddy 项目的稳定工作目录。这个目录的 AGENTS.md 会引导助手读取 `current.json` 并找到当前代码；不要把项目永远绑定在某个旧版本目录。

当前 WorkBuddy 若支持项目创建/配置，完成项目设置；若没有可调用入口，先完成工作台打开，并只说明一次如何将该目录设为项目工作目录。不编造已写入个人记忆或已安装市场 Skill。项目配置与兼容文件的官方说明见 [WorkBuddy 项目](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)。

在当前对话及后续该项目的对话中，执行 `docs/workbuddy.md`：解析当前选中/显示序号/原榜名次 → 固定作品任务 → 读取可用正文 → 由当前对话模型分析 → 保存报告。网页不需要直接调用 WorkBuddy 的模型 API。

## 5. 黑岩浏览器依赖

主界面和三家长篇的已接入书目路径只需 Python 标准库。先把工作台打开，再准备黑岩所需浏览器环境，避免额外下载阻塞整个首屏。

检查 Node 满足 `package.json` 的版本要求，缺少时使用 Node.js 官方发行及校验信息准备用户目录运行时，不改变系统 Node。在当前 `release_dir` 执行 `npm ci --omit=dev --ignore-scripts`，再执行 `npm run browser:install`。这会使用锁文件版本，不安装测试依赖。

Node 必须能被工作台的服务进程找到；不能只在助手的某条命令中临时可用。若为私有 Node，在核心安装完成后，将其绝对路径写入 `app/runtime.json` 的 `node_executable` 字段，例如 `{"node_executable":"实际的 node 可执行文件绝对路径"}`。安装器与固定入口会仅为工作台子进程补充该目录的 PATH，不改全局环境，不覆盖这个本机配置。核对并重启已有工作台服务后，再检查 worker 是否可启动；不要复用其他工具的临时运行时路径，也不要修改生成的固定入口。

依赖完成后，让用户点击短篇黑岩卡片的登录入口，在平台窗口本人登录。仅取得真实书库响应后才显示可用；不能预设共享账号或把普通读者账号当成有书库访问权。依赖或账号步骤失败时保留可用的工作台，其余平台继续使用。

## 6. 重复启动与更新

“打开”优先运行固定入口，不每次下载新版本。“更新”才重新解析目标版本并执行安装器。同版本会复用服务；原快照、收藏、正文、报告、账号会话保持在原 `data_dir`。

若返回 `restart_required`，表示新代码已备好但未切换。先核对现有服务身份、PID、源码路径和资料目录；只停止明确属于本工作台的旧进程，再执行同一安装命令。不能仅凭过期的 PID 文件结束进程。旧实例退出前不要宣称更新完成。

## 7. 交付验收

发布者依次验证：真实公开仓库可匿名读取 → 固定 SHA 下载 → 新目录安装 → 服务健康 → 用户浏览器实际显示界面 → 再次打开复用 → 更新后资料保留。在 Windows、macOS 与 WorkBuddy 客户端分别记录实测情况。任一步未完成都保留具体状态。

用户最终只收到真实仓库链接与一段安装话术。源码归档是内部传输形式，不让用户逐个解压部署。
