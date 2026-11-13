# 19 · 项目宪章硬预算、Epoch 检查点、租约撤回与运行简报（migration 0011）

本轮新增项目级硬预算、任务中间检查点、受控租约撤回交接和诚实运行简报，围绕控制平面权威状态与断点可恢复边界推进，仍不代表完整自主多 Agent 平台或远程进程强杀能力已就绪。

## 项目宪章与硬预算（ProjectCharter）

- `PUT /api/v1/projects/{project_id}/charter`（所有者）与 `GET /api/v1/projects/{project_id}/charter`（项目成员或所有者）管理项目宪章。
- 包含 `scope_boundary`、`stop_conditions`、`max_concurrent_tasks`（1–50）和 `max_total_tasks`（1–2000，且并发上限不得超过总量上限）。
- **硬性服务端门禁**：
  - `POST /api/v1/projects/{project_id}/tasks` 与 A2A `message/send` 在锁住项目行后校验非取消任务总数不超过 `max_total_tasks`（A2A 对相同 `messageId` 的幂等重放仍可返回原任务）。
  - `claim` 在事务中校验当前运行中任务数不超过 `max_concurrent_tasks`；`choose_dispatch_targets` 调度器把已运行任务与已定向 `ready` 任务一并计入占用额度，不超额指派。
- **生命周期控制**：`POST /api/v1/projects/{project_id}/status` 支持将项目切换为 `active`、`paused` 或 `archived`（需提供至少 5 字原因）。当项目非 `active` 时，控制平面停止新任务创建、调度与领取；但返回明确提示 `remote_processes_stopped: false`，不会假定外部正在执行的进程已终止。

## Epoch 隔离检查点与交接包（TaskCheckpoint / Handoff）

- `POST /api/v1/tasks/{task_id}/checkpoints`：仅持有当前有效租约与准确 `lease_epoch` 的执行 Agent 可提交进度检查点（每轮 `lease_epoch` 最多 50 条），并可绑定同一任务的 `artifact_ids`。
- **自述与核验分离**：新提交的检查点固定为 `verification_status: "self_reported"`（`verified_by_owner: false`）。仅所有者可通过 `POST /api/v1/checkpoints/{checkpoint_id}/verify` 将其标记为 `owner_verified` 或 `rejected`（驳回须提供至少 5 字原因）。
- `GET /api/v1/tasks/{task_id}/handoff` 组装标准交接包，显式区分 `latest_verified_checkpoint`（所有者已核验）与 `latest_self_reported_checkpoint`（Agent 自述未核验），并附带目标、依赖、成果校验和、已批准项目决策及交接边界说明。
- `GET /api/v1/tasks/{task_id}/context` 在返回执行上下文时附带 `latest_verified_checkpoint`，仅向恢复接手的执行者提供经过所有者核验的最新检查点。
- MCP 工具扩展：`otternest.mcp_bridge` 新增 `record_task_checkpoint` 与 `get_task_handoff`，底层仍走同一套 Agent Token 权限与租约隔离校验。

## 受控租约撤回与交接（Revoke Lease）

- `POST /api/v1/tasks/{task_id}/revoke-lease`（所有者）：针对 `running` 状态任务，增加 `lease_epoch` 以隔离旧执行者后续心跳、检查点写入、产物上传和提交，清空 `lease_until` 与 `assigned_agent_id`，并支持可选指定合法项目成员 `target_agent_id`。
- **边界保护**：
  - 若任务存在未释放的外部 A2A 委托（`A2ADelegation.status != "released"`），拒绝直接本地撤回，要求走远端取消流程。
  - `R3` 高风险任务撤回租约后强制回到 `blocked` 并重置 `approval_granted=False`，要求人工核查后重新解锁与授权。
  - 返回 `remote_execution_stopped_verified: false`——撤回租约只隔离控制平面写入权，不代表远端主机进程已停止或外部副作用已回滚。

## 诚实运行简报（Briefing）

- `GET /api/v1/projects/{project_id}/briefing` 汇总项目宪章用量、任务状态分布、待所有者审批/验收/阻塞事项以及近期已核验检查点与成果。
- 明确区分 `running_lease_active`（中心租约未过期）与 `running_lease_expired_unverified`（中心租约已过期但未收到结束或恢复），并在 `honesty_notice` 中声明租约状态基于控制平面时间戳，不代表远端执行进程已获得实时核实。

## 升级与已知限制

- 数据库迁移 `0011_charter_checkpoints_handoff.py` 新增 `project_charters` 与 `task_checkpoints` 表。
- 尚未实现跨主机自动化进程强杀、API 费用计量、自动 checkpoint 触发或真实第三方平台外部副作用回滚。
