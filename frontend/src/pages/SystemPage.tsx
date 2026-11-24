import { useCallback, useEffect, useMemo, useState } from "react";
import {
  AlertCircle, Activity, CheckCircle2, Clock3, DownloadCloud, RefreshCw,
  Shield, ShieldAlert, X
} from "lucide-react";
import { getApiOrigin, healthRequest, request } from "../api/client";
import type { Project, SystemMode } from "../api/types";
import { useSystemMode } from "../auth/SystemModeProvider";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";
import ServerHealthCards from "../components/ServerHealthCards";

interface ProjectEvent {
  id: number;
  kind: string;
  subject_id: string;
  payload: Record<string, unknown>;
  created_at: string;
}
interface ProjectStatusResult { id: string; status: string; reason: string; }

interface ConnectionCheck {
  state: "ok" | "error";
  detail: string;
  latencyMs: number;
}
interface ConnectionDiagnostics {
  api: ConnectionCheck;
  database: ConnectionCheck;
  identity: ConnectionCheck;
}
interface UpdateCheckResult {
  enabled: boolean;
  git_available?: boolean;
  branch: string;
  target_ref: string;
  current_commit: string;
  remote_commit: string;
  behind_count: number;
  update_available: boolean;
  working_tree_clean: boolean;
  recent_commits: string[];
  fetch_error: string;
  restart_command_configured: boolean;
  checked_at: string;
}
interface UpdateStatusResult {
  state: "idle" | "running" | "succeeded" | "failed" | string;
  step: string;
  logs: string[];
  started_at: string | null;
  finished_at: string | null;
  error: string;
}

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
  const dialog = useDialog();
  const { refreshSystemMode } = useSystemMode();
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

  const [connectionChecks, setConnectionChecks] = useState<ConnectionDiagnostics | null>(null);
  const [checkingConnection, setCheckingConnection] = useState(false);
  const [connectionCheckedAt, setConnectionCheckedAt] = useState("");
  const [updateCheck, setUpdateCheck] = useState<UpdateCheckResult | null>(null);
  const [updateStatus, setUpdateStatus] = useState<UpdateStatusResult | null>(null);
  const [updateError, setUpdateError] = useState("");
  const [checkingUpdate, setCheckingUpdate] = useState(false);
  const [applyingUpdate, setApplyingUpdate] = useState(false);

  const checkForUpdates = useCallback(async (fetchRemote = true) => {
    setCheckingUpdate(true);
    setUpdateError("");
    try {
      const [info, status] = await Promise.all([
        request<UpdateCheckResult>("/system/update/check?fetch=" + (fetchRemote ? "true" : "false")),
        request<UpdateStatusResult>("/system/update/status")
      ]);
      setUpdateCheck(info);
      setUpdateStatus(status);
    } catch (reason) {
      setUpdateError(reason instanceof Error ? reason.message : "检查更新失败");
    } finally {
      setCheckingUpdate(false);
    }
  }, []);

  const applySelfUpdate = async (force = false) => {
    const confirmed = await dialog.confirm({
      title: force ? "强制执行更新" : "确认一键更新",
      message: force
        ? "当前仍有运行中任务，强制继续更新可能会中断正在执行的任务进程。确认继续？"
        : "确认执行一键更新？将自动拉取最新代码、执行数据库迁移、重新构建前端并重启服务。",
      confirmText: force ? "强制更新" : "开始更新",
      tone: "warning"
    });
    if (!confirmed) return;
    setApplyingUpdate(true);
    setUpdateError("");
    try {
      const res = await request<UpdateStatusResult>("/system/update/apply", {
        method: "POST",
        body: JSON.stringify({ force })
      });
      setUpdateStatus(res);
    } catch (reason) {
      const msg = reason instanceof Error ? reason.message : "触发更新失败";
      if (!force && msg.includes("running tasks")) {
        const forceConfirm = await dialog.confirm({
          title: "存在运行中的任务",
          message: msg + "\n\n是否强制继续更新？",
          confirmText: "强制继续",
          tone: "danger"
        });
        if (forceConfirm) {
          await applySelfUpdate(true);
          return;
        }
      } else {
        setUpdateError(msg);
      }
    } finally {
      setApplyingUpdate(false);
    }
  };

  useEffect(() => {
    void checkForUpdates(false);
  }, [checkForUpdates]);

  useEffect(() => {
    if (updateStatus?.state !== "running") return;
    const timer = window.setInterval(() => {
      void request<UpdateStatusResult>("/system/update/status")
        .then((status) => {
          setUpdateStatus(status);
          if (status.state !== "running") {
            void checkForUpdates(false);
          }
        })
        .catch(() => {
          // Server may be restarting during final step; keep polling until it recovers.
        });
    }, 2500);
    return () => window.clearInterval(timer);
  }, [updateStatus?.state, checkForUpdates]);

  const checkConnection = useCallback(async () => {
    setCheckingConnection(true);
    const runCheck = async (check: () => Promise<string>): Promise<ConnectionCheck> => {
      const started = performance.now();
      try {
        const detail = await check();
        return { state: "ok", detail, latencyMs: Math.round(performance.now() - started) };
      } catch (reason) {
        return {
          state: "error",
          detail: reason instanceof Error ? reason.message : "连接检查失败",
          latencyMs: Math.round(performance.now() - started)
        };
      }
    };
    const [api, database, identity] = await Promise.all([
      runCheck(async () => {
        const result = await healthRequest("/health/live");
        if (result.status !== "alive") throw new Error("FastAPI 服务未报告 alive 状态");
        return "FastAPI 服务可访问";
      }),
      runCheck(async () => {
        const result = await healthRequest("/health/ready");
        if (result.status !== "ready") throw new Error("数据库尚未就绪");
        return "数据库连接正常";
      }),
      runCheck(async () => {
        const result = await request<{ kind: string }>("/me");
        return result.kind === "owner" ? "所有者会话有效" : "已认证身份：" + result.kind;
      })
    ]);
    setConnectionChecks({ api, database, identity });
    setConnectionCheckedAt(new Date().toISOString());
    setCheckingConnection(false);
  }, []);


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
  useEffect(() => { void checkConnection(); }, [checkConnection]);
  useEffect(() => { if (selectedId) { void loadEvents(selectedId); const p = projects.find((item) => item.id === selectedId); if (p) setProjectStatus(p.status); } else setEvents([]); }, [selectedId, projects, loadEvents]);

  const saveMode = async () => {
    if (mode !== "normal" && modeReason.trim().length < 5) {
      setError("进入降级或只读保护模式时，必须填写至少 5 个字符的原因。");
      return;
    }
    const description = mode === "normal" ? "恢复正常运行" : mode === "degraded" ? "切换到降级运行" : "切换到只读保护";
    const confirmed = await dialog.confirm({
      title: "确认更改全局保护模式",
      message: "确认" + description + "？该设置会改变全局写操作策略。",
      confirmText: "确认切换",
      tone: mode === "normal" ? "default" : "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      const result = await request<SystemMode>("/system/mode", {
        method: "PUT", body: JSON.stringify({ mode, reason: modeReason.trim() })
      });
      setSystemMode(result);
      await refreshSystemMode();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "系统保护模式更新失败"); }
    finally { setSaving(false); }
  };

  const saveProjectStatus = async () => {
    if (!selectedId || projectReason.trim().length < 5) {
      setError("项目状态变更原因至少需要 5 个字符。");
      return;
    }
    const confirmed = await dialog.confirm({
      title: "确认调整项目运行状态",
      message: "确认将项目「" + (selectedProject?.name || "") + "」切换为" + projectStatusLabel[projectStatus] + "？",
      confirmText: "确认变更"
    });
    if (!confirmed) return;
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

      <ServerHealthCards />

      <section className="panel connection-diagnostics-panel">
        <div className="panel-heading">
          <div className="panel-title"><Activity size={17} /><h3>API 与数据库连接诊断</h3></div>
          <div className="heading-actions">
            <span className="muted-inline">{connectionCheckedAt ? "检测于 " + new Date(connectionCheckedAt).toLocaleString("zh-CN", { hour12: false }) : "尚未检测"}</span>
            <button className="secondary-button compact" onClick={() => void checkConnection()} disabled={checkingConnection}>
              <RefreshCw size={14} className={checkingConnection ? "spin" : ""} />重新检测
            </button>
          </div>
        </div>
        <div className="connection-origin-list">
          <div><span>前端 Origin</span><code>{window.location.origin}</code></div>
          <div><span>后端 API Origin</span><code>{getApiOrigin()}</code></div>
        </div>
        <div className="stat-grid connection-check-grid">
          <div className="stat-card">
            <div className={"stat-icon " + (connectionChecks?.api.state === "ok" ? "tone-green" : connectionChecks?.api.state === "error" ? "tone-amber" : "tone-blue")}><Activity size={18} /></div>
            <div className="stat-copy"><span>API 服务</span><strong>{connectionChecks?.api.state === "ok" ? "可访问" : connectionChecks?.api.state === "error" ? "连接失败" : checkingConnection ? "检测中" : "待检测"}</strong><small>{connectionChecks?.api.detail || "GET /health/live"}</small><small>{connectionChecks?.api.latencyMs ?? "—"} ms</small></div>
          </div>
          <div className="stat-card">
            <div className={"stat-icon " + (connectionChecks?.database.state === "ok" ? "tone-green" : connectionChecks?.database.state === "error" ? "tone-amber" : "tone-blue")}><CheckCircle2 size={18} /></div>
            <div className="stat-copy"><span>数据库连接</span><strong>{connectionChecks?.database.state === "ok" ? "正常" : connectionChecks?.database.state === "error" ? "未就绪" : checkingConnection ? "检测中" : "待检测"}</strong><small>{connectionChecks?.database.detail || "GET /health/ready · 服务端执行 SELECT 1"}</small><small>{connectionChecks?.database.latencyMs ?? "—"} ms</small></div>
          </div>
          <div className="stat-card">
            <div className={"stat-icon " + (connectionChecks?.identity.state === "ok" ? "tone-green" : connectionChecks?.identity.state === "error" ? "tone-amber" : "tone-blue")}><Shield size={18} /></div>
            <div className="stat-copy"><span>身份认证</span><strong>{connectionChecks?.identity.state === "ok" ? "有效" : connectionChecks?.identity.state === "error" ? "失败" : checkingConnection ? "检测中" : "待检测"}</strong><small>{connectionChecks?.identity.detail || "GET /api/v1/me"}</small><small>{connectionChecks?.identity.latencyMs ?? "—"} ms</small></div>
          </div>
        </div>
        {(connectionChecks?.api.state === "error" || connectionChecks?.database.state === "error" || connectionChecks?.identity.state === "error") && (
          <div className="connection-help">
            如果当前从 Google AI Studio 预览访问，请确认 <code>VITE_API_BASE_URL</code> 指向后端 HTTPS Origin，并让服务端 <code>OTTERNEST_CORS_ORIGINS</code> 精确包含上方的前端 Origin。不要把数据库地址或令牌写入 VITE_ 变量。
          </div>
        )}
        <div className="form-actions">
          <a className="secondary-button compact" href={getApiOrigin() + "/api/docs"} target="_blank" rel="noreferrer">打开 FastAPI 文档</a>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div className="panel-title"><DownloadCloud size={17} /><h3>系统版本与一键更新</h3></div>
          <div className="heading-actions flex-row-gap">
            {updateCheck && updateCheck.git_available !== false && (
              <span className={"status-tag " + (updateCheck.update_available ? "tag-amber" : "tag-green")}>
                {updateCheck.update_available ? `发现 ${updateCheck.behind_count} 个新提交` : "已是最新版本"}
              </span>
            )}
            <button className="secondary-button compact" onClick={() => void checkForUpdates(true)} disabled={checkingUpdate || updateStatus?.state === "running"}>
              <RefreshCw size={14} className={checkingUpdate ? "spin" : ""} />检查更新
            </button>
            <button
              className="primary-button compact"
              onClick={() => void applySelfUpdate(false)}
              disabled={applyingUpdate || updateStatus?.state === "running" || !updateCheck?.enabled || updateCheck?.git_available === false}
              title={!updateCheck?.enabled ? "需在服务端配置 OTTERNEST_ENABLE_WEB_UPDATE=true" : ""}
            >
              <DownloadCloud size={14} />{updateStatus?.state === "running" ? "更新执行中…" : "一键更新并重启"}
            </button>
          </div>
        </div>
        {updateError && <div className="connection-help mb-10">当前环境未启用 Git 在线更新检测：{updateError}</div>}
        {updateCheck && updateCheck.git_available === false && (
          <div className="connection-help mb-10">当前后端运行在无 <code>.git</code> 目录的打包/容器环境中，在线代码更新不可用。</div>
        )}
        {updateCheck && updateCheck.git_available !== false && (
          <div className="update-meta-row">
            <span>分支：<code>{updateCheck.branch}</code></span>
            <span>当前版本：<code>{updateCheck.current_commit}</code></span>
            <span>远端版本：<code>{updateCheck.remote_commit}</code></span>
            <span>工作区：{updateCheck.working_tree_clean ? "干净" : "有未提交修改"}</span>
            <span>自动重启：{updateCheck.restart_command_configured ? "已配置" : "未配置（仅拉取/迁移/构建）"}</span>
          </div>
        )}
        {updateCheck && updateCheck.git_available !== false && !updateCheck.enabled && (
          <div className="connection-help mb-10">
            网页一键更新当前处于关闭状态。如需在页面直接更新，请在服务端环境变量中配置 <code>OTTERNEST_ENABLE_WEB_UPDATE=true</code>（以及可选的 <code>OTTERNEST_RESTART_COMMAND</code>）。
          </div>
        )}
        {updateCheck && Array.isArray(updateCheck.recent_commits) && updateCheck.recent_commits.length > 0 && (
          <pre className="json-preview mb-10">{updateCheck.recent_commits.join("\n")}</pre>
        )}
        {updateStatus && updateStatus.state !== "idle" && (
          <div className="update-status-box">
            <div className="update-status-head">
              <span className={"status-tag " + (updateStatus.state === "succeeded" ? "tag-green" : updateStatus.state === "running" ? "tag-amber" : "tag-neutral")}>
                {updateStatus.state === "running" ? `执行中：${updateStatus.step}` : updateStatus.state === "succeeded" ? "更新成功" : "更新失败"}
              </span>
              {updateStatus.error && <span className="update-error-text">{updateStatus.error}</span>}
            </div>
            {Array.isArray(updateStatus.logs) && updateStatus.logs.length > 0 && <pre className="json-preview">{updateStatus.logs.join("\n")}</pre>}
          </div>
        )}
      </section>

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
