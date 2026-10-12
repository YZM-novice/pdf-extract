# 04: 批量 standard 的 api-server 复用与必杀

**What to build:** 批量且需要 `standard` 档时，起一次常驻 api-server 复用 VLM，随后逐篇走远程调用，避免每篇重复加载大模型。启动前探测专用端口是否空闲：若已有本工具自己的健康服务则复用，否则报错。健康检查通过后才开始。跑完用 `try/finally` + `trap` 按进程组**必杀**子进程，写 pid 文件；启动时若发现陈旧 pid 先清理，保证无残留进程。

**Blocked by:** 02, 03.

**Status:** ready-for-agent

- [x] 批量 standard 只起一次服务、逐篇复用
- [x] 端口被占用时按约定复用或报错，不误伤他人进程
- [x] 服务健康检查通过后才开始转换
- [x] 跑完按进程组必杀，无残留进程
- [x] 启动时清理陈旧 pid

## Comments

- 2026-10-01: 实现完成。新增 `scripts/mineru_server.py`（固定的 127.0.0.1:18765 专用端口生命周期管理）与 `tests/test_server.py`；`pdf_extract.py` 在「批量且任一篇路由到 standard」时起服务、逐篇走 `--remote-url`，并新增 `--server-port`。全套 78 个测试通过。
- 归属协议：pid 文件 `XDG_CACHE_HOME|~/.cache/pdf-extract/api-server-<port>.pid`。仅当「pid 存活 + cmdline 含 mineru api-server + /v1/health 返回 status=ok」时才复用；否则只要端口被占就报错退出码 2，绝不 kill 他人进程。
- 启动顺序：先清陈旧 pid（进程已死/pid 被复用则仅删标记，不杀）→ `ss -ltn` 探测端口（无 ss 时 socket 回退）→ 决定复用/报错/新起 → 起后写 pid → 轮询 `/v1/health`，超时则先 stop 再报错。
- 必杀：子进程以 `start_new_session=True` 独立进程组，`try/finally` + 进程组 SIGTERM（10s 宽限后 SIGKILL）；另外在 CLI 安装 SIGTERM/SIGINT 处理器调 `stop()`，被外部终止也能清理。
- 真实冒烟：2×2 页 standard 批量只起一次服务并全部远程解析（~30s，无残留，端口释放）；自建健康服务+pid 文件时正确「reused」且不杀、不清 pid；对外来占用端口的进程报错退出 2 且该进程存活；运行中发 SIGTERM → 无残留进程、端口释放、pid 清除。
- 已知边界：若进程被 SIGKILL（无法捕获）遗留健康服务，下一次启动会把它当作自有健康服务复用并在结束时必杀，不会持续泄漏。
