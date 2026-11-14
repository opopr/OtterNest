# 11 · 正式产品开发跟踪

## 当前阶段

OtterNest 已进入**完整产品开发**。此文件是工程状态登记，而非缩减需求的 MVP 计划。未实现项不应被标记为已完成。

**技术栈**：Python 3.11+、FastAPI、SQLAlchemy、PostgreSQL、Alembic、轻量原生 Web；控制平面与执行平面解耦部署。

## 功能矩阵

| 领域 | 当前实际状态 | 完整产品目标 |
|---|---|---|
| 项目与长期目标 | 基础 API、项目宪章与硬预算（范围/并发/总量）、生命周期暂停/归档 | 目标依赖、自治契约、Portfolio |
| 账号 / Agent / 运行环境 | 注册、令牌、项目成员、运行实例与心跳、能力声明/核验、待办轮询、通用 CLI Runner | 能力验证、环境探测、健康状态、Agent 生命周期 |
| 任务执行 | 领取、租约、epoch、提交、失败阻塞、人工验收；依赖 DAG、优先级、定向分配、调度；epoch 检查点/核验、租约撤回与交接包 | 依赖 DAG、调度、checkpoint、恢复、外部副作用防重 |
| 数据与记忆 | 记忆提案/发布/修订/归档、受审跨项目知识推广、无密文项目导出及暂停状态导入恢复、授权上下文包与内部证据检查 | 自动检索排序、成果文件内容的完整迁移与自动化恢复 |
| 权限与审批 | 单所有者、Agent 项目隔离、R3 任务门禁、版本化 CommandPlan 预览确认、受限中文模板/可选模型建议 | RBAC/ABAC 细粒度策略、临时能力、Secrets 管理 |
| 成果 | 5 MiB 受限本地上传、不可变同名版本、成员下载 SHA-256 校验、所有者版本验收/驳回 | 成果二进制可携带迁移、备份与 GC |
| 事件 | 持久 Event + Outbox、SSE、签名 Webhook 及重试投递 | 工作流订阅规则、外部事件回调和完整恢复 |
| MCP / A2A | MCP 标准 SDK stdio 桥接（15 个工具）；A2A v0.3 入站网关与受控出站委托 | 完整 MCP/A2A 互操作与能力协商 |
| OpenAPI | FastAPI 自动文档 | 版本兼容、规范校验与 SDK 示例 |
| Skills / AG-UI / A2UI / WS | Skills 提案/发布/评估/归档与跨项目推广已实现，AG-UI/A2UI/WS 未实现 | 安全可插拔的 Skills 生命周期和 UI 事件协议 |
| 多 Agent 协商 / 动态团队 | 第一/二轮独立决策复核、异议记录、显式受邀自动推进名单 | 结构化提案、独立复核、组队与预算 |
| 自主改进 / 质量评估 | Skill 结构化评估记录（pass/fail/concern）与归档已实现；自动实验闭环未实现 | 实验室、回归测试、策略发布/回滚 |
| Web 管理台 | 项目简报/宪章、导出包、操作预览、任务/检查点/副作用/交接、Agent/环境、知识/Skill 生命周期与跨项目审批、事件页面 | 指挥中心、审批、权限/资源和团队可视化 |
| 备份 / 安全运维 | 配置框架与测试 | 加密与恢复演练、事故响应、日志保留 |

**注意**：本仓库早期代码可用于本地开发验证，尚未进行第三方平台接入、真实 PostgreSQL 并发压力测试或生产安全审计。不要把它部署到公网。

## 本轮：项目可携带包导入与恢复

- 所有者可以在管理台先校验 `otternest.export.v1` 项目包的 SHA-256 摘要和内部引用，再确认恢复为一个新项目。
- 新项目与项目内记录使用新 ID，初始状态为 `paused`；未完成任务转为 `blocked`，R3 待审批任务保留待审批状态，所有旧执行者指派与授权状态清空。
- Agent 仅恢复为禁用的无凭据身份占位；已发布记忆、Skill 与决策转为提案，审议轮次不会自动继续。任务副作用台账与状态保留，未对账条目继续阻止重试。
- 外部关联项目以归档占位记录保留；成果文件只恢复元数据和校验和，二进制内容、登录凭据、Webhook/A2A 端点与其他外部连接不在包内，也不会被触发。
- SHA-256 用于检查包的内容一致性，不是签名或来源认证。恢复行为与精确限制见 [21 · 项目可携带包导入与恢复](21-project-import-restore.md)。

## 本轮：成果文件版本与验收（migration 0014）

- 相同任务下按文件名形成不可变版本族；再次上传创建新版本，旧版本文件和 SHA-256 保留。
- 授权项目成员可按 ID 下载成果。服务端确认文件仍在配置的项目目录内、大小未超过 5 MiB，并在响应前核对内容 SHA-256。
- 所有者可对每个版本做一次验收或驳回并填写备注；修改成果须重新上传为新版本，不能覆盖或重写旧版本。
- 导出包携带版本与验收元数据，不包含文件字节；导入时版本验收重置为待本地复核。备份、二进制跨机迁移和清理策略仍待实现。
- 细节见 [22 · 成果版本与验收](22-artifact-lifecycle.md)。

## 工程规则

1. 核心安全与业务状态必须集中在服务器侧，不允许因协议不同绕过授权。
2. 后续所有数据库变更使用 Alembic 迁移，不让 API 启动时静默变更 Schema。
3. 每种任务状态变化必须有事件、审计与自动化测试。
4. 生产配置不能含明文密钥，仓库禁止提交真实凭据。
5. 任何外部平台接入需验证其真实调用能力及使用条款。
6. 功能提交明确填写已实现部分、测试范围、已知限制，不虚称整套系统完成。

## 本轮：任务 DAG / MCP 工程变更

- **TaskDependency**：任务创建时或领取前编辑前置任务，禁止跨项目、自依赖、重复依赖和循环；最大 64 个前置依赖。
- **事务性放行**：前置任务须经过独立人工验收为 `completed`，最后一项验收时同一数据库事务释放后续任务。R3 审批与依赖状态分别记录，不能通过前置任务自动绕过审批。
- **调度基础**：任务优先级 `0..100`、可由管理员定向目标 Agent；Agent 队列按优先级返回，实际领取时校验项目角色与目标，不表示第三方已启动。
- **MCP stdio**：官方 MCP SDK FastMCP 建立 11 个工具，Agent Token 通过 REST 接口访问任务、租约、记忆及 Skills；后端权限逻辑不重复实现。
- **正式迁移**：Alembic `0004_task_graph`，兼容既有记录的 R3 审批状态。
- **明确不在本次声称完成**：A2A 标准互操作、真实第三方云端接单、全自动按能力调度、PostgreSQL 并发压力验证。

## 持续集成补充

新增 PostgreSQL 16 CI 工作：运行全部 Alembic 迁移，并在两个独立事务中并发领取同一任务，验证任务行级锁阻止重复领取。SQLite 单元测试与 PostgreSQL 集成测试分离运行。

## 本轮：Agent 运行实例与调度（migration 0005）

- `AgentRuntime` 与逻辑 Agent 分离，支持实例登记、身份绑定心跳、失联判定、禁用。
- Agent 的声明能力与管理员验证能力分开，Task 可以指定经过核验的能力要求。
- Owner API 或可信的 `otternest-scheduler` 定期根据项目、身份、运行模式、心跳、能力和容量为 ready 任务设置候选执行者。
- 通用 Runner 的可选 `--runtime-id` 用于上报状态；任务仍需使用原子 claim 才进入运行状态。
- 本地与 PostgreSQL 并发测试覆盖调度事务、能力门禁及跨项目容量。
- 尚未实现 A2A SDK 接入、真实远端唤醒、成本优化调度或独立的运行实例凭据。细节参见 [13 · Agent 执行实例与调度](13-agent-runtime-scheduler.md)。

### A2A JSON-RPC v0.3 gateway

`otternest.a2a` now exposes an explicit v0.3.0 Agent Card plus `message/send`, `tasks/get`, `tasks/cancel` with Bearer-based project authorization, durable request deduplication and truthful status mapping. Configure `OTTERNEST_A2A_BASE_URL` for the real external HTTPS origin before advertising discovery. Apply Alembic revision `0006` before serving requests. Details: [docs/14-a2a-gateway.md](14-a2a-gateway.md).

This is the task ingress and status layer. It does not yet provide automatic remote wake-up, streaming/push, or proven interoperability with hosted third-party Agent products.

## A2A owner-approved outbound handoff

The `otternest-a2a-outbound --once` CLI processes explicitly authorized outbound
A2A work. It does not discover/launch third-party cloud agents automatically.
See [docs/15-a2a-outbound.md](15-a2a-outbound.md) for credentials, trust model,
network restrictions, manual reconciliation and cancellation semantics.

## 受控指挥中心与独立决策复核（migration 0008）

新增数据库持久的命令预览/确认机制，校验任务版本、到期时间与审批边界；重复确认不会再次执行。Agent 对提议中的项目决策可独立提交证据/支持/异议；存在异议时需所有者留下明确的解决说明。Web 新增人工操作预览页面与查看决策意见。见 [docs/16-command-governance.md](16-command-governance.md)。

**尚未实现**自然语言意图解析、LLM 自动协商、证据真实性核实或无人工控制的自动执行。


## 受限中文指令与两轮协商（migration 0009）

指挥中心增加严格模板中文指令的解析预览，仍由管理员二次确认并按任务版本执行，不是自由自然语言模型推理。决策可由管理员在两位以上独立 Agent 第一轮复核后开启唯一第二轮，参与者独立回应其他观点；关闭协商后仍由管理员裁决，任意一轮的异议需留实质处理说明。见 [17 · 中文指令预览与两轮协商](17-intent-and-deliberation.md)。真实 LLM 自然语言规划、自动辩论调度、证据真实性检验与第三方平台接入尚未完成。

## 可选模型规划、证据检查与自动协商推进（migration 0010）

增加默认关闭的 OpenAI-compatible 候选操作建议接口 `POST /api/v1/command/interpret-model`（仅生成待二次确认的 `CommandPlan` 预览）、仅限项目内部引用的证据来源检查 `POST /api/v1/decisions/{id}/evidence/inspect`（仅校验记录存在性，不核验论点真伪），以及所有者预设 2–8 名评审员后自动推进到唯一第二轮的机制（决策最终批准仍归所有者）。见 [18 · 可选模型规划、证据来源检查与自动协商](18-model-planner-evidence-auto-debate.md)。

## 项目宪章硬预算、Epoch 检查点、租约撤回与简报（migration 0011）

增加 `ProjectCharter`（项目范围、停止条件与并发/任务总量硬上限，强制拦截任务创建、A2A 入站建单、领取及调度）、`active`/`paused`/`archived` 项目生命周期控制（暂停仅停止控制平面新调度与领取，不虚称停止远程进程）、受 `lease_epoch` 隔离的 `TaskCheckpoint` 与所有者核验分离、`POST /api/v1/tasks/{id}/revoke-lease` 租约撤回与交接包 `GET /api/v1/tasks/{id}/handoff`（同时注入 `task_context` 与 MCP 工具），以及诚实区分有效租约与过期租约的 `GET /api/v1/projects/{id}/briefing`。见 [19 · 项目宪章硬预算、Epoch 检查点、租约撤回与运行简报](19-charter-checkpoints-handoff.md)。

## 外部副作用幂等、知识生命周期与可携带导出（migration 0012）

增加 `TaskSideEffect` 不可回滚外部动作幂等台账（按 `(task_id, idempotency_key)` 防重、`reported_executed`/`unknown` 自述上报、阻断未对账任务的 `claim`/`unblock`，仅所有者 `reconcile` 核对 `verified_executed` 或 `verified_not_executed` 并在 MCP 桥接提供 2 个工具使总数达 15 个）、`Memory` 修订升级/归档（归档后自动退出上下文与非所有者列表）、`SkillEvaluation` 结构化评估与 `Skill` 归档、`CrossProjectShare` 双项目成员校验与所有者审批克隆（附来源溯源标记，拒绝时零污染），以及不含凭证的规范化 `GET /api/v1/projects/{id}/export`（`otternest.export.v1` + `sha256_digest`）。见 [20 · 外部副作用幂等、知识生命周期与可携带导出](20-side-effects-knowledge-portability.md)。

