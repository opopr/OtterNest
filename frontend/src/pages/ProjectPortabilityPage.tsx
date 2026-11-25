import { useCallback, useEffect, useState, type ChangeEvent, type FormEvent } from "react";
import { AlertCircle, ArchiveRestore, Download, FileJson, RefreshCw, ShieldAlert, ShieldCheck, UploadCloud, X } from "lucide-react";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import type { Project } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";
import "./AdminGaps.css";

interface Bundle {
  schema_version: string;
  sha256_digest: string;
  project: { id: string; name: string };
  secrets_included: boolean;
  [key: string]: unknown;
}
interface ImportPreview {
  schema_version: string;
  sha256_digest: string;
  source_project: { id: string; name: string };
  counts: Record<string, number>;
  restore_boundaries: Record<string, string>;
}
interface ImportResult {
  status: string;
  project_id: string;
  project_name: string;
  created: Record<string, number>;
  disabled_agents: number;
  artifacts_metadata_only: number;
  activation_required: boolean;
  notice: string;
}
const MAX_FILE_SIZE = 10 * 1024 * 1024;
const nameMap: Record<string, string> = {
  agents: "Agent", goals: "目标", tasks: "任务", memberships: "成员关系", memories: "记忆",
  decisions: "决策", artifacts: "成果元数据", checkpoints: "检查点", side_effects: "副作用",
  skills: "Skills", events: "事件", workflow_rules: "工作流规则", decision_reviews: "决策复核",
  task_dependencies: "任务依赖", goal_dependencies: "目标依赖",
};
const failText = (reason: unknown) => reason instanceof Error ? reason.message : "请求失败";

export default function ProjectPortabilityPage() {
  const { user } = useAuth();
  const isOwner = user?.kind === "owner";
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const [bundle, setBundle] = useState<Bundle | null>(null);
  const [preview, setPreview] = useState<ImportPreview | null>(null);
  const [newName, setNewName] = useState("");
  const [confirmed, setConfirmed] = useState(false);
  const [result, setResult] = useState<ImportResult | null>(null);
  const dialog = useDialog();

  const refresh = useCallback(async () => {
    if (!isOwner) { setLoading(false); return; }
    setLoading(true);
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setProjectId((current) => rows.some((row) => row.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(failText(reason)); }
    finally { setLoading(false); }
  }, [isOwner]);

  useEffect(() => { void refresh(); }, [refresh]);

  const exportBundle = async () => {
    if (!projectId) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const data = await request<Bundle>("/projects/" + encodeURIComponent(projectId) + "/export");
      if (data.schema_version !== "otternest.export.v1" || !data.sha256_digest) {
        throw new Error("服务端未返回有效的可携带项目包。");
      }
      const blob = new Blob([JSON.stringify(data, null, 2)], { type: "application/json;charset=utf-8" });
      const filename = (projects.find((project) => project.id === projectId)?.name || "project").replace(/[\\/:*?"<>|]/g, "_");
      const href = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = href;
      link.download = "otternest-" + filename + "-" + new Date().toISOString().slice(0, 10) + ".json";
      document.body.appendChild(link);
      link.click();
      link.remove();
      window.setTimeout(() => URL.revokeObjectURL(href), 1000);
      setSuccess("项目 JSON 包已导出。它仅包含成果元数据，不包含二进制成果文件与账号凭据。");
    } catch (reason) { setError(failText(reason)); }
    finally { setBusy(false); }
  };

  const readFile = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0];
    event.target.value = "";
    setBundle(null); setPreview(null); setResult(null); setNewName(""); setConfirmed(false); setSuccess(""); setError("");
    if (!file) return;
    if (file.size > MAX_FILE_SIZE) { setError("项目包超过前端 10 MiB 限制。"); return; }
    try {
      const value: unknown = JSON.parse(await file.text());
      if (typeof value !== "object" || value === null || Array.isArray(value)) throw new Error("项目包必须是 JSON 对象。");
      const candidate = value as Bundle;
      if (candidate.schema_version !== "otternest.export.v1" || !candidate.sha256_digest) {
        throw new Error("仅支持 otternest.export.v1 项目包。");
      }
      setBundle(candidate);
    } catch (reason) { setError(failText(reason)); }
  };

  const validate = async () => {
    if (!bundle) return;
    setBusy(true); setError(""); setSuccess(""); setPreview(null); setConfirmed(false);
    try {
      const valid = await request<ImportPreview>("/projects/import/preview", {
        method: "POST", body: JSON.stringify({ bundle })
      });
      if (valid.sha256_digest !== bundle.sha256_digest) throw new Error("项目包摘要与预检结果不一致。");
      setPreview(valid);
      setNewName(valid.source_project.name + " (导入)");
    } catch (reason) { setError(failText(reason)); }
    finally { setBusy(false); }
  };

  const restore = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!bundle || !preview || !confirmed || !newName.trim()) return;
    const ok = await dialog.confirm({
      title: "确认恢复项目包",
      message: "确认将经预检的项目包恢复成一个新项目？不会覆盖现有项目；恢复后默认处于暂停状态。",
      confirmText: "确认创建新项目",
    });
    if (!ok) return;
    setBusy(true); setError(""); setSuccess("");
    try {
      const imported = await request<ImportResult>("/projects/import", {
        method: "POST",
        body: JSON.stringify({ bundle, new_name: newName.trim(), confirm_restore: true })
      });
      setResult(imported);
      setBundle(null); setPreview(null); setConfirmed(false);
      setSuccess("项目已恢复到暂停状态。请先审核内容、恢复必要连接，再手动激活。");
      await refresh();
    } catch (reason) { setError(failText(reason)); }
    finally { setBusy(false); }
  };

  if (!isOwner) return <div className="page-stack"><div className="page-heading"><h1>项目导入导出</h1></div><div className="panel empty-state"><ShieldCheck size={26} /><strong>仅所有者可以导入或导出项目包。</strong></div></div>;

  return <div className="page-stack admin-gap-page">
    <div className="page-heading"><div><h1>项目导入导出</h1>
      <p>备份项目的可携带 JSON 记录，预检后恢复成全新、默认暂停的项目。</p></div>
      <button className="secondary-button compact" onClick={() => void refresh()} disabled={loading || busy}><RefreshCw size={14} />刷新</button></div>
    {error && <div className="inline-error"><AlertCircle size={15} />{error}<button onClick={() => setError("")}><X size={14} /></button></div>}
    {success && <div className="success-notice"><ShieldCheck size={15} />{success}<button onClick={() => setSuccess("")}>关闭</button></div>}

    <div className="admin-two-column">
      <section className="panel admin-portable-card">
        <div className="panel-heading"><div className="panel-title"><Download size={17} /><h3>导出项目包</h3></div></div>
        <p className="form-hint">导出项目任务、目标、记忆、决策、检查点等元数据。真实成果文件与第三方访问凭据不会包含在 JSON 包中。</p>
        <ProjectSelect projects={projects} value={projectId} onChange={setProjectId} disabled={loading || busy} />
        <button className="primary-button compact" disabled={!projectId || busy} onClick={() => void exportBundle()}><Download size={15} />{busy ? "处理中…" : "导出 JSON"}</button>
        <p className="form-hint">要保留成果文件本身，请另外使用<Link to="/artifacts" className="admin-inline-link">成果中心的二进制归档</Link>。此功能不能替代 PostgreSQL 数据库备份。</p>
      </section>

      <section className="panel admin-portable-card">
        <div className="panel-heading"><div className="panel-title"><UploadCloud size={17} /><h3>导入项目包</h3></div></div>
        <p className="form-hint">先选择文件，再提交后端校验版本、SHA-256 摘要、引用一致性和大小。仅通过预检后才能恢复。</p>
        <label className="admin-file-field"><FileJson size={19} />选择 JSON 项目包（最大 10 MiB）
          <input type="file" accept=".json,application/json" onChange={(event) => void readFile(event)} disabled={busy} /></label>
        {bundle && <div className="admin-bundle-brief"><strong>{bundle.project?.name || "待校验项目"}</strong>
          <code>{bundle.sha256_digest}</code>
          <span>请点击预检，验证来自该文件的完整内容。</span></div>}
        <button className="secondary-button compact" onClick={() => void validate()} disabled={!bundle || busy}><ShieldCheck size={14} />{busy ? "处理中…" : "预检项目包"}</button>
      </section>
    </div>

    {preview && <section className="panel">
      <div className="panel-heading"><div className="panel-title"><ArchiveRestore size={17} /><h3>项目恢复预检</h3></div><span className="status-tag tag-green">后端校验通过</span></div>
      <p className="form-hint">原项目：{preview.source_project.name} · 来源 ID：{preview.source_project.id}</p>
      <div className="admin-digest">SHA-256：<code>{preview.sha256_digest}</code></div>
      <div className="admin-count-grid">{Object.entries(preview.counts).filter(([, count]) => count > 0).map(([key, count]) =>
        <div key={key}><span>{nameMap[key] || key}</span><strong>{count}</strong></div>)}</div>
      <div className="admin-import-warnings"><ShieldAlert size={18} />
        <div><strong>恢复是保守操作，不会自动续跑或恢复连接</strong>
          <ul><li>新项目默认暂停；未完成任务通常保持阻塞或等待重新审批。</li>
            <li>智能体 身份恢复为禁用状态，密钥与外部连接不会恢复。</li>
            <li>成果仅恢复元数据，需要重新校验并恢复二进制文件。</li></ul>
        </div></div>
      <form className="form-panel admin-stack-form" onSubmit={(event) => void restore(event)}>
        <label>新项目名称<input required maxLength={160} value={newName} onChange={(event) => setNewName(event.target.value)} /></label>
        <label className="admin-confirm"><input type="checkbox" checked={confirmed} onChange={(event) => setConfirmed(event.target.checked)} />
          <span>我已查看预检摘要，理解新项目将被暂停，所有外部能力必须重新授权和验证。</span></label>
        <div className="form-actions"><button className="primary-button compact" disabled={!confirmed || !newName.trim() || busy}>
          <ArchiveRestore size={15} />{busy ? "恢复中…" : "确认创建新项目"}</button></div>
      </form>
    </section>}

    {result && <section className="panel">
      <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17} /><h3>导入结果</h3></div><span className="status-tag tag-amber">项目已暂停</span></div>
      <p>已创建：<strong>{result.project_name}</strong></p>
      <p className="form-hint">禁用 Agent：{result.disabled_agents} · 待重建的成果文件元数据：{result.artifacts_metadata_only}</p>
      <Link className="secondary-button compact" to={"/projects?project=" + encodeURIComponent(result.project_id)}>查看新项目</Link>
    </section>}
  </div>;
}
