# 独立 Harness Worker：受控测试环境部署与演练（不自动执行）

> 此指南提供**部署模板**，不是已经部署或成功跨机运行的证明。与 `otternest-node-probe` 分开：Probe 只监控，Worker 才有任务执行能力。首期使用独立测试项目、低风险 R0/R1 任务、有限能力、人工验收；禁止用于生产自动部署。

## 控制平面准备

1. 在测试项目中登记独立的执行 Agent（`execution_mode=polling`），添加为项目 Worker 成员；只核验测试需要的能力。
2. 为该 Agent 注册 **Worker 专用** Runtime，签发独立 `on_runtime_` 令牌；**不要复用探针专用 Runtime/Token**。不要复制所有者 Token、数据库连接串或部署凭据到 Worker。
3. 初期不开放 R3，高风险任务仍走原有审批。启动前确保 `/runtimes` 中可定位该 Runtime，确认其当前状态与环境标识。

## 独立 Linux 节点准备

- Python 3.11+、HTTPS 出站、`bubblewrap`（`/usr/bin/bwrap`）和 `prlimit`，可用的 Linux user namespace；检查系统是否允许该用户启动 bubblewrap。
- 建立不具备 sudo/Docker socket/SSH 私钥的 `otternest-worker` 专用账号。
- 安装已审核的 OtterNest 包到 `/opt/otternest-node/venv`（参照探针文档），并由管理员创建 `/var/lib/otternest-worker/workspaces`，所属用户为 `otternest-worker`，权限 `0700`。
- 将 `deploy/harness-worker.env.example` 的副本放至 `/etc/otternest-worker/worker.env`，仅 root 可读，权限 `0600`。真实值只在机器本地填写，不能提交 Git。
- 将 `deploy/systemd/otternest-harness-worker.service.example` 复制为实际 systemd unit，但**不要直接 enable/start**。将示例中的 `/usr/bin/APPROVED_SYSTEM_WORKER` 替换为已审查的真实程序；沙箱对该程序要求是 `/usr` 下的受信任系统二进制。未经替换的模板必须保持不可运行。

## 启动前门槛

1. 检查服务配置 `systemd-analyze verify`，确认进程用户、工作目录、权限和令牌文件。
2. 在测试环境用一次性低风险任务做 `--once` 演练；必须使用隔离的测试 Agent、专用 Runtime Token，任务内容不得含密钥。首次运行前对用户程序的 stdin JSON 解析、失败退出、输出上限和执行超时进行审查。
3. 检查 sandbox 实际成功启动；若 user namespace/bwrap 不可用，**停止部署**，不要切换为 `--sandbox none` 绕过保护。
4. 验证任务提交后状态是 `submitted`，不会自动转为 `completed`；只有所有者审核才最终完成。
5. 轮换/禁用 Worker Runtime Token 后，新任务领取和心跳应失败。模拟断网、控制端重启、Runner 崩溃后，检查租约及 epoch 对账；不得盲目重复执行外部副作用。
6. 验收通过且明确允许该 Worker 处理的任务类别后，才使用 `systemctl enable --now` 启用常驻服务。

## 已知边界与风险

- systemd `Restart=on-failure` 只负责**进程重启**，不等于租约恢复、任务幂等或跨机任务续跑。
- Bubblewrap 提供私有网络/文件系统视图，但不能替代宿主机隔离、cgroup 限额、安全审计；尤其不要把未经信任的第三方代码默认当安全。
- Probe 的 `monitoring` 状态防止它被**自动调度**；它不等于服务端持久化的“只监控权限”凭据。对探针凭据也要严格保密，后续应增加服务端独立 scope 才能对人工调用做到完整权限隔离。
- Worker 专用 Runtime Token 可以承担 Agent 获授权任务：请确保测试阶段只注册在有限权限的独立 Agent 上。
- 本文档与模板不能宣称实机已联调：真实服务器的安装、网络限制和密钥放置需要运维侧完成。
