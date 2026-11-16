# OtterNest · Google AI Studio 开发约束

本仓库的前端工程位于 frontend/，仓库根目录 package.json 提供 workspace scripts。

## 只改前端

- 只在 frontend/ 下新增或修改 React、TypeScript、CSS 和前端测试。
- 不要改动 src/otternest Python 服务、数据库迁移、服务端鉴权与权限规则，除非用户明确要求后端 API 变更。
- 不要把前端改造成 Node.js 数据库后端。FastAPI 是唯一业务后端。
- 不要连接 PostgreSQL，不要把数据库连接串放入浏览器。

## API 规则

- OpenAPI：/openapi.json
- 业务 API：/api/v1
- 认证：Authorization: Bearer <token>
- 当前前端将会话令牌保存在内存中；不要改为 localStorage 或把令牌写入 URL。
- 不要编造成功响应、项目、任务或 Agent。所有数据必须来自真实 API。
- WebSocket 事件是状态变化提示，必要时通过 REST API 重新读取权威状态。

## 环境变量

- VITE_API_BASE_URL：FastAPI 的 HTTPS Origin，不含 /api/v1。
- 该变量会暴露在浏览器中，只能放公开地址等非秘密配置。
- 不要创建 VITE_OWNER_TOKEN、VITE_DATABASE_URL 或任何包含秘密的 VITE_ 变量。

## 同步

GitHub 仓库 opopr/OtterNest 的分支与 Pull Request 是代码审查依据。AI Studio 的修改应先检查文件差异，再同步到 GitHub；不要覆盖未审查的分支或直接把实验性代码推到 main。
