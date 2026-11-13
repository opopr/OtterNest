# 13 · Agent 执行实例、能力核验与有界自动调度

> 实现于本项目的 `0005_runtime_dispatch` 数据库迁移。这里描述**已编码的控制平面功能**，而不是对 Muse/Codex/Google 等外部平台远程控制能力的承诺。

## 身份与运行实例分离

- `Agent` 是一个拥有独立认证令牌、平台标签、执行模式和**声明能力**的逻辑主体。
- `AgentRuntime` 是该主体的一个运行实例（环境标签、启停状态、自报告状态、最后心跳）。一个 Agent 可以对应多个实例。
- 能力有两个列表：`declared_capabilities` 仅由管理员注册时输入；`verified_capabilities` 只能由**系统所有者**在核验实际能力后更新。
- 能力标识为唯一小写词，如 `python`、`browser`、`testing`。Task 的 `required_capabilities` 是必要条件，**不能靠 Agent 自报或项目经理权限绕过**。
- 当前运行实例复用所属 Agent 的令牌验证身份，**不能证明两个同 Agent 实例之间的独立隔离或远端环境可信度**；后续可演进为运行实例专属凭据。

## 授权 API

- 管理员注册 Agent：`POST /api/v1/agents`（`capabilities: ["python"]`）。
- 管理员确认能力：`PUT /api/v1/agents/{agent_id}/verified-capabilities`，只允许声明能力的子集。
- 管理员注册执行实例：`POST /api/v1/agents/{agent_id}/runtimes`，包含 `label` 与 `environment`。
- Agent 自行上报心跳：`POST /api/v1/agent/runtimes/{runtime_id}/heartbeat`，`{"status":"idle"}` 或 `busy` / `stopped`；禁止冒充不同 Agent 的实例。
- 管理员查询、禁用实例：`GET /api/v1/agents/{id}/runtimes` / `POST /api/v1/agents/{id}/runtimes/{runtime_id}/disable`。
- 创建任务指定需求：`POST /api/v1/projects/{id}/tasks`，传 `required_capabilities: ["python"]`。
- 手工触发调度：`POST /api/v1/projects/{id}/schedule`，参数 `limit` 和 `heartbeat_ttl_seconds`；仅使用者可操作。
- Agent 使用已有 `GET /api/v1/agent/queue` 和 `POST /api/v1/tasks/{id}/claim`。**实际接单仍以事务性 claim 结果为准**。

## 调度规则

1. 项目必须处于 `active`；任务必须处于 `ready`，不能有未验收前置依赖或待审批 R3。
2. Agent 必须启用，拥有 `worker` / `manager` / `reviewer` 项目成员身份，执行模式为 `polling`。
3. 至少一个已启用 Runtime 在指定心跳 TTL 内上报 `idle`；默认 180 秒。该状态**来自 Agent 自述，不代表底层环境远程证明**。
4. 任务的所有 `required_capabilities` 必须在 Agent 的 **verified** 能力集合内。
5. 调度会考虑跨项目的已运行任务和已预留待领取任务，每名 Agent 暂时限定一个未完成的工作槽位。
6. 多任务按 `priority` 降序，时间顺序确定性选择。项目行锁和任务行锁保护调度的一致性；数据库事件保留审计记录。
7. 调度**只设置** `target_agent_id`，不改变任务到 `running`，不调用第三方平台，也不预设远程 Agent 能被唤醒。

## 后台调度器

正式 Python 程序入口为 `otternest-scheduler`，运行在**可信控制平面**，直接使用受限的 PostgreSQL 连接，不需要泄露 Owner Token 给远程 Runner。支持 `--once`、`--interval`、`--heartbeat-ttl` 和 `--limit-per-project`。后台运行与管理由部署时的进程管理器负责；项目当前**没有自动部署**。

通用 Runner 可选设置 `--runtime-id` 或 `OTTERNEST_RUNTIME_ID`。它会在轮询、执行中和完成后更新状态，任务心跳仍使用原来的租约 `epoch`；子进程不会继承控制平面令牌。

## 限制和未来工作

- **不能把远端自述心跳当作可信执行证明**。失联不代表远端进程已停止。
- 当前不包含 A2A SDK 1.x 的任务交换端点和远端 Agent Card 安全验证；协议适配与平台真实能力验证属于后续工作。
- 手工指定的任务不会由调度器覆盖；已定向任务若执行者长期离线，需所有者重新定向。
- 当前容量规则为**每个 Agent 一槽位**，还没有动态权重、成本与公平分配算法。
- 代码完成不等于经过真实 Muse/Codex/Google 环境联调。
