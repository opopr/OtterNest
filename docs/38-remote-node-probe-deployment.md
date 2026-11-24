# 远程节点：只读心跳探针部署与验收

> 当前只连接「健康采样/Runtime 心跳」，**不执行任务、不部署 AI、不访问 Docker 或 SSH**。此处所说在线是 Runtime 上报在线，不是可信宿主机证明。先在测试环境验收。

## 准备（独立 Linux 服务器）

1. 在 OtterNest 的 Agent 管理中为该节点创建 Agent 和 Runtime，通过 monitor-token API 签发该 Runtime 的专用 on_monitor_ 监控令牌（不要使用普通 Runtime 执行令牌）。每台机器一个 Runtime ID 与监控令牌。管理者令牌不应复制到节点。
2. 节点安装 Python >= 3.11，确保能通过 HTTPS **主动访问**控制平面。不要为了采集开放反向 SSH/Docker/Postgres 端口。
3. 以管理员身份建立专用的无登录用户和目录（以下命令需要在目标服务器上手动执行；不要在控制平面上运行）：

```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin otternest-probe
sudo mkdir -p /opt/otternest-node /etc/otternest-node
sudo chown root:root /opt/otternest-node /etc/otternest-node
# 把本仓库代码放到 /opt/otternest-node/app，然后：
python3 -m venv /opt/otternest-node/venv
sudo /opt/otternest-node/venv/bin/pip install /opt/otternest-node/app
sudo cp /opt/otternest-node/app/deploy/node-probe.env.example /etc/otternest-node/probe.env
sudo chmod 0600 /etc/otternest-node/probe.env
sudoedit /etc/otternest-node/probe.env
```

上面示例假设已经由可信流程放置代码到 /opt/otternest-node/app。根据实际目录调整文件路径；不要直接复制未审核的仓库代码到生产服务器自动运行。

```bash
sudo cp /opt/otternest-node/app/deploy/systemd/otternest-node-probe.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now otternest-node-probe.service
sudo systemctl status otternest-node-probe.service
```

## 验收条件

- Runtime 列表显示最近心跳，约 180 秒无心跳后应自动显示离线。这里没有任务执行/进程控制能力。
- 「系统设置」的远程执行节点卡片在探针运行时显示实际 CPU、内存、磁盘使用率。GPU 未实现应为「未上报」。
- 在控制端禁用 Runtime 或撤销/轮换令牌后，下一次探针请求必须失败，服务状态可通过 `systemctl status` 和 `journalctl -u otternest-node-probe` 核对。
- 断网/服务端重启后，systemd 应通过失败退出及 Restart=on-failure 自动重启探针；当网络恢复，心跳才能更新。
- 若探针跑在容器中，Linux procfs/statvfs 的数字可能代表容器看到的系统/挂载，而不是物理宿主机；**不能当作远程主机硬件事实**。
- 部署前确认凭据文件权限为 root:root 0600、API 为 HTTPS、没有输出密钥和请求体到日志；systemd 的 EnvironmentFile 由服务管理器读取。

## 容量与进阶

首期一个主机运行一个探针实例；同一宿主机多个 Agent Runtime 可能存在重复资源数值，不要把每个卡片的资源相加当整台机器总量。Harness Worker 的远程任务执行，需要单独审批、隔离配置、任务租约和故障对账，**并非安装此探针就自动开启**。
