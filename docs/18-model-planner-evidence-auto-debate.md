# 18 · 可选模型规划、证据来源检查与自动协商（0010）

本轮新增的能力并不代表整个 OtterNest 自主团队 OS 已完成。真实模型和第三方 Agent 仍需独立配置及验证。

## 可选 OpenAI-compatible 模型建议

默认关闭；仅所有者可在私有运行环境中设置：

```ini
OTTERNEST_PLANNER_ALLOW_EXTERNAL=1
OTTERNEST_PLANNER_URL=https://api.example.com/v1/chat/completions
OTTERNEST_PLANNER_ALLOWED_HOSTS=api.example.com
OTTERNEST_PLANNER_MODEL=your-model
OTTERNEST_PLANNER_API_KEY=YOUR_PRIVATE_SECRET
```

- `POST /api/v1/command/interpret-model` 接收 `{"project_id":"...","text":"..."}`，使用标准 Chat Completions 消息格式取得严格 JSON 的**单项候选操作**。
- 仅可提议 `authorize_task`、`cancel_task`、`review_task`、`route_task`。响应必须符合严格 Schema，涉及的任务和 Agent 必须属于当前项目允许候选集。
- 发送给提供商的内容：所有者指令、当前项目最多 50 个任务的 ID/标题/状态/风险/版本，以及启用的工作 Agent ID/名称。**任务标题和指令可能敏感；开启前须确认外发范围。**不会上传任务全文、附件、记忆或密钥。
- 系统继续调用已有版本化 `CommandPlan`，保留权限检查、10 分钟时效与二次确认；模型绝不能通过该接口自行执行或确认。
- 无有效配置返回 503；格式不符、未授权候选和模糊结果拒绝建预览。HTTP 客户端禁用自动重定向、环境代理和重试。
- **DNS / 网络出口风险**：HTTPS 域名允许列表并不能彻底避免 DNS 重绑定。生产环境必须在网络出口层落实可验证的目标 IP / 目的地隔离。
- 旧接口 `POST /api/v1/command/interpret` 仍为不外传的严格中文模板。当前尚未实现自由多轮模型规划或工具调用。

## 内部证据引用检查

`POST /api/v1/decisions/{id}/evidence/inspect`（所有者权限）：

```json
{"references":["artifact:<uuid>","memory:<uuid>","task:<uuid>","decision:<uuid>"]}
```

- `linked`：本项目对应记录存在；memory 已发布、task 已验收、decision 已批准，artifact 已持久化。
- `unresolved`：不存在、其他项目或不满足以上状态；三者返回同一个状态以免泄露其他项目记录存在性。
- `unsupported`：无效或未知格式；不会通过外部 URL 抓取数据，降低 SSRF 风险。
- 每条 `factual_claim_verified` 均为 `false`。**引用能解析，并不代表引用内容的真实性、及时性和结论得到独立核验。**
- Agent 第一轮和第二轮的返回结果增加 `evidence_checks`，原有 `evidence_verified:false` 不变。

## 所有者授权的自动协商推进

所有者先创建决策，在任何 Agent 提交首轮意见**之前**调用：

`POST /api/v1/decisions/{id}/deliberation/auto-policy`

```json
{"reviewer_ids":["<agent-id-1>","<agent-id-2>"]}
```

- 必须是同一项目中启用且有评审/工作/管理/顾问角色的 2–8 名不同 Agent。名单提交后不可更改。
- 系统等待**全部受邀参与者**各自独立提交首轮意见；不允许第三方插队或只收两条就提前结束。
- 全部赞成：记录 `decision.first_round_completed`，等待所有者判断，不自动批准。
- 若存在反对或替代观点：事务中自动开启唯一第二轮，第一轮参与者可针对另一人的观点各回应一次。
- 被邀请者全部完成第二轮时自动关闭讨论；未参与者未响应时不得伪造回复，所有者仍可手动结束已经开启的第二轮。
- 任意阶段最终批准**仍只能由所有者完成**，任何异议仍须书面处理说明。最多两轮。
- 仅自动推进**已接收的结构化 Agent 意见**；尚不能唤醒或自动驱动第三方 Agent 模型独立发表意见。

## 升级与检查

数据库迁移 `0010_auto_deliberation.py` 新增 `decision_auto_policies`。升级前备份 PostgreSQL 及附件。

测试覆盖禁用条件、模型候选限制、确认前无副作用、跨项目隔离、证据引用状态、自动协商人数与轮次、原有回归，并提供 PostgreSQL 并发下仅开启一轮的检查。所有测试使用模拟模型响应；未把真实 Muse、Codex 或 Google AI 互操作声称为已验证。
