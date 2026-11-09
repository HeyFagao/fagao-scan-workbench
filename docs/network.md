# 网络配置与会话代理隔离（v0.6.1）

工作台服务可能由 WorkBuddy、IDE 或终端启动。这些进程的 `HTTP_PROXY` 等变量可能指向临时端口，不能作为长驻服务的网络配置。

## 默认行为

- 启动器在创建服务前移除 `HTTP_PROXY`、`HTTPS_PROXY`、`ALL_PROXY`、`NO_PROXY`、`FTP_PROXY` 的全部大小写变体，保留 Python、PATH、证书和资料目录等运行环境。
- 直接运行服务或 CLI 时也在入口清理这些变量。国内与海外榜单、番茄正文与公开榜单补充源统一使用工作台网络层，不使用 urllib 全局默认 opener。
- 默认 `system`：每次请求重新读取 macOS/Windows 系统手动代理；Linux 默认直连。不会缓存 WorkBuddy 的会话代理地址。PAC 自动代理脚本不在本版支持范围。
- 只有系统配置中的本机代理明确拒绝连接时，才新建请求并直连重试一次。用户明确设置的固定代理失败时返回错误；HTTP 拒绝、验证码、登录、证书错误均保持原有处理。
- 请求日志记录目标主机、路由、代理主机和端口、异常类型与错误码；不记录查询参数、正文、Cookie 或代理密码。

## 应用自己的配置

配置存放在资料目录的 `network.json`，程序升级保留该文件。由 WorkBuddy 使用安装时确定的 Python 和原资料目录执行：

```sh
python3 -m fagao --data-dir DATA_DIR network-config --mode system
python3 -m fagao --data-dir DATA_DIR network-config --mode direct
python3 -m fagao --data-dir DATA_DIR network-config --mode proxy --proxy-url http://127.0.0.1:PORT
```

`DATA_DIR`、`PORT` 须替换为用户设备上的实际值。固定代理仅接受不带账号密码的 HTTP 代理地址；不把临时会话端口写进持久配置。每次新请求重新读取配置，无需为切换网络模式重启服务。

## 诊断与启动

`/api/health` 的 `network` 和 `/api/network` 返回当前策略、最近请求状态与启动探测结果。启动时后台通过真实采集请求检查一次番茄榜单响应头，记录目标、时间、耗时和结果；页面打开不等待这个请求。单次 socket 等待为 5 秒，启动探测状态超过 10 秒会结束为超时，迟到结果不会覆盖超时状态。

本地 `readiness.status=ready` 说明运行环境和服务就绪。`network.probe.status` 分为 `not_checked`、`checking`、`reachable`、`failed`。番茄页面可连接不能代表另外两家或正文均可取得，实际任务结果分别记录。需要重新诊断时执行：

```sh
python3 -m fagao --data-dir DATA_DIR network-check
```

该命令检测当前 CLI 的应用网络策略；诊断正在运行的服务则读取它的 `/api/network`，避免把另一个进程的结果冒充服务已恢复。

## 从旧版升级

固定最新 main 提交，按 INSTALL.md 安装 v0.6.1。若旧服务仍占用端口，核对应用身份、资料目录、源码路径和实际进程后停止旧实例，再完成安装。验收 `/api/health.version` 为 `0.6.1` 且 `network.policy` 为 `isolated-system-proxy-v1`；旧进程的健康响应没有该字段时不能判定隔离已生效。

安装目录有完整性校验，正式修复应更新整个固定版本；不要现场修改 release 内的文件后宣称已完成升级。
