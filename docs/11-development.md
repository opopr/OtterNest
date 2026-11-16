# 11 · 正式产品开发跟踪

## 当前阶段

OtterNest 已进入**完整产品开发**。此文件是工程状态登记，而非缩减需求的 MVP 计划。未实现项不应被标记为已完成。

**技术栈**：Python 3.11+、FastAPI、SQLAlchemy、PostgreSQL、Alembic、React、TypeScript、Vite；控制平面与执行平面解耦部署。

## 功能矩阵

| 领域 | 当前实际状态 | 完整产品目标 |
|---|---|---|
| 项目与长期目标 | 基础 API、项目宪章与硬预算、生命周期暂停/归档、目标暂停/恢复/人工完成、GoalDependency 与多项目 Portfolio 公平调度 | 跨项目目标协同与完整自治契约 |
| 账号 / Agent / 运行环境 | 注册、令牌、项目成员有效期与敏感度、独立 `on_runtime_` 凭据、健康自报、能力声明/核验、通用 CLI Runner、OpenAI-compatible 文本模型 Worker、Codex CLI Worker | 第三方平台真实接入与自动化环境探测 |
| 任务执行 | 领取、租约、epoch、提交、失败阻塞与多因归因、人工验收；依赖 DAG、优先级、定向分配、调度；检查点/核验、撤回与交接；限量成果回传；可选 bubblewrap、进程限制与 cgroup v2 护栏 | 跨云主机自动恢复、恶意代码安全审计与真实远端进程控制 |
| 数据与记忆 | 记忆提案/发布/修订/归档、受审跨项目知识推广、项目包导出/导入恢复、关键词排序和敏感级过滤的 Context Broker、有限且已验收的前置任务交接 | pgvector 语义检索与自动全库灾难恢复 |
| 权限与审批 | 所有者账号+TOTP 2FA、Agent 项目隔离、敏感级 ABAC 与临时 `TaskGrant`、R3 门禁、`CommandPlan` 预览确认、多轮 `/command/chat` | 企业级外部 Secrets 轮换与公网安全审计 |
| 成果 | 5 MiB 受限本地上传、不可变同名版本、成员下载 SHA-256 校验、所有者版本验收/驳回、`.tar.gz` 二进制归档导出/校验恢复与 GC | 超大文件对象存储与跨机自动同步 |
| 事件 | 持久 Event + Outbox、SSE/WebSocket、签名 Webhook、事件驱动工作流规则（含链路深度与冷却熔断） | 跨系统分布式工作流与完整死信重放 |
| MCP / A2A | MCP 标准 SDK stdio 桥接（15 个工具）；A2A v0.3 入站网关与受控出站委托 | 完整 MCP/A2A 互操作与 streaming 推送 |
| OpenAPI | FastAPI 自动文档 | 版本兼容、规范校验与 SDK 示例 |
| Skills / AG-UI / A2UI / WS | Skills 生命周期已实现；已新增项目 WebSocket 实时事件流、AG-UI 事件投影与 A2UI 组件白名单安全校验 | 全量客户端深度双向 AG-UI/A2UI 渲染 |
| 多 Agent 协商 / 动态团队 | 第一/二轮独立决策复核、异议记录、显式受邀自动推进名单 | 自动辩论调度与动态成本预算组队 |
| 自主改进 / 质量评估 | Skill 结构化评估、质量指标与失败归因统计、受控优化实验与回归门禁（`promote`/`rollback`） | 自动化沙箱回归数据集与灰度发布 |
| Web 管理台 | React 已覆盖登录、总览、项目/任务基础操作、Agent 登记与 Runtime、任务审批/决策审查、多轮 Command Center（规则模板/可选模型建议/A2UI 预览）、目标依赖/工作流、知识库、Skills、TaskGrant、成果版本/验收/归档/恢复/GC、Portfolio/通知/实验、系统保护模式/项目状态/事件流、API/数据库/身份连接诊断；旧版管理台继续兜底低频运维页面 | 对话持久化、A2UI/AG-UI 深度交互、真实远端 Runner 唤醒与恢复演练、生产级安全审计，并完成全量回归 |
| 备份 / 安全运维 | `normal`/`degraded`/`readonly_protect` 保护模式、项目 JSON 与成果二进制归档工具、本机 PostgreSQL+成果一致性备份服务与可选 timer | 异机加密复制、自动恢复验证与生产 RPO/RTO 演练 |

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
- 导出包携带版本与验收元数据，不包含文件字节；导入时版本验收重置为待本地复核。独立成果归档支持校验恢复和 GC；自动加密备份、远端复制与生产恢复演练仍待实现。
- 细节见 [22 · 成果版本与验收](22-artifact-lifecycle.md)。

## 本轮：三阶段控制平面与控制台补强（migrations 0015–0016）

- **阶段 1（安全治理、Runtime 凭据、降级保护与成果归档 GC）**：新增 `AgentRuntime` 独立 `on_runtime_` 凭据与健康自报；成员有效期与敏感度上限（`public`/`internal`/`confidential`/`restricted`）及任务级临时授权 `TaskGrant`；控制平面 `SystemState`（`normal`/`degraded`/`readonly_protect`）；成果 `.tar.gz`（`otternest.artifacts.v1`）二进制归档导出、全量路径/清单/大小/SHA-256 校验后恢复及垃圾回收（GC）。恢复限制为 50 MiB 传输、1000 个文件、单文件 5 MiB、载荷 44 MiB；文件写入前必须完成整个归档校验。
- **阶段 2（工作流规则、检索排序、Portfolio 调度与质量实验）**：新增 `GoalDependency` 环检测及目标暂停/恢复/人工完成生命周期；完成目标必须先完成其所有前置目标并验收关联任务，且完成后不可重开；带最大链路深度与冷却熔断的 `WorkflowRule`/`WorkflowRun` 可按目标状态触发；Context Broker 关键词分词评分与敏感度过滤；Portfolio 优先级加权防饥饿调度；质量指标（通过率/返工率/未知副作用率/多因失败归因）、证据化每日简报、三级 `Notification` 收件箱与受控 `Experiment` 回归门禁。
- **阶段 3（WebSocket/AG-UI/A2UI 与 Web 控制台）**：新增 `/api/v1/projects/{id}/events/ws` 认证 WebSocket 实时事件流、AG-UI 只读事件投影、A2UI 组件白名单安全校验、多轮上下文 `/api/v1/command/chat`，以及 Web 控制台 DAG 拓扑图、多轮对话、分级通知抽屉、质量看板与归档 GC 操作面板。
- **已知限制**：仍未接入真实第三方云端 AI 平台（Muse/Codex/Google AI），未引入 pgvector 与外部 KMS/Secrets 引擎，也未在真实 PostgreSQL 集群上进行高并发压测。
- 目标生命周期与人工验收边界、API 及手工验收清单见 [31 · 目标生命周期与依赖](31-goal-lifecycle.md)。

## 本轮：React 前后端分离与 AI Studio 工作流

- 新增 `frontend/` React + TypeScript + Vite workspace；仓库根目录 npm scripts 统一执行开发、类型检查和构建。
- FastAPI 继续作为唯一业务后端与权限边界，前端通过 `/api/v1` 和 `/openapi.json` 访问，不直连 PostgreSQL。
- 构建产物位于 `frontend/dist`，FastAPI 在构建存在时由 `/` 提供 React，并将旧管理台保留在 `/legacy`。
- 首批 React 页面覆盖所有者登录/初始化/2FA、团队总览、项目与任务基础操作、Agent 登记；其他复杂业务页面暂由旧版管理台承接。
- 新增精确 Origin CORS 配置和 WebSocket Origin 校验；默认不开放跨域，禁止通配符 Origin。
- GitHub Actions 新增前端依赖安装、TypeScript 检查和 Vite 构建任务。分离方案与 AI Studio 导入步骤见 [32 · React 前后端分离](32-react-frontend-separation.md)。

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


## 本轮：文本模型执行适配与 Runner 输入稳健性

- 新增 `otternest-model-worker`：明确配置可信 HTTPS Chat Completions 端点、模型 ID 与独立的模型 API Key；单次文本生成后由既有 Runner 提交 `submitted`，仍需所有者验收。
- 模型适配器不继承 OtterNest 控制平面令牌，不支持外部工具/文件写入/自动授权，不假装已接通 Codex、Gemini 或 Muse 的个人订阅账号。
- 通用 Runner 使用匿名临时文件提供 stdin，避免子进程不读取上下文时管道写入导致挂起；超出上限的 stdout 不再截断后提交。
- 新增 MockTransport 与本地 HTTP 子进程联调测试；真实第三方模型仍待接入验证。运行与安全边界见 [23 · OpenAI-compatible 文本模型 Runner 适配器](23-model-worker.md)。

## 本轮：受控执行器成果自动回传与已验收任务交接

- 通用 CLI Runner 增加操作者指定的私有 workspace root，每个任务独立临时目录，限制可上传的成果清单为 8 个文件、每文件最高 5 MiB、总计 20 MiB，禁止目录穿越、符号链接、硬链接、重复文件名。失败时阻断任务而非虚假提交成功。
- 成果由已授权的 Agent Token 使用当前租约 epoch 上传，并比对服务端 SHA-256；随后创建仅为 `self_reported` 的检查点，引用已上传成果 ID，再提交给所有者单独审核。
- 已完成、通过所有者验收的 DAG 前置任务向后续 Agent 提供有限的交接元数据：任务摘要、最新 owner-verified 检查点、逐项 accepted 的成果记录。只有前置任务被验收完成，后继才能领取。
- 文本模型适配器在非可信参考区读取交接摘要，仍不可自行使用工具、授权或下载文件。测试覆盖两个独立 Agent 的前后任务工作流。
- **未实现/未声称**：生产级不可信代码沙箱、跨 HTTP 成果上传事务、第三方产品真实账号联调、自动开发 GitHub 提交或多 Agent 无人自治。
- 操作和安全边界见 [24 · Runner 成果自动回传与多 Agent 验收交接](24-runner-artifact-handoff.md)。


## 本轮：可选 Linux bubblewrap 执行隔离（不依赖 Docker）

- Runner 增加 `--sandbox bwrap`，只在显式启用、存在私有 `--workspace-root` 且 Linux 系统的 bubblewrap 命名空间预检成功时才领取任务；不可用就拒绝执行，**不**自动退回普通子进程。
- 受控 Worker 仅可使用只读系统运行时、独立 `/work` 工作目录以及私有 `/tmp`/`/proc`/`/dev`，网络与进程/用户命名空间隔离，拒绝透传环境变量和非系统可执行路径。
- 原成果版本、SHA-256、检查点和所有者验收流程保持不变；常规 CI 测试只验证防绕过策略和配置失败，真实命名空间验证需要在单独 Linux 主机上显式运行。
- **尚未完成**：cgroup v2 CPU/内存/PID 硬限额、专用 seccomp 策略、恶意代码威胁模型验证、自动外部仓库写入、生产隔离等级认证。当前文本模型 Worker 仍不能在断网沙箱内调用云端 API。
- 使用说明见 [25 · Linux 受控代码执行（bubblewrap，可选）](25-runner-bwrap-isolation.md)。


## 本轮：Runner 子进程清理与可选 Linux 资源护栏

- Runner 在任务执行结束、超时、心跳失效时，对独立 POSIX 进程组执行 TERM/KILL 回收，不再只停止主进程；对可信非沙箱子程序主动脱离进程组的情况仍不能保证完全回收。
- 可选 `--cpu-seconds`、`--address-space-mib`，仅允许与 `--sandbox bwrap` 配合使用；在领取任务前检查 `prlimit` 和用户程序合法性，失败关闭、不默认降级。
- 限额依赖每进程的 `RLIMIT_CPU` 和 `RLIMIT_AS`，**不是** cgroup v2 任务级 CPU/真实内存/PID 配额；不应据此宣称达到生产级不可信代码隔离。
- 单元与 Linux 执行测试覆盖超时后的子进程回收及 CPU 限额，详细文档见 [26 · Runner 子进程回收与 Linux 资源限制](26-runner-process-limits.md)。


## 本轮：Linux cgroup v2 任务级硬配额（需提前委托）

- 为显式启用 `--sandbox bwrap` 的独立执行器增加可选 `--cgroup-parent`、`--memory-max-mib`、`--cpu-max-percent`、`--pids-max`。只有 Linux cgroup v2 父目录已委托、对应控制器启用且测试进程能够成功入组时才允许领取任务。
- 每次领取任务后在已委托父目录内创建新的子组，写 `memory.max` / `cpu.max` / `pids.max`，由可信启动程序**先写 cgroup.procs，再 exec worker**，规避先运行再入组的竞态；结束时用 `cgroup.kill` 清理。
- 现有 PostgreSQL、任务 epoch、模型文本 Worker、Agent 成果回传与人工验收无需变动。没有要求引入 Docker。
- 单元测试覆盖资源配额参数、流程检查、进程预入组与失败关闭；真实 cgroup / WSL2 仅在具有委托权限的独立 Linux 测试机上 opt-in 验证。崩溃后的孤儿子组清理、系统级恶意代码安全审计、seccomp 和真实第三方 AI 平台接入仍待实现，见 [27 · cgroup v2 任务级资源控制](27-cgroup-v2-task-budgets.md)。


## 本轮：cgroup 异常退出后的恢复与独占管理

- 显式开启任务级 cgroup 的 Runner 启动时对委托父目录持有进程生命周期级 `flock`，降低多个执行器误用同一委托父组的风险；发现此前遗留的 `otternest-task-*` 资源组时，拒绝接单，不自动强杀运行中进程。
- 新增 `otternest-cgroup-recover` 运维 CLI：默认只预览 `cgroup.events` 的真实 `populated` 状态；显式 `--apply` 才清理已空的资源组，额外 `--terminate-populated` 才对存活的遗留进程调用 `cgroup.kill`。
- 只清理严格匹配命名的直属资源组，检查 owner、symlink、锁文件权限和父目录授权；不修改宿主机全局 cgroup 配额。
- 这一功能不代替真实 Linux/WSL2 委托测试、安全审计或无人值守恢复机制。说明见 [28 · cgroup 崩溃恢复](28-cgroup-crash-recovery.md)。


## 本轮：Codex CLI 真实非交互协议 Worker（需操作员自行登录）

- 新增 `otternest-codex-worker`：接收授权的 Task Context Packet，调用官方 `codex exec` 非交互命令，并以 `--json` 事件和 `--output-last-message` 核验完成状态；原始模型/CLI 日志不作为成功摘要写入控制面。
- 默认 `read-only`；显式授权 `workspace-write` 且声明具体成果路径后，才允许操作独立任务目录内文件，之后沿用 Runner 文件 SHA-256、检查点和人工验收。未提供 `danger-full-access`、自动 Git push/PR 或生产部署。
- 使用操作者本机安装和认证的 Codex CLI；不从 OtterNest 获取登录 Cookie、API Key 或会话，不宣称已接入用户真实个人订阅。模拟 CLI 的端到端测试覆盖 Agent 任务领取到人工验收；真实 WSL2/账户联调尚待进行。
- Cloud Codex CLI 需要网络与单独受控的认证目录；不能放入此前断网的 bubblewrap Worker 后又声称可访问云模型。当前以专用可信执行身份运行 CLI，进一步的恶意代码沙箱/独立节点隔离仍为必做项。
- 使用方式、来源和边界参见 [29 · Codex CLI 非交互 Worker](29-codex-cli-worker.md)。


## 本轮：固定 GitHub 提交快照与 Codex 代码补丁交接

- Codex CLI Worker 可由执行机操作员指定 GitHub 公共仓库 `owner/repo` 或可信本地 Git 仓库，必须一并指定完整 commit SHA 与明确的 `refs/heads/...`。只接受经核对的分支头，不接受任务内容修改来源或 Git 身份凭据。
- 使用有界 `git archive` 生成不包含 `.git` 的独立代码快照，限制文件数量/体积并拒绝特殊文件、链接及不安全路径；默认只读时拒绝实际源码修改。
- 显式授权 `workspace-write` 时，比较源基线和结果工作区并输出 `changes.patch` 与 `otternest-provenance.json`，记录原始提交、补丁 SHA-256 和变更数量；**不会自动向 GitHub push、提交 PR、应用补丁或合并**。
- 自动化测试基于临时真实 Git 源仓库、模拟 Codex CLI、Git 补丁校验和完整 Runner/API/人工验收流水线。真实公共 GitHub 网络拉取、个人 Codex 登录、WSL2/ARM 联调、私有仓库认证、完整代码安全隔离及人工审核后的 PR 创建仍未完成。
- 边界与操作说明见 [30 · GitHub 固定提交快照与代码补丁](30-git-snapshot-patch.md)。


## 本轮：React 领域页面迁移（前后端分离第二阶段）

- 新增指挥中心：按项目读取待授权/待验收任务和决策；任务写操作使用 CommandPlan 先预览、再确认，服务端二次校验任务版本；决策审查显示独立 Agent 意见，第二轮讨论必须显式关闭，最终批准仍由所有者完成。
- 新增目标与工作流：读取 Goal Board；创建目标、维护前置依赖、暂停/恢复/完成人工状态；规则按事件前缀匹配，可显式评估并查看 WorkflowRun。前置依赖环检测、完成门槛、链深度和冷却熔断仍由 FastAPI 强制执行。
- 新增知识库：记忆提案、发布、修订、归档；项目章程与并发/任务总量预算；跨项目知识共享申请和所有者审批。共享批准在目标项目创建独立副本。
- 新增 Portfolio：跨项目质量指标、每日简报、三级通知收件箱、公平调度入口、受控实验评估/采纳/回滚。实验采纳门槛仍由后端执行。
- 新增系统与事件：全局 normal/degraded/readonly_protect 模式、项目 active/paused/archived 状态和项目持久事件流。
- 前端页面只调用现有 /api/v1 路由，不新增数据库表，不将数据库访问移入浏览器。详细页面/API 对照见 [33 · React 领域页面迁移](33-react-domain-pages.md)。
- 本阶段交付时尚未迁移的 Skills、TaskGrant、成果版本/二进制归档与 Runtime 诊断，已在第三阶段完成；多轮 Command Center 与模型建议入口已在后续阶段完成。深度 AG-UI 双向交互、对话持久化和生产恢复演练仍待实现。


## 本轮：React 运行与成果管理页面迁移（第三阶段）

- Skills：新增项目级 Skill 提案/列表 API；提案默认 proposed，只有所有者发布后 Agent 才能读取；支持评价记录和归档。新增 API 回归测试覆盖提案可见性、重复版本冲突、发布、评价和归档。
- TaskGrant：新增任务级临时授权 UI，按项目成员授予范围、敏感级上限和有效期；支持列表与撤销。后端仍验证 Agent 必须是该任务所属项目成员。
- 成果中心：新增二进制上传/下载、版本列表、所有者验收/驳回、.tar.gz 导出/恢复和 GC dry-run/执行。增加 UTF-8 文件名编码请求头，兼容中文成果名；单文件 5 MiB 与后端路径/哈希校验不变。
- Agent Runtime：新增 Runtime 注册、独立凭据签发/轮换、在线状态/健康心跳展示、禁用 Runtime、声明能力与所有者核验能力管理。禁用 Agent/Runtime 不代表外部进程已停止。
- 指挥中心增加受限中文命令模板入口。它只调用 /command/interpret，不调用外部模型；解析后仍生成 CommandPlan，必须由所有者检查预览并显式确认。
- 当前 React 页面继续复用 FastAPI 现有权限和数据库；新增 Skill 列表/提案/发布 API 不新增数据库表。成果文件二进制流通过独立 authenticated binaryRequest 处理，不将文件内容误作 JSON。
- 细节见 [34 · React 运行与成果管理](34-react-operations-pages.md)。


## 本轮：AI Studio 与真实后端连接诊断

- React「系统设置与运行事件」页面新增连接诊断，分别检查 FastAPI 存活、数据库就绪与当前所有者会话。
- `/health/live` 与 `/health/ready` 使用不携带 Bearer 令牌的公开健康请求；`/api/v1/me` 仅使用当前页面内存中的会话令牌。
- 页面显示浏览器前端 Origin 和配置的 API Origin，并在失败时提示检查 `VITE_API_BASE_URL` 与 `OTTERNEST_CORS_ORIGINS`。
- 诊断仅验证连通性，不等于公网部署、生产安全审计或真实 Codex/Muse/Google AI 平台联调。


## 本轮：私有 Linux 原生部署基线

- 新增 ARM64 私有服务器部署模板：FastAPI、PostgreSQL、任务调度器和持久工作流 Worker 使用独立 systemd 单元；Alembic 迁移由运维人员显式触发。
- 新增可选签名 Webhook 投递单元、Nginx 私有 HTTPS 反向代理模板、受限 systemd 安装脚本和服务器环境变量示例。
- 部署指南覆盖本机 PostgreSQL peer 认证、Owner Token 与 TOTP 初始化、AI Studio 精确 Origin/CORS、独立 Runner 执行节点、升级和配对备份/恢复验收。
- **尚未完成/未声称**：没有连接或修改用户的 Oracle 服务器；没有实机验证目标发行版、ARM64 systemd/Nginx 配置、TLS/VPN、资源占用、备份恢复或第三方平台接入。该部署包仅作为私有试运行基线，不能视为公网生产就绪。
- 详细步骤见 [35 · 私有 Linux 原生部署基线](35-native-linux-deployment.md)。


## 本轮：本机一致性备份与可选定时器

- 新增 root-only 一次性备份服务，先停止 Webhook、工作流、调度器和 API，再检查是否存在 running 任务；发现运行任务时拒绝备份并恢复原有服务状态。
- 无运行任务时生成 PostgreSQL custom dump、成果目录归档、SHA-256 清单及 Git/Alembic 版本清单；归档默认保留 14 天，且不包含环境文件或其他秘密配置。
- 新增每日 02:30 UTC 的可选 systemd timer，但安装时不自动启用；只有运维人员确认维护窗口后才启用，避免在运行任务期间制造非预期中断。
- 新增备份归档校验步骤；完整恢复仍要求先在隔离数据库和暂存目录验证，不提供自动覆盖式恢复。
- **尚未完成/未声称**：异机加密复制、自动灾难恢复、RPO/RTO 保证及真实 Oracle ARM 主机上的备份恢复演练。
- 操作边界见 [35 · 私有 Linux 原生部署基线](35-native-linux-deployment.md)。


## 本轮：外部接口管理台入口

- React 新增「外部接口」导航和 `/interfaces` 页面，统一显示 REST/OpenAPI、MCP stdio、A2A 入站和外部 AI Worker 的真实实现边界。
- 按项目读取、创建和禁用已存在后端 API 支持的签名 Webhook 与 A2A Remote Endpoint；Webhook 签名密钥仅在创建响应中短暂显示，A2A 远端令牌仅通过服务端环境变量配置。
- 后台不会把静态登记状态误报成连通或模型可调用；OpenAI-compatible、Codex CLI 仍需实际执行节点配置和端到端验证，Google AI/Muse 无直接订阅连接器。
- 目前没有数据库保存的通用第三方模型密钥管理器、实时连通性探测或开放式第三方 OAuth 授权；这些不应被误认为已经完成。
