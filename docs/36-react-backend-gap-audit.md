# 36 · FastAPI 后端与新版 React 管理台功能对账

> 对账范围：`src/otternest/*.py` 中已注册 FastAPI 路由，和 `frontend/src/pages/`、`frontend/src/components/` 当前 React 调用。目标是完整管理产品，不是通过“后端有接口”推断“后台已实现”。

本表的“已实现”只说明代码中存在 API 或页面操作，**不代表服务器已部署/运行、对外协议互通、三方模型账号可调用**。旧版 `/legacy` 可能有部分入口，本表重点统计新版 React。

## 已落实：此次 React 补齐

| 域 | 对应后端 API | 本次新增前端 | 边界 |
|---|---|---|---|
| Owner 账号安全 | `/auth/account`、`/auth/password`、`/auth/2fa/setup`、`/auth/2fa/enable`、`/auth/2fa/recovery-codes`、`/auth/2fa/disable`、`/auth/logout-all`、`/auth/events` | **账号安全** `/account-security` | 只有所有者可用；秘密仅一次显示；改密吊销会话后要求重新登录；Owner Token 轮换仍属于服务端运维 |
| 项目可携带包 | `GET /projects/{project_id}/export`、`POST /projects/import/preview`、`POST /projects/import` | **项目导入导出** `/project-portability` | 后端校验哈希及引用；仅创建新的暂停项目；不包含成果二进制与密钥 |
| Webhook、A2A 远端登记 | `/projects/{project_id}/webhooks`、`/webhooks/{webhook_id}/disable`、`/projects/{project_id}/a2a/remotes`、`/a2a/remotes/{endpoint_id}/disable` | 上轮 **外部接口** `/interfaces` | 仅注册/禁用；不等于外部连通、远端身份验证或 Worker 正常 |

## 尚缺完整 React 操作入口：优先级清单

| 优先级 | 域 | 后端有但新版 React 不完整的 API / 业务 | 推荐补齐位置 |
|---|---|---|---|
| P0 | A2A 任务委托 | `POST /tasks/{task_id}/a2a/delegate`、`GET /a2a/delegations/{delegation_id}`、`POST /a2a/delegations/{delegation_id}/cancel`、`/release`、`/reconcile` | 外部接口中的「委托管理」或任务详情的远端执行面板；把已登记/已投递/已执行/未知状态严格区分 |
| P0 | 任务副作用台账 | `POST /tasks/{task_id}/side-effects`、`GET /tasks/{task_id}/side-effects`、`POST /side-effects/{effect_id}/report`、`/reconcile` | 任务详情，带副作用性质及结果“未知时禁止盲目重试”的治理提示 |
| P1 | 检查点与故障交接 | `POST/GET /tasks/{task_id}/checkpoints`、`POST /checkpoints/{checkpoint_id}/verify`、`POST /tasks/{task_id}/revoke-lease`、`GET /tasks/{task_id}/handoff` | 任务详情中的“执行连续性”标签页；管理员可以核验、撤租约、查看交接包，不能伪造 Runner 已完成 |
| P1 | 决策审核与证据 | `POST /decisions/{decision_id}/reviews`、`POST /decisions/{decision_id}/evidence/inspect` | 审批与治理；现有页面主要展示复核、协商与最终决议，尚缺评审提交和证据链检查 |
| P1 | 第二轮协商策略 | `POST/GET /decisions/{decision_id}/deliberation/auto-policy`、`POST /decisions/{decision_id}/deliberation/rebuttals` | 审批与治理，新增自动继续名单与反驳意见管理，服务端仍裁定参与和权限 |
| P2 | 项目级简报 | `GET /projects/{project_id}/briefing` | 项目详情；全局每日简报已存在，不能替代单项目简报 |
| P2 | 记忆检索 | `GET /projects/{project_id}/memories/search` | 知识库，提供关键词与敏感级筛选；不得绕过服务端按身份施加的访问限制 |
| P2 | AG-UI 流、A2UI 调试工具 | `GET /projects/{project_id}/ag-ui/events`、`/ag-ui/stream`、`POST /a2ui/validate` | 调试/实时事件页；已有 WebSocket 与 A2UI 部分展示，完整事件调试与验证工具未统一 |
| P2 | 命令计划历史检索 | `GET /command/plans/{plan_id}` | 指挥中心的可追溯查询；目前新建/确认流程较完整 |

## 不宜直接变成管理员“操作按钮”的执行端 API

- `GET /agent/queue`、`POST /tasks/{id}/claim`、`/heartbeat`、`/submit`、`/fail`：由认证 Agent/Runtime 或 Runner 触发；可以在后台显示进度，但不应用所有者权限伪造执行。
- `GET /tasks/{id}/context`：是按 Agent 可见范围构造的任务包，不等同管理员可随意复制的全文上下文。
- `POST /agent/runtimes/{runtime_id}/heartbeat`、MCP stdio 入口：属于独立运行节点的协议与生命周期。
- A2A 入站 `/a2a` 和 Agent Card 是协议端点；前端要展示能力、状态与权限，不应把端点“可访问”当成任务成功。

## 验收要求

1. 每个新增 React 动作必须调用真实现有后端 API，而非只提供表单或静态演示。
2. 所有者/Agent 权限、项目边界、一次性密钥展示、外部 URL allowlist 由后端验证；前端只能做次级提示。
3. 破坏性操作必须明确二次确认；对恢复、远端副作用和未知执行状态不得自动重试。
4. 完成的顺序建议：**任务副作用 + A2A 委托 → 检查点/交接 → 决策审核 → 单项目简报与知识检索**。
5. 每批上线验证 `frontend`、`unit`、`postgres` CI；真实 Oracle 主机、Worker 和三方服务另做现场验收。
