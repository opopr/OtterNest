# OtterNest 开发说明

项目已进入完整产品开发，功能覆盖和进度见 [docs/11-development.md](docs/11-development.md)。以下仅适用于**本地隔离开发环境**，不是公网部署建议。

## 配置

需要 Python 3.11+ 和 PostgreSQL。将 ".env.example" 的变量设置为自己的安全值，切勿把真实凭据提交到仓库。管理员令牌必须是至少 32 个随机字符。


a) 创建 Python 虚拟环境并执行 `pip install -e '.[dev]'`。

b) 设定 `OTTERNEST_OWNER_TOKEN`、`OTTERNEST_DATABASE_URL`。

c) **空数据库首次迁移**：`alembic upgrade head`。

d) 本地启动：`uvicorn otternest.app:app --host 127.0.0.1 --port 8000`。

e) 管理界面：`http://127.0.0.1:8000/`，API 文档：`/api/docs`。

f) 运行测试：`pytest -q`。早期单元/接口测试使用 SQLite；真实 PostgreSQL 和并发的验证仍需补充。

## 安全限制

- 初期使用单所有者 Bearer 令牌，UI 仅在当前页面内存保存令牌。尚未达到生产级会话、反滥用、审计保留和远程访问控制标准。
- 仅绑定本机地址，不直接暴露公网。尤其不得公开数据库、管理 API 或未经安全审核的 Agent 接口。
- 目前提供 Agent 通用轮询队列和任务上下文接口；**仍没有**真实第三方模型自动调用或云端唤醒能力，Agent 注册本身只代表身份登记。
- 当前 artifacts 上传上限 5 MiB，超大文件需要后续对象存储/分段上传设计。

## 通用 Agent Runner（隔离执行环境使用）

Agent 通过所有者创建的受限令牌加入一个项目后，可以在**外部独立环境**上配置 

a) `OTTERNEST_AGENT_TOKEN`（只交给 Runner，本机秘密）。

b) 一个可信的可执行文件，**标准输入读取 JSON 上下文包**，标准输出返回文本或 `{"summary":"..."}`。

c) 启动 `otternest-runner --server https://CONTROL_HOST --once -- /absolute/path/to/trusted-command`；去掉 `--once` 才会轮询接单。

执行命令由操作者配置，**不会从任务文本拼接 shell 命令**；默认不继承 OtterNest 管理员令牌、Agent 令牌和数据库 URL。需要其他环境变量时显式使用 `--pass-env KEY`。运行账户、文件系统和网络沙箱需由环境所有者另外隔离。当前 Runner 只提交结果等待人工验收，不能宣称第三方平台已经被成功集成。

## 签名 Webhook 事件投递

仅在明确需要推送事件时配置 `OTTERNEST_WEBHOOK_SIGNING_KEY`（独立高强度密钥）和 `OTTERNEST_WEBHOOK_ALLOWED_HOSTS`（逗号分隔、严格匹配的**可信公开域名**）。系统默认拒绝任意 URL、HTTP、非 443 端口和非公开 IP 的回调。

由所有者创建项目 Webhook 后，注册接口**仅返回一次**签名密钥。接收端按 `X-OtterNest-Timestamp + "." + 原始请求体` 计算 HMAC-SHA256，并以 `X-OtterNest-Event-ID` 幂等处理重复事件。投递器在**独立进程**运行：`otternest-webhooks --once` 执行单轮投递；去掉 `--once` 才进入循环。这里不自动触发任何第三方 AI 产品，目标端是否可接单需要实测。

由于域名解析与建立连接之间仍可能发生 DNS 改变，生产环境需要额外部署出口网络限制和可信域名 DNS 策略，不能仅依赖应用层过滤。事件投递是**至少一次**，接收端必须按事件 ID 去重。
