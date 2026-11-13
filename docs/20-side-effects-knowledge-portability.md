# 20 · 外部副作用幂等、知识生命周期与可携带导出（migration 0012）

本轮围绕**不可回滚外部副作用的跨重试幂等保护与人工对账门禁**、**记忆与 Skill 的持续演化/评估/归档生命周期**、**严格隔离且需所有者批准的跨项目知识推广**以及**无密文可携带项目导出包**推进，持续保持控制平面对能力边界的诚实声明。

## 外部副作用幂等登记与人工对账门禁（TaskSideEffect）

- `POST /api/v1/tasks/{task_id}/side-effects`（租约持有 Agent，锁定任务与副作用记录）：在执行真实外部动作（如发帖、提交表单、付款、外发通知等）前，按 `(task_id, idempotency_key)` 登记唯一外部副作用声明，数据库唯一约束 `uq_task_side_effect_key` 保证并发请求只注册一次。
- `POST /api/v1/side-effects/{effect_id}/report`（同租约、同 `lease_epoch` 执行者）：上报执行结果摘要，状态仅允许设为 `reported_executed` 或 `unknown`。
- **防止跨重试/跨 Epoch 重复执行**：
  - 同一任务在新 `lease_epoch` 上若尝试再次注册同一个 `idempotency_key`（只要其状态不是 `verified_not_executed`），服务端一律以 `409 Conflict` 拒绝，防止网络中断或租约切换后重复触发外部动作。
  - 若任务存在任意处于 `pending` 或 `unknown` 的未核对副作用，控制平面自动阻断后续 `claim()` 认领以及所有者 `POST /api/v1/tasks/{id}/unblock`，直到所有者完成人工对账。
- `POST /api/v1/side-effects/{effect_id}/reconcile`（仅所有者，至少 5 字对账说明）：将副作用显式核对为 `verified_executed`（允许任务安全继续/恢复，但保留幂等锁禁止重执行）或 `verified_not_executed`（允许后续新 epoch 执行者合法重开该 `idempotency_key`）。
- 公开视图字段明确标示 `reversible_by_control_plane: false`——控制平面只能记录、拦截重放和要求人工核对，不会虚称可自动撤销或回滚第三方系统中的已发生副作用。
- `GET /api/v1/tasks/{task_id}/handoff` 附带完整 `side_effects` 清单；`GET /api/v1/projects/{project_id}/briefing` 在 `pending_owner_actions` 中汇总 `side_effects_unreconciled`。
- MCP 桥接（`otternest.mcp_bridge`）同步新增 `register_task_side_effect` 与 `report_task_side_effect` 工具（共 15 个工具）。

## 记忆与 Skill 生命周期（修订、评估、归档）

- **记忆修订与归档（Memory）**：
  - `POST /api/v1/memories/{memory_id}/revise`（仅所有者，需提供至少 5 字修订原因）：原地更新标题、内容与出处并递增 `version`，保持 `published` 状态；已归档记忆禁止直接修订。
  - `POST /api/v1/memories/{memory_id}/archive`（仅所有者，需提供至少 5 字原因）：将记忆标记为 `archived` 并递增 `version`。归档记录仍保留在数据库事件与导出审计中，但不再注入 Agent `task_context` 及非所有者 `GET /memories` 查询。
- **Skill 评估与归档（SkillEvaluation / Skill）**：
  - `POST /api/v1/skills/{skill_id}/evaluations`（本项目授权成员或所有者）：记录结构化评估（`verdict: pass | fail | concern`、`notes`、可选同项目 `task_id`；跨项目任务会被 `422` 拒绝），并写入 `skill.evaluated` 事件与审计。
  - `GET /api/v1/skills/{skill_id}/evaluations`：本项目成员或所有者查看评估历史。
  - `POST /api/v1/skills/{skill_id}/archive`（仅所有者）：将过时或有问题 Skill 标记为 `archived`，非所有者 `GET /skills` 仅可见 `published` 条目。

## 跨项目知识推广与项目边界隔离（CrossProjectShare）

- `POST /api/v1/projects/{project_id}/knowledge-shares`（提案者须同时拥有源项目与目标项目成员资格或为所有者）：只能针对已发布（`status == "published"`）的记忆或 Skill 发起共享提案。仅单一项目成员的 Agent 无法探知或向其他项目注入内容。
- `GET /api/v1/projects/{project_id}/knowledge-shares`：查看涉及该项目作为源或目标的共享提案。
- `POST /api/v1/knowledge-shares/{share_id}/resolve`（仅所有者）：批准（`approved: true`）后在目标项目中创建一条独立克隆副本，并在 `source` 字段强制写入来源溯源标识 `cross_project:{source_project_id}:{kind}:{source_id}`（目标项目必须为 `active`，且同版本 Skill 不重复克隆）；拒绝（`approved: false`）则保持目标项目完全不受影响。

## 可携带无密文项目导出（Portable Export）

- `GET /api/v1/projects/{project_id}/export`（仅所有者）：将项目、宪章、目标、成员关系、任务与依赖、Epoch 检查点、副作用台账、记忆、决策与两轮评审/反驳、Skills 与评估记录、跨项目共享提案、成果校验和（不含凭证/Secret）以及事件日志导出为规范化 JSON 包。
- 导出载荷显式声明 `schema_version: "otternest.export.v1"`、`secrets_included: false`，并提供规范 JSON 的 `sha256_digest` 校验摘要，便于离线迁移、审计和备份。

## 升级与已知限制

- 数据库迁移 `0012_side_effects_knowledge_lifecycle.py` 新增 `task_side_effects`、`skill_evaluations` 与 `cross_project_shares` 表及约束索引。
- 尚未实现自动外部对账轮询、向量检索（语义 RAG）、跨项目共享自动导入（仍坚持复制而非跨项目引用共享与所有者审批），以及全量导入还原接口。
