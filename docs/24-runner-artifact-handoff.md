# 24 · Runner 成果自动回传与多 Agent 验收交接

本轮基于现有 Agent Token、租约 `epoch`、成果文件 API、检查点和任务依赖 DAG 实现**可验证的通用执行链路**。无需新增数据库迁移。此功能是完整产品研发的一部分，不等于未授权代码的安全沙箱，更不意味着个人订阅账号已被自动接入。

## 一、执行输出协议

仍支持既有命令在标准输出打印纯文本或 `{"summary":"..."}`。启用成果回传时，可信可执行程序应在自己的**当前工作目录**写入文件，并在标准输出返回单个 JSON 对象：

```json
{
  "summary": "第一名 Agent 已生成初稿；等待所有者核验。",
  "artifacts": [
    {"path": "reports/report.txt", "name": "report.txt"}
  ],
  "checkpoint": {
    "summary": "初稿生成及校验完成",
    "next_steps": "由所有者审核文件和检查点，之后放行后续任务"
  }
}
```

- `summary` 必需，最长 20000 字符。
- `artifacts` 可选、最多 8 个。路径只能是相对于当前临时工作目录的 POSIX 相对路径，拒绝 `..`、绝对路径、反斜杠、符号链接、硬链接、目录、重复来源和超限文件。每文件最高 5 MiB，Runner 总上传上限 20 MiB。
- `name` 为服务器保存的文件名，当前仅支持 1—240 字符可打印 ASCII（HTTP header 兼容），且不能重复。未提供时取路径的末尾名称。
- `checkpoint` 可选。其摘要上限 10000 字符，后续步骤上限 6000 字符；Runner 上传文件得到 ID 后创建 **self_reported** 检查点，绝不自我核验。

这个输出协议由**运维者预先批准的可执行程序**生成。OtterNest 不会把用户自然语言任务拼成 shell 命令；是否运行 Python、测试器或编码 Agent 由独立执行主机的操作员决定。

## 二、启动方式

在隔离执行主机上预先创建可写且仅执行账户有权访问的绝对目录，例如 `/srv/otternest/workspaces`。准备好已加入项目的 Worker Agent。推荐先注册一个 Runtime 并签发该实例的独立 Runtime Token；旧 Agent Token 仍可兼容。运行示例：

```bash
export OTTERNEST_RUNTIME_TOKEN='<runtime-token>'
export OTTERNEST_RUNTIME_ID='<runtime-id>'
# 兼容模式（未绑定 Runtime）：
# export OTTERNEST_AGENT_TOKEN='<agent-token>'
mkdir -p /srv/otternest/workspaces
chmod 700 /srv/otternest/workspaces

otternest-runner \
  --server https://YOUR_PRIVATE_CONTROL_HOST \
  --once \
  --workspace-root /srv/otternest/workspaces \
  -- /absolute/path/to/operator-reviewed-worker
```

`--workspace-root` 必须为已经存在的**绝对**目录。Runner 为每轮任务分配一个独立的临时目录，作为子进程的 cwd；上传和检查点记录完成或失败后清理目录。未指定此选项时保持旧行为：允许纯文本结果，但**拒绝**包含文件清单的输出。

子进程不会继承 `OTTERNEST_AGENT_TOKEN`、`OTTERNEST_RUNTIME_TOKEN`、`OTTERNEST_OWNER_TOKEN` 或数据库 URL。确有必要传递的非控制平面凭据，需要逐个用 `--pass-env KEY` 显式许可；不能把服务端凭据放进工作目录。所有 Agent 运行身份仍由后端访问控制校验。

## 三、后续 Agent 如何接收成果

1. Agent A 在当前租约内输出文件。Runner 上传并验证服务器返回的 SHA-256，随后创建自述检查点、提交任务，状态为 `submitted`。
2. 所有者按需下载验收成果并单独核验检查点，然后验收任务。只有任务被接受为 `completed` 才能释放 DAG 后继任务。
3. Agent B 领取依赖任务后，`GET /tasks/{id}/context` 新增 `dependency_handoffs`。它只包含已完成的前置任务，并对每项提供被验收的任务摘要、最新 **owner_verified** 检查点，以及状态为 **accepted** 的成果元数据（ID、名称、版本、SHA-256）。
4. 前置依赖最多 64 个，但上下文最多携带前 12 项交接，每项最多 8 个已验收成果的元数据。响应提供 `dependency_handoffs_total` 和 `dependency_handoffs_truncated`，避免误认为较大的依赖集合已经全部传输。
5. Agent B 可用已有受控成果下载 API 取得文件内容，但应核对 SHA-256 并把前序结果当作不可信参考数据，不能当成执行权限或系统指令。

OpenAI-compatible 文本模型适配器现也能在用户消息里的**参考数据**部分接收这些交接摘要，它不会因此获得文件系统或工具能力。

## 四、错误与未解决边界

- 文件名、工作目录、大小、摘要或租约校验失败后，Runner 不会把任务标记为成功；它会尝试上报失败，任务需要所有者检查再解除阻塞。
- 文件上传和任务提交不是一个跨 HTTP 的原子事务。若前几份文件已经提交而下一份上传失败，前面的版本可能仍在数据库中；此时不得自动重试，以免重复产生文件版本或外部副作用。
- 当前临时工作目录提供**路径收口**，不是 OS 容器/虚拟机/权限隔离；运行不可信代码需配置独立用户、内核资源限制、网络出口限制和可信构建环境。用户不必安装 Docker，但在没有等效隔离前不得执行来源不可信代码。
- Runner 不自动执行 Git 提交、PR 合并、远端 API 写入或其他有副作用动作；这些动作需要单独的能力边界、幂等台账及所有者审批。
- 限制性的路径校验不等于抵御同一操作系统身份下的主动恶意进程；磁盘加密、隔离、恢复演练和生产运维尚待继续完成。
- 回归测试覆盖真实 ASGI API、可信子进程写文件、完整成果回传、两名不同身份 Agent 的依赖放行和核验交接；不代表公网部署或第三方产品已实测。

查看之前的模型适配器说明：[23 · 模型执行器](23-model-worker.md)。


## 五、Runtime 级执行身份

- 推荐为每个真实执行实例单独注册 Runtime，并通过管理界面签发 Runtime Token。
- 设置 `OTTERNEST_RUNTIME_TOKEN` 与 `OTTERNEST_RUNTIME_ID` 后，Runner 在读取任务队列前先向绑定 Runtime 发送心跳；Runtime Token 必须与该 ID 匹配，否则请求被拒绝。
- Runtime Token 的 Agent 归属由服务端从哈希凭据解析，不接受客户端自报 Agent ID。该身份可领取其 Agent 有权访问的任务，但 Runtime 心跳只能更新其绑定实例。
- Runner 子进程环境明确禁止继承 Runtime Token。Agent Token 模式继续兼容旧部署，但共享 Agent Token 不提供单实例凭据隔离。
