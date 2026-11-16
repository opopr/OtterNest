# 35 · 私有 Linux 原生部署基线

> 状态：已提供 systemd、Nginx 和环境配置模板；尚未在用户的 Oracle ARM 实例上实机安装或验收。它是受控私有试运行基线，不代表已完成生产安全审计。

## 1. 部署目标与边界

本指南面向单台 ARM64、约 1 vCPU / 6 GiB RAM 的私有控制平面主机，采用 Linux 原生进程和 systemd，不依赖 Docker。

- 控制平面：FastAPI、PostgreSQL、任务调度器、事件工作流 Worker。
- 可选进程：签名 Webhook 投递器。只有配置密钥和目标主机白名单后才启用。
- 反向代理：Nginx 终止 HTTPS，Uvicorn 只监听 127.0.0.1。
- 执行平面：Runner、Codex CLI 或其他实际执行环境放在独立执行节点；不要把不可信任务代码放到控制平面主机。
- 浏览器访问：通过 VPN/私有网络访问 HTTPS。不要把 8000 或 5432 端口暴露到公网。

建议使用 Ubuntu 24.04 LTS ARM64，Python 3.11+、Node.js 22、PostgreSQL 16、Nginx。以上是部署目标，不是对用户服务器当前系统版本的实测结论。

## 2. 请求与数据路径

```text
Browser / Google AI Studio preview
        |
        | HTTPS over private VPN; exact Origin allowlist
        v
Nginx (private interface :443)
        |
        | loopback only
        v
Uvicorn / FastAPI (127.0.0.1:8000)
        |
        | local Unix socket / peer authentication
        v
PostgreSQL

systemd: API + scheduler + workflow worker
optional: signed webhook dispatcher

Separate execution host:
otternest-runner -> claim / context / heartbeat / submit -> FastAPI
```

PostgreSQL 不向浏览器开放。前端不得携带数据库 URL、Owner Token、Runtime Token 或模型密钥。

## 3. 安装系统依赖

先确认 CPU 架构：

```bash
uname -m
# ARM64 通常显示 aarch64

sudo apt update
sudo apt install -y git nginx postgresql postgresql-contrib \
  python3 python3-venv python3-dev build-essential libpq-dev \
  ca-certificates openssl
```

另行安装受信任软件源提供的 Node.js 22 和 npm。不要在生产主机上使用来源不明的安装脚本。若发行版仓库提供的 Node.js 版本低于 Vite 要求，请使用 Node.js 官方安装文档选择适合 ARM64 的受信任发行包。

## 4. 创建专用系统用户和 PostgreSQL 数据库

创建无登录权限的服务用户：

```bash
sudo useradd --system --create-home --home-dir /opt/otternest \
  --shell /usr/sbin/nologin otternest

sudo install -d -o otternest -g otternest -m 0750 \
  /opt/otternest /var/lib/otternest/artifacts
sudo install -d -o root -g otternest -m 0750 /etc/otternest
```

使用同名 Linux 用户和 PostgreSQL 角色，走本机 Unix socket 的 peer 认证：

```bash
sudo -u postgres createuser --login otternest
sudo -u postgres createdb --owner=otternest otternest
```

如果角色或数据库已经存在，先核对归属再继续，不要重复创建。保持 PostgreSQL 仅接受本机 Unix socket/loopback 连接；云安全组和主机防火墙均不要开放 5432。

## 5. 拉取代码并构建前后端

```bash
sudo -u otternest git clone --branch main --depth 1 \
  https://github.com/opopr/OtterNest.git /opt/otternest/app

sudo -u otternest python3 -m venv /opt/otternest/venv
sudo -u otternest /opt/otternest/venv/bin/pip install --upgrade pip
sudo -u otternest /opt/otternest/venv/bin/pip install -e /opt/otternest/app

cd /opt/otternest/app
sudo -u otternest npm install --no-audit --no-fund
sudo -u otternest npm run build
```

生产构建默认由 FastAPI 同源提供，因此不需要设置 VITE_API_BASE_URL。Google AI Studio 单独预览前端时，再在 AI Studio 的构建环境中设置 VITE_API_BASE_URL 为本服务的 HTTPS Origin。

## 6. 配置服务器秘密

```bash
sudo install -o root -g otternest -m 0640 \
  /opt/otternest/app/deploy/otternest.env.example \
  /etc/otternest/otternest.env
sudoedit /etc/otternest/otternest.env
```

必须替换 Owner Token：

```bash
openssl rand -hex 32
```

将生成值填入 OTTERNEST_OWNER_TOKEN。不要把输出提交到 GitHub、前端环境变量或聊天记录。环境文件必须保持 root:otternest、0640 权限。

数据库 URL 使用：

```text
postgresql+psycopg:///otternest
```

这是本机 peer 认证配置：systemd 进程以 Linux 用户 otternest 运行，PostgreSQL 中也存在同名角色。若你改用密码认证，必须使用正确 URL 编码并把密码只放在服务器秘密文件中。

OTTERNEST_ARTIFACT_DIR 应保持为 /var/lib/otternest/artifacts。若 React 页面与 API 同源，OTTERNEST_CORS_ORIGINS 留空；若 Google AI Studio 预览从另一个 Origin 请求 API，则填写浏览器地址栏中实际显示的精确 Origin，多个值用英文逗号分隔，不带路径，不使用通配符。

Webhook 和 A2A 的变量仅在明确启用对应功能时配置。模型 API Key 不需要放在控制平面环境文件中；模型 Worker 应在独立、受信任的执行环境中通过专用环境变量接收。

## 7. 安装并启动 systemd 服务

```bash
cd /opt/otternest/app
sudo bash deploy/install-systemd.sh

# 迁移必须由运维人员显式执行，不在 API 启动时自动改 Schema。
sudo systemctl start otternest-migrate.service
sudo systemctl status otternest-migrate.service

sudo systemctl enable --now \
  otternest-api.service \
  otternest-scheduler.service \
  otternest-workflows.service
```

主要单元：

| 单元 | 用途 | 默认处理 |
|---|---|---|
| otternest-api | FastAPI 控制平面 | 安装后启用 |
| otternest-migrate | Alembic 数据库迁移 | 仅人工执行 |
| otternest-scheduler | ready 任务安全路由 | 安装后启用 |
| otternest-workflows | 持久事件规则处理 | 安装后启用 |
| otternest-webhooks | 签名 Webhook 投递 | 可选，默认不启用 |

Webhook 只有在配置 OTTERNEST_WEBHOOK_SIGNING_KEY 和 OTTERNEST_WEBHOOK_ALLOWED_HOSTS 后才可启用：

```bash
sudo systemctl enable --now otternest-webhooks.service
```

A2A 出站委托不应因部署而自动启动；它涉及明确授权的外部网络副作用，按现有运维流程单独运行。

## 8. 配置 Nginx 与私有 HTTPS

将模板复制到 Nginx conf.d 目录，并替换私有监听 IP、域名和证书路径：

```bash
sudo install -m 0644 \
  /opt/otternest/app/deploy/nginx/otternest.conf.example \
  /etc/nginx/conf.d/otternest.conf
sudoedit /etc/nginx/conf.d/otternest.conf
sudo nginx -t
sudo systemctl reload nginx
```

模板默认使用 100.64.0.10 作为示例 VPN 地址，必须替换成服务器真实、稳定的私有接口 IP。证书必须由访问设备信任的 CA 签发。可用受控 DNS-01 方式签发证书，再通过私有 DNS/VPN 将域名解析到私有 IP；不要为了签证书而开放管理 API。

如果你的私有网络方案不支持绑定单独的接口地址，可以监听 443，但必须在云安全组和主机防火墙上限制来源为 VPN/管理网段。8000 仅允许 loopback，5432 不允许公网访问。配置 UFW 或等效防火墙时，先保留当前 SSH 管理来源，再仅允许 VPN 网段访问 443，并在云安全组同步执行相同限制；不要在未验证 SSH 规则前启用默认拒绝策略。

## 9. Google AI Studio 预览连接

当 AI Studio 仅作为前端预览时：

1. 在 AI Studio 的构建环境设置 VITE_API_BASE_URL 为本服务 HTTPS Origin，不添加 /api/v1。
2. 确保运行预览的浏览器设备已接入可访问该私有 API 的 VPN。
3. 将预览页面的精确 Origin 添加到服务器 OTTERNEST_CORS_ORIGINS，然后重启 API。
4. 不要把 Owner Token、Runtime Token、PostgreSQL URL 或模型密钥放入 VITE_ 环境变量。
5. 在 OtterNest 系统页面执行 API 存活、数据库就绪和当前身份三项诊断。

如果浏览器不能通过 VPN 访问服务器，就不能把“AI Studio 已导入 GitHub”当成后端连通。不要以开放 PostgreSQL 或无保护公网 API 作为临时解决方案。

## 10. 首次初始化与健康检查

通过私有 HTTPS 地址打开首页，使用服务器环境文件中的 Owner Token 完成首次所有者账号初始化。随后使用用户名和密码登录，立即启用 TOTP 双因素认证，并离线保存一次性恢复码。Owner Token 仍是高权限秘密，不能删除或暴露。

```bash
curl -fsS https://otternest.example.com/health/live
curl -fsS https://otternest.example.com/health/ready
sudo systemctl --no-pager --full status \
  otternest-api otternest-scheduler otternest-workflows
```

查看日志：

```bash
sudo journalctl -u otternest-api -f
sudo journalctl -u otternest-scheduler -f
sudo journalctl -u otternest-workflows -f
```

健康检查只能证明 API 进程和数据库基本可访问，不代表外部 Agent 已连接或任务能够实际执行。

## 11. 执行节点与资源边界

Runner 使用独立 Runtime 凭据，并在单独执行节点运行。控制平面只负责目标、任务、授权、租约、状态与成果记录；它不会自动唤醒 Muse、Codex 或 Google AI 的订阅会话。

1 vCPU / 6 GiB 主机先保持单个 Uvicorn Worker，不在控制平面运行多个重型代码执行任务。实际并发量需根据 PostgreSQL 连接数、CPU、内存和任务等待时间测量后调整。不要仅凭 Agent 心跳就认定远端进程仍在工作。

## 12. 升级、备份与恢复

### 一致性备份

安装脚本会安装本地备份服务和可选 timer。手工备份使用：

```bash
sudo systemctl start otternest-backup.service
sudo journalctl -u otternest-backup.service --no-pager
```

备份服务会先停止 Webhook、工作流、调度器和 API，随后检查数据库中是否仍有 status 为 running 的任务；如果存在运行任务，会拒绝备份并恢复此前正在运行的服务。确认没有运行任务后，才会生成 PostgreSQL custom dump、成果目录归档、SHA-256 清单和版本清单。备份结束后，只重新启动备份前处于 active 状态的服务。

归档保存在 /var/backups/otternest，文件权限为 root-only，默认保留 14 天。备份不包含 /etc/otternest/otternest.env 等秘密配置，也不会自动上传或加密异机副本；请单独安全保管配置秘密，并把备份加密复制到另一台主机或受控对象存储。

otternest-backup.timer 已安装但默认不启用。它的示例时间为每日 02:30 UTC，并带随机延迟；只有确认这个维护窗口可接受短暂控制平面停机后，才启用：

```bash
sudo systemctl enable --now otternest-backup.timer
systemctl list-timers otternest-backup.timer
```

恢复前先在隔离目录校验归档，不要直接覆盖正在使用的数据库或成果目录：

```bash
sudo install -d -m 0700 /var/tmp/otternest-restore
sudo tar -xzf /var/backups/otternest/otternest-backup-YYYYMMDDTHHMMSSZ-PID.tar.gz \
  -C /var/tmp/otternest-restore
sudo bash -c 'cd /var/tmp/otternest-restore && sha256sum -c SHA256SUMS && pg_restore --list database.dump && tar -tzf artifacts.tar.gz'
```

完整恢复应先还原到隔离数据库和暂存成果目录，核对 Alembic revision、成果 SHA-256 和应用版本，再安排停机窗口切换。当前仅提供备份和归档完整性验证，不提供自动覆盖式恢复命令。

记录当前 Git commit，并且只部署已经审核的 main commit：

```bash
cd /opt/otternest/app
sudo -u otternest git fetch --prune origin
sudo -u otternest git pull --ff-only origin main
sudo -u otternest /opt/otternest/venv/bin/pip install -e .
sudo -u otternest npm install --no-audit --no-fund
sudo -u otternest npm run build

sudo systemctl start otternest-migrate.service
sudo systemctl restart otternest-api otternest-scheduler otternest-workflows
```

数据库迁移不是普通代码回滚。不要在未检查 Alembic downgrade 语义的情况下直接降级数据库。回滚代码前确认旧版本兼容当前 Schema；必要时从迁移前备份恢复到隔离环境验证。

升级前先确认最近一次一致性备份已成功，并完成至少一次隔离恢复演练。数据库记录与成果文件必须作为一个恢复单元处理；不要只恢复 PostgreSQL 而遗漏 artifacts。当前备份服务只生成本机 root-only 归档，异机加密复制、定期恢复演练和生产级 RPO/RTO 仍需运维侧落实。

## 13. 上线前验收清单

- [ ] 服务器只允许 VPN/管理网段访问 HTTPS。
- [ ] 8000 只监听 127.0.0.1；5432 不对公网开放。
- [ ] Owner Token 与环境文件权限正确，没有进入 Git 或前端构建产物。
- [ ] 所有 Alembic 迁移成功，/health/ready 返回 ready。
- [ ] 所有者登录、TOTP 和恢复码已验证。
- [ ] Nginx WebSocket、SSE、上传请求通过验证。
- [ ] AI Studio 使用精确 CORS Origin，且没有任何 VITE_ 秘密变量。
- [ ] Runner 位于独立执行节点，Runtime 凭据与 Agent 身份绑定正确。
- [ ] PostgreSQL 和成果目录完成一次配对备份及恢复演练。
- [ ] 真实 PostgreSQL 并发、资源上限和远程执行故障场景已在目标主机验证。

在完成这些检查和生产安全审计前，保持私有试运行，不要宣称系统达到公网生产部署标准。
