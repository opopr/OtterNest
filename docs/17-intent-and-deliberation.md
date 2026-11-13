# 17 · 中文指令预览与两轮协商（migration 0009）

## 已实现的边界

这一轮增加了有明确语法规则的中文指令预览，不是自由自然语言模型理解；同时增加了有明确上限的第二轮 Agent 证据回应，不是多模型自动辩论引擎。

### 受限中文指令

POST /api/v1/command/interpret，使用管理员 Bearer 凭据，请求内容包含 project_id 和 text。例如：

    {"project_id":"<当前项目 UUID>","text":"请批准任务「报告」。"}

目前支持：
- 批准任务「任务名」
- 取消任务「任务名」
- 验收通过任务「任务名」
- 验收驳回任务「任务名」原因：说明
- 指派任务「任务名」给「Agent名」

目标必须在指定项目内有唯一且准确的名称，也可引用 UUID。跨项目、模糊、追加指令、不可识别句式拒绝生成动作。返回 interpretation.llm_interpreted=false；没有安装或调用 AI 模型。绝不能将本解析器宣传成完整的自然语言理解。

成功仅生成现有 CommandPlan 的十分钟预览，不改变目标任务；仍必须由所有者明确调用 POST /api/v1/command/plans/{id}/confirm，系统会重新校验任务版本、权限、风险门禁和外部 A2A 委托状态。重复确认幂等。

### 第二轮协商

第一轮依然由项目成员 Agent 通过 POST /api/v1/decisions/{id}/reviews 独立提交支持、反对或替代意见，所有者批准或开启第二轮之前，Agent 无权读取别人观点。

拥有至少两名不同 Agent 的第一轮意见后，仅所有者可开启第二轮：
- POST /api/v1/decisions/{id}/deliberation/open
- GET /api/v1/decisions/{id}/deliberation
- POST /api/v1/decisions/{id}/deliberation/rebuttals
- POST /api/v1/decisions/{id}/deliberation/close

第二轮 Agent 仅限第一轮参与者，每人只能回复一次，必须明确指向另一 Agent 的第一轮意见。第二轮进行中，别人的第二轮回应保持不可见；所有者可以查看全部。关闭需要实质原因，最多两轮，不允许重复开启。

**所有决策最终仍须由系统所有者批准**。第二轮未关闭时不能批准；任一轮有 oppose/alternative 时，最终 /resolve 必须记录所有者的实质异议处理说明。证据引用是未经验证的自述，字段 evidence_verified=false；不因参与者多数同意就自动认为事实成立。

新增 decision_deliberations 和 decision_rebuttals 数据表，Alembic 迁移版本 0009。通过事件与 Outbox 保存审计记录。单元测试包含歧义、越权、重复、阶段阻断和异议处理，PostgreSQL CI 额外验证并发第二轮提交。

尚未实现自由对话式 LLM 规划、自动协商发起、独立的证据真实性核验、第三方模型平台接入或生产部署。
