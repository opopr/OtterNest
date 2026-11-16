import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle, Activity, CheckCircle2, Clock3, RefreshCw,
  Shield, ShieldAlert, X
} from "lucide-react";
import { request } from "../api/client";
import type { Project, SystemMode } from "../api/types";
import ProjectSelect from "../components/ProjectSelect";

interface ProjectEvent {
  id: number;
  kind: string;
  subject_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}
interface ProjectStatusResult { id: string; status: string; reason: string; }

const modeLabel: Record<string, string> = { normal: "正常运行", degraded: "降级运行", readonly_protect: "只读保护" };
const projectStatusLabel: Record<string, string> = { active: "运行中", paused: "已暂停", archived: "已归档" };
const eventLabel: Record<string, string> = {
  "project.created": "项目创建", "project.charter_updated": "章程更新", "project.status_changed": "项目状态变更",
  "task.created": "任务创建", "task.authorized": "任务授权", "task.accepted": "任务验收通过",
  "task.changes_requested": "任务退回返工", "task.cancelled": "任务取消", "goal.created": "目标创建",
  "goal.status_changed": "目标状态变更", "goal.dependency_added": "添加目标依赖",
  "workflow.rule_created": "创建工作流规则", "workflow.rule_toggled": "切换工作流规则",
  "memory.proposed": "提出知识记忆", "memory.published": "发布知识记忆", "memory.revised": "修订知识记忆",
  "memory.archived": "归档知识记忆", "decision.proposed": "提出决策", "decision.approved": "决策批准",
  "artifact.archive_exported": "导出成果归档", "artifact.archive_restored": "恢复成果归档",
  "system.mode_changed": "系统保护模式变更"
};

export default function SystemPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [systemMode, setSystemMode] = useState<SystemMode | null>(null);
  const [events, setEvents] = useState<ProjectEvent[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [mode, setMode] = useState<SystemMode["mode"]>("normal");
  const [modeReason, setModeReason] = useState("");
  const [projectStatus, setProjectStatus] = useState("active");
  const [projectReason, setProjectReason] = useState("");

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);

  const loadSystem = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [projectRows, currentMode] = await Promise.all([
        request<Project[]>("/projects"),
        request<SystemMode>("/system/mode")
      ]);
      setProjects(projectRows);
      setSystemMode(currentMode);
      setMode(currentMode.mode);
      setModeReason(currentMode.reason);
      setSelectedId((current) => projectRows.some((item) => item.id === current) ? current : projectRows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "系统状态读取失败"); }
    finally { setLoading(false); }
  }, []);

  const loadEvents = useCallback(async (projectId: string) => {
    try {
      const rows = await request<ProjectEvent[]>("/projects/" + encodeURIComponent(projectId) + "/events?after=0&limit=100");
      setEvents(rows.slice().reverse());
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目事件读取失败"); }
  }, []);

  useEffect(() => { void loadSystem(); }, [loadSystem]);
  useEffect(() => { if (selectedId) { void loadEvents(selectedId); const p = projects.find((item) => item.id === selectedId); if (p) setProjectStatus(p.status); } else setEvents([]); }, [selectedId, projects, loadEvents]);

  const saveMode = async () => {
    if (mode !== "normal" && modeReason.trim().length < 5) {
      setError("进入降级或只读保护模式时，必须填写至少 5 个字符的原因。");
      return;
    }
    const description = mode === "normal" ? "恢复正常运行" : mode === "degraded" ? "切换到降级运行" : "切换到只读保护";
    if (!window.confirm("确认" + description + "？该设置会改变全局写操作策略。")) return;
    setSaving(true);
    setError("");
    try {
      const result = await request<SystemMode>("/system/mode", {
        method: "PUT", body: JSON.stringify({ mode, reason: modeReason.trim() })
      });
      setSystemMode(result);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "系统保护模式更新失败"); }
    finally { setSaving(false); }
  };

  const saveProjectStatus = async () => {
    if (!selectedId || projectReason.trim().length < 5) {
      setError("项目状态变更原因至少需要 5 个字符。");
      return;
    }
    if (!window.confirm("确认将项目「" + (selectedProject?.name || "") + "」切换为" + projectStatusLabel[projectStatus] + "？")) return;
    setSaving(true);
    try {
      await request<ProjectStatusResult>("/projects/" + encodeURIComponent(selectedId) + "/status", {
        method: "POST", body: JSON.stringify({ status: projectStatus, reason: projectReason.trim() })
      });
      setProjects((current) => current.map((project) => project.id === selectedId ? { ...project, status: projectStatus } : project));
      setProjectReason("");
      await loadEvents(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目状态变更失败"); }
    finally { setSaving(false); }
  };

  const isProtective = systemMode?.mode !== "normal";

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">SYSTEM OPERATIONS</div><h1>系统设置与运行事件</h1><p>全局保护模式、项目运行状态，以及由服务端持久化的项目事件流。</p></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => void loadSystem()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新状态</button></div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      {loading && !systemMode ? <div className="panel-loading"><div className="spinner" />正在读取系统状态…</div> : <>
        <section className={"system-mode-banner " + (isProtective ? "protective" : "normal")}>
          <div className="system-mode-icon">{isProtective ? <ShieldAlert size={23} /> : <CheckCircle2 size={23} />}</div>
          <div><div className="eyebrow">GLOBAL CONTROL PLANE</div><h2>{modeLabel[systemMode?.mode || "normal"]}</h2><p>{systemMode?.reason || "当前未启用全局保护限制。"}</p></div>
          <span className={"status-tag " + (isProtective ? "tag-amber" : "tag-green")}>{isProtective ? "保护策略生效" : "运行正常"}</span>
        </section>

        <div className="operations-grid">
          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><Shield size={17} /><h3>全局保护模式</h3></div><span className="muted-inline">仅所有者可修改</span></div>
            <div className="mode-options">
              <label className={"mode-option " + (mode === "normal" ? "selected" : "")}><input type="radio" checked={mode === "normal"} onChange={() => setMode("normal")} /><span><strong>正常运行</strong><small>允许正常业务操作</small></span></label>
              <label className={"mode-option " + (mode === "degraded" ? "selected" : "")}><input type="radio" checked={mode === "degraded"} onChange={() => setMode("degraded")} /><span><strong>降级运行</strong><small>限制高风险或需要严格授权的写操作</small></span></label>
              <label className={"mode-option " + (mode === "readonly_protect" ? "selected" : "")}><input type="radio" checked={mode === "readonly_protect"} onChange={() => setMode("readonly_protect")} /><span><strong>只读保护</strong><small>冻结普通写操作，优先保护数据状态</small></span></label>
            </div>
            <label className="field-label">切换原因<textarea rows={3} value={modeReason} onChange={(event) => setModeReason(event.target.value)} maxLength={2000} placeholder="记录触发保护模式的原因；非正常模式至少 5 个字符。" /></label>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || (mode !== "normal" && modeReason.trim().length < 5)} onClick={() => void saveMode()}>保存全局模式</button></div>
          </section>

          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><Activity size={17} /><h3>项目运行状态</h3></div></div>
            <ProjectSelect projects={projects} value={selectedId} onChange={setSelectedId} />
            {selectedProject ? <>
              <div className="project-status-current"><span>当前项目</span><strong>{selectedProject.name}</strong><span className={"status-tag " + (selectedProject.status === "active" ? "tag-green" : "tag-neutral")}>{projectStatusLabel[selectedProject.status] || selectedProject.status}</span></div>
              <label className="field-label">目标状态<select value={projectStatus} onChange={(event) => setProjectStatus(event.target.value)}><option value="active">运行中</option><option value="paused">暂停</option><option value="archived">归档</option></select></label>
              <label className="field-label">状态变更原因<textarea rows={3} value={projectReason} onChange={(event) => setProjectReason(event.target.value)} maxLength={2000} placeholder="至少 5 个字符，说明为什么调整项目状态。" /></label>
              <div className="form-actions"><button className="secondary-button compact" disabled={saving || projectReason.trim().length < 5 || projectStatus === selectedProject.status} onClick={() => void saveProjectStatus()}>提交状态变更</button></div>
            </> : <div className="empty-mini">暂无项目</div>}
          </section>
        </div>

        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><Clock3 size={17} /><h3>项目事件流</h3></div><div className="heading-actions"><span className="muted-inline">最近 100 条 · 倒序展示</span><button className="icon-button" title="刷新事件" onClick={() => selectedId && void loadEvents(selectedId)}><RefreshCw size={15} /></button></div></div>
          <div className="event-timeline">
            {events.map((event) => <article className="event-timeline-row" key={event.id}>
              <span className="event-timeline-marker" />
              <div className="event-timeline-content"><div><strong>{eventLabel[event.kind] || event.kind}</strong><code>{event.kind}</code></div><p>对象：{event.subject_id}</p>{Object.keys(event.payload || {}).length > 0 && <pre>{JSON.stringify(event.payload, null, 2)}</pre>}<small>{new Date(event.created_at).toLocaleString("zh-CN", { hour12: false })} · 事件 #{event.id}</small></div>
            </article>)}
            {!events.length && <div className="empty-state"><Activity size={24} /><strong>暂无项目事件</strong><p>项目中的任务、目标、记忆和治理变更会在这里形成可追踪记录。</p></div>}
          </div>
        </section>
      </>}
    </div>
  );
}
