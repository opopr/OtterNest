# 32 · React 前后端分离与 AI Studio 工作流

## 目标

将管理台交互层从 FastAPI 内嵌 HTML 中拆出，同时保持现有 Python 业务内核、PostgreSQL 数据模型、权限检查和事件语义不变。

- 后端：Python + FastAPI，继续作为唯一业务 API 和权限边界。
- 前端：React + TypeScript + Vite，位于 frontend/。
- 数据库：PostgreSQL，只允许后端访问。
- API 契约：FastAPI 自动生成 OpenAPI，前端通过 HTTP API 调用。
- 实时事件：继续使用现有项目 WebSocket、AG-UI 投影和 A2UI 校验接口。
- 兼容期：原有管理台保留在 /legacy；React 构建后由 / 提供。

## 仓库结构

仓库根目录 package.json 使用 npm workspace 指向 frontend/。这让本地开发和 Google AI Studio 从仓库根目录启动时，都能找到统一的 npm scripts；Python 包与数据库迁移仍保持原位置。

React 代码分层：
- frontend/src/api：HTTP 客户端、DTO 和 OpenAPI 生成类型。
- frontend/src/auth：所有者登录、初始化、2FA 和会话状态。
- frontend/src/components：布局、导航和通用 UI。
- frontend/src/pages：总览、项目任务、Agent、指挥中心、目标与工作流、知识库、Portfolio、系统与事件页面。

第一阶段迁移登录、总览、项目/任务基础操作、Agent 登记和令牌一次性展示。第二阶段进一步迁移任务审批/决策审查、目标依赖/工作流、记忆/项目章程/跨项目共享、Portfolio/通知/实验、系统保护模式/项目状态/事件流。成果版本/归档、TaskGrant、Skills 完整管理、运行时诊断和深度 Command Center 仍由 /legacy 承接；旧页面不在迁移期间删除。

## 本地开发

1. 启动 PostgreSQL 并配置服务端环境变量。
2. 执行 alembic upgrade head。
3. 在仓库根目录执行 uvicorn otternest.app:app --host 127.0.0.1 --port 8000。
4. 在另一个终端执行 npm install 和 npm run dev。
5. 打开 http://127.0.0.1:5173。Vite 将 /api 与 /assets 代理到 FastAPI。

生产或同源预览：
1. 在仓库根目录执行 npm install 和 npm run build。
2. 启动 FastAPI。应用自动检测 frontend/dist，并在根路径提供 React 应用。
3. 原版管理台继续可从 /legacy 访问。

可通过 OTTERNEST_WEB_DIST 覆盖构建目录。

## 跨域与 Google AI Studio

AI Studio 预览与 API 服务可能属于不同 Origin。此时：
- 前端构建环境设置 VITE_API_BASE_URL 为 FastAPI 的 HTTPS Origin，不包含 /api/v1。
- 后端设置 OTTERNEST_CORS_ORIGINS 为精确 Origin 列表，使用英文逗号分隔。
- 不启用通配符 Origin，不开放数据库端口。
- 仅允许必要 HTTP 方法和 Authorization、Content-Type 请求头。
- API 服务应位于 HTTPS、私有网络或受控访问入口之后。不要为了预览而把管理 API 无保护地暴露到公网。
- VITE_ 前缀变量会进入浏览器构建产物，绝不能存放令牌、密码、数据库 URL 或其他秘密。

AI Studio 导入工作流：
1. Build mode → 输入框旁 + → Import from GitHub → 选择 opopr/OtterNest。
2. 保留仓库根目录 package.json 和 frontend/ workspace。
3. 导入后打开 Settings → GitHub，使用 Link repository 连接现有的 opopr/OtterNest 仓库；首次推送前检查文件差异和目标分支，避免覆盖未合并的 GitHub 提交。
4. 让 AI Studio 只修改 frontend/ 下的 React 代码；所有业务状态和授权仍调用 FastAPI。
5. 在 Settings → GitHub 使用双向同步。推送或拉取前检查差异；GitHub 分支和 Pull Request 仍是代码审查边界。

建议给 AI Studio 的项目约束：
“本项目是 OtterNest 的 React 前端工作区。只在 frontend/ 内开发 UI；业务 API 的权威实现位于 Python FastAPI，契约为 /openapi.json。所有业务数据必须通过 /api/v1 API 获取或修改，不能直连 PostgreSQL，不能在浏览器保存服务端密钥，不能重写 auth、权限、审计或任务状态规则。跨域预览使用 VITE_API_BASE_URL 指向 FastAPI HTTPS Origin。修改接口需求时先提出 OpenAPI 契约变更方案，不要自行模拟后端响应。”

## OpenAPI 类型同步

FastAPI 运行时暴露 /openapi.json。启动本地 API 后执行 npm run api:types，可生成 frontend/src/api/schema.d.ts。前端 DTO 文件保留对关键页面的可读类型；生成文件用于检查契约差异和后续逐步收紧类型。

## 迁移原则

- 先迁移页面，不重写领域服务。
- 所有写操作继续经过后端 owner_only、require_project、require_writable_system 等校验。
- Bearer 会话只保存在当前页面内存，不使用 localStorage。
- 业务 API 的超时、错误和未知状态必须明确展示，不用模拟数据掩盖。
- WebSocket 只用于事件提示；页面状态仍从权威 REST API 刷新。
- 旧管理台至少保留到新前端覆盖全部高频操作并通过回归验收。


## 第二阶段页面路由

| React 路由 | 页面 | 核心后端接口 |
|---|---|---|
| /governance | 指挥中心与审批 | /command/plans、/decisions/*/reviews、/decisions/*/deliberation/*、/decisions/*/resolve |
| /goals-workflows | 目标与工作流 | /projects/*/goal-board、/goals/*/status、/goals/*/dependencies、/projects/*/workflow-* |
| /knowledge | 知识库与项目章程 | /projects/*/memories、/memories/*、/projects/*/charter、/projects/*/knowledge-shares |
| /portfolio | Portfolio 总览 | /portfolio/metrics、/briefing/daily、/notifications、/projects/*/experiments、/portfolio/schedule |
| /system | 系统设置与运行事件 | /system/mode、/projects/*/status、/projects/*/events |
| /skills | Skills 管理 | /projects/*/skills、/skills/*/publish、/skills/*/evaluations、/skills/*/archive |
| /access | 任务临时授权 | /projects/*/members、/projects/*/tasks、/tasks/*/grants |
| /artifacts | 成果中心 | /tasks/*/artifacts、/artifacts/*/content、/artifacts/*/review、/projects/*/artifacts/archive、/restore、/gc |

所有副作用操作都通过页面中的明确操作按钮触发；关键治理、调度、模式切换和归档类操作会要求用户确认。前端展示的是 API 响应，不以本地状态代替服务端状态。


## 第三阶段页面路由

| React 路由 | 页面 | 核心后端接口 |
|---|---|---|
| /skills | Skills 管理 | /projects/{id}/skills、/skills/{id}/publish、/skills/{id}/evaluations、/skills/{id}/archive |
| /access | 任务临时授权 | /projects/{id}/members、/projects/{id}/tasks、/tasks/{id}/grants |
| /artifacts | 成果中心 | /tasks/{id}/artifacts、/artifacts/{id}/content、/artifacts/{id}/review、/projects/{id}/artifacts/archive、/restore、/gc |

Agent 页面同时管理 AgentRuntime 注册、独立 token 签发/轮换、心跳状态与能力核验。指挥中心的自然语言入口仅使用白名单中文模板解析；不把模型规划器配置密钥暴露给浏览器。


## 第二阶段：统一任务中心

- React 新增 `/tasks` 跨项目任务中心，聚合当前所有者可访问项目的任务列表。
- 支持项目、状态、风险等级和关键词筛选；优先展示待授权、待验收与阻塞任务。
- 所有者授权与任务验收均先调用 `POST /api/v1/command/plans` 生成有时效的命令预览，再由用户显式确认 `POST /api/v1/command/plans/{id}/confirm`。
- 确认成功后重新读取项目任务，不在浏览器中乐观伪造任务状态。后端仍负责版本校验、权限校验、系统保护模式与审计事件。


## 第二阶段补充：任务详情与依赖 DAG

- 任务中心的每条记录可打开详情面板，读取 `GET /tasks/{task_id}`、项目 `task-graph`、任务依赖状态和关联项目事件。
- 详情包含任务指令、验收标准、执行结果、失败原因、Agent 指派、审批状态、租约代次、版本和完成时间。
- DAG 显示当前任务的上游依赖与直接下游任务；事件区按 `subject_id` 关联任务事件。
- 所有者可在任务尚未领取且处于允许编辑状态时增加或移除依赖。前端排除明显的下游循环候选，最终循环检测、跨项目校验和任务状态冻结仍由 FastAPI 强制执行。
- 项目事件接口单次最多读取 500 条，因此详情明确提示事件历史可能不完整，不将其冒充完整执行日志。
