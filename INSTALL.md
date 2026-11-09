# WorkBuddy 自动安装入口

这是给执行安装的助手看的说明。用户的目标是“发来一句话，最后看到工作台”。自行完成可执行的步骤，不把下面的终端操作转交给普通用户。官网登录、授权确认以及第三方平台登录需要用户本人完成。

本文件随 `0.7.0-dev.1` 开发候选交付。候选尚未公开发布，线上授权尚未启用；本轮公开源码基线是 `0.6.2`。普通用户安装会员版必须等待正式发布，不能从浮动 `main` 下载旧版后声称已完成会员接入。维护者本地预演方式见 [会员使用说明](docs/membership.md)。

## 1. 确认来源并固定版本

本项目的公开仓库是 [hanyyyou/fagao-scan-workbench](https://github.com/hanyyyou/fagao-scan-workbench)。用户复制 README 的安装话术时，`owner/repo` 为 `hanyyyou/fagao-scan-workbench`。若用户明确指定另一个来源，先核对该来源及用途；不要擅自切换到其他仓库，也不把参考项目 Oh Story 当成安装对象。公开仓库无需 GitHub 登录。

先读取仓库 `INSTALL.md`、`AGENTS.md`、`README.md` 与 `docs/membership.md`。核对正式发布说明中的版本和官网授权启用状态，再通过 GitHub 确认对应完整 40 位提交 SHA；不要将开发分支或浮动 `main` 直接当作正式版本。若用户指定版本，按该版本核验；若会员版尚未发布，报告真实状态并保留原安装。下载已确认 SHA 下的安装器：

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
- 先检查 Python 标准库、SQLite 读写、HTTPS 环境、页面文件与番茄字表；任何必需项失败都不打开浏览器，并返回维护助手可处理的原因。
- 运行项目启动器，核对健康响应中的应用身份、资料目录、源码版本目录。
- 通过健康检查后写入 `current.json`、固定启动入口和供 WorkBuddy 使用的固定项目目录。

读取安装器的 JSON 返回值。只有 `status=ready` 且 `readiness.status=ready` 才表示该版本的本地环境和服务已就绪。`readiness.features` 分别标明三家榜单与番茄正文的 `local_ready`，以及黑岩的 `external_link`。`network_verified=false` 表示这里没有替用户登录或验证外站网络，不把依赖自检当成全部平台可用。记录 `url`、`data_dir`、`release_dir`、`project_dir`、`interpreter`、`shortcut`。

`browser_opened=true` 只表示系统接受了打开请求；若助手拥有浏览器能力，还要检查窗口是否实际出现、页面是否是工作台。`browser_opened=false` 时，用宿主的打开网页能力访问返回的本机 URL。不要把远程服务器上的 localhost 地址当成用户电脑的工作台。

首次应看到连接官网账号的入口。由用户在官网登录并确认，工作台只接收专用授权，不读取密码、验证码或普通浏览器 Cookie。有效会员连接成功后，若发现旧匿名资料，先请用户选择认领或跳过，再启动自动采集。之后显示本账号三家平台汇总首页，也能进入长篇/短篇列表。到期账号核验身份后只显示已有资料；不能以本地服务 ready 冒充会员可用。“我的收藏”上方提供独立的“海外网文”入口，中文分类、英文书名和简介；海外采集也只依赖 Python 标准库，缺少两个 overseas 模块时启动自检会报错，维护助手应重新下载完整版本。番茄汇总只含男女频新书榜，阅读榜保留单独浏览；首次没有真实数据时显示真实读取状态。不要自动加载虚构榜单，不要强迫用户每次选择“本次任务类型”。初次完成后给用户固定启动入口；后续只说“打开发糕工作台”即可复用该入口。

## 4. 保留后续对话入口

用返回的 `project_dir` 作为 WorkBuddy 项目的稳定工作目录。这个目录的 AGENTS.md 会引导助手读取 `current.json` 并找到当前代码；不要把项目永远绑定在某个旧版本目录。

当前 WorkBuddy 若支持项目创建/配置，完成项目设置；若没有可调用入口，先完成工作台打开，并只说明一次如何将该目录设为项目工作目录。不编造已写入个人记忆或已安装市场 Skill。项目配置与兼容文件的官方说明见 [WorkBuddy 项目](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)。

在当前对话及后续该项目的对话中，执行 `docs/workbuddy.md`：解析当前选中/显示序号/原榜名次 → 固定作品任务 → 读取可用正文 → 由当前对话模型分析 → 保存报告。网页不需要直接调用 WorkBuddy 的模型 API。

## 5. 安装时完成依赖检查，不让用户点到功能后报缺项

当前日常功能包括三家长篇榜单、番茄公开正文和黑岩腾讯文档入口。它们不需要 Node 或 Playwright：前三者使用 Python 标准库，黑岩直接打开用户平时使用的浏览器。

在启动失败时读取具体原因，由 WorkBuddy 完成修复并重试：

- Python 缺少 `sqlite3` 或 `ssl` 等组件，按第 2 节准备完整的用户目录 Python，不尝试用 pip 安装同名替代包。
- 页面、番茄字表缺失或损坏，重新取得同一固定 SHA 的完整版本。保留资料目录；不能把缺少文件的安装标为完成。
- 需要单独诊断时执行 `PYTHON_ABSOLUTE_PATH scripts/launch.py --check`。此命令只进行本地自检，不启动服务、不联网、不打开浏览器。
- 打开浏览器失败时使用宿主浏览器打开安装器返回的 URL，再核对实际页面；不要把用户留在终端。

黑岩故事会使用这个固定书单：

<https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7>

点击“打开黑岩书单”后，由用户在腾讯文档页面登录或申请访问。工作台不显示未经核验的“已登录”，也不扫描普通浏览器 Cookie。不要改成 `manage.zhangwenpindu.cn` 后台；它是另一个来源。腾讯文档的登录与访问资格不能由安装器代为完成。

旧版留下的 Node 路径和专用浏览器资料仍保留；失效的可选 Node 路径不阻断当前功能。会员版不启动历史掌文后台 worker，避免旧采集路径脱离统一授权。黑岩的正常入口始终为腾讯文档书单；不能用安装旧适配器的方式绕过会员门禁。

## 6. 重复启动与更新

v0.6.1 起，长驻服务与采集 CLI 自动隔离父进程的临时代理，默认使用系统手动代理/直连。不要为服务复制 WorkBuddy 终端的 `HTTP_PROXY` 等变量。固定代理由应用自己的 `network-config` 配置，诊断方式见 [网络维护](docs/network.md)。升级后核对真实服务版本与选定的正式发布版本一致，并确认 `network.policy=isolated-system-proxy-v1`，不要用旧进程的健康响应判定修复完成。后台网络探测失败不阻止打开已有资料。

“打开”优先运行固定入口，不每次下载新版本。“更新”才重新解析目标版本并执行安装器。同版本会复用服务；原快照、收藏、正文、报告、账号会话保持在原 `data_dir`。

若返回 `restart_required`，表示新代码已备好但未切换。先核对现有服务身份、PID、源码路径和资料目录；只停止明确属于本工作台的旧进程，再执行同一安装命令。不能仅凭过期的 PID 文件结束进程。旧实例退出前不要宣称更新完成。

## 7. 交付验收

发布者依次验证：真实公开仓库可匿名读取 → 固定 SHA 下载 → 新目录安装 → 服务健康 → 用户浏览器实际显示界面 → 再次打开复用 → 更新后资料保留。会员版还需验证官网真实登录授权、3 台设备上限及移除、有效会员更新、到期历史只读、续费恢复、退款或撤销失效、退出与换号隔离、旧资料明确认领与保留。在 Windows、macOS 与 WorkBuddy 客户端分别记录实测情况。任一步未完成都保留具体状态；合成账号和本地联调不能替代上述验收。

用户最终只收到真实仓库链接与一段安装话术。源码归档是内部传输形式，不让用户逐个解压部署。
