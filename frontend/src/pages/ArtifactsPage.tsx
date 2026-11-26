import LocalizedFilePicker from "../components/LocalizedFilePicker";
import EmptyState from "../components/EmptyState";
import { useDisplayPreference, ViewModeSwitch } from "../components/ViewModeSwitch";
import DataTable from "../components/DataTable";
import { useProjectSelection } from "../components/ProjectSelection";
import { ErrorNotice } from "../components/PageFeedback";
import { useDraftGuard, UnsavedDraftNotice } from "../components/DraftGuard";
import { useScopedRequest } from "../utils/useScopedRequest";
import ListPagination, { useListPage } from "../components/ListPagination";
import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Archive, Check, Download, FileArchive, FileCheck2, FileUp, RefreshCw, RotateCcw, Trash2, X } from "lucide-react";
import { binaryRequest, request } from "../api/client";
import type { Project, Task } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";

interface Artifact {
  id: string;
  family_id: string;
  version: number;
  name: string;
  checksum: string;
  size_bytes: number;
  sensitivity: string;
  review_status: string;
  review_note: string;
  reviewed_at: string | null;
  deleted_at: string | null;
  created_at: string;
}
interface GCResult {
  project_id: string;
  dry_run: boolean;
  candidate_count: number;
  candidate_ids: string[];
  reclaimed_bytes: number;
}
const sizeText = (bytes: number) => bytes < 1024 ? bytes + " B" : bytes < 1024 * 1024 ? (bytes / 1024).toFixed(1) + " KB" : (bytes / 1024 / 1024).toFixed(2) + " MB";
const reviewLabel: Record<string, string> = { pending: "待验收", accepted: "已验收", rejected: "已驳回" };

function saveBlob(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  document.body.appendChild(anchor);
  anchor.click();
  anchor.remove();
  URL.revokeObjectURL(url);
}

export default function ArtifactsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useProjectSelection(projects);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [artifacts, setArtifacts] = useState<Artifact[]>([]);
  const [viewMode,setViewMode]=useDisplayPreference("otternest-artifact-view","list");
  const [fileQuery, setFileQuery] = useState("");
  const [reviewFilter, setReviewFilter] = useState("all");
  const visibleArtifacts = useMemo(() => artifacts.filter(a => (reviewFilter === "all" || a.review_status === reviewFilter) && a.name.toLocaleLowerCase().includes(fileQuery.trim().toLocaleLowerCase())), [artifacts, fileQuery, reviewFilter]);
  const artifactPage = useListPage(visibleArtifacts, JSON.stringify([selectedProjectId, selectedTaskId, fileQuery, reviewFilter]));
  const [uploadFile, setUploadFile] = useState<File | null>(null);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [sensitivity, setSensitivity] = useState("internal");
  const [keepVersions, setKeepVersions] = useState("2");
  const [includeRejected, setIncludeRejected] = useState(true);
  const [gcResult, setGcResult] = useState<GCResult | null>(null);
  const [gcPreviewKey, setGcPreviewKey] = useState("");
  const gcKey = JSON.stringify([selectedProjectId,keepVersions,includeRejected]);
  const validKeepVersions = Number.isInteger(Number(keepVersions)) && Number(keepVersions) >= 1 && Number(keepVersions) <= 50;
  const canConfirmGC = validKeepVersions && gcResult?.dry_run === true && gcResult.project_id === selectedProjectId && gcResult.candidate_count > 0 && gcPreviewKey === gcKey;
  const [restoreResult, setRestoreResult] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [uploadProgress, setUploadProgress] = useState<number | null>(null);
  const [error, setError] = useState("");
  const dialog = useDialog();

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedProjectId) || null, [projects, selectedProjectId]);
  const selectedTask = useMemo(() => tasks.find((item) => item.id === selectedTaskId) || null, [tasks, selectedTaskId]);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const raw = await request<Project[]>("/projects");
      const rows = Array.isArray(raw) ? raw : [];
      setProjects(rows);
      setSelectedProjectId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, []);

  const beginProjectRequest = useScopedRequest(selectedProjectId);
  const beginArtifactRequest = useScopedRequest(selectedTaskId);
  const hasUnsavedDraft = Boolean(uploadFile || restoreFile);
  const confirmDiscard = useDraftGuard(hasUnsavedDraft);
  const changeProject = async (id: string) => { if (saving || id === selectedProjectId) return; if (!(await confirmDiscard())) return; setTasks([]); setArtifacts([]); setSelectedTaskId(""); setUploadFile(null); setRestoreFile(null); setGcResult(null); setGcPreviewKey(""); setRestoreResult(null); setFileQuery(""); setReviewFilter("all"); setLoading(true); setSelectedProjectId(id); };
  const changeTask = async (id: string) => { if (saving || id === selectedTaskId) return; if (uploadFile && !(await confirmDiscard())) return; setArtifacts([]); setUploadFile(null); setFileQuery(""); setReviewFilter("all"); setSelectedTaskId(id); };
  const loadProjectTasks = useCallback(async (projectId: string) => {
    const isCurrent = beginProjectRequest(projectId); if (!isCurrent()) return;
    setLoading(true);
    try {
      const raw = await request<Task[]>("/projects/" + encodeURIComponent(projectId) + "/tasks");
      const rows = Array.isArray(raw) ? raw : [];
      if (!isCurrent()) return;
      setTasks(rows);
      setSelectedTaskId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "项目任务读取失败"); }
    finally { if (isCurrent()) setLoading(false); }
  }, [beginProjectRequest]);

  const loadArtifacts = useCallback(async (taskId: string) => {
    const isCurrent = beginArtifactRequest(taskId); if (!isCurrent()) return;
    if (!taskId) { setArtifacts([]); return; }
    setLoading(true);
    try {
      const raw = await request<Artifact[]>("/tasks/" + encodeURIComponent(taskId) + "/artifacts");
      if (isCurrent()) setArtifacts(Array.isArray(raw) ? raw : []);
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "成果列表读取失败"); }
    finally { if (isCurrent()) setLoading(false); }
  }, [beginArtifactRequest]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedProjectId) void loadProjectTasks(selectedProjectId); else { setTasks([]); setArtifacts([]); } }, [selectedProjectId, loadProjectTasks]);
  useEffect(() => { void loadArtifacts(selectedTaskId); }, [selectedTaskId, loadArtifacts]);

  const uploadArtifact = async () => {
    if (!selectedTaskId || !uploadFile) return;
    setSaving(true);
    setUploadProgress(0);
    setError("");
    try {
      const MULTIPART_THRESHOLD = 4 * 1024 * 1024;
      const CHUNK_SIZE = 2 * 1024 * 1024;
      if (uploadFile.size > MULTIPART_THRESHOLD) {
        const init = await request<{ upload_id: string }>("/tasks/" + encodeURIComponent(selectedTaskId) + "/artifacts/multipart/init", {
          method: "POST",
          body: JSON.stringify({ filename: uploadFile.name, sensitivity })
        });
        const parts: Array<{ part_number: number; sha256: string; size: number }> = [];
        const totalParts = Math.max(1, Math.ceil(uploadFile.size / CHUNK_SIZE));
        let partNumber = 1;
        for (let offset = 0; offset < uploadFile.size; offset += CHUNK_SIZE) {
          const chunk = uploadFile.slice(offset, offset + CHUNK_SIZE);
          const partRes = await binaryRequest("/tasks/" + encodeURIComponent(selectedTaskId) + "/artifacts/multipart/" + encodeURIComponent(init.upload_id) + "/parts/" + partNumber, {
            method: "PUT",
            body: chunk
          });
          parts.push(await partRes.json() as { part_number: number; sha256: string; size: number });
          setUploadProgress(Math.min(99, Math.round((partNumber / totalParts) * 100)));
          partNumber += 1;
        }
        await request("/tasks/" + encodeURIComponent(selectedTaskId) + "/artifacts/multipart/" + encodeURIComponent(init.upload_id) + "/complete?filename=" + encodeURIComponent(uploadFile.name) + "&sensitivity=" + encodeURIComponent(sensitivity), {
          method: "POST",
          body: JSON.stringify({ parts })
        });
      } else {
        await binaryRequest("/tasks/" + encodeURIComponent(selectedTaskId) + "/artifacts", {
          method: "POST",
          headers: {
            "X-Artifact-Name-Encoded": encodeURIComponent(uploadFile.name),
            "X-Artifact-Sensitivity": sensitivity
          },
          body: uploadFile
        });
      }
      setUploadProgress(100);
      setUploadFile(null);
      await loadArtifacts(selectedTaskId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果上传失败"); }
    finally {
      setSaving(false);
      setUploadProgress(null);
    }
  };

  const downloadArtifact = async (artifact: Artifact) => {
    setSaving(true);
    try {
      const response = await binaryRequest("/artifacts/" + encodeURIComponent(artifact.id) + "/content");
      saveBlob(await response.blob(), artifact.name);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果下载失败"); }
    finally { setSaving(false); }
  };

  const reviewArtifact = async (artifact: Artifact, accepted: boolean) => {
    const input = await dialog.prompt({
      title: accepted ? "验收通过成果" : "驳回成果版本",
      message: accepted
        ? `确认验收成果「${artifact.name} v${artifact.version}」？验收后不能再修改此版本。`
        : `驳回「${artifact.name} v${artifact.version}」后，如需修改必须重新上传新版本。`,
      label: accepted ? "验收说明（可选）" : "驳回原因（至少 5 个字符）",
      placeholder: accepted ? "可选填写验收结论或检查备注" : "请说明具体需要修正的问题…",
      multiline: true,
      required: !accepted,
      minLength: accepted ? 0 : 5,
      confirmText: accepted ? "确认验收通过" : "确认驳回",
    });
    if (input === null) return;
    setSaving(true);
    try {
      await request("/artifacts/" + encodeURIComponent(artifact.id) + "/review", {
        method: "POST", body: JSON.stringify({ accepted, note: input })
      });
      await loadArtifacts(selectedTaskId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果验收失败"); }
    finally { setSaving(false); }
  };

  const exportArchive = async () => {
    if (!selectedProjectId) return;
    setSaving(true);
    try {
      const response = await binaryRequest("/projects/" + encodeURIComponent(selectedProjectId) + "/artifacts/archive");
      saveBlob(await response.blob(), "otternest-artifacts-" + selectedProjectId.slice(0, 8) + ".tar.gz");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果归档导出失败"); }
    finally { setSaving(false); }
  };

  const restoreArchive = async () => {
    if (!selectedProjectId || !restoreFile) return;
    const confirmed = await dialog.confirm({
      title: "恢复成果归档",
      message: "确认将归档包恢复到当前项目？系统会先完整校验清单、路径、大小和 SHA-256。",
      confirmText: "确认校验并恢复",
      tone: "warning",
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      const response = await binaryRequest("/projects/" + encodeURIComponent(selectedProjectId) + "/artifacts/restore", {
        method: "POST", headers: { "Content-Type": "application/gzip" }, body: restoreFile
      });
      setRestoreResult(await response.json() as Record<string, unknown>);
      setRestoreFile(null);
      if (selectedTaskId) await loadArtifacts(selectedTaskId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果归档恢复失败"); }
    finally { setSaving(false); }
  };

  const runGC = async (dryRun: boolean) => {
    if (!selectedProjectId || !validKeepVersions || (!dryRun && !canConfirmGC)) return;
    if (!dryRun) {
      const confirmed = await dialog.confirm({
        title: "确认执行文件回收",
        message: "这将物理删除符合条件的未验收/旧版本成果文件，并保留数据库审计元数据。是否继续？",
        confirmText: "确认回收",
        tone: "danger",
      });
      if (!confirmed) return;
    }
    setSaving(true);
    setError("");
    setGcPreviewKey("");
    try {
      const result = await request<GCResult>("/projects/" + encodeURIComponent(selectedProjectId) + "/artifacts/gc", {
        method: "POST",
        body: JSON.stringify({
          dry_run: dryRun, keep_versions: Number(keepVersions), include_rejected: includeRejected
        })
      });
      setGcResult(result);
      if (dryRun) setGcPreviewKey(gcKey);
      if (!dryRun && selectedTaskId) await loadArtifacts(selectedTaskId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "成果回收失败"); }
    finally { setSaving(false); }
  };

  const renderArtifactActions=(artifact:Artifact)=>(<div className="artifact-card-actions"><span className={"status-tag " + (artifact.review_status === "accepted" ? "tag-green" : artifact.review_status === "rejected" ? "tag-red" : "tag-amber")}>{reviewLabel[artifact.review_status] || artifact.review_status}</span><button className="icon-button" title="下载并校验" disabled={saving || Boolean(artifact.deleted_at)} onClick={() => void downloadArtifact(artifact)}><Download size={15} /></button>{artifact.review_status === "pending" && <><button className="icon-button" title="验收通过" disabled={saving} onClick={() => void reviewArtifact(artifact, true)}><Check size={15} /></button><button className="icon-button" title="驳回" disabled={saving} onClick={() => void reviewArtifact(artifact, false)}><X size={15} /></button></>}</div>);
  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>成果中心</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedTaskId && void loadArtifacts(selectedTaskId)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      <ErrorNotice message={error} onDismiss={() => setError("")} />
      <UnsavedDraftNotice dirty={hasUnsavedDraft} />
      <section className="panel project-toolbar"><div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedProjectId} onChange={id => void changeProject(id)} disabled={saving} /><label className="task-select"><span>选择任务</span><select aria-label="选择任务" value={selectedTaskId} disabled={saving || loading} onChange={(event) => void changeTask(event.target.value)}><option value="">选择任务…</option>{tasks.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}</select></label></section>
      <section className="panel">
        <div className="panel-heading"><div className="panel-title"><FileCheck2 size={17} /><h3>成果版本</h3><span className="count-chip">{artifacts.length}</span></div></div>
        <p className="form-hint">任务：{selectedTask?.title || "未选择任务"}</p>
        <details className="library-disclosure artifact-upload-disclosure"><summary>上传新版本</summary><div className="artifact-upload">
          <label>上传文件<LocalizedFilePicker key={selectedTaskId} label="上传文件" fileName={uploadFile?.name} disabled={saving} onChange={(event) => {setUploadFile(event.target.files?.[0] || null);event.target.value="";}} /></label>
          <label>敏感级<select value={sensitivity} onChange={(event) => setSensitivity(event.target.value)}><option value="public">公开</option><option value="internal">内部</option><option value="confidential">机密</option><option value="restricted">受限</option></select></label>
          <button className="primary-button compact" disabled={saving || !selectedTaskId || !uploadFile} onClick={() => void uploadArtifact()}>
            <FileUp size={14} />{uploadProgress !== null ? `上传中 ${uploadProgress}%` : "上传为新版本"}
          </button>
        </div>
        <p className="form-hint">常规单次上传上限 5 MiB；超过 4 MiB 自动启用分块上传。相同任务和文件名会创建新版本，不覆盖旧文件。</p></details>
        <div className="library-filter-bar"><label>文件名<input aria-label="搜索成果文件" value={fileQuery} onChange={event => setFileQuery(event.target.value)} placeholder="搜索文件名" /></label><label>验收状态<select aria-label="成果验收状态" value={reviewFilter} onChange={event => setReviewFilter(event.target.value)}><option value="all">全部状态</option><option value="pending">待验收</option><option value="accepted">已通过</option><option value="rejected">已驳回</option></select></label><button type="button" className="text-button" onClick={() => { setFileQuery(""); setReviewFilter("all"); }}>清除筛选</button><ViewModeSwitch value={viewMode} onChange={setViewMode} label="成果显示方式" alternate="table"/></div>{viewMode==="table" && visibleArtifacts.length ? <DataTable rows={artifactPage.rows} rowKey={artifact=>artifact.id} label="成果表格" preferenceKey="otternest-artifact-columns" className="artifact-data-table" columns={[
 {id:"file",label:"文件",required:true,render:a=><><strong className="data-table-title">{a.name}</strong>{a.deleted_at&&<small>文件已回收</small>}{a.review_note&&<small>验收说明：{a.review_note}</small>}</>},
 {id:"version",label:"版本",render:a=>a.version,numeric:true},{id:"size",label:"大小",render:a=>sizeText(a.size_bytes)}, {id:"checksum",label:"校验值",render:a=><code>{a.checksum.slice(0,16)}…</code>}, {id:"sensitivity",label:"敏感级",required:true,render:a=>getUiLabel(a.sensitivity)}, {id:"created",label:"创建时间",render:a=>new Date(a.created_at).toLocaleString("zh-CN",{hour12:false})}, {id:"actions",label:"验收与操作",required:true,render:renderArtifactActions}
 ]}/> : <div className="artifact-list">{artifactPage.rows.map((artifact) => <article className="artifact-card" key={artifact.id}>
          <div className="artifact-card-main"><div className="artifact-file-icon"><Archive size={18} /></div><div><strong>{artifact.name}</strong><small>v{artifact.version} · {sizeText(artifact.size_bytes)} · {artifact.checksum.slice(0, 16)}…</small><small>敏感级 {getUiLabel(artifact.sensitivity)} · {new Date(artifact.created_at).toLocaleString("zh-CN", { hour12: false })}</small></div></div>
          {renderArtifactActions(artifact)}
          {artifact.review_note && <p className="artifact-review-note">验收说明：{artifact.review_note}</p>}
          {artifact.deleted_at && <small className="muted-inline">文件已回收，元数据仍保留。</small>}
        </article>)}{!visibleArtifacts.length && <EmptyState icon={FileArchive} title={artifacts.length ? "没有符合条件的文件" : "暂无成果文件"} action={artifacts.length ? {label:"重置搜索",onClick:()=>{setFileQuery("");setReviewFilter("all");}}:undefined}/>}</div>}
        {!loading && <ListPagination {...artifactPage} label="成果分页" />}
      </section>

      <details className="panel maintenance-disclosure"><summary>归档与回收</summary><div className="operations-grid">
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><FileArchive size={17} /><h3>成果归档</h3></div></div>
          <p className="form-hint">导出当前项目内可读取且 SHA-256 校验通过的文件。恢复前会先完整验证归档包，再写入目标文件。</p>
          <div className="artifact-archive-actions"><button className="secondary-button compact" disabled={saving || !selectedProjectId} onClick={() => void exportArchive()}><Download size={14} />导出 .tar.gz</button></div>
          <label className="field-label">选择归档包<LocalizedFilePicker key={selectedProjectId} label="选择归档包" fileName={restoreFile?.name} disabled={saving} accept=".gz,.tgz,application/gzip" onChange={(event) => {setRestoreFile(event.target.files?.[0] || null);event.target.value="";}} /></label>
          <div className="form-actions"><button className="primary-button compact" disabled={saving || !restoreFile || !selectedProjectId} onClick={() => void restoreArchive()}><RotateCcw size={14} />校验并恢复</button></div>
          {restoreResult && <pre className="json-preview">{JSON.stringify(restoreResult, null, 2)}</pre>}
        </section>
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><Trash2 size={17} /><h3>文件回收</h3></div></div>
          <p className="form-hint">先预览回收范围。已验收成果不会被回收，驳回版本可按选项纳入。</p>
          <div className="form-grid"><label>每个版本族保留版本数<input type="number" min="1" max="50" aria-label="保留版本数" disabled={saving} value={keepVersions} onChange={(event) => {setKeepVersions(event.target.value);setGcResult(null);setGcPreviewKey("");}} /></label><label className="checkbox-field"><input type="checkbox" disabled={saving} checked={includeRejected} onChange={(event) => {setIncludeRejected(event.target.checked);setGcResult(null);setGcPreviewKey("");}} />包含已驳回版本</label></div>
          <div className="artifact-archive-actions"><button className="secondary-button compact" disabled={saving || !selectedProjectId || !validKeepVersions} onClick={() => void runGC(true)}>预览回收范围</button><button className="primary-button compact" disabled={saving || !canConfirmGC} onClick={() => void runGC(false)}>确认回收</button></div>
          {gcResult && <div className="gc-result"><strong>{gcResult.dry_run ? "预览结果" : "执行结果"}</strong><span>{gcResult.candidate_count} 个候选文件</span><span>预计释放 {sizeText(gcResult.reclaimed_bytes)}</span><small>{gcResult.candidate_ids.length ? gcResult.candidate_ids.slice(0, 5).join(" · ") : "没有符合条件的文件"}{gcResult.candidate_ids.length > 5 ? " …" : ""}</small></div>}
        </section>
      </div></details>
    </div>
  );
}
