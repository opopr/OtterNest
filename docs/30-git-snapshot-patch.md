# 30 · GitHub 固定提交源码快照与 Codex 补丁交接

本轮将 [29 · Codex CLI Worker](29-codex-cli-worker.md) 从“在空工作区生成文件”扩展到**在特定 GitHub 分支的完整提交 SHA 对应的不可变代码快照中工作**，然后把变更生成为**供所有者审查的文本补丁**。不会自动写回源仓库、推送 commit、创建 PR 或直接合并。

## 使用场景与授权模式

源只能由 **Runner 操作员明确配置**，不能从任务正文或 Agent 输出中获得：可选择公共 GitHub `owner/repo` 或执行机上**事先经过信任审查的本地 Git 仓库**。必须同时提供 `--repo-ref refs/heads/main`（示例）和完整的 `--repo-commit <full_sha>`。不接受简写 SHA、GitHub URL（只接受仓库名）、PR URL、任意 Git 传输协议或动态任务指定的源地址。

对 GitHub 公共源，程序固定构造 `https://github.com/owner/repo.git`，仅拉取指定 `refs/heads/...` 的浅层提交，校验其 commit SHA **必须与操作员预先指定的 SHA 完全一致**。分支被推进或替换后，任务直接失败；不会偷偷运行另一个版本。Git 禁用交互式认证、任意 credential helper、外部配置与 URL 重定向，不复用用户浏览器 Cookie。私有 GitHub 仓库、GitHub App 身份认证和 Git LFS 二进制资产仍需单独设计，不在本轮声称支持。

离线本地源同样验证明确分支头和完整 SHA；在 CI 中使用的是独立临时 Git 源仓库，不需要外网或 Codex 账号。

## 两种执行方式

### 只读仓库分析

```bash
export OTTERNEST_AGENT_TOKEN='<agent-token>'
otternest-runner \
  --server http://127.0.0.1:8000 \
  --workspace-root "$HOME/otternest-workspaces" \
  --once --timeout 900 \
  -- otternest-codex-worker \
     --mode read-only \
     --github-repo opopr/OtterNest \
     --repo-ref refs/heads/main \
     --repo-commit <full_40_or_64_digit_commit_sha>
```

先从 GitHub 拉取被固定的提交，再把受限文件集合提取到临时工作区，整个过程中不会包含 `.git` 工作目录。Codex CLI 按默认只读模式分析，结束后检查源码快照是否发生变化；若改变则视为违规，拒绝成功提交。命令中的 GitHub 仓库是示范，完整 SHA 必须由操作员独立确认。

### 代码修改与补丁回传

```bash
otternest-runner \
  --server http://127.0.0.1:8000 \
  --workspace-root "$HOME/otternest-workspaces" \
  --once --timeout 900 \
  -- otternest-codex-worker \
     --mode workspace-write --enable-workspace-write \
     --github-repo opopr/OtterNest \
     --repo-ref refs/heads/main \
     --repo-commit <full_40_or_64_digit_commit_sha> \
     --patch-file changes.patch \
     --timeout 780
```

Codex 完成后，适配器比较工作区和基线内容，自动生成两个 Runner 可上传的普通成果文件：

- `changes.patch`：标准 unified text diff，包含新增、删除、修改文件；**不执行 `git apply`**，不会改变 GitHub 仓库。
- `otternest-provenance.json`：`otternest.git-patch.v1` 来源记录，包括仓库来源、明确的分支、**基线 commit SHA**、补丁文件名、补丁 SHA-256、变更文件数量、未自动应用标记。

成果依旧经过 Runner 的任务 epoch 检查、SHA-256 对账和 owner 验收。所有者应先将 patch 下载到**独立的仓库副本**，确认当前提交仍等于基线 SHA，然后执行 `git apply --check changes.patch`、审查 diff 和测试，最后决定是否提交代码。OtterNest 不会代替这一步。报告文件仍可用 `--artifact report.txt` 等额外清单声明，最多合计 8 项成果。

如果已有本地可信 Git 仓库，可将上面 `--github-repo` 换成 `--local-repo /absolute/trusted/repo`。这只适合操作员控制的存储位置，不应让 Agent 决定路径，也不能让陌生用户上传一个自称“本地可信”的仓库。

## 安全与资源限制

- 单次快照 tar 上限 64 MiB，普通文件合计 40 MiB、最多 1200 项、单文件最多 3 MiB。超过限制、符号链接、硬链接、特殊文件、危险相对路径会失败关闭。
- 对变更后的工作区重新扫描，拒绝 symlink、hardlink、超额文件和特殊文件；**只支持 UTF-8 文本变更**。二进制文件可以保留不变，但修改过的二进制无法作为这个文本补丁提交；已修改文件必须以换行符结尾。
- 变更路径为了补丁应用可验证，只接受安全 ASCII 文件名；生成的补丁不超过原 Runner 的 5 MiB 文件上限。
- 不信任仓库里的 AGENTS.md、README、工作流脚本等文本为权限授权；也不执行 `git checkout`、Git hooks、Git filter 或 Git push。Codex 在专用账号与 `workspace-write` 权限下运行，**这仍不是对恶意代码的生产级 OS 沙箱**。
- 公共 GitHub 拉取需要网络和 GitHub 可用，执行主机必须由管理员配置出口限制、DNS/代理和防火墙，且避免高权限认证与机密数据。此阶段只做固定来源的 HTTPS Git fetch，尚未实现强制网络出口 allowlist 的内核级保护。
- 此流程尚未完成用户实际 Codex 登录、WSL2/ARM 联调；GitHub 远程拉取和真实 Codex 执行也未被 CI 中的模拟程序替代验证。CI 测试使用**本地临时真实 Git 仓库 + 模拟 Codex CLI + 真实 Runner/API**，验证 patch 能被 `git apply --check` 识别并进入人工验收流程。
- 当前无补丁自动合并、审查机器人、GitHub App 写入、跨 AI 节点共享快照、私有仓库授权或 LFS 支持。需要进入生产阶段时应引入外部隔离执行节点与端到端账号联调。

## 验证与后续

```bash
pytest -q tests/test_git_snapshot.py tests/test_codex_git_handoff.py
```

下一阶段建议：GitHub App 或只读 Token 获取私有仓库、从审查通过的 patch 创建受控 PR（**必须人类确认**）、加入真实 Codex/Gemini CLI 联调和质量/成本记录、再做持久化协作执行与恢复。
