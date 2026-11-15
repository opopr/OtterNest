# 25 · Linux 受控代码执行（bubblewrap，可选）

OtterNest 的通用 Runner 原先只约束了 JSON 输入、环境变量继承和成果上传路径，**不**隔离进程、文件系统或网络。本次新增 `--sandbox bwrap`，用于执行由操作员明确配置的本地代码 Worker；它不需要 Docker，也不会更改现有 FastAPI / PostgreSQL 架构。

**重要边界**：当前模式提供 Linux 命名空间和只读运行时的基础隔离，不是针对任意恶意程序的生产级强安全保证；尚未实现 cgroup CPU/内存/PID 硬配额、专用 seccomp 策略、内核漏洞防护、网络出站代理或虚拟机隔离。只有在安全评估和独立执行宿主机上才能逐步放行高风险代码。

## 使用要求

- 仅 Linux / WSL2；安装系统软件 `bubblewrap`（命令 `bwrap`，必须位于 root 拥有且不可被组/其他用户修改的 `/usr` 路径）。
- 执行者使用单独的低权限 Linux 账户；绝不以拥有 PostgreSQL、部署密钥或私有数据的服务账户运行不可信任务。
- `--workspace-root` 为执行者可访问、已经存在的绝对私有目录。每一任务新建模式为 0700 的工作目录，任务结束后自动清理。
- 由运维者预先批准执行程序，且可执行文件必须是 `/usr` 内已安装的系统工具（例如 `/usr/bin/python3`）；脚本和数据可写到沙箱内部的 `/work`。
- 当前沙箱断网：**不能**在其中调用 Gemini/Codex/Muse 或外部模型 API。调用模型和运行不可信代码应采用不同 Worker 身份与执行策略。
- 不允许 `--pass-env` 给沙箱传递任何主机变量，尤其不能传管理令牌和模型 API 密钥。

## 本地尝试

先在隔离 Linux 主机或 WSL2 创建私有目录，并检查命名空间支持：

```bash
sudo apt-get install bubblewrap
mkdir -p "$HOME/otternest-workspaces"
chmod 700 "$HOME/otternest-workspaces"
```

使用单独的 Agent Token。下面的固定 Python 程序只是示范成果回传：它把任务标题作为**文本**写入文件，不执行任务描述中的代码或 shell 命令。

```bash
export OTTERNEST_AGENT_TOKEN='<agent-token>'
otternest-runner \
  --server http://127.0.0.1:8000 \
  --once \
  --sandbox bwrap \
  --workspace-root "$HOME/otternest-workspaces" \
  -- /usr/bin/python3 -c 'import json,sys,pathlib; p=json.load(sys.stdin); pathlib.Path("result.txt").write_text(p["task"]["title"],encoding="utf8"); print(json.dumps({"summary":"示例文件已生成，等待所有者审核","artifacts":[{"path":"result.txt"}]}))'
```

若 bubblewrap 不存在、Linux 内核不允许创建所需命名空间、预检返回错误、工作目录不私有、执行程序不位于允许的系统路径，Runner **在领取任务前终止**，不会自动以 `--sandbox none` 重试。若运行时隔离失败，任务会走原有失败/人工干预流程。

## 实际隔离边界

- 使用 bubblewrap 的全部命名空间隔离（含网络、PID、IPC、用户），采用 `--die-with-parent`、`--new-session`、`--cap-drop ALL`。
- 只读挂载 `/usr`，以及系统中实际存在的 `/bin`、`/sbin`、`/lib`、`/lib64` 系统目录或其 usr-merge 符号链接。
- 沙箱独立 `/proc`、`/dev`、临时 `/tmp`；当前任务目录读写挂载为 `/work`；沙箱 `HOME=/work`。
- 不挂载主机 `/home`、`/root`、`/etc`、`/run`、`/var`，也不继承主机环境变量。旧 `--sandbox none` 模式仍保留，仅供**已经可信**的子进程使用。
- 保留原有执行超时、租约检查、输出上限、成果哈希校验与所有者审核；这些仍不是 OS 资源配额或跨请求事务。

## 验证范围与下一步

普通 CI 的单元测试检查命令构造、拒绝不安全路径、禁止继承主机凭据以及预检失败关闭执行。真实 Linux 命名空间测试默认跳过，可在独立安全测试主机显式指定 `OTTERNEST_LIVE_SANDBOX_TEST=1 pytest -q tests/test_runner_sandbox.py`。该测试只验证基本隔离，并不意味着沙箱达到防攻击认证。

后续必须补齐 cgroup v2 CPU/内存/PID 限额、进程树回收、严格 seccomp、受控源码/依赖输入、代码审查、端到端真实 Worker 联调，并将执行宿主机与控制平面隔离。生产运行前需进行威胁建模和恢复演练。
