import ValidatedForm from "../components/ValidatedForm";
import { ErrorNotice, LoadingState } from "../components/PageFeedback";
import { useDraftGuard, UnsavedDraftNotice } from "../components/DraftGuard";
import { useScopedRequest } from "../utils/useScopedRequest";
import ListPagination, { useListPage } from "../components/ListPagination";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Archive, BookOpen, Check, Database, FilePlus2, Globe2, LockKeyhole, Pencil, Plus, RefreshCw, Search, Share2, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Project } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";

interface Memory {
  id: string;
  kind: string;
  title: string;
  content: string;
  source: string;
  sensitivity: string;
  status: string;
  version: number;
  score?: number;
  relevance_score?: number;
}
interface Charter {
  project_id: string;
  configured: boolean;
  scope_boundary: string;
  stop_conditions: string;
  max_concurrent_tasks: number;
  max_total_tasks: number;
  running_tasks: number;
  active_total_tasks: number;
  updated_at: string | null;
}
interface KnowledgeShare {
  id: string;
  source_project_id: string;
  target_project_id: string;
  item_kind: string;
  source_item_id: string;
  promoted_item_id: string | null;
  rationale: string;
  status: string;
  owner_approval_required: boolean;
  owner_note: string;
  created_at: string;
  resolved_at: string | null;
}
interface MemoryDraft {
  kind: string;
  title: string;
  content: string;
  source: string;
  sensitivity: string;
}
const kindLabel: Record<string, string> = { fact: "事实", decision: "决策", experience: "经验", skill: "技能", procedure: "流程" };
const statusLabel: Record<string, string> = { proposed: "待审核", published: "已发布", archived: "已归档" };
const sensitivityLabel: Record<string, string> = { public: "公开", internal: "内部", confidential: "机密", restricted: "受限" };

const emptyDraft: MemoryDraft = { kind: "fact", title: "", content: "", source: "", sensitivity: "internal" };

export default function KnowledgePage() {
  const dialog = useDialog();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [memories, setMemories] = useState<Memory[]>([]);
  const [charter, setCharter] = useState<Charter | null>(null);
  const [shares, setShares] = useState<KnowledgeShare[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showMemoryForm, setShowMemoryForm] = useState(false);
  const [editingId, setEditingId] = useState("");
  const [memoryDraft, setMemoryDraft] = useState<MemoryDraft>(emptyDraft);
  const [revisionReason, setRevisionReason] = useState("");
  const [charterDraft, setCharterDraft] = useState({ scope_boundary: "", stop_conditions: "", max_concurrent_tasks: "5", max_total_tasks: "200" });
  const [targetProjectId, setTargetProjectId] = useState("");
  const [shareMemoryId, setShareMemoryId] = useState("");
  const [shareRationale, setShareRationale] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [sensitivityFilter, setSensitivityFilter] = useState<string>("all");
  const [memoryStatus, setMemoryStatus] = useState("all");
  const [tab, setTab] = useState<"memories" | "charter" | "sharing">("memories");

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);
  const publishedMemories = memories.filter((item) => item.status === "published");
  const visibleMemories = useMemo(
    () => memories.filter(m => (sensitivityFilter === "all" || m.sensitivity === sensitivityFilter) && (memoryStatus === "all" || m.status === memoryStatus)),
    [memories, sensitivityFilter, memoryStatus]
  );
  const memoryPage = useListPage(visibleMemories, JSON.stringify([selectedId, searchQuery, sensitivityFilter, memoryStatus]));

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
      setTargetProjectId((current) => rows.some((item) => item.id === current) ? current : rows.find((item) => item.id !== selectedId)?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, [selectedId]);

  const beginProjectRequest = useScopedRequest(selectedId);
  const resetProjectView = () => { setMemories([]); setShares([]); setCharter(null); setCharterDraft({scope_boundary:"",stop_conditions:"",max_concurrent_tasks:"5",max_total_tasks:"200"}); setShowMemoryForm(false); setEditingId(""); setMemoryDraft(emptyDraft); setRevisionReason(""); setShareMemoryId(""); setShareRationale(""); setTargetProjectId(""); setSearchQuery(""); setSensitivityFilter("all"); setMemoryStatus("all"); };
  const hasUnsavedDraft = Boolean(JSON.stringify(memoryDraft) !== JSON.stringify(emptyDraft) || revisionReason || shareMemoryId || shareRationale || (charter && (charterDraft.scope_boundary !== (charter.scope_boundary ?? "") || charterDraft.stop_conditions !== (charter.stop_conditions ?? "") || charterDraft.max_concurrent_tasks !== String(charter.max_concurrent_tasks ?? 5) || charterDraft.max_total_tasks !== String(charter.max_total_tasks ?? 200))));
  const confirmDiscard = useDraftGuard(hasUnsavedDraft);
  const changeProject = async (id: string) => { if (id === selectedId || saving) return; if (!(await confirmDiscard())) return; resetProjectView(); setLoading(true); setError(""); setSelectedId(id);  };

  const loadProjectData = useCallback(async (projectId: string, q?: string) => {
    const isCurrent = beginProjectRequest(projectId);
    if (!isCurrent()) return;
    setLoading(true);
    setError("");
    try {
      const encoded = encodeURIComponent(projectId);
      const trimmed = q?.trim() || "";
      const memoryEndpoint = trimmed
        ? "/projects/" + encoded + "/memories/search?q=" + encodeURIComponent(trimmed) + "&limit=50"
        : "/projects/" + encoded + "/memories";
      const [memoryRows, projectCharter, shareRows] = await Promise.all([
        request<Memory[]>(memoryEndpoint),
        request<Charter>("/projects/" + encoded + "/charter"),
        request<KnowledgeShare[]>("/projects/" + encoded + "/knowledge-shares")
      ]);
      if (!isCurrent()) return;
      setMemories(Array.isArray(memoryRows) ? memoryRows : []);
      setCharter(projectCharter || null);
      setShares(Array.isArray(shareRows) ? shareRows : []);
      setCharterDraft({
        scope_boundary: projectCharter?.scope_boundary ?? "",
        stop_conditions: projectCharter?.stop_conditions ?? "",
        max_concurrent_tasks: String(projectCharter?.max_concurrent_tasks ?? 5),
        max_total_tasks: String(projectCharter?.max_total_tasks ?? 200)
      });
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "知识与项目章程读取失败"); }
    finally { if (isCurrent()) setLoading(false); }
  }, [beginProjectRequest]);

  useEffect(() => { void loadProjects(); }, []);
  useEffect(() => { if (selectedId) void loadProjectData(selectedId); else { setMemories([]); setShares([]); setCharter(null); } }, [selectedId, loadProjectData]);

  const setDraftField = (key: keyof MemoryDraft, value: string) => setMemoryDraft((current) => ({ ...current, [key]: value }));

  const saveMemory = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    setError("");
    try {
      if (editingId) {
        await request("/memories/" + encodeURIComponent(editingId) + "/revise", {
          method: "POST",
          body: JSON.stringify({ title: memoryDraft.title, content: memoryDraft.content, source: memoryDraft.source, reason: revisionReason.trim() })
        });
      } else {
        await request("/projects/" + encodeURIComponent(selectedId) + "/memories", {
          method: "POST", body: JSON.stringify(memoryDraft)
        });
      }
      setMemoryDraft(emptyDraft); setRevisionReason(""); setEditingId(""); setShowMemoryForm(false);
      await loadProjectData(selectedId, searchQuery);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存知识记录失败"); }
    finally { setSaving(false); }
  };

  const publishMemory = async (memory: Memory) => {
    const confirmed = await dialog.confirm({
      title: "发布项目知识",
      message: `确认发布「${memory.title}」？发布后它会成为可被项目智能体使用的知识。`,
      confirmText: "确认发布"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/memories/" + encodeURIComponent(memory.id) + "/publish", { method: "POST" });
      await loadProjectData(selectedId, searchQuery);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发布知识失败"); }
    finally { setSaving(false); }
  };

  const archiveMemory = async (memory: Memory) => {
    const reason = await dialog.prompt({
      title: "归档项目知识",
      message: `归档「${memory.title}」后不再作为活跃上下文提供，但历史审计记录仍会保留。`,
      label: "归档原因（至少 5 个字符）",
      placeholder: "说明信息过期、已替代或需要封存的原因…",
      required: true,
      minLength: 5,
      multiline: true,
      confirmText: "确认归档"
    });
    if (reason === null) return;
    setSaving(true);
    try {
      await request("/memories/" + encodeURIComponent(memory.id) + "/archive", { method: "POST", body: JSON.stringify({ reason }) });
      await loadProjectData(selectedId, searchQuery);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "归档知识失败"); }
    finally { setSaving(false); }
  };

  const editMemory = (memory: Memory) => {
    setEditingId(memory.id);
    setMemoryDraft({ kind: memory.kind, title: memory.title, content: memory.content, source: memory.source, sensitivity: memory.sensitivity });
    setRevisionReason("");
    setShowMemoryForm(true);
    setTab("memories");
  };

  const saveCharter = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    try {
      const updated = await request<Charter>("/projects/" + encodeURIComponent(selectedId) + "/charter", {
        method: "PUT",
        body: JSON.stringify({
          scope_boundary: charterDraft.scope_boundary,
          stop_conditions: charterDraft.stop_conditions,
          max_concurrent_tasks: Number(charterDraft.max_concurrent_tasks),
          max_total_tasks: Number(charterDraft.max_total_tasks)
        })
      });
      setCharter(updated);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "保存项目章程失败"); }
    finally { setSaving(false); }
  };

  const proposeShare = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId || !shareMemoryId || !targetProjectId || targetProjectId === selectedId) return;
    setSaving(true);
    try {
      await request<KnowledgeShare>("/projects/" + encodeURIComponent(selectedId) + "/knowledge-shares", {
        method: "POST",
        body: JSON.stringify({ target_project_id: targetProjectId, item_kind: "memory", source_item_id: shareMemoryId, rationale: shareRationale.trim() })
      });
      setShareRationale("");
      await loadProjectData(selectedId, searchQuery);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "提交跨项目共享申请失败"); }
    finally { setSaving(false); }
  };

  const resolveShare = async (share: KnowledgeShare, approved: boolean) => {
    const note = await dialog.prompt({
      title: approved ? "批准跨项目知识共享" : "拒绝跨项目知识共享",
      message: approved ? "批准后将把该知识的受控副本复制到目标项目。" : "拒绝后不会向目标项目写入任何副本。",
      label: approved ? "批准说明（至少 5 个字符）" : "拒绝原因（至少 5 个字符）",
      placeholder: approved ? "记录批准共享的适用边界或审查意见…" : "说明拒绝跨项目共享的原因…",
      required: true,
      minLength: 5,
      multiline: true,
      confirmText: approved ? "批准并复制" : "确认拒绝"
    });
    if (note === null) return;
    setSaving(true);
    try {
      await request("/knowledge-shares/" + encodeURIComponent(share.id) + "/resolve", {
        method: "POST", body: JSON.stringify({ approved, note })
      });
      await loadProjectData(selectedId, searchQuery);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "处理共享申请失败"); }
    finally { setSaving(false); }
  };

  const cancelMemoryForm = () => { setShowMemoryForm(false); setEditingId(""); setMemoryDraft(emptyDraft); setRevisionReason(""); };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>知识库与项目章程</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadProjectData(selectedId, searchQuery)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      <ErrorNotice message={error} onDismiss={() => setError("")} />
      <UnsavedDraftNotice dirty={hasUnsavedDraft} />
      <section className="panel project-toolbar"><div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedId} onChange={id => void changeProject(id)} disabled={saving} /><span className="muted-inline">{memories.length} 条记忆 · {publishedMemories.length} 条已发布</span></section>
      <div className="governance-tabs knowledge-tabs">
        <button className={tab === "memories" ? "selected" : ""} onClick={() => setTab("memories")}><BookOpen size={16} />项目记忆 <span>{memories.length}</span></button>
        <button className={tab === "charter" ? "selected" : ""} onClick={() => setTab("charter")}><ShieldCheck size={16} />项目章程</button>
        <button className={tab === "sharing" ? "selected" : ""} onClick={() => setTab("sharing")}><Share2 size={16} />跨项目共享 <span>{shares.filter((item) => item.status === "proposed").length}</span></button>
      </div>
      {loading ? <LoadingState label="正在读取项目知识…"/> : tab === "memories" ? (
        <section className="panel">
          <div className="panel-heading">
            <div className="panel-title"><Database size={17} /><h3>项目知识</h3><span className="count-chip">{visibleMemories.length}</span></div>
            <div className="heading-actions flex-row-gap">
              <ValidatedForm onSubmit={(e) => { e.preventDefault(); if (selectedId) void loadProjectData(selectedId, searchQuery); }} className="flex-row-gap-sm">
                <input
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  aria-label="知识检索" placeholder="输入检索关键词"
                  className="search-input-compact"
                />
                <select
                  aria-label="按敏感级别筛选"
                  value={sensitivityFilter}
                  onChange={(e) => setSensitivityFilter(e.target.value)}
                  className="compact-select"
                >
                  <option value="all">全部敏感级</option>
                  <option value="public">公开</option>
                  <option value="internal">内部</option>
                  <option value="confidential">机密</option>
                  <option value="restricted">受限</option>
                </select>
                <select className="compact-select" aria-label="知识状态" value={memoryStatus} onChange={e => setMemoryStatus(e.target.value)}><option value="all">全部状态</option><option value="proposed">待发布</option><option value="published">已发布</option><option value="archived">已归档</option></select>
                <button type="submit" className="secondary-button compact" disabled={!selectedId || loading}><Search size={14} />检索</button>
                {(searchQuery || sensitivityFilter !== "all" || memoryStatus !== "all") && <button type="button" className="text-button" onClick={() => { setSearchQuery(""); setSensitivityFilter("all"); setMemoryStatus("all"); if (selectedId) void loadProjectData(selectedId, ""); }}>清除</button>}
              </ValidatedForm>
              <button className="primary-button compact" onClick={() => { cancelMemoryForm(); setShowMemoryForm(true); }} disabled={!selectedId}><Plus size={15} />新增记忆</button>
            </div>
          </div>
          {showMemoryForm && <ValidatedForm className="form-panel embedded-form" onSubmit={saveMemory}>
            <div className="panel-heading"><div className="panel-title"><FilePlus2 size={16} /><h3>{editingId ? "修订知识记录" : "提出新记忆"}</h3></div><button type="button" className="icon-button" onClick={cancelMemoryForm}><X size={16} /></button></div>
            <div className="form-grid">
              <label>知识类型<select value={memoryDraft.kind} onChange={(event) => setDraftField("kind", event.target.value)}><option value="fact">事实</option><option value="decision">决策</option><option value="experience">经验</option><option value="skill">技能</option><option value="procedure">流程</option></select></label>
              <label>敏感级别<select value={memoryDraft.sensitivity} onChange={(event) => setDraftField("sensitivity", event.target.value)}><option value="public">公开</option><option value="internal">内部</option><option value="confidential">机密</option><option value="restricted">受限</option></select></label>
            </div>
            <label>标题<input value={memoryDraft.title} onChange={(event) => setDraftField("title", event.target.value)} required maxLength={240} /></label>
            <label>内容<textarea rows={4} value={memoryDraft.content} onChange={(event) => setDraftField("content", event.target.value)} required maxLength={20000} /></label>
            <label>来源与依据<input value={memoryDraft.source} onChange={(event) => setDraftField("source", event.target.value)} required maxLength={3000} /></label>
            {editingId && <label>修订原因（至少 5 个字符）<textarea rows={2} value={revisionReason} onChange={(event) => setRevisionReason(event.target.value)} required minLength={5} /></label>}
            <p className="form-hint">新记忆默认进入待审核状态。只有所有者发布后，智能体才能把它作为正式项目知识使用。</p>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || (Boolean(editingId) && revisionReason.trim().length < 5)}>{saving ? "保存中…" : editingId ? "保存修订" : "提交记忆"}</button></div>
          </ValidatedForm>}
          <div className="memory-grid">
            {memoryPage.rows.map((memory) => {
              const displayScore = typeof memory.relevance_score === "number" ? memory.relevance_score : memory.score;
              return <article className="memory-card" key={memory.id}>
                <div className="memory-card-head"><span className="capability-tag">{kindLabel[memory.kind] || memory.kind}</span><span className={"status-tag " + (memory.status === "published" ? "tag-green" : memory.status === "proposed" ? "tag-amber" : "tag-neutral")}>{statusLabel[memory.status] || memory.status}</span></div>
                <h3>{memory.title}</h3><details className="library-disclosure"><summary>查看知识内容</summary><p className="memory-expanded-content">{memory.content}</p><small className="memory-source">来源：{memory.source}</small></details>
                <div className="memory-meta"><span><LockKeyhole size={12} />{sensitivityLabel[memory.sensitivity] || memory.sensitivity}</span><span>版本 {memory.version}</span>{typeof displayScore === "number" && <span className="status-tag tag-green">相关度 {displayScore.toFixed(2)}</span>}</div>

                <div className="memory-actions">{memory.status !== "archived" && <button className="text-button" onClick={() => editMemory(memory)}><Pencil size={13} />修订</button>}{memory.status === "proposed" && <button className="secondary-button compact" disabled={saving} onClick={() => void publishMemory(memory)}><Check size={13} />发布</button>}{memory.status !== "archived" && <button className="icon-button" title="归档" disabled={saving} onClick={() => void archiveMemory(memory)}><Archive size={15} /></button>}</div>
              </article>;
            })}
            {!visibleMemories.length && <div className="empty-state"><BookOpen size={25} /><strong>{memories.length ? "当前筛选下暂无记忆" : "暂无项目记忆"}</strong><p>{memories.length ? "请调整敏感级别筛选或检索关键词。" : "先提出事实、决策、经验或流程，再由所有者审核发布。"}</p></div>}
          </div>
          {!loading && <ListPagination {...memoryPage} label="知识分页" />}
        </section>
      ) : tab === "charter" ? (
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17} /><h3>项目执行章程</h3></div><span className={"status-tag " + (charter?.configured ? "tag-green" : "tag-neutral")}>{charter?.configured ? "已配置" : "使用系统默认预算"}</span></div>
          <div className="charter-usage">
            <div><strong>{charter?.running_tasks ?? 0}/{charter?.max_concurrent_tasks ?? 5}</strong><small>并发任务占用</small></div>
            <div><strong>{charter?.active_total_tasks ?? 0}/{charter?.max_total_tasks ?? 200}</strong><small>累计有效任务</small></div>
            <div><strong>{charter?.configured ? "自定义" : "默认"}</strong><small>预算来源</small></div>
          </div>
          <ValidatedForm className="form-panel embedded-form" onSubmit={saveCharter}>
            <label>范围边界<textarea rows={4} value={charterDraft.scope_boundary} onChange={(event) => setCharterDraft((current) => ({ ...current, scope_boundary: event.target.value }))} maxLength={4000} placeholder="明确项目允许做什么、不允许做什么，以及交付范围。" /></label>
            <label>停止条件<textarea rows={4} value={charterDraft.stop_conditions} onChange={(event) => setCharterDraft((current) => ({ ...current, stop_conditions: event.target.value }))} maxLength={4000} placeholder="明确何时必须暂停执行并请求所有者介入。" /></label>
            <div className="form-grid"><label>最大并发任务<input type="number" min="1" max="50" value={charterDraft.max_concurrent_tasks} onChange={(event) => setCharterDraft((current) => ({ ...current, max_concurrent_tasks: event.target.value }))} /></label><label>最大任务总量<input type="number" min="1" max="2000" value={charterDraft.max_total_tasks} onChange={(event) => setCharterDraft((current) => ({ ...current, max_total_tasks: event.target.value }))} /></label></div>
            <p className="form-hint">并发上限不得大于任务总量。章程是后端强制执行的预算边界，不是单纯展示说明。</p>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || Number(charterDraft.max_concurrent_tasks) > Number(charterDraft.max_total_tasks)}>保存项目章程</button></div>
          </ValidatedForm>
        </section>
      ) : (
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><Globe2 size={17} /><h3>跨项目知识共享</h3></div><span className="muted-inline">共享会在目标项目创建独立副本</span></div>
          <ValidatedForm className="form-panel embedded-form" onSubmit={proposeShare}>
            <div className="form-grid">
              <label>已发布的源记忆<select value={shareMemoryId} onChange={(event) => setShareMemoryId(event.target.value)} required><option value="">选择记忆…</option>{publishedMemories.map((memory) => <option key={memory.id} value={memory.id}>{memory.title}</option>)}</select></label>
              <label>目标项目<select value={targetProjectId} onChange={(event) => setTargetProjectId(event.target.value)} required><option value="">选择目标项目…</option>{projects.filter((item) => item.id !== selectedId && item.status === "active").map((project) => <option key={project.id} value={project.id}>{project.name}</option>)}</select></label>
            </div>
            <label>共享理由<textarea rows={2} value={shareRationale} onChange={(event) => setShareRationale(event.target.value)} required minLength={5} maxLength={3000} /></label>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || !publishedMemories.length || !targetProjectId}>提交共享申请</button></div>
          </ValidatedForm>
          <div className="share-list">{shares.map((share) => <article className="share-row" key={share.id}><div><strong>{projects.find((item) => item.id === share.source_project_id)?.name || share.source_project_id.slice(0, 8)} → {projects.find((item) => item.id === share.target_project_id)?.name || share.target_project_id.slice(0, 8)}</strong><p>{share.rationale}</p><small>{share.item_kind} · {share.source_item_id} · {new Date(share.created_at).toLocaleString("zh-CN", { hour12: false })}</small></div><div className="share-actions"><span className={"status-tag " + (share.status === "approved" ? "tag-green" : share.status === "proposed" ? "tag-amber" : "tag-neutral")}>{share.status === "proposed" ? "待审批" : share.status === "approved" ? "已批准" : "已拒绝"}</span>{share.status === "proposed" && <><button className="secondary-button compact" disabled={saving} onClick={() => void resolveShare(share, true)}>批准</button><button className="text-button" disabled={saving} onClick={() => void resolveShare(share, false)}>拒绝</button></>}</div></article>)}{!shares.length && <div className="empty-state"><Share2 size={24} /><strong>暂无跨项目共享记录</strong><p>只有已发布的知识可以申请共享，审批通过后才会复制到目标项目。</p></div>}</div>
        </section>
      )}
    </div>
  );
}

