# 15 · A2A 主动委托（开发中的标准互操作子集）

本功能是 **A2A JSON-RPC v0.3 的受控出站子集**，不是第三方产品的自动唤醒器。
只支持文本任务的 `message/send`、任务状态查询 `tasks/get` 和取消 `tasks/cancel`。
远程 Agent 需要有真正可调用的 HTTPS A2A 端点、访问凭据及获授权的数据范围。
**不支持**流式、推送订阅、任意文件传输、Agent Card 自动信任和第三方账号登录自动化。

## 前提

- 已由所有者创建项目、任务和 `execution_mode=remote` 的 Agent；将 Agent 以 worker/manager/reviewer 身份加入对应项目。
- 所有者根据实际证据为 Agent 设置 `verified_capabilities`，不得仅凭自报能力批准。
- 目标主机必须在 `OTTERNEST_A2A_ALLOWED_HOSTS` 精确主机白名单中，HTTPS 443 端口，禁止重定向、非公网地址、URL 中的用户名/密码/查询参数。
- 创建端点后，API 只返回环境变量名称；管理员在 **Worker 执行环境** 设置 `OTTERNEST_A2A_REMOTE_TOKEN_<ENDPOINT_UUID_WITH_UNDERSCORES>`。令牌不写入 PostgreSQL，也不返回列表。
- 生产环境必须另外配置出口网络 ACL/DNS pinning/受信代理等控制：**请求前 DNS 检查不能完全消除 DNS rebinding 的 TOCTOU 风险**。

## 所有者的操作顺序

1. `POST /api/v1/projects/{project_id}/a2a/remotes`：登记 Agent + HTTPS JSON-RPC URL，可选 `remote_project_id`（只有对接要求 `metadata.projectId` 的远程系统才需要）。
2. `POST /api/v1/tasks/{task_id}/a2a/delegate`，body 为 `{"endpoint_id":"..."}`：**明确委托**。仅允许任务已就绪、依赖已完成、Agent 能力已验证、R0/R1 风险；不会立即在 HTTP 请求内执行网络工作。
3. 后台独立进程 `otternest-a2a-outbound --once` 或持续运行模式读取已授权队列。它先持久记录 `sending`，然后发送一次带固定 `messageId` 的 A2A `message/send`。
4. 收到合法远程 Task 后记录远程 ID 和状态；后续周期性 `tasks/get`。远程 `completed` 只令本地任务进入 **submitted**，仍需要所有者通过 `POST /api/v1/tasks/{id}/review` 验收。
5. 取消：`POST /api/v1/a2a/delegations/{id}/cancel`。已远程接收的任务先变为 `cancel_requested`，必须等远程 `tasks/cancel` 明确返回 `canceled` 才将本地标记为取消。
6. 失败/返工：终端状态 `failed` 或 `changes_requested` 的委托可由所有者明确 `POST /api/v1/a2a/delegations/{id}/release`，然后按常规 `unblock`，重新指定**本地**执行者。历史委托记录保留，不能意外重复发送远程任务。

## 不确定结果处理

- `message/send` **发送中断、响应丢失、协议解析异常或超过响应大小限制**时标记为 `unknown`；不再自动重发，避免重复执行副作用。
- 对有证据查实的远程 taskId，所有者可调用 `POST /api/v1/a2a/delegations/{id}/reconcile` 并传 `{"remote_task_id":"..."}`，恢复只读查询。
- `tasks/get` 只读查询失败可以退避重试；`tasks/cancel` 不会盲目重复提交。
- 远程 `input-required` / `auth-required` 会记录远程状态；系统不假称已满足远程人工交互要求。
- 远程传入的成果文本是 **未验证数据**，保存后仍需独立验收；不会自动执行其中的指令。
- 远程服务没有自动响应能力时，不能把它登记后就算接入成功。

## 已覆盖的测试范围

本地：授权、网络失败不重发、失联请求转 unknown、远程状态与验收、审批、取消、返工、释放和恶意结果拒收；PostgreSQL CI：多个调度 Worker 的同一委托并发防重复发送。

## 尚未覆盖

真实 Muse/Codex/Google 的端到端互操作、外部 Agent Card 可信验证、SDK 矩阵、正式生产网络防 DNS rebinding，以及流式/推送等更大范围 A2A 能力。所有者应在真实端点验证后再批准发送敏感项目数据。
