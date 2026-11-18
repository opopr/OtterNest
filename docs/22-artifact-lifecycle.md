# 22 · 成果版本与验收

`Artifact` 是已上传文件的不可变记录。每个任务内，以精确文件名区分版本族；再次上传同名文件会创建下一个版本和独立文件路径，不会覆盖旧版本。

## API

- `POST /api/v1/tasks/{task_id}/artifacts`：在有效任务租约下上传，单文件最大 5 MiB。相同任务和文件名递增 `version`，返回新的 `id`、`family_id` 和 `review_status: pending`。
- `GET /api/v1/tasks/{task_id}/artifacts`：项目成员查看全部版本、校验和与所有者审查状态。
- `GET /api/v1/artifacts/{artifact_id}/content`：所有者或项目成员下载文件。服务端检查路径位于 `OTTERNEST_ARTIFACT_DIR/{project_id}` 内，确认文件大小和 SHA-256 与记录一致后才返回字节。
- `POST /api/v1/artifacts/{artifact_id}/review`（仅所有者）：`{"accepted": true, "note": "..."}` 验收，或 `{"accepted": false, "note": "至少五字的驳回说明"}` 驳回。最终审查不可覆盖；修改内容须上传新版本。

审查事件通过项目事件/审计流记录。Artifact 仍按任务授权读取，不因下载链接而公开文件路径。被删除、移出配置目录、超过大小上限或校验和不匹配的文件不会被返回。

## 迁移与既有数据

数据库迁移 `0014_artifact_versions_review` 将已有每条 Artifact 记录作为独立版本族的 `v1`，初始审查状态为 `pending`。新版本受到 `(family_id, version)` 唯一约束保护。

JSON 项目包携带版本族、版本和历史审查说明，但不包含文件字节。恢复时生成新的版本族 ID，并把审查状态重置为待本地所有者复核。

## 二进制归档与回收

- `GET /api/v1/projects/{project_id}/artifacts/archive` 由所有者导出本机目录内、SHA-256 校验通过的成果文件及 `otternest.artifacts.v1` 清单。
- `POST /api/v1/projects/{project_id}/artifacts/restore` 接收 `.tar.gz`，按 SHA-256 匹配目标项目的成果记录，因此也适用于 JSON 导入后 ID 已变化的项目副本。服务端先验证全部成员路径、清单、大小和摘要，再写文件；不调用 `extractall`。
- 归档传输上限 50 MiB、最多 1000 个文件、单文件 5 MiB、文件载荷合计 44 MiB、清单 2 MiB。超限归档会被拒绝，不会部分恢复。
- `POST /api/v1/projects/{project_id}/artifacts/gc` 支持预览和执行清理；清理符合保留规则的本地文件及当前配置的 S3 桶内该项目的成果对象，保留 Artifact 元数据与审计记录。执行结果分别报告已删除、原本缺失和删除失败的记录；失败记录保留以便重试。只读保护期间仅允许预览。S3 删除要求有效凭据和成功响应，失败时保留记录。
- 二进制归档不等同于 PostgreSQL 备份，也不提供自动定时加密、远端复制或完整的跨系统事务恢复。运行者仍须协调备份 `OTTERNEST_ARTIFACT_DIR` 与数据库快照。
