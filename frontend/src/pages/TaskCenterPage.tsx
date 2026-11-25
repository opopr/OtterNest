import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, ClipboardCheck, Eye, Filter, GitBranch, RefreshCw, RotateCcw, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Agent, Project, Task } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useDialog } from "../components/AppDialog";
import TaskDetailPanel, { type TaskDetailSelection } from "../components/TaskDetailPanel";

interface Row extends Task { project_name: string; }
interface Plan {
  id: string;
  task_id: string;
  action: string;
  status: string;
  expires_at: string;
  expected_version: number;
  task_title?: string;
  parameters?: { accepted?: boolean; reason?: string };
}
interface DispatchRoute { task_id: string; agent_id: string; }
interface DispatchResponse { project_id: string; routes: DispatchRoute[]; count: number; }
interface DispatchNotice {
  routes: Array<DispatchRoute & { project_name: string; task_title: string; agent_name: string }>;
  errors: string[];
  project_count: number;
}
const labels: Record<string, string> = {
  ready: "待执行", waiting_dependencies: "等待依赖", waiting_approval: "等待授权",
  running: "执行中", submitted: "等待验收", blocked: "已阻塞",
  completed: "已完成", cancelled: "已取消", failed: "执行失败"
};
const tones: Record<string, string> = {
  ready: "tag-blue", waiting_dependencies: "tag-neutral", waiting_approval: "tag-red",
  running: "tag-blue", submitted: "tag-amber", blocked: "tag-red",
  completed: "tag-green", cancelled: "tag-neutral", failed: "tag-red"
};
const risks: Record<string, string> = { R0: "低风险", R1: "一般风险", R2: "较高风险", R3: "高风险" };
const time = (v: string) => new Date(v).toLocaleString("zh-CN", { hour12: false });

export default function TaskCenterPage() {
  const { user } = useAuth();
  const dialog = useDialog();
  const [detailSelection, setDetailSelection] = useState<TaskDetailSelection | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [tasks, setTasks] = useState<Row[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [project, setProject] = useState("all");
  const [status, setStatus] = useState("all");
  const [risk, setRisk] = useState("all");
  const [query, setQuery] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [dispatching, setDispatching] = useState(false);
  const [error, setError] = useState("");
  const [plan, setPlan] = useState<Plan | null>(null);
  const [dispatchNotice, setDispatchNotice] = useState<DispatchNotice | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [ps, agentRows] = await Promise.all([request<Project[]>("/projects"), request<Agent[]>("/agents")]);
      setProjects(ps);
      setAgents(agentRows);
      const groups = await Promise.all(
        ps.map(async (p) => ({ p, rows: await request<Task[]>("/projects/" + encodeURIComponent(p.id) + "/tasks") }))
      );
      setTasks(groups.flatMap((g) => g.rows.map((t) => ({ ...t, project_name: g.p.name }))));
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务加载失败");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const rows = useMemo(() => {
    const q = query.trim().toLocaleLowerCase();
    return tasks
      .filter((t) =>
        (project === "all" || t.project_id === project) &&
        (status === "all" || t.status === status) &&
        (risk === "all" || t.risk === risk) &&
        (!q || [t.title, t.instruction, t.project_name, t.id].some((v) => v.toLocaleLowerCase().includes(q)))
      )
      .sort((a, b) => {
        const rank = (t: Row) => t.status === "waiting_approval" ? 0 : t.status === "submitted" ? 1 : t.status === "blocked" ? 2 : 3;
        return rank(a) - rank(b) || b.priority - a.priority;
      });
  }, [tasks, project, status, risk, query]);

  const counts = {
    all: tasks.length,
    approval: tasks.filter((t) => t.status === "waiting_approval").length,
    review: tasks.filter((t) => t.status === "submitted").length,
    blocked: tasks.filter((t) => t.status === "blocked").length
  };

  const preview = async (t: Row, action: "authorize_task" | "review_task", accepted = true) => {
    let reason = "";
    if (action === "review_task" && !accepted) {
      const entered = await dialog.prompt({
        title: "退回任务返工",
        message: `将任务「${t.title}」退回执行者修改并重新执行。`,
        label: "退回原因",
        placeholder: "请填写具体需要修改或补充验收的内容…",
        required: true,
        minLength: 1,
        multiline: true,
        confirmText: "生成退回预览"
      });
      if (entered === null) return;
      reason = entered;
    }
    setSaving(true);
    setError("");
    setPlan(null);
    try {
      const body = action === "authorize_task"
        ? { task_id: t.id, action }
        : accepted
          ? { task_id: t.id, action, accepted: true }
          : { task_id: t.id, action, accepted: false, reason };
      const generated = await request<Plan>("/command/plans", { method: "POST", body: JSON.stringify(body) });
      setPlan({ ...generated, parameters: generated.parameters || (action === "review_task" ? { accepted, reason } : undefined) });
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法生成命令预览");
    } finally {
      setSaving(false);
    }
  };

  const confirmPlan = async () => {
    if (!plan) return;
    setSaving(true);
    setError("");
    try {
      await request<Plan>("/command/plans/" + encodeURIComponent(plan.id) + "/confirm", { method: "POST", body: "{}" });
      setPlan(null);
      await load();
    } catch (e) {
      setError(e instanceof Error ? e.message : "确认失败");
    } finally {
      setSaving(false);
    }
  };

  const dispatchNow = async () => {
    const targets = projects.filter((p) => p.status === "active" && (project === "all" || p.id === project));
    if (!targets.length) {
      setError("当前筛选范围内没有处于活动状态的项目。");
      return;
    }
    const scope = project === "all" ? "全部活动项目" : "当前项目";
    const confirmed = await dialog.confirm({
      title: "执行任务调度",
      message: `确认对${scope}执行一次任务调度？\n\n只会路由到最近 180 秒内心跳且处于 idle 的 Runtime。调度只设置目标 Agent，不会远程启动进程；Runner 仍需自行轮询并领取任务。`,
      confirmText: "立即调度"
    });
    if (!confirmed) return;
    setDispatching(true);
    setError("");
    setDispatchNotice(null);
    try {
      const settled = await Promise.allSettled(
        targets.map((p) =>
          request<DispatchResponse>("/projects/" + encodeURIComponent(p.id) + "/schedule", {
            method: "POST",
            body: JSON.stringify({ limit: 20, heartbeat_ttl_seconds: 180 })
          })
        )
      );
      const routes: Array<DispatchRoute & { project_name: string; task_title: string; agent_name: string }> = [];
      const errors: string[] = [];
      settled.forEach((result, index) => {
        if (result.status === "rejected") {
          const reason = result.reason;
          errors.push(targets[index].name + "：" + (reason instanceof Error ? reason.message : "调度失败"));
          return;
        }
        result.value.routes.forEach((route) => {
          routes.push({
            ...route,
            project_name: targets[index].name,
            task_title: tasks.find((task) => task.id === route.task_id)?.title || route.task_id,
            agent_name: agents.find((agent) => agent.id === route.agent_id)?.name || route.agent_id
          });
        });
      });
      setDispatchNotice({ routes, errors, project_count: targets.length });
      await load();
    } finally {
      setDispatching(false);
    }
  };

  const planActionLabel = plan?.action === "authorize_task"
    ? "授权执行"
    : plan?.parameters?.accepted === false
      ? `退回返工（${plan.parameters?.reason || "未填原因"}）`
      : "验收通过";

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>任务中心</h1><p>跨项目汇总任务状态；授权、验收和任务调度均由服务端治理。</p></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void load()} disabled={loading || dispatching}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => void dispatchNow()} disabled={loading || dispatching || saving}><GitBranch size={15} />{dispatching ? "正在调度…" : "立即调度"}</button>
        </div>
      </div>

      {dispatchNotice && (
        <section className="panel dispatch-notice">
          <div className="dispatch-notice-head">
            <div><strong>本轮调度结果</strong><p>扫描 {dispatchNotice.project_count} 个活动项目，成功路由 {dispatchNotice.routes.length} 个任务。</p></div>
            <button className="icon-button" onClick={() => setDispatchNotice(null)} aria-label="关闭调度结果"><X size={15} /></button>
          </div>
          {dispatchNotice.routes.length > 0 ? (
            <div className="dispatch-route-list">
              {dispatchNotice.routes.slice(0, 12).map((route, index) => (
                <div className="dispatch-route-row" key={route.task_id}>
                  <span className="dispatch-route-index">{index + 1}</span>
                  <div><strong>{route.task_title}</strong><small>{route.project_name}</small></div>
                  <span className="dispatch-route-arrow">→</span>
                  <div><strong>{route.agent_name}</strong><small>{route.agent_id.slice(0, 8)}</small></div>
                  <span className="status-tag tag-green">已路由</span>
                </div>
              ))}
            </div>
          ) : (
            <div className="dispatch-empty">本轮没有任务被路由。请检查 Runner 是否在线且处于 idle、智能体 能力是否已核验，以及任务是否处于待执行状态。</div>
          )}
          {dispatchNotice.routes.length > 12 && <p className="form-hint">另有 {dispatchNotice.routes.length - 12} 个路由结果未在此处展开，可在任务列表查看。</p>}
          {dispatchNotice.errors.length > 0 && <div className="dispatch-errors">{dispatchNotice.errors.map((item, index) => <p key={index}><AlertCircle size={13} />{item}</p>)}</div>}
          <p className="dispatch-disclaimer">调度只写入目标 智能体 和审计事件，不会启动远程进程。Runner 通过自己的 运行实例 凭据轮询队列并领取任务。</p>
        </section>
      )}

      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={14} /></button></div>}

      <div className="stat-grid task-center-stats">
        <div className="stat-card"><div className="stat-icon tone-violet"><ClipboardCheck size={18} /></div><div className="stat-copy"><span>全部任务</span><strong>{counts.all}</strong><small>所有可访问项目</small></div></div>
        <div className="stat-card"><div className="stat-icon tone-amber"><ShieldCheck size={18} /></div><div className="stat-copy"><span>等待授权</span><strong>{counts.approval}</strong><small>需要所有者审批</small></div></div>
        <div className="stat-card"><div className="stat-icon tone-blue"><CheckCircle2 size={18} /></div><div className="stat-copy"><span>等待验收</span><strong>{counts.review}</strong><small>等待所有者验收</small></div></div>
        <div className="stat-card"><div className="stat-icon tone-green"><AlertCircle size={18} /></div><div className="stat-copy"><span>阻塞任务</span><strong>{counts.blocked}</strong><small>需要排查原因</small></div></div>
      </div>

      <section className="panel task-center-panel">
        <div className="panel-heading"><div className="panel-title"><Filter size={17} /><h3>筛选任务</h3></div><span className="muted-inline">{rows.length} 条结果</span></div>
        <div className="task-filters">
          <label>项目<select value={project} onChange={(e) => setProject(e.target.value)}><option value="all">全部项目</option>{projects.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
          <label>状态<select value={status} onChange={(e) => setStatus(e.target.value)}><option value="all">全部状态</option>{Object.entries(labels).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select></label>
          <label>风险等级<select value={risk} onChange={(e) => setRisk(e.target.value)}><option value="all">全部等级</option>{Object.entries(risks).map(([k, v]) => <option key={k} value={k}>{k} · {v}</option>)}</select></label>
          <label>关键词<input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="任务名称、项目或 ID" /></label>
        </div>
      </section>

      <section className="panel task-center-panel">
        <div className="panel-heading"><div className="panel-title"><ClipboardCheck size={17} /><h3>任务列表</h3></div><span className="muted-inline">待授权、待验收和阻塞任务优先</span></div>
        {loading ? (
          <div className="panel-loading"><span className="spinner" />正在汇总项目任务…</div>
        ) : rows.length ? (
          <div className="task-center-list">
            {rows.map((t) => (
              <article className="task-center-row" key={t.id}>
                <div className="task-center-main">
                  <div className="task-center-title">
                    <strong>{t.title}</strong>
                    <span className={"status-tag " + (tones[t.status] || "tag-neutral")}>{labels[t.status] || t.status}</span>
                    <span className="status-tag tag-neutral">{t.risk}</span>
                  </div>
                  <p>{t.project_name} · 优先级 {t.priority} · {getUiLabel(t.sensitivity)}</p>
                  <small>{t.result_summary || t.failure_reason || t.instruction}</small>
                  <small>任务 ID：{t.id}</small>
                </div>
                <div className="task-center-actions">
                  <button className="secondary-button compact" onClick={() => setDetailSelection({ id: t.id, projectId: t.project_id, projectName: t.project_name })}><Eye size={14} />详情</button>
                  {t.status === "waiting_approval" && <button className="secondary-button compact" disabled={saving} onClick={() => void preview(t, "authorize_task")}><ShieldCheck size={14} />预览授权</button>}
                  {t.status === "submitted" && (
                    <>
                      <button className="secondary-button compact" disabled={saving} onClick={() => void preview(t, "review_task", true)}><CheckCircle2 size={14} />预览验收</button>
                      <button className="secondary-button compact" disabled={saving} onClick={() => void preview(t, "review_task", false)}><RotateCcw size={14} />退回返工</button>
                    </>
                  )}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="empty-state"><ClipboardCheck size={24} /><strong>没有符合条件的任务</strong><p>调整筛选条件，或先在项目中创建任务。</p></div>
        )}
      </section>

      {detailSelection && <TaskDetailPanel selection={detailSelection} isOwner={user?.kind === "owner"} onClose={() => setDetailSelection(null)} onChanged={() => void load()} />}

      {plan && (
        <div className="modal-backdrop">
          <section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title">
            <div className="panel-heading"><div className="panel-title"><ShieldCheck size={18} /><h3 id="command-title">确认任务操作</h3></div><button className="icon-button" onClick={() => setPlan(null)}><X size={16} /></button></div>
            <p>服务端已生成限时命令预览。请核对操作，再明确确认。</p>
            <div className="command-summary">
              <strong>{plan.task_title || tasks.find((t) => t.id === plan.task_id)?.title || "任务"}</strong>
              <span>操作：{planActionLabel}</span>
              <span>预览状态：{getUiLabel(plan.status)}</span>
              <span>任务版本：{plan.expected_version}</span>
              <span>有效期：{time(plan.expires_at)}</span>
            </div>
            <div className="form-actions">
              <button className="secondary-button" onClick={() => setPlan(null)} disabled={saving}>取消</button>
              <button className="primary-button" onClick={() => void confirmPlan()} disabled={saving}>{saving ? "正在确认…" : "确认并执行"}</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

