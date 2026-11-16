# OtterNest 开发说明

项目已进入完整产品开发，功能覆盖和进度见 [docs/11-development.md](docs/11-development.md)。以下仅适用于**本地隔离开发环境**，不是公网部署建议。

## 配置

需要 Python 3.11+ 和 PostgreSQL。将 ".env.example" 的变量设置为自己的安全值，切勿把真实凭据提交到仓库。管理员令牌必须是至少 32 个随机字符。


a) 创建 Python 虚拟环境并执行 `pip install -e '.[dev]'`。

b) 设定 `OTTERNEST_OWNER_TOKEN`、`OTTERNEST_DATABASE_URL`。

c) **空数据库首次迁移**：`alembic upgrade head`。

d) 本地启动：`uvicorn otternest.app:app --host 127.0.0.1 --port 8000`。

e) 管理界面：`http://127.0.0.1:8000/`，API 文档：`/api/docs`。

f) 运行测试：`pytest -q`。GitHub Actions 分别运行 Python 单元/接口测试、Alembic SQLite 迁移、PostgreSQL 16 迁移与并发领取测试，以及 React 类型检查和生产构建。目标服务器上的压力与故障恢复仍需单独验收。

## React 前端开发

React + TypeScript + Vite 工程位于 `frontend/`，仓库根目录提供 npm workspace 命令。

- 安装前端依赖：`npm install`
- 启动开发前端：`npm run dev`，默认访问 `http://127.0.0.1:5173`
- Vite 将 `/api` 和 `/assets` 代理到本地 FastAPI（默认 `http://127.0.0.1:8000`）
- 类型检查：`npm run typecheck`
- 构建：`npm run build`，输出到 `frontend/dist`
- OpenAPI 类型生成：后端运行后执行 `npm run api:types`

构建完成后，FastAPI 自动在 `/` 提供 React 页面，旧版完整管理台保留在 `/legacy`。若前端构建目录不在仓库默认位置，可设置 `OTTERNEST_WEB_DIST`。

跨域预览（例如 Google AI Studio）时，前端设置 `VITE_API_BASE_URL` 为 FastAPI 的 HTTPS Origin；后端设置 `OTTERNEST_CORS_ORIGINS` 为预览页面的精确 Origin，多个值用英文逗号分隔。不要使用通配符，不要开放 PostgreSQL 端口。VITE_ 变量会进入浏览器构建产物，不能放置任何令牌或数据库密码。详细步骤见 [docs/32-react-frontend-separation.md](docs/32-react-frontend-separation.md) 与 [AI_STUDIO_GUIDE.md](AI_STUDIO_GUIDE.md)。

## 安全限制

- Web 登录使用所有者初始化令牌完成首次账号创建，后续使用有时效的会话令牌，并支持 TOTP 2FA；会话令牌仅保存在当前页面内存。Owner Token 仍是高权限服务器秘密，必须妥善保管。系统尚未完成生产级反滥用、审计保留和公网安全审计。
- 仅绑定本机地址，不直接暴露公网。尤其不得公开数据库、管理 API 或未经安全审核的 Agent 接口。
- 已提供可选 OpenAI-compatible 文本模型 Worker、Codex CLI Worker、Google Gemini Worker (`otternest-gemini-worker`) 与 Muse 云端机器适配器 (`otternest-muse-worker`)，但它们需要操作者在可信执行节点显式配置 API 或机具登录；不代表自动接通个人订阅或未经授权的云端操作。Agent 注册本身只代表身份登记。
- 当前支持单文件直接上传及基于 `storage.py` 的可插拔分段/分块大文件上传管理器（支持 S3/MinIO 与本地目录）。
- 提供 `otternest-security-audit` CLI 工具进行上线前凭据熵值、存储权限与运行模式的安全自检。

## 通用 Agent Runner（隔离执行环境使用）

Agent 通过所有者创建的受限令牌加入一个项目后，可以在**外部独立环境**上配置 

a) 优先配置 `OTTERNEST_RUNTIME_TOKEN` 与匹配的 `OTTERNEST_RUNTIME_ID`；旧版 `OTTERNEST_AGENT_TOKEN` 仍兼容。凭据只交给对应 Runner，作为本机秘密保管。

b) 一个可信的可执行文件，**标准输入读取 JSON 上下文包**，标准输出返回文本或 `{"summary":"..."}`。

c) 启动 `otternest-runner --server https://CONTROL_HOST --once -- /absolute/path/to/trusted-command`；去掉 `--once` 才会轮询接单。

执行命令由操作者配置，**不会从任务文本拼接 shell 命令**；默认不继承 OtterNest 管理员令牌、Agent 令牌和数据库 URL。需要其他环境变量时显式使用 `--pass-env KEY`。运行账户、文件系统和网络沙箱需由环境所有者另外隔离。当前 Runner 只提交结果等待人工验收，不能宣称第三方平台已经被成功集成。

## 签名 Webhook 事件投递

仅在明确需要推送事件时配置 `OTTERNEST_WEBHOOK_SIGNING_KEY`（独立高强度密钥）和 `OTTERNEST_WEBHOOK_ALLOWED_HOSTS`（逗号分隔、严格匹配的**可信公开域名**）。系统默认拒绝任意 URL、HTTP、非 443 端口和非公开 IP 的回调。

由所有者创建项目 Webhook 后，注册接口**仅返回一次**签名密钥。接收端按 `X-OtterNest-Timestamp + "." + 原始请求体` 计算 HMAC-SHA256，并以 `X-OtterNest-Event-ID` 幂等处理重复事件。投递器在**独立进程**运行：`otternest-webhooks --once` 执行单轮投递；去掉 `--once` 才进入循环。这里不自动触发任何第三方 AI 产品，目标端是否可接单需要实测。

由于域名解析与建立连接之间仍可能发生 DNS 改变，生产环境需要额外部署出口网络限制和可信域名 DNS 策略，不能仅依赖应用层过滤。事件投递是**至少一次**，接收端必须按事件 ID 去重。

## 项目导出包导入与恢复

管理台「系统设置 → 校验并恢复项目 JSON 包」支持恢复已导出的 `otternest.export.v1` JSON 文件；接口也可用于自动化：

- `POST /api/v1/projects/import/preview`，请求体 `{"bundle": <导出包>}`：校验 schema、摘要、大小和内部 ID 引用，不写入数据。
- `POST /api/v1/projects/import`，请求体 `{"bundle": <导出包>, "new_name": "恢复项目名称", "confirm_restore": true}`：在单个数据库事务中创建新项目与 ID 映射。

恢复的项目从 `paused` 开始。未完成任务需要检查后手动解除阻塞；R3 任务需重新授权。旧 Agent 被映射为禁用身份，不迁移任何令牌或能力核验；非归档记忆、Skill、决策以提案形式恢复。文件内容及 Webhook/A2A 配置不在 JSON 包中。SHA-256 仅检查文件完整性，不验证导出来源。请先预览包并确认其中数据可信，再恢复。更多细节见 [docs/21-project-import-restore.md](docs/21-project-import-restore.md)。

## 成果文件存储与版本

`OTTERNEST_ARTIFACT_DIR` 指向本机私有成果目录（默认 `./var/artifacts`）。上传单文件上限 5 MiB；同一任务同名文件每次上传都会生成新版本并保留旧内容。项目成员下载前服务端核对路径仍位于对应项目目录，并验证 SHA-256。所有者可对每个版本做一次验收或驳回；需要修改时上传新版本。项目 JSON 导出只包含版本与验收元数据，不包含文件字节。请将 PostgreSQL 与该目录纳入同一备份/恢复计划；目前仓库尚未提供自动备份或跨机文件迁移命令。详见 [docs/22-artifact-lifecycle.md](docs/22-artifact-lifecycle.md)。

## 真实模型 API 与第三方 Worker 适配器

- **OpenAI-compatible**: `otternest-model-worker`（通过 Chat Completions 生成文本成果）。
- **Google Gemini**: `otternest-gemini-worker`（通过 Gemini REST API 进行受控文本生成）。
- **Codex CLI**: `otternest-codex-worker`（调用操作员本机授权的 Codex CLI 非交互执行并生成补丁/成果）。
- **Muse Cloud**: `otternest-muse-worker`（调度远端 Muse 云端机器并完成产物 SHA-256 校验回传）。

独立设置 `OTTERNEST_MODEL_API_KEY` 并用 `--pass-env OTTERNEST_MODEL_API_KEY` **仅对可信子进程放行**。完整命令、验证方式、明文数据外发边界和平台兼容限制见 [23 · 模型 Runner 适配器](docs/23-model-worker.md)。

## Workspace 成果自动回传与多 Agent 交接

在可信执行主机上，通用 Runner 可选 `--workspace-root /absolute/private/workspaces`。每项任务创建独立临时目录作为可执行程序 cwd；可信 Worker 可返回 `{"summary":"...","artifacts":[{"path":"result.txt"}],"checkpoint":{"summary":"..."}}`。Runner 将使用绑定的 Runtime Token（兼容 Agent Token）上传校验后的成果，在同一租约 epoch 记录自述检查点并提交任务。后继 Agent 只能从**已完成并验收**的 DAG 依赖中取得有界交接元数据。此功能**不是**不可信代码安全沙箱，不应将任意任务文本转为 shell。详见 [24 · Runner 成果与交接](docs/24-runner-artifact-handoff.md)。

## 私有 Linux 服务器部署

原生 systemd、Nginx、PostgreSQL 和私有 HTTPS 部署模板见 [docs/35-native-linux-deployment.md](docs/35-native-linux-deployment.md)。该指南面向 ARM64 私有试运行环境，尚未在实际服务器上验收，不代表生产就绪。

## 目标与依赖

管理台「项目与任务 → 目标管理」可以创建目标、设置前置目标、暂停/恢复目标并提交完成确认，也可以在创建任务时将任务关联到活动目标。暂停目标会阻止其关联任务的新调度和领取，但不会远端停止已运行任务。完成目标必须提供至少 5 个字符的完成依据；系统要求其前置目标已完成、关联任务均已由所有者验收为 `completed`。没有关联任务的目标可以由所有者依据成功标准直接确认完成。完成状态是终态，后续调整应新建目标。

对应接口：`GET /api/v1/projects/{project_id}/goal-board`、`POST /api/v1/goals/{goal_id}/dependencies`、`DELETE /api/v1/goals/{goal_id}/dependencies/{predecessor_id}`、`POST /api/v1/goals/{goal_id}/status`。目标状态变更写入项目事件；完成依据对可读取该项目事件的成员可见，不要在其中放入密钥或其他敏感内容。手工验收步骤和限制见 [docs/31-goal-lifecycle.md](docs/31-goal-lifecycle.md)。
