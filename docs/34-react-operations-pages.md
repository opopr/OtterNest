# 34 · React 运行与成果管理（第三阶段）

## 页面与能力

### 1. Skills 管理 /skills

- 项目成员可提出 Skill 草稿；草稿状态为 proposed。
- 只有所有者可发布 proposed Skill。非所有者的列表接口只返回 published 记录。
- 支持 Skill 评价记录（pass/fail/concern）和所有者归档。
- 同一项目内 Skill 名称与版本组合唯一；重复组合返回冲突。
- 新增接口：POST/GET /projects/{project_id}/skills、POST /skills/{skill_id}/publish。原有 evaluations/archive 接口复用。

### 2. 任务临时授权 /access

- 从项目成员列表选择 Agent，只能向已加入该项目的 Agent 授权。
- 授权限定任务、granted_scopes、max_sensitivity 与 expires_in_hours。
- 支持查看当前任务全部授权和撤销授权。
- 授权不会替代项目成员身份、任务租约或敏感级校验；后端继续执行 ABAC 与到期检查。

### 3. 成果中心 /artifacts

- 按项目和任务查看成果文件版本、SHA-256、大小、敏感级、验收状态与验收备注。
- 原始文件上传使用 authenticated binaryRequest，X-Artifact-Name-Encoded 承载 UTF-8 百分号编码文件名，避免浏览器 Header 对中文文件名的限制；后端解码后仍执行文件名路径字符校验。
- 下载走 /artifacts/{id}/content，浏览器读取二进制 Blob；后端仍核对权限、敏感级与 SHA-256。
- 所有者只能对 pending 版本验收或驳回；最终版本不可修改，修改必须重新上传新版本。
- 支持项目级 .tar.gz 二进制归档导出、完整校验后恢复，以及 GC dry-run 和显式执行。执行 GC 前要求二次确认。

### 4. Agent Runtime /agents

- Agent 登记可提交能力声明；能力声明与所有者核验结果分开管理。
- 所有者可创建 Runtime、查看 enabled/status/online/last_seen/last_health。
- Runtime 使用独立凭据签发/轮换；令牌只在签发响应中返回一次。
- 支持禁用 Runtime 与 Agent。禁用只影响控制平面接入和调度，不宣称会终止外部机器上的进程。

### 5. 受限中文命令 /governance

- 新增自然语言命令输入区，只调用 POST /command/interpret。
- 当前只支持后端白名单模板：批准任务、取消任务、验收通过、验收驳回并说明原因、指派任务给 Agent。
- 模板解析失败时不创建计划；解析成功后创建版本化 CommandPlan。
- 此功能不是 LLM，不连接外部模型，不执行即时动作。所有操作仍需检查预览并显式确认。

## 新增 API 测试

tests/test_stage4_react_api.py 覆盖：
- Skill proposed/published 可见性、重复版本冲突、评价和归档。
- 中文文件名成果上传、下载内容一致性。

## 仍未迁移

- 完整多轮 LLM Command Center 对话与外部模型规划。
- 深度 AG-UI/A2UI 双向交互、动态组件渲染。
- 外部 Runtime 远端诊断、生产级自动凭据轮换与节点恢复。
- 自动加密备份、异地复制与生产恢复演练。

## 安全边界

React 仅是控制面客户端。所有权威数据、授权判定、任务状态转换、文件路径检查、版本锁定和哈希校验仍由 FastAPI/PostgreSQL 负责；前端不得绕过后端写库。
