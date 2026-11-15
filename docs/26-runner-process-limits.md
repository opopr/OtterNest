# 26 · Runner 子进程回收与 Linux 资源限制

本轮在 PR #12 基础上加强执行器故障处理：即使启动的工作程序退出、超时或失去租约，Runner 也应尽量回收其直接创建的进程组，避免留下仍在运行的子进程。针对显式启用 `--sandbox bwrap` 的工作程序，额外提供 util-linux `prlimit` 资源护栏。**本轮不声称完成了 cgroup v2 配额或生产级恶意代码隔离。**

## 运行方式

系统需要 Linux 与 bubblewrap。使用资源限制时，还需要以系统软件包安装的 `prlimit`。不要从不可信目录加载这两个程序。无 Docker 容器依赖。

```bash
sudo apt-get install bubblewrap util-linux
mkdir -p "$HOME/otternest-workspaces"
chmod 700 "$HOME/otternest-workspaces"

export OTTERNEST_AGENT_TOKEN='<agent-token>'
otternest-runner \
  --server http://127.0.0.1:8000 \
  --once \
  --sandbox bwrap \
  --workspace-root "$HOME/otternest-workspaces" \
  --timeout 120 \
  --cpu-seconds 60 \
  --address-space-mib 512 \
  -- /usr/bin/python3 -c 'import sys,json; p=json.load(sys.stdin); print(json.dumps({"summary":p["task"]["title"]}))'
```

这些限制只有在选择 `--sandbox bwrap` 时才允许启用，且**不会默默忽略用户声明的配额**。如果 `prlimit` 缺失、无效、不是 root 拥有或可被组/其他人修改；如果 Linux 无法建立所需沙箱；或者输入限额超出范围，Runner 都会在领取任务前失败。

## 具体安全与资源语义

- `--cpu-seconds`（1–3600）：内核 `RLIMIT_CPU`，限制**每个被执行进程**累计 CPU 时间，而不是整个任务进程树的共享 CPU 配额，也不是墙钟耗时。现有 `--timeout` 仍限制墙钟执行时长。
- `--address-space-mib`（64–16384）：`RLIMIT_AS`，限制**每个进程**的虚拟地址空间（包括虚拟映射），**不等于**真实常驻内存 RSS，也不等于整个任务的内存总量。需要对整个任务的物理内存进行限制时，必须补充 cgroup v2 `memory.max`。
- 使用独立 OS 会话和进程组启动工作程序。无论执行结果成功、超时、心跳报告租约失效，还是其他异常，Runner 都会先向该进程组发送 SIGTERM，再对剩余组内进程发送 SIGKILL；如有存活的主进程则等待回收。对于 bwrap，结束其外层 PID 命名空间领导进程还会触发命名空间内进程终止。
- 这不是对任意进程树的万能回收：没有启用沙箱的**可信**子程序可以自行脱离进程组；OS 崩溃、执行器被 SIGKILL、内核漏洞或者拥有额外主机权限时也无法保证回收。未获得完整资源隔离或安全审计的任务不得运行高风险不可信代码。
- 预检验证用户配置的可执行文件位置，但只运行一个固定的 `true` 探针；绝不为了预检而运行用户工作代码。只有预检通过才领取远程任务。
- 旧有 Agent epoch、DAG 接力、SHA-256 成果校验、检查点与所有者审核流程保持不变。子进程不持有控制平面 Token。

## 测试

持续集成运行 `tests/test_runner_process_limits.py`，检查输入边界、配置拒绝、Linux 限额命令构造、超时和正常退出后的子进程清理，以及在 Linux 环境使用真实内核 CPU 时间限制。真实 bubblewrap 命名空间联调仍需在具备 user namespace 的独立 Linux / WSL2 环境显式进行（参见 [25 · bubblewrap 隔离](25-runner-bwrap-isolation.md)）。

下一阶段重点应是 **cgroup v2 的任务级 `memory.max`、`cpu.max`、`pids.max` 和 `cgroup.kill`**，以及完整权限、生命周期、并发与恢复测试；此项仍未完成，不得用本轮进程级 RLIMIT 代替。
