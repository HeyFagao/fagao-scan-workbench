# WorkBuddy 自动安装入口

这是给执行安装的助手看的说明。用户的目标是“发来一句话，最后看到工作台”。自行完成可执行的步骤，不把下面的终端操作转交给普通用户。官网登录、授权确认以及第三方平台登录需要用户本人完成。

### 助手执行原则

- 一次接手完成下载、校验、环境准备、安装、启动、打开与验收。已获用户安装或更新指令后，正常诊断、修复和有限重试直接继续，不逐步询问“是否继续”，不把终端命令、证书排查或进程管理交给普通用户。
- 先复用已有解释器、安装入口和资料目录。固定提交的文档、安装器与来源只需审阅一次；没有版本或校验变化时，不在每次重试前重复整套审阅，也不重复向用户播报哈希、端口和内部路径。
- 每次失败先读取真实错误和安装器的结构化诊断，再选择对应恢复步骤。同一证书错误不盲目连跑，同一恢复路径连续失败后换已支持的路径；自动恢复均须有次数或时间上限。所有支持的路径仍失败时，给出具体阻碍和确实需要用户的一步操作，不能无限循环或伪报成功。
- 普通权限和系统现有安全配置内完成工作。不得关闭 TLS 校验、全局改代理或证书、永久放宽执行策略、申请无关管理员权限，也不创建系统服务、计划任务或开机启动项来规避宿主管理。
- 只在本人登录、验证码、官网授权确认、旧资料归属选择，或系统确实要求用户批准时打断。用户无需提供密码、Cookie、私有生产回执，也无需手动下载压缩包。
- 最终只报工作台是否已打开、旧资料是否保留、下一步是否需要登录。诊断细节保存在本机；仅在未解决、用户追问或维护交接时提供，且先脱敏。不要把内部检查清单当作面向用户的长篇交付结果。

本文件供 WorkBuddy 执行安装和升级。发布版本与校验值由发行仓库默认分支的 `install-release.json` 提供；不要求用户提供私有生产回执。账号资格以安装后官网实际返回为准，客户端安装成功不代表已经登录或取得使用资格。

Mac 和 Windows x64 的正常安装和更新会自动装配与这次发行相符的个人投稿组件。请沿用下文同一条 bootstrap 命令。成功后打开顶部「小说投稿」，读回本机组件已就绪。已有投稿任务或投稿窗口正在运行而本次需要换版时，请用户从投稿助手菜单完整退出，再用原命令继续。稿件和邮箱设置保存在独立的本机资料目录。

## 1. 确认来源并固定版本

当前发行仓库是 [ShiQi-Han03/fagao-scan-workbench](https://github.com/ShiQi-Han03/fagao-scan-workbench)，原始来源为 [hanyyyou/fagao-scan-workbench](https://github.com/hanyyyou/fagao-scan-workbench)。本流程的 `--repo` 使用 `ShiQi-Han03/fagao-scan-workbench`，不回退到原始仓库的旧版本。公开仓库下载无需 GitHub 登录，也不要求用户自己下载压缩包。

首次安装或用户明确要求更新时，由助手完成以下步骤：

1. 读取发行仓库默认分支的 `install-release.json`。当前固定入口为 `https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/main/install-release.json`。此文件是发行指针；安装源码归档可能先于指针提交，因此归档内没有这个文件时仍从上述公开地址读取。不要把本地旧副本当成当前发行状态。
2. 必须确认 `status` 为 `published`，`source_commit` 为完整的 40 位十六进制提交，`installer_sha256` 和 `source_archive_sha256` 均为完整的 64 位 SHA-256。检查该提交在发行仓库可获取，核对公开发布元数据中的版本。字段缺失、状态不符、无法获取或校验不符时，如实报告具体原因并保留原安装；不猜测链接、不改用浮动源码、不回退到旧版冒充完成。
3. 读取该 `source_commit` 对应的 `INSTALL.md`、`AGENTS.md`、`README.md` 与 `docs/membership.md`。再从同一提交下载 `scripts/bootstrap.py` 到临时目录。下载地址为：

   ```text
   https://raw.githubusercontent.com/ShiQi-Han03/fagao-scan-workbench/SOURCE_COMMIT/scripts/bootstrap.py
   ```

   将 `SOURCE_COMMIT` 替换为已经核验的 `source_commit`。计算下载文件的 SHA-256，必须与 `installer_sha256` 一致；随后阅读安装器，确认与本工程一致再执行。
4. 按第 2 节确认 Python，用第 3 节的 `--repo` 和 `--commit` 方式安装。安装器自动下载同一提交的完整工程、检查归档并建立固定入口。安装器与工程源码必须使用同一 `source_commit`，不能混用默认分支安装器和旧源码。

`install-release.json` 的 `published` 表示发行方已发布并核验对应客户端来源；它不代表当前电脑已经装好、外站均可访问或当前用户具有会员资格。按第 3、4、6、7 节完成本机检查、账号连接和资料保护。只要公开发布元数据与固定提交可核验，就不再要求用户提供服务器发布记录、账号清单或其他私有材料。

### 用户已提供完整源码包时

仍先读取发行指针，核对该包的来源和版本。仅在用户明确指定了可信源码包且具有相应校验记录时，才使用该包自己的 `scripts/bootstrap.py --source-dir ABSOLUTE_EXTRACTED_SOURCE`；同时保留原有安装根目录和资料目录。不要直接覆盖已经安装的 release，不同时传 `--repo` 或 `--commit`。普通的一句话安装优先使用上述固定 GitHub 提交流程，无需用户转交附件。

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
PYTHON_ABSOLUTE_PATH bootstrap.py --repo ShiQi-Han03/fagao-scan-workbench --commit SOURCE_COMMIT --archive-sha256 SOURCE_ARCHIVE_SHA256
```

将 `SOURCE_COMMIT` 和 `SOURCE_ARCHIVE_SHA256` 替换为第 1 节发行指针中的 `source_commit` 与 `source_archive_sha256`，`PYTHON_ABSOLUTE_PATH` 替换为第 2 节确认的解释器绝对路径。安装器在下载或写入前要求完整的归档校验值，下载后校验整包再解压；不能用临时计算的未知来源哈希代替发行指针。已有安装时先核对并复用原 `install_root` 与 `data_dir`；路径曾自定义时显式传入 `--install-root` 和 `--data-dir`，不要另建空资料目录代替升级。

普通安装不要传 `--no-browser`。不用启动第二个示例服务器，也不要改动线上发糕主站。默认服务只绑定 `127.0.0.1:18376`。

安装器会：

- 限制下载与解压大小，验证发行指针指定的源码归档 SHA-256 与归档目录；拒绝路径穿越、链接和特殊文件。
- 将固定版本放进 `app/releases/版本号`，保留已有资料；不执行包安装钩子。
- 先检查 Python 标准库、SQLite 读写、HTTPS 环境、页面文件与番茄字表；任何必需项失败都不打开浏览器，并返回维护助手可处理的原因。
- 运行项目启动器，核对健康响应中的应用身份、资料目录、源码版本目录。
- 通过健康检查后写入 `current.json`、固定启动入口和供 WorkBuddy 使用的固定项目目录。

读取安装器的 JSON 返回值。`status=ready` 且 `readiness.status=ready` 只说明安装器检查时本地环境和服务已就绪，尚不能证明宿主结束安装命令后服务仍在。GitHub 安装还须核对返回的 `release_id` 等于已选定的 `source_commit`，本机 `/api/health` 的 `version` 等于发行指针的 `app_version`、`source_root` 等于返回的 `release_dir`，且 `data_dir` 为预期资料目录。`readiness.features` 分别标明三家榜单与番茄正文的 `local_ready`，以及黑岩的 `external_link`。`network_verified=false` 表示这里没有替用户登录或验证外站网络，不把依赖自检当成全部平台可用。记录 `url`、`data_dir`、`release_dir`、`project_dir`、`interpreter`、`shortcut`。

**必须在安装命令返回后另起一次独立工具调用**，再次检查上述健康身份和页面响应。随后复用固定入口，确认打开的是同一份资料与同一版本；服务若已退出，先按第 5 节处理宿主后台运行，再继续验收。不能在同一个尚未结束的安装命令里连续请求两次，就声称已验证跨命令存活。

`browser_opened=true` 只表示系统接受了打开请求；若助手拥有浏览器能力，还要检查窗口是否实际出现、页面是否是工作台。`browser_opened=false` 时，用宿主的打开网页能力访问返回的本机 URL。不要把远程服务器上的 localhost 地址当成用户电脑的工作台。

首次应看到连接官网账号的入口。由用户在官网登录并确认，工作台只接收专用授权，不读取密码、验证码或普通浏览器 Cookie。具有官网返回的当前体验、字数包、会员或实验使用资格的账号连接成功后，若发现旧匿名资料，先请用户选择认领或跳过，再启动自动采集。之后显示本账号三家平台汇总首页，也能进入长篇/短篇列表。除指定的独立实验账号外，到期账号核验身份后只显示已有资料；不能以本地服务 ready 冒充授权可用。客户端不得靠改本机配置开启实验模式。“我的收藏”上方提供独立的“海外网文”入口，中文分类、英文书名和简介；海外采集也只依赖 Python 标准库，缺少两个 overseas 模块时启动自检会报错，维护助手应重新下载完整版本。番茄汇总只含男女频新书榜，阅读榜保留单独浏览；首次没有真实数据时显示真实读取状态。不要自动加载虚构榜单，不要强迫用户每次选择“本次任务类型”。初次完成后给用户固定启动入口；后续只说“打开发糕工作台”即可复用该入口。

## 4. 保留后续对话入口

用返回的 `project_dir` 作为 WorkBuddy 项目的稳定工作目录。这个目录的 AGENTS.md 会引导助手读取 `current.json` 并找到当前代码；不要把项目永远绑定在某个旧版本目录。

当前 WorkBuddy 若支持项目创建/配置，完成项目设置；若没有可调用入口，先完成工作台打开，并只说明一次如何将该目录设为项目工作目录。不编造已写入个人记忆或已安装市场 Skill。项目配置与兼容文件的官方说明见 [WorkBuddy 项目](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)。

在当前对话及后续该项目的对话中，执行 `docs/workbuddy.md`：解析当前选中/显示序号/原榜名次 → 固定作品任务 → 读取可用正文 → 由当前对话模型分析 → 保存报告。网页不需要直接调用 WorkBuddy 的模型 API。

## 5. 安装时完成依赖检查，不让用户点到功能后报缺项

当前日常功能包括三家长篇榜单、番茄公开正文、起点与刺猬猫前 10 章公开正文，以及黑岩腾讯文档入口。它们不需要 Node、Playwright 或额外的 pip 安装：读取功能使用 Python 标准库与随包提供的纯 Python 组件，黑岩直接打开用户平时使用的浏览器。

在启动失败时读取具体原因，由 WorkBuddy 完成修复并重试：

- Python 缺少 `sqlite3` 或 `ssl` 等组件，按第 2 节准备完整的用户目录 Python，不尝试用 pip 安装同名替代包。
- 页面、番茄字表缺失或损坏，重新取得同一固定 SHA 的完整版本。保留资料目录；不能把缺少文件的安装标为完成。
- 需要单独诊断时执行 `PYTHON_ABSOLUTE_PATH scripts/launch.py --check`。此命令只进行本地自检，不启动服务、不联网、不打开浏览器。
- 打开浏览器失败时使用宿主浏览器打开安装器返回的 URL，再核对实际页面；不要把用户留在终端。

### 下载或 HTTPS 出错

安装器针对可重试的连接失败和服务端暂时错误执行有界重试，总共最多尝试 3 次。Windows 出现 Python 证书链错误或暂时网络错误时会尝试受支持的系统 TLS 下载路径，仍然校验证书和归档 SHA-256；这与关闭证书校验不同。让安装器先完成这套恢复，不手工反复调用相同命令赌网络碰巧成功。

如果宿主已有能正常验证 HTTPS 的下载工具，可以由助手下载同一官方固定提交的 ZIP，并传给安装器；无需用户下载或解压，也不把它降为无法识别提交的本地源码版本：

```text
PYTHON_ABSOLUTE_PATH bootstrap.py --repo ShiQi-Han03/fagao-scan-workbench --commit SOURCE_COMMIT --archive-sha256 SOURCE_ARCHIVE_SHA256 --archive-file ABSOLUTE_OFFICIAL_ZIP_PATH
```

只使用 `https://codeload.github.com/ShiQi-Han03/fagao-scan-workbench/zip/SOURCE_COMMIT` 对应的官方归档，保持系统正常证书校验；归档仍由安装器验证大小、哈希和安全解压。不要混用 `--source-dir`，不要使用下载镜像或未经核验的旧包。哈希不符立即停止该路径并保留原安装，不能修改发行指针中的校验值去迁就文件。

### Windows 服务随安装命令退出

某些终端或助手宿主会在一次工具调用结束时清理其子进程。`DETACHED_PROCESS` 或一次健康响应不等于已摆脱这种生命周期管理。以安装后独立调用的结果为准；若进程消失且日志没有应用异常，不反复重新下载、不试一串 `start` 命令，也不让用户自己调进程。

启动器通过 `CREATE_BREAKAWAY_FROM_JOB` 请求独立运行，由 Windows 作业规则决定是否允许。如果系统拒绝，返回退出码 `3`、`status=background_required`、`code=native_background_required`，而不是降为安装命令结束就可能退出的后台进程。这个结果表示需要宿主托管服务，不表示安装已经完成。

优先使用当前宿主正式提供的后台任务/长运行工具，把返回的 `action.argv` 作为参数数组、`action.cwd` 作为工作目录执行。其命令形式为：

```text
PYTHON_ABSOLUTE_PATH RELEASE_DIR/scripts/launch.py --data-dir DATA_DIR --port PORT --foreground --no-browser
```

参数必须使用返回值，不猜测解释器、资料目录或端口。前台模式会完成相同的依赖和健康检查，打印 `status=started` 与 PID 后继续运行；不要把它作为普通等待退出的短命令执行。后台任务已建立后，用下一次独立工具调用访问 `action.health_url`，核对应用、源码版本与资料目录，再以返回的 `resume.argv` 参数数组和 `resume.cwd` 工作目录完成安装，最后打开页面。`resume.type=complete_install`、`archive_reverified_on_resume=true` 表示恢复时仍会重新核验保留的源码归档；不需要再次联网下载。安装器成功写入当前版本后清理自己保留的归档，不删除用户提供的 ZIP。

`background_required` 和 `restart_required` 都返回上述恢复指令；直接执行返回值，不自行拼接临时路径或假设上一次下载文件仍在。源码目录安装的恢复指令会指回原源码目录。安装先返回 ready 但下一次调用发现进程已消失时，按上述前台托管方式运行已安装的同一版本，再独立核对健康和固定入口；已完成安装时不需要重新下载或另建空库。

宿主不支持后台任务、或设备策略仍阻止运行时，保留已有安装与资料，简短说明真实阻碍；有已验证固定入口时给出该入口，但不要宣称后台已稳定。不要通过提权、计划任务或修改系统策略绕过限制。Windows 的 [进程创建标志](https://learn.microsoft.com/en-us/windows/win32/procthread/process-creation-flags) 与[作业对象](https://learn.microsoft.com/en-us/windows/win32/procthread/job-objects)说明了控制台分离与作业生命周期的区别；实际宿主行为仍须逐台验证。

黑岩故事会使用这个固定书单：

<https://docs.qq.com/sheet/DYVNwaVJZV2ZxTWl3?tab=q1ans7>

点击“打开黑岩书单”后，由用户在腾讯文档页面登录或申请访问。工作台不显示未经核验的“已登录”，也不扫描普通浏览器 Cookie。不要改成 `manage.zhangwenpindu.cn` 后台；它是另一个来源。腾讯文档的登录与访问资格不能由安装器代为完成。

旧版留下的 Node 路径和专用浏览器资料仍保留；失效的可选 Node 路径不阻断当前功能。会员版不启动历史掌文后台 worker，避免旧采集路径脱离统一授权。黑岩的正常入口始终为腾讯文档书单；不能用安装旧适配器的方式绕过会员门禁。

## 6. 重复启动与更新

v0.6.1 起，长驻服务与采集 CLI 自动隔离父进程的临时代理，默认使用系统手动代理/直连。不要为服务复制 WorkBuddy 终端的 `HTTP_PROXY` 等变量。固定代理由应用自己的 `network-config` 配置，诊断方式见 [网络维护](docs/network.md)。升级后核对真实服务版本与选定的正式发布版本一致，并确认 `network.policy=isolated-system-proxy-v1`，不要用旧进程的健康响应判定修复完成。后台网络探测失败不阻止打开已有资料。

“打开”优先运行固定入口，不每次下载新版本。“更新”才重新读取默认分支的 `install-release.json`，按第 1 节核验目标版本并执行安装器。同版本会复用服务；原快照、收藏、正文、报告、账号会话保持在原 `data_dir`。

若返回 `restart_required`，表示新代码已备好但未切换。先核对现有服务身份、PID、源码路径和资料目录；只停止明确属于本工作台的旧进程，确认退出后，以返回的 `resume.argv` 参数数组和 `resume.cwd` 工作目录继续安装。恢复会复用并重新校验保留的归档，不重复下载。不能仅凭过期的 PID 文件结束进程。旧实例退出前不要宣称更新完成。

## 7. 交付验收

发布者按实际渠道验证：公开仓库固定提交下载，或官网实验 ZIP 下载并回读 SHA-256 → 新目录安装 → 服务健康 → 用户浏览器实际显示界面 → 再次打开复用 → 更新后资料保留。本版还需验证官网免费首次领取、历史字数包首次领取、新购买续期、重叠权益与到期只读；官网真实登录授权、3 台设备上限及移除、有效四档会员（付费/赠送/测试）更新、独立实验授权开启及手动关闭、其他账号到期历史只读、续费恢复、退款/封禁/停用/撤销失效、退出与换号隔离、旧资料明确认领与保留。确认实验账号的真实会员截止仍保持原值，未伪造永久会员或付款记录。在 Windows、macOS 与 WorkBuddy 客户端分别记录实测情况。任一步未完成都保留具体状态；合成账号和本地联调不能替代上述验收。

每台用户电脑的安装交付必须包含：安装命令已经返回；下一次独立调用的健康身份和页面检查通过；再次打开复用同一份服务或由宿主后台任务持续托管；浏览器确实显示工作台。只能报告已经验证的宿主生命周期，不把“当前能打开”扩张为“关闭所有宿主或重启电脑后仍自动常驻”。登录授权尚待用户操作时，明确说“工作台已打开，请在官网完成连接”，而不是声称账号已可用。

用户最终只需复制 README 中的安装请求。助手通过公开发行指针取得固定版本与校验值，自行完成下载、校验、解压和固定入口安装；用户本人完成官网登录与授权确认。验收依据记录在公开发布说明或验收文档中，不把这些维护步骤转交给普通用户。
