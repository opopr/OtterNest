# 27 · cgroup v2 任务级资源控制（可选，需 Linux 委托）

OtterNest 在原有 `--sandbox bwrap`（bubblewrap 命名空间）以及可选每进程 `prlimit` 的基础上，增加 **按任务进程树共享计量和限流的 cgroup v2**。每次任务创建新子 cgroup，写入 `memory.max`、`cpu.max` 和 `pids.max`，在实际工作程序执行**之前**把启动进程加入该 cgroup，随后由 Linux 内核对继承到该 cgroup 的子进程实施限制。结束时执行 `cgroup.kill` 并尝试删除该子组。

**禁止夸大：**当前实现需要真实宿主机权限配置和实际内核验证；CI 默认只验证逻辑和失败关闭。它还不能取代安全审计、最小权限运行身份、seccomp、可信来源校验或独立 VM。没有 Docker 强制依赖。

## 前置准备

需在一台**独立执行主机**上由运维预先建立用户可写的 cgroup v2 委托子树。软件**不会**替用户修改祖先 cgroup、系统服务或全局 `cgroup.subtree_control`。父目录必须位于 `/sys/fs/cgroup` 下，拥有该 Runner 的进程 UID、不可被组/其他用户修改，且其 `cgroup.subtree_control` 已包含 `cpu memory pids`。内核必须支持 `cgroup.kill`。必要时用 systemd 的 `Delegate=yes` 配置一个专用低权限服务/用户范围；具体委托方式依赖系统发行版及版本，**不要把系统 cgroup 根目录直接授予 Runner**。

如果是 WSL2 / Oracle 云 Linux：先检查系统是否挂载并委托了 cgroup v2；即便 `bwrap` 可以运行，也不意味着当前用户有权创建 cgroup 子组。无委托则该功能**拒绝启动**，不以模拟限额代替真实内核保护。

## Runner 参数

```bash
export OTTERNEST_RUNTIME_TOKEN='<runtime-token>'
export OTTERNEST_RUNTIME_ID='<runtime-id>'
# 兼容模式（未绑定 Runtime）：
# export OTTERNEST_AGENT_TOKEN='<agent-token>'
otternest-runner \
  --server https://YOUR_PRIVATE_HOST \
  --once \
  --sandbox bwrap \
  --workspace-root /srv/otternest/private-workspaces \
  --cgroup-parent /sys/fs/cgroup/YOUR_DELEGATED_SUBTREE \
  --memory-max-mib 512 \
  --cpu-max-percent 100 \
  --pids-max 64 \
  --timeout 120 \
  -- /usr/bin/python3 -c 'import json,sys; task=json.load(sys.stdin)["task"]; print(json.dumps({"summary":task["title"]}))'
```

上述四项 `--cgroup-*` 和预算参数必须**一起**提供，否则拒绝启动。选项范围：`memory.max` 为 64–16384 MiB；`cpu.max` 相对于一个逻辑 CPU 的使用率是 10–400%，即 100% 写为 `100000 100000`；`pids.max` 为 8–1024 个任务（包括线程）。数值在启动时严格校验。

任务每次创建独立子组，关闭时**始终**写入其 `cgroup.kill`，避免工作程序从普通进程组逃逸后残留。子组意外无法删除时，任务以错误终止并应由操作员检查。重启崩溃后残留子组的恢复扫描、审计和 GC 仍需后续版本增加。

## 为什么选择「先入组，后执行」

执行器不采用「先 `Popen` 执行用户程序，再把 PID 写入 `cgroup.procs`」：这会产生进程在未限额状态下执行或派生子进程的竞态。

相反，Runner 先打开独立子 cgroup 的 `cgroup.procs`，启动**固定的可信 Python 启动小程序**，由它把自身 PID 写入继承的文件描述符，关闭文件描述符，然后才用 `execvpe` 替换为 `prlimit` / `bwrap` / 用户指定程序。启动小程序本身不执行任务正文，不继承控制平面 Token。如果入组失败，后续的 worker 程序绝不会运行。Runner 使用 `pass_fds` 仅向启动小程序传递这一个已授权的 cgroup 文件描述符，后续执行程序不会继承该句柄。

## 限制与验证

- `cgroup v2 memory.max` 限制任务组整体内存计量；超限时内核可能触发组内 OOM killer。与 `RLIMIT_AS` 虚拟地址空间限制不同。
- `cpu.max` 是任务组总体的 CPU 带宽限流，**不是任务级累计 CPU 秒数**；`pids.max` 限制组内任务和线程数量。
- `--timeout` 仍负责墙钟限时；Epoch 租约/心跳负责控制平面中止；bubblewrap 负责 namespace 和断网；这几种机制互相补充，不能相互替代。
- 需要手工联调时在隔离 Linux 测试机设置 `OTTERNEST_LIVE_CGROUP_PARENT=/sys/fs/cgroup/YOUR_DELEGATED_SUBTREE` 再运行 `pytest -q tests/test_runner_cgroups.py`。常规 CI 对真实 cgroup 操作默认跳过，绝不把模拟文件写入解释为真实内核限额生效。
- 仅在显式指定 `--sandbox bwrap` 时允许启用 cgroup 预算；缺少委托、控制器、内核 `cgroup.kill` 或启动检查失败，都不领取任务。
- **待补**：进程崩溃后 orphan cgroup 扫描与清理、恶意程序隔离压测、seccomp 规则、任务级监控指标和复杂多租约并发恢复；真实 Codex、Gemini 等平台联调也尚未完成。

参见 [25 · bubblewrap](25-runner-bwrap-isolation.md)、[26 · Runner 进程回收和 RLIMIT](26-runner-process-limits.md)。


推荐使用 Runtime 级凭据启动 Runner：设置 `OTTERNEST_RUNTIME_TOKEN` 和 `OTTERNEST_RUNTIME_ID`。Runtime Token 只能为签发时绑定的实例发送心跳；Runner 会在读取队列前先验证绑定关系。旧 `OTTERNEST_AGENT_TOKEN` 仍可用于兼容部署。两种控制平面凭据均不会传递给子进程。
