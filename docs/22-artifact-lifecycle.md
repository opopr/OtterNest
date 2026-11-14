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

JSON 项目包携带版本族、版本和历史审查说明，但不包含文件字节。恢复时生成新的版本族 ID，并把审查状态重置为待本地所有者复核。Artifact 二进制备份、跨主机恢复、保留期和垃圾回收尚未实现；运行者须自行备份 `OTTERNEST_ARTIFACT_DIR`，并与 PostgreSQL 快照保持一致。
