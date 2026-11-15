# 23 · OpenAI-compatible 文本模型 Runner 适配器

本轮在现有 `otternest-runner`、任务租约、Agent 权限、Context Packet 和人工验收链路上增加 **operator-configured** 的文本模型执行器 `otternest-model-worker`。此适配器是真正的 HTTP API 客户端，但**不是**凭订阅账号自动控制 Codex/Gemini/Muse 的连接器，也不具备浏览器、代码仓库或系统工具调用能力。

## 执行路径与边界

1. 所有者注册独立 Agent，赋予项目成员身份并核验实际声明的能力。任务必须处于 `ready`，R3 还需已批准，并由 Agent 领取到有效 `lease_epoch`。
2. 原有 Runner 读取授权队列、`claim`、读取 `/tasks/{id}/context`，以 stdin 提供有界 JSON Task Context Packet。Runner 的 Owner/Agent Token 和数据库连接**不传递**给子进程。
3. 独立子进程将任务、验收条件和已授权的有限参考上下文组织成标准 `chat/completions` 消息；模型密钥从操作者指定的环境变量读取。所有者需要自行确认目标服务的数据保留与跨境传输政策。
4. 模型提供单次文本输出，经格式、结束原因与大小校验后交回 Runner；Runner 通过同一租约 epoch 提交 `submitted`，由所有者单独验收。模型**不能**自我批准、改变权限或写入外部系统。
5. 如果调用失败、服务返回错误、出现工具调用需求、输出不完整或为空，子进程返回非零退出码；Runner 尝试把该任务标记为 `blocked`，等待人工核查。网络状态不明时不可把请求自动当作已成功。

## 使用条件

- Python 3.11+，正常安装 OtterNest（`pip install -e '.[dev]'`）。
- 后端已通过 PostgreSQL 迁移，服务仅在受信任网络中运行；独立执行主机可访问该控制平面。
- 管理员创建 Agent Token、项目成员和（若指定）运行实例。设置两个**互不相同**的凭据：`OTTERNEST_AGENT_TOKEN` 供 Runner 请求 OtterNest；`OTTERNEST_MODEL_API_KEY` 仅供子进程调用模型供应商。不要向子进程转发 Owner Token。
- 配置一个**确实开放给用户的** OpenAI-compatible `/chat/completions` HTTPS 接口和模型 ID。许多用户订阅不包含独立 API 访问额度，不能假设这些订阅可用。

示例（在隔离执行机的 shell 中，由操作者自行设置有效凭据）：

```bash
export OTTERNEST_AGENT_TOKEN='<your-independent-agent-token>'
export OTTERNEST_MODEL_API_KEY='<your-independent-model-api-key>'

otternest-runner \
  --server https://YOUR_PRIVATE_CONTROL_HOST \
  --once \
  --timeout 180 \
  --pass-env OTTERNEST_MODEL_API_KEY \
  -- \
  otternest-model-worker \
    --endpoint https://YOUR_TRUSTED_PROVIDER.example/v1/chat/completions \
    --model YOUR_MODEL_ID \
    --timeout 90 \
    --max-tokens 1024
```

不要在共享终端历史、CI 日志或仓库中写入真实密钥。凭据更适合通过执行机的受限 secret 注入机制提供。命令中的域名和模型名只是占位符，必须替换成可信且实际支持的端点。

如果特定提供者仅支持 `max_completion_tokens` 字段，可给子进程增加 `--token-parameter max_completion_tokens`。与实际 API 的兼容性需逐家验证，不保证任意供应商支持完全相同的 Chat Completions 语义。

本地开发模型服务允许显式使用 `--allow-local-http`，但仅放行 `127.0.0.1`、`localhost` 或 `::1`，且运行在操作者控制的机器上。默认必须 HTTPS，拒绝 URL 内的用户名、密码、查询参数与重定向。这个限制不能代替真实生产环境中的防火墙、DNS 和 egress allowlist。

## 安全和限制

- 系统提示明确禁止模型虚称已运行代码、部署、外部动作或核验。模型输出仍可能不准确，**必须由人验收**。
- 命令由操作员配置，任务内容不会拼接到 shell 中。文本模型适配器不提供工具调用和副作用执行；需要真正的编码、浏览器或多步骤 Agent，继续使用受限制环境里的可信 CLI executable 或单独的协议适配层。
- 上下文中的记忆、决策与检查点只是非可信参考数据，不获得 system 优先级；任务内容与项目信息**会发送到指定模型提供方**，应当对敏感项目设置额外许可规则。
- 模型端点只由执行机命令行配置，不读取任务内 URL；默认不使用代理环境变量，也不跟随重定向。生产网络出口仍需系统层控制。
- 单次调用，无自动重试，最长模型等待 600 秒，摘要上限 20000 字符；成本、速率限制、配额、质量验证仍需继续研发。
- Runner 将 JSON stdin 预先写到匿名临时文件，避免子进程不消费 stdin 时阻塞主执行循环；超出 20000 字节的 stdout 会失败，不再静默截断。
- 测试覆盖模拟模型 HTTP 响应、真实本地 HTTP 服务器与子进程，**不是第三方真实账密联调**。
