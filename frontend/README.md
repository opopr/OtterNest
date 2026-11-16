# OtterNest Web

React + TypeScript + Vite 前端。它只调用 OtterNest FastAPI，不直连 PostgreSQL，也不保存数据库凭据。

## 本地开发

先在仓库根目录启动 FastAPI：
- 配置 OTTERNEST_DATABASE_URL 和 OTTERNEST_OWNER_TOKEN
- 执行 alembic upgrade head
- 执行 uvicorn otternest.app:app --host 127.0.0.1 --port 8000

然后在仓库根目录执行：
- npm install
- npm run dev
- 浏览器打开 http://127.0.0.1:5173

Vite 会把 /api 和 /assets 代理到本地 FastAPI。

## 构建与同源部署

在仓库根目录执行 npm run build。构建结果位于 frontend/dist。

FastAPI 启动时会自动检测 frontend/dist：
- / 和 React 页面路由由 React 应用提供
- /ui-assets/* 提供 Vite 构建资源
- /legacy 保留原有完整 HTML 管理台
- /api/* 仍由 FastAPI 提供
- /assets/* 仍提供原有水獭形象与图标

若构建目录不在默认位置，可设置 OTTERNEST_WEB_DIST 指向 dist 目录。

## Google AI Studio

1. 在 Build mode 的输入框旁点击 +，选择 Import from GitHub，导入 opopr/OtterNest。
2. 导入后以仓库根目录的 package.json 启动工作区；React 应用实际位于 frontend/。
3. 导入后打开 Settings > GitHub，使用 Link repository / 连接现有仓库的入口绑定 opopr/OtterNest。首次推送前检查文件差异，确认目标分支；不要覆盖 main 上尚未合并的改动。
4. 日常页面开发只修改 frontend/。不要重写 Python/FastAPI 后端、数据库迁移或服务端权限逻辑。
5. 若预览需要访问远程 OtterNest API，在 AI Studio 环境变量中设置 VITE_API_BASE_URL 为 API 的 HTTPS 源站地址，例如 https://otternest-api.example.com。不要在该变量中放入路径、令牌或数据库连接串。
6. 在后端设置 OTTERNEST_CORS_ORIGINS，填入 AI Studio 实际预览页面的精确 Origin，多个 Origin 用英文逗号分隔。不要使用 *。
7. 远程 API 必须通过 HTTPS 可达。只开放业务 API，不开放 PostgreSQL 端口；不要把所有者令牌、Agent Token 或数据库密码写进前端代码或 VITE_ 环境变量。

AI Studio 支持从现有仓库导入，并可在 Settings > GitHub 中双向同步。同步前先检查差异，避免覆盖本地或 GitHub 上的更新。

## API 契约

FastAPI 的 OpenAPI 文档位于 /openapi.json，交互文档位于 /api/docs。启动本地后端后，可在仓库根目录执行 npm run api:types，生成 frontend/src/api/schema.d.ts。
