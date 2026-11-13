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
- 目前没有实际第三方模型调用或云端自动唤醒能力，Agent 注册只是身份记录。
- 当前 artifacts 上传上限 5 MiB，超大文件需要后续对象存储/分段上传设计。
