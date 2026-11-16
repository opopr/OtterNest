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

- VITE_API_BASE_URL：FastAPI 的 HTTPS Origin，只填写协议、主机和可选端口；不要附加 `/api`、`/api/v1` 或具体接口路径，前端客户端会自行拼接业务路径。
- 该变量会暴露在浏览器中，只能放公开地址等非秘密配置。
- 不要创建 VITE_OWNER_TOKEN、VITE_DATABASE_URL 或任何包含秘密的 VITE_ 变量。

## 同步

导入仓库后，在 Settings > GitHub 中将工作区连接到现有的 opopr/OtterNest 仓库，确认目标分支后再启用双向同步。导入代码与建立 GitHub 同步绑定是两个步骤；首次推送前必须检查文件差异。

GitHub 仓库 opopr/OtterNest 的分支与 Pull Request 是代码审查依据。AI Studio 的修改应先检查文件差异，再同步到 GitHub；不要覆盖未审查的分支或直接把实验性代码推到 main。


## 连接真实后端

配置好 `VITE_API_BASE_URL` 与服务端 CORS 后，打开 React 的「系统设置与运行事件」页面，使用「API 与数据库连接诊断」执行检查：

- API 服务：`/health/live`；
- 数据库就绪：`/health/ready`；
- 当前登录身份：`/api/v1/me`。

只有三项均成功，才说明当前浏览器预览已经连到预期的 FastAPI 服务、数据库和所有者会话。若前两项成功但身份失败，重新登录并检查 Authorization 预检请求是否被 CORS 放行。健康检查不暴露数据库连接串或任何密钥。
