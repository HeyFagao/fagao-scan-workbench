# 发布检查

源码发布到 GitHub；用户数据与工作台运行仍在本机。以下针对当前工作树，最终发布前再检查新增文件与提交差异。

## 历史公开基线检查（2026-11-09）

- 发布文件为源码、静态资源、通用文档、测试和依赖锁文件；未包含数据库、小说正文、浏览器资料、个人账号凭据或下载日志。
- 对已跟踪文本扫描常见 API/GitHub 令牌、私钥、凭据字面量和本次执行环境路径，未发现匹配。
- `docs/integration-results.json` 仅含公开来源地址、样本数及验证状态，可随源码发布；它不包含原始作品正文或用户数据。
- `THIRD_PARTY_NOTICES.md` 保留 Oh Story 参考提交及其完整 MIT 许可，说明 Playwright 的 Apache-2.0 许可。

## GitHub 发行状态

当前客户端为 `0.7.0-dev.4`，使用既有官网实验授权接口。发行仓库为 ShiQi-Han03/fagao-scan-workbench，保留 hanyyyou 原始来源。默认分支 install-release.json 指向通过公开下载和隔离安装核验的固定源码提交；只有发布回读后才能将其状态标为 published。官网当前账号资格由接口实时返回。验收记录见 [acceptance.md](acceptance.md)，旧版本记录保留为历史，不代替本版验证。

## 每次发布前

1. 检查 `git status --short`、`git diff --cached --stat` 与实际暂存差异。仅加入本次源码和文档；不要打包整个用户数据目录。
2. 确认暂存文件没有 `.env`、密钥、Cookie、令牌、SQLite 数据库、浏览器 profiles、正文、报告、日志、本机绝对路径或下载产生的运行状态。特别检查 `.membership/`、`accounts/`、`legacy-claims/`、`legacy-claim.json`、`legacy-decisions.json`、`.legacy-claim.lock` 和 `network.json`；确认忽略规则、安装器复制与归档拒绝规则都生效。核对压缩包目录与已批准的 Git 文件清单一致，不打包整个交接目录或主站私有源码。
3. 使用已核实的 Python 3.10+ 运行 `python3 -m unittest discover -s tests -v`，并运行会员 UI、国内 UI、海外 UI 验证。安装器、启动器、平台采集有改动时，执行对应实测并更新 `docs/acceptance.md`；未实测的 Windows/macOS 和 WorkBuddy 客户端保持如实标注。
4. 确认安装文档中的仓库、原始文件、版本或压缩包链接实际存在，安装后健康检查成功，再宣布可运行；不把文档中写出的命令当作已完成客户端验证。
5. 确认本地 HTTP 与浏览器控制仅监听 `127.0.0.1`，主站返回地址正确，积分站未配置时不可点击；代码公开不等于将用户资料服务公开。
6. 上游声明、第三方许可证和依赖锁文件随版本发布。项目自身许可与 README 的用词一致，不能只凭上游 MIT 声明所有新代码已有同样许可。
7. 在新目录安装并复用原资料目录，验证明确认领或跳过前没有自动采集、认领保留原库、退出和换号不泄露前账号数据、到期只读与续费恢复；不覆盖资料、不自动认领，也不改变已有服务的所属目录。
8. 完成官网和客户端的真实授权闭环、设备上限/移除、取消授权、退款/封禁/停用、许可过期、重启断网及账户切换验收。实验阶段验证四档当前有效的付费、赠送、测试权益均可用，普通到期账号历史只读；指定实验账号手动关闭前有独立工作台使用资格，不能写成永久会员。所有许可最长 15 分钟，普通会员不越官网截止时间，独立实验账号同样须续领；只读身份不能变成新任务权限。
9. 在发布记录写明本版是官方客户端控制。历史公开版本和修改过的源码仍可能运行，不声明已实现服务器端付费核心保护。主站源代码、支付配置和部署材料不进入本公共仓库。

## 安装资料的官方依据

- [WorkBuddy 项目配置](https://www.codebuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Project)：项目指令注入任务上下文，支持项目 `.codebuddy/` 配置并兼容 `AGENTS.md`。
- [WorkBuddy 技能安装](https://www.workbuddy.cn/docs/workbuddy/From-Beginner-to-Expert-Guide/Function-Description/Skills-Market)：市场技能可对话安装；自行提供的技能包通过上传入口导入。本工程使用项目上下文，不默认声称已成为市场技能。
- [uv 独立安装器](https://docs.astral.sh/uv/getting-started/installation/) 与 [安装位置选项](https://docs.astral.sh/uv/reference/installer/)：支持 Windows、macOS/Linux 及用户目录安装。
- [uv 托管 Python](https://docs.astral.sh/uv/guides/install-python/)：可取得指定 Python 版本，底层使用 Astral `python-build-standalone` 分发。

安装所需权限与网络能力仍以目标电脑为准；遇到企业策略或下载失败，应报告具体失败步骤，不能以模拟成功继续。
