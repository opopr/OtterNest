# OtterNest 测试服务器部署指南（Linux / Muse 测试机）

> 注意：以下脚本适用于**受控测试服务器**（内网 / SSH 隧道 / 反向代理 TLS 后方），暂不建议无 TLS 与未经安全审计直接裸露公网。

## 1. 环境准备
- Python **3.11+**（建议 3.12）
- PostgreSQL **16+**，已创建独立数据库与用户，例如：
  ```sql
  CREATE USER otternest WITH PASSWORD '请替换为强密码';
  CREATE DATABASE otternest OWNER otternest;
  ```
- 若运行本地隔离 Sandbox（可选）：Linux 下需安装 `bubblewrap` 并按需委托 `cgroup v2`。

## 2. 生成配置并初始化
```bash
git clone <你的仓库地址> /opt/OtterNest
cd /opt/OtterNest
cp deploy/env.test.template .env
# 生成 >= 32 位管理员 Token 并写入 .env:
python3 -c "import secrets; print(secrets.token_urlsafe(48))"
```

编辑 `.env` 后运行一键初始化脚本（自动建 `.venv`、安装依赖、执行 `alembic upgrade head`、运行编译与 `pytest -q`）：
```bash
chmod +x deploy/setup_test_server.sh deploy/start_control_plane.sh
./deploy/setup_test_server.sh
```

若使用 PostgreSQL，建议额外跑一次并发锁验证测试：
```bash
OTTERNEST_PG_TEST_URL="$OTTERNEST_DATABASE_URL" .venv/bin/pytest -q tests/test_postgres_concurrency.py
```

## 3. 启动控制平面与健康检查
### 方式 A：前台快速验证
```bash
./deploy/start_control_plane.sh
```
验证存活与数据库就绪：
- `GET http://127.0.0.1:8000/health/live` -> `{"status":"alive"}`
- `GET http://127.0.0.1:8000/health/ready` -> `{"status":"ready"}`
- 管理台：`http://127.0.0.1:8000/`（或通过 SSH 端口转发 `ssh -L 8000:127.0.0.1:8000 user@server` / 内部 Nginx HTTPS 反代访问）

### 方式 B：systemd 常驻守护（测试环境长期运行）
```bash
sudo cp deploy/otternest-api.service deploy/otternest-scheduler.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now otternest-api otternest-scheduler
sudo systemctl status otternest-api otternest-scheduler
```
