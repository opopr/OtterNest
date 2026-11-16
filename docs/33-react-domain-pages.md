# 33 · React 领域页面迁移（第二阶段）

## 范围

第二阶段在保留 /legacy 的前提下，将现有 FastAPI 领域能力迁移到 React。此阶段不改变数据库模型、不重写后端权限，也不在浏览器直连 PostgreSQL。

## 页面与接口

### 1. 指挥中心与审批 /governance

- 以项目为边界读取任务和决策。
- waiting_approval 任务通过 POST /command/plans 创建 authorize_task 预览；submitted 任务通过 review_task 预览验收或退回。
- 所有任务操作都必须再调用 POST /command/plans/{id}/confirm。后端按任务版本、状态和授权规则重新校验，预览不能被视为执行。
- 决策审查读取 GET /decisions/{id}/reviews。证据引用在 UI 中明确标记为“尚未自动验证”，不把引用存在误报为证据可信。
- 第二轮讨论需满足后端人数与状态条件；关闭第二轮后，仍需调用 /resolve 由所有者最终裁决。独立 Agent 意见不是投票，也不自动批准决策。

### 2. 目标与工作流 /goals-workflows

- 使用 GET /projects/{id}/goal-board 展示目标、关联任务进度、前置目标和完成阻塞原因。
- 创建目标走 /projects/{id}/goals；状态变更走 /goals/{id}/status。
- 依赖新增/移除走 /goals/{id}/dependencies。循环检测、同项目约束、已完成目标不可改依赖等规则完全由 FastAPI 执行。
- 工作流规则只允许后端定义的 create_task 与 notify_inbox 动作。规则显式评估后，页面展示 WorkflowRun。事件去重、链深度、冷却和预算熔断由服务端负责。

### 3. 知识库与项目章程 /knowledge

- 项目记忆支持提案、发布、修订、归档。新提案默认不是已发布知识；只有发布后才可作为 Agent 正式项目知识。
- 修订接口仅接受标题、内容、来源和修订原因；记忆类型与敏感级沿用原记录。
- 项目章程设置范围边界、停止条件、最大并发任务和最大任务总量；后端校验预算边界并在创建任务时执行。
- 跨项目共享只从已发布记忆发起；批准后目标项目得到独立副本，拒绝或批准均需所有者记录理由。

### 4. Portfolio 总览 /portfolio

- 展示跨项目任务质量指标、每日简报和通知收件箱。
- 通知支持单条标记已读与全部标记已读。
- 公平调度是会改变任务路由状态的操作，执行前要求用户确认；调度顺序与防饥饿策略由服务端决定。
- 实验记录基线分数、候选分数和样本量；只有候选分数不低于基线且样本量至少为 3 时，后端才允许采纳。回滚/采纳必须有所有者说明。

### 5. 系统与事件 /system

- 提供 normal、degraded、readonly_protect 三种全局模式；进入保护模式要求说明原因。
- 项目可切换 active、paused、archived 状态，必须填写原因。
- 项目事件流从持久化事件接口读取，按时间倒序显示。事件流是项目事件记录，不应在 UI 中误称为独立、不可篡改的安全审计报告。

## 第三阶段补充说明

Skills、TaskGrant、成果版本/归档与 AgentRuntime 已在第三阶段迁移到 React，具体边界和接口见 [34 · React 运行与成果管理](34-react-operations-pages.md)。指挥中心新增的自然语言入口仅支持后端白名单模板，不是完整多轮 LLM 对话。

## 尚未迁移的功能

以下能力仍由旧版管理台或后续阶段承接：

- 完整多轮自然语言 Command Center 对话、外部模型规划与人工协作体验。
- 深度 AG-UI/A2UI 双向交互与动态组件渲染。
- 外部 Agent Runtime 远端诊断、密钥自动轮换和生产级节点恢复。

## 验收原则

1. 所有页面必须通过 npm run typecheck 与 npm run build。
2. React 只使用 FastAPI 的 /api/v1 API；不直接访问数据库。
3. 任何关键写操作都应有清楚的动作说明、错误提示和必要的二次确认。
4. UI 状态更新以 API 响应为准；请求失败时不乐观地假装成功。
5. 旧版 /legacy 在迁移全部完成并回归前保持可用。
