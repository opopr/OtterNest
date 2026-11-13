# 12 · 任务 DAG、优先级与 MCP 标准接入

> 本文件记录实际新增的源代码接口。请以 API OpenAPI 文档与当前测试为准。

## 任务依赖

创建项目任务：`POST /api/v1/projects/{project_id}/tasks`（Owner）

```json
{
  "title": "审核接口",
  "instruction": "审查依赖任务的成果",
  "acceptance": "完成独立审查报告",
  "risk": "R1",
  "priority": 80,
  "dependency_ids": ["前置任务UUID"]
}
```

- 任务进入 `waiting_dependencies`，直到全部前置任务状态为 **`completed`**（须通过 owner 验收）。
- R3 任务初始 `waiting_approval`；获得审批后，若前置任务未完成则转为 `waiting_dependencies`。
- 前置任务最后一个被验收后，事务中触发 `task.dependencies_satisfied` 并转 `ready`。
- `GET /api/v1/projects/{project_id}/task-graph` 同时提供任务及依赖边，供 UI 展示。
- 任务执行前才能增删依赖。跨项目、自依赖、重复、形成循环均被拒绝。
- 最大 64 个直接前置任务；大图检查存在服务端边界以避免无限遍历。
- 取消前置任务不等于取消下游；下游继续等待，留给所有者处理。

## Agent 定向与优先级

- Owner 使用 `PUT /api/v1/tasks/{id}/target`，例如 `{"agent_id":"UUID"}`；`null` 可取消预指派。
- Agent 队列依优先级降序返回可领取任务；对于指定了目标的任务，非目标 Agent 既看不到队列项目，也不能成功 Claim。
- 这是**受控路由**，不是主动启动远程 Agent；状态仍按实际接单/执行情况记录。

## 使用正式 MCP SDK

本版本采用 **Agent 侧 stdio** MCP 桥接，旨在让可运行 MCP Client 的 Agent 访问 OtterNest 已授权的业务工具，不是暴露在公网上的公众匿名 MCP 服务。

安装可选依赖：

```bash
python -m pip install -e '.[mcp]'
```

由已授权 Agent 的 MCP Client 启动以下命令：

```bash
export OTTERNEST_AGENT_TOKEN='on_agent_...'
otternest-mcp --server https://your-otternest.example
```

Token 应通过执行环境的受保护凭据配置注入，**不要在公有仓库、日志或标准输出里暴露**。外部接入需要真实 HTTPS；仅 localhost 开发允许 HTTP。

标准工具覆盖可见任务、查看依赖、领取任务、获取上下文、心跳、提交/报告失败、已发布记忆、技能和提交记忆提案。所有 R3 操作仍由中央服务的 Owner 授权门禁控制，MCP 不包含“批准自己成果”的工具。

## 已知范围限制

- 当前运行环境不具备官方 MCP SDK 安装条件；本地可测试 REST 委托层，GitHub CI 安装额外依赖并验证官方工具注册。**不将其描述为已完成与具体 Muse / Codex / Google 环境的实际互操作测试。**
- A2A 协议与 Agent 可用性检测、能力验证仍待后续开发。
- 目前自动化 Runner 仍可通过通用 REST 轮询工作，不要求 MCP 才可运行。
