# 29 · Codex CLI 非交互 Worker（真实 CLI 接口，人工验收）

本轮基于 Codex 官方 `codex exec` 非交互接口，新增 `otternest-codex-worker`，作为现有 `otternest-runner` 的子进程。**这里实现的是可执行的 Codex CLI 适配器和模拟 CLI 的端到端验证，不代表已经使用你自己的 Codex 账号完成真实付费服务联调。**

参考：OpenAI 官方 [Non-interactive mode](https://developers.openai.com/codex/noninteractive) 与 [CLI reference](https://developers.openai.com/codex/cli/reference)。

## 设计与权限

1. OtterNest 控制平面创建 Agent / 成员 / Task，Runner 用 Agent Token 原子领取、接收带 epoch 的上下文包。子进程不继承 OtterNest Owner/Agent Token 或数据库连接。
2. 适配器只从标准输入接受任务与经授权的知识、决策、已验收前置任务元数据；有界 JSON 文本发送给**操作者预先安装并认证过**的 Codex CLI。内容数据不会被解释为扩大工具/网络/账号权限的指令。
3. 调用的 CLI 为固定 `codex exec --json --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check --sandbox <mode> --output-last-message <private-file> -`，任务文本仅通过 stdin 传递；不会用 `shell=True`。
4. **默认 `--mode read-only`**。只有运维显式指定 `--mode workspace-write --enable-workspace-write`，且声明至少一个具体相对成果文件 `--artifact <path>`，才允许在独立临时任务工作区内进行写入。**不**支持 `danger-full-access`、`--yolo`、自动 Git push / PR / merge 或部署。
5. 解析有界 JSONL 事件流，必须有一次 `turn.started` 和一次 `turn.completed`，且无 `turn.failed` / `error`，然后读取私有目录的最终消息文件。退出码、JSONL 或最终消息不合法时拒绝提交成功；原始 stderr 不转发到任务记录。
6. `--artifact` 只能由运维预先声明，成功后复用 Runner 的相对路径 / 普通文件 / 符号链接 / 大小校验；由 Runner 在有效租约内上传、验证 SHA-256、生成 `self_reported` 检查点，**最终仍需所有者审核**。

## 首次手动验证（WSL2，Docker 可不安装）

在**专用、可信、低权限的 Linux 账户或 WSL2 执行节点**上安装对应版本 Codex CLI。先由操作者按官方方式完成 `codex login`，单独运行一次 `codex exec --sandbox read-only` 验证真实登录和套餐资格。不要将个人账号浏览器 Cookie、CLI `auth.json` 或 API Key 放入本仓库、CI、OtterNest 控制面数据库或聊天记录。

确保 OtterNest 服务和 PostgreSQL 已按 `DEVELOPMENT.md` 启动，创建项目和独立 Agent Token，加入 worker 成员。使用操作员的 CLI 登录，在相同用户身份下运行：

```bash
export OTTERNEST_AGENT_TOKEN='<agent-token>'
mkdir -p "$HOME/otternest-workspaces"
chmod 700 "$HOME/otternest-workspaces"

otternest-runner \
  --server http://127.0.0.1:8000 \
  --workspace-root "$HOME/otternest-workspaces" \
  --once --timeout 600 \
  -- otternest-codex-worker --mode read-only --timeout 540
```

上述例子 **Runner 使用 `--sandbox none`**：CLI 需要访问自己的认证目录和云端网络连接，而 OtterNest 之前的 `--sandbox bwrap` 是断网、隐藏用户 HOME 的受控本地代码环境，不适合把云端 CLI 整体塞入。Codex CLI 自己在 `read-only` 模式执行受限命令；但这并不等价于 OtterNest 已经用系统级权限隔离其 CLI 进程。应使用与控制面分离的专用执行身份与节点，且禁止向该身份赋予部署密钥。

如果本机需要 `CODEX_HOME` 指向由操作者控制的不同配置目录，使用 `otternest-runner --pass-env CODEX_HOME` 显式传递；默认只继承用户 `HOME`、`PATH` 等基础变量，不将代理、GitHub 或控制平面密钥透传给 Codex。个人账号的使用方式与额度必须符合平台当前的条款，不提供批量登录、Cookie 复用、绕过配额的实现。

### 在已隔离节点中显式授权写入一个文件

```bash
otternest-runner \
  --server http://127.0.0.1:8000 \
  --workspace-root "$HOME/otternest-workspaces" \
  --once --timeout 600 \
  -- otternest-codex-worker \
     --mode workspace-write --enable-workspace-write \
     --artifact report.txt --timeout 540
```

**注意：**目前 Runner 工作区是按任务创建的空临时目录。这一轮适合由 Codex 在任务要求下生成指定新文件或分析文本，**尚不等于拥有一个已检出的 GitHub 项目仓库**。后续必须实现可信仓库快照导入、commit/sha 绑定、独立测试与 patch/PR 人工审核，才能支持可靠的真实代码维护流水线。

写入模式不能在子进程环境中使用 `CODEX_API_KEY`（防止密钥被项目代码和工具进程继承）。请在专用执行身份下使用其自行登录保存的 CLI 凭据。即使这样，个人登录认证也必须与不可信代码环境隔离；高风险代码不应在此模式运行。

## CI 与未完成事项

新增测试使用模拟的 Codex CLI 程序完整验证 CLI 参数、stdin 上下文、read-only / workspace-write 权限、失败事件拒绝、结果大小上限、成果清单、Runner claim → 执行 → artifact → checkpoint → owner review。**测试不接触真实 Codex 账号、不会消耗真实 Token，也不能证明当前用户已完成登录。**

下一步：在用户受控 WSL2 真实执行节点检查版本与登录、进行一次只读任务联调；再补 operator-provided Git 仓库只读快照、不可变 commit 锚点与受审的代码补丁回传。真实 Codex/Gemini/Muse 多账号编排、预算与自动调度仍需在实际支持的接口与使用条款内逐项验证。
