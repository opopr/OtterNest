# 28 · Runner 异常退出后的 cgroup 恢复与接单保护

上轮任务级 `memory.max`、`cpu.max`、`pids.max` 和 `cgroup.kill` 只能覆盖正常关闭或 Runner 能正常执行异常处理的情况。**如果 Runner 被强制终止、主机重启或内核突然退出，cgroup 目录可能遗留**；且其中原有任务进程可能仍在运行。

本轮增加「同一委托父目录独占锁 + 启动前遗留检查 + 显式恢复工具」：不自动清除可疑的运行中任务，不自动扩大授权。

## 运行语义

在配置任务级 cgroup 时，Runner：
1. 预检原有 bubblewrap 的可用性；验证 cgroup v2 委托父目录。
2. 为 **该 UID 和委托父目录** 使用确定性 lock 文件（`/tmp/otternest-cgroup-<uid>-<sha256-prefix>.lock`），通过 Linux `flock(LOCK_EX|LOCK_NB)` 取得独占权。检查文件所有者、权限和软硬链接，避免跟随符号链接；锁持有到 Runner 关闭。
3. 只扫描名字严格符合 `otternest-task-<8-char-suffix>` 的直属子 cgroup；读取 `cgroup.events` 中的 `populated` 字段。发现遗留目录（无论是否有存活进程）就**停止启动、拒绝领取新任务**；人工恢复后才重新启动。
4. 若没有遗留目录，进行原有 cgroup 自入组探针，再正常接单。每项新任务仍会拥有独立资源组并按流程清理。

该 `flock` 只约束**安装了此版本且使用同一委托父目录**的 Runner；不能限制旧版本 Runner、同一 UID 的恶意进程或其他不合作服务。因此一个委托父目录只能用于一组受到相同运维管理的 Runner；恢复前必须停止旧版本进程。

## 恢复工具

安装项目后，执行：

```bash
# 只预览，绝不清理或终止进程
otternest-cgroup-recover --parent /sys/fs/cgroup/YOUR_DELEGATED_SUBTREE

# 确认已停止其他 Runner 后，仅清理空资源组
otternest-cgroup-recover \
  --parent /sys/fs/cgroup/YOUR_DELEGATED_SUBTREE --apply

# 危险：操作员确认所有相关运行实例已经停止或已允许中断，
#       显式要求终止遗留组的运行中进程
otternest-cgroup-recover \
  --parent /sys/fs/cgroup/YOUR_DELEGATED_SUBTREE \
  --apply --terminate-populated
```

预览返回 JSON，分别显示空组和 populated（含存活进程）组；无 `--apply` 时不做任何修改。普通 `--apply` 对 populated 组只报告并保留；只有额外给出 `--terminate-populated` 时，才通过对应子组的 `cgroup.kill` 向内核请求终止其进程，并尝试移除该资源组。

工具只能操作符合严格命名规则且属当前用户的直属任务组，**不能**修改祖先 cgroup 配额或停止其他名称的组。若权限不足、目录名可疑、出现 symlink、缺少 `cgroup.events`，则失败关闭。执行失败或残留 cgroup 无法删除时，向操作员返回错误；不会默认为已完全恢复。

## 安全边界和后续

- **重要**：`--apply --terminate-populated` 会对组内进程产生真实终止效果。操作员必须先审查预览结果、确认父目录专用且已停止旧版 Runner。不应对共用 cgroup 父目录使用该操作。
- 锁文件位于具有 sticky bit 的标准 `/tmp`，由当前 UID 拥有、权限最多 0600；同 UID 进程不是安全隔离边界。勿在多不互信用户共用一个 Unix UID 的机器上运行。
- 本轮恢复是**运维显式操作**，不是无人值守的自动强杀/定时 GC；在获取系统级证据证明存活进程归属与安全性之前不应自动终止它们。
- 仍需要在具有委托权限的真实 Linux、WSL2 或独立 ARM 主机完成内核联调。普通 GitHub Actions 通过的测试不等于已经完成这些实际部署测试。
- 后续应补充任务级审计指标、真正的端到端恢复演练、cgroup 资源使用观测和对可信 Codex/Gemini CLI 的独立执行节点联调。

参见 [27 · cgroup v2 任务级资源控制](27-cgroup-v2-task-budgets.md)。
