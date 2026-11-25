import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useState, type FormEvent } from "react";
import {
  Activity, AlertCircle, Bell, Check, CheckCheck, Clock3, FlaskConical,
  FolderKanban, Gauge, Play, Plus, RefreshCw, ShieldAlert, TrendingUp, X
} from "lucide-react";
import { request } from "../api/client";
import type { DailyBriefing, PortfolioMetrics, Project } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";

interface NotificationItem {
  id: string;
  project_id: string | null;
  level: "urgent" | "important" | "digest" | string;
  title: string;
  body: string;
  source_kind: string;
  source_id: string;
  read: boolean;
  created_at: string;
}
interface Experiment {
  id: string;
  project_id: string;
  target_kind: "skill" | "context_policy" | "workflow_rule";
  target_id: string;
  hypothesis: string;
  baseline_score: number | null;
  candidate_score: number | null;
  sample_count: number | null;
  status: string;
  results: Record<string, unknown> | null;
  owner_note: string;
  created_at: string;
  resolved_at: string | null;
}
interface ScheduleResult {
  projects_evaluated: number;
  total_routes: number;
  per_project: Array<{ project_id: string; project_name: string; priority: number; routes: unknown[] }>;
}

const dateText = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN", { hour12: false }) : "—";
const percent = (value: number | null | undefined) => value == null ? "暂无数据" : Math.round(value * 100) + "%";
const levelLabel: Record<string, string> = { urgent: "紧急", important: "重要", digest: "摘要" };
const statusLabel: Record<string, string> = { proposed: "待评估", evaluated: "已评估", promoted: "已采纳", rolled_back: "已回滚" };

export default function PortfolioPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [metrics, setMetrics] = useState<PortfolioMetrics | null>(null);
  const [briefing, setBriefing] = useState<DailyBriefing | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [experiments, setExperiments] = useState<Experiment[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [scheduleResult, setScheduleResult] = useState<ScheduleResult | null>(null);
  const [showExperimentForm, setShowExperimentForm] = useState(false);
  const [targetKind, setTargetKind] = useState<Experiment["target_kind"]>("workflow_rule");
  const [targetId, setTargetId] = useState("");
  const [hypothesis, setHypothesis] = useState("");
  const [runDrafts, setRunDrafts] = useState<Record<string, { baseline: string; candidate: string; samples: string; notes: string }>>({});
  const dialog = useDialog();

  const loadOverview = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [projectRows, portfolio, daily, inbox] = await Promise.all([
        request<Project[]>("/projects"),
        request<PortfolioMetrics>("/portfolio/metrics"),
        request<DailyBriefing>("/briefing/daily"),
        request<NotificationItem[]>("/notifications?limit=50")
      ]);
      setProjects(projectRows);
      setMetrics(portfolio);
      setBriefing(daily);
      setNotifications(inbox);
      if (!projectRows.some((item) => item.id === selectedId)) setSelectedId(projectRows[0]?.id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "运营分析数据读取失败");
    } finally {
      setLoading(false);
    }
  }, [selectedId]);

  const loadExperiments = useCallback(async (projectId: string) => {
    try {
      setExperiments(await request<Experiment[]>("/projects/" + encodeURIComponent(projectId) + "/experiments"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "实验记录读取失败");
    }
  }, []);

  useEffect(() => { void loadOverview(); }, []);
  useEffect(() => { if (selectedId) void loadExperiments(selectedId); else setExperiments([]); }, [selectedId, loadExperiments]);

  const markRead = async (id: string) => {
    try {
      await request<NotificationItem>("/notifications/" + encodeURIComponent(id) + "/read", { method: "POST" });
      setNotifications((items) => items.map((item) => item.id === id ? { ...item, read: true } : item));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "标记通知失败"); }
  };

  const markAllRead = async () => {
    setSaving(true);
    try {
      await request<{ marked_read: number }>("/notifications/read-all", { method: "POST" });
      setNotifications((items) => items.map((item) => ({ ...item, read: true })));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "批量标记失败"); }
    finally { setSaving(false); }
  };

  const createExperiment = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    setError("");
    try {
      await request<Experiment>("/projects/" + encodeURIComponent(selectedId) + "/experiments", {
        method: "POST",
        body: JSON.stringify({ target_kind: targetKind, target_id: targetId.trim(), hypothesis: hypothesis.trim() })
      });
      setTargetId("");
      setHypothesis("");
      setShowExperimentForm(false);
      await loadExperiments(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建实验失败"); }
    finally { setSaving(false); }
  };

  const recordRun = async (experiment: Experiment) => {
    const draft = runDrafts[experiment.id] || { baseline: "", candidate: "", samples: "3", notes: "" };
    setSaving(true);
    setError("");
    try {
      await request<Experiment>("/experiments/" + encodeURIComponent(experiment.id) + "/record-run", {
        method: "POST",
        body: JSON.stringify({
          baseline_score: Number(draft.baseline), candidate_score: Number(draft.candidate),
          sample_count: Number(draft.samples), notes: draft.notes
        })
      });
      await loadExperiments(experiment.project_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "记录实验结果失败"); }
    finally { setSaving(false); }
  };

  const resolveExperiment = async (experiment: Experiment, action: "promote" | "rollback") => {
    const note = await dialog.prompt({
      title: action === "promote" ? "采纳实验结果" : "回滚实验",
      message: `实验假设：${experiment.hypothesis}`,
      label: action === "promote" ? "采纳说明（至少 5 个字符）" : "回滚原因（至少 5 个字符）",
      multiline: true,
      required: true,
      minLength: 5,
      confirmText: action === "promote" ? "确认采纳" : "确认回滚",
    });
    if (!note) return;
    setSaving(true);
    try {
      await request<Experiment>("/experiments/" + encodeURIComponent(experiment.id) + "/" + action, {
        method: "POST", body: JSON.stringify({ note })
      });
      await loadExperiments(experiment.project_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "实验状态更新失败"); }
    finally { setSaving(false); }
  };

  const runSchedule = async () => {
    const confirmed = await dialog.confirm({
      title: "执行跨项目公平调度",
      message: "将按项目优先级与等待时间执行一次公平调度，并可能为智能体分配任务。是否继续？",
      confirmText: "确认执行调度",
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      setScheduleResult(await request<ScheduleResult>("/portfolio/schedule", {
        method: "POST", body: JSON.stringify({ limit_per_project: 10, heartbeat_ttl_seconds: 180 })
      }));
      await loadOverview();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "调度失败"); }
    finally { setSaving(false); }
  };

  const unread = notifications.filter((item) => !item.read).length;

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>运营分析 总览</h1><p>跨项目运行指标、每日简报、通知收件箱与受控实验。</p></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void loadOverview()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => void runSchedule()} disabled={saving}><Play size={15} />执行公平调度</button>
        </div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      {loading && !metrics ? <div className="panel-loading"><div className="spinner" />正在汇总 运营分析 数据…</div> : (
        <>
          <div className="stat-grid portfolio-stat-grid">
            <div className="stat-card"><div className="stat-icon tone-violet"><FolderKanbanIcon /></div><div className="stat-copy"><span>项目总数</span><strong>{metrics?.project_count ?? 0}</strong><small>全部项目空间</small></div></div>
            <div className="stat-card"><div className="stat-icon tone-blue"><Activity size={18} /></div><div className="stat-copy"><span>任务总量</span><strong>{metrics?.total_tasks ?? 0}</strong><small>跨项目累计</small></div></div>
            <div className="stat-card"><div className="stat-icon tone-green"><Check size={18} /></div><div className="stat-copy"><span>已完成任务</span><strong>{metrics?.completed_count ?? 0}</strong><small>{metrics?.total_tasks ? percent((metrics.completed_count || 0) / metrics.total_tasks) : "暂无数据"}</small></div></div>
            <div className="stat-card"><div className="stat-icon tone-amber"><ShieldAlert size={18} /></div><div className="stat-copy"><span>阻塞任务</span><strong>{metrics?.blocked_count ?? 0}</strong><small>需要排查原因</small></div></div>
            <div className="stat-card"><div className="stat-icon tone-violet"><Bell size={18} /></div><div className="stat-copy"><span>未读通知</span><strong>{unread}</strong><small>其中紧急 {briefing?.unread_urgent_notifications ?? 0} 条</small></div></div>
          </div>

          <div className="portfolio-grid">
            <section className="panel">
              <div className="panel-heading"><div className="panel-title"><Gauge size={17} /><h3>项目运行表现</h3></div><span className="muted-inline">按优先级排序</span></div>
              <div className="portfolio-project-list">
                {(metrics?.projects || []).map((item) => {
                  const project = projects.find((row) => row.id === item.project_id);
                  const rate = item.total_tasks ? item.completed_count / item.total_tasks : 0;
                  return <div className="portfolio-project-row" key={item.project_id}>
                    <div className="portfolio-project-main"><strong>{project?.name || item.project_id.slice(0, 8)}</strong><small>{item.completed_count}/{item.total_tasks} 项完成 · {item.blocked_count} 项阻塞</small></div>
                    <div className="portfolio-progress"><span style={{ width: Math.round(rate * 100) + "%" }} /></div>
                    <strong className="portfolio-rate">{Math.round(rate * 100)}%</strong>
                  </div>;
                })}
                {!metrics?.projects.length && <div className="empty-mini">尚无项目指标</div>}
              </div>
            </section>

            <section className="panel">
              <div className="panel-heading"><div className="panel-title"><Clock3 size={17} /><h3>每日简报</h3></div><span className="muted-inline">{dateText(briefing?.generated_at)}</span></div>
              <div className="briefing-mini-grid">
                <div><strong>{briefing?.enabled_agents ?? 0}</strong><small>已启用 智能体</small></div>
                <div><strong>{briefing?.pending_r3_tasks.length ?? 0}</strong><small>高风险待授权</small></div>
                <div><strong>{briefing?.pending_review_tasks.length ?? 0}</strong><small>待验收</small></div>
                <div><strong>{briefing?.blocked_tasks.length ?? 0}</strong><small>阻塞任务</small></div>
              </div>
              <div className="briefing-attention">
                {(briefing?.pending_r3_tasks || []).slice(0, 3).map((task) => <div key={task.task_id}><span className="status-tag tag-red">待授权</span><span>{task.title}</span></div>)}
                {(briefing?.pending_review_tasks || []).slice(0, 3).map((task) => <div key={task.task_id}><span className="status-tag tag-blue">待验收</span><span>{task.title}</span></div>)}
                {(briefing?.blocked_tasks || []).slice(0, 3).map((task) => <div key={task.task_id}><span className="status-tag tag-neutral">已阻塞</span><span>{task.title}</span></div>)}
                {!(briefing?.pending_r3_tasks.length || briefing?.pending_review_tasks.length || briefing?.blocked_tasks.length) && <div className="all-clear"><CheckCheck size={18} /><strong>当前没有待处理事项</strong></div>}
              </div>
            </section>
          </div>

          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><Bell size={17} /><h3>通知收件箱</h3><span className="count-chip">{unread}</span></div><button className="text-button" onClick={() => void markAllRead()} disabled={saving || unread === 0}>全部标为已读</button></div>
            <div className="notification-list">
              {notifications.map((item) => <div className={"notification-row " + (item.read ? "is-read" : "")} key={item.id}>
                <span className={"notification-dot level-" + item.level} />
                <div className="notification-copy"><div><strong>{item.title}</strong><span className={"status-tag " + (item.level === "urgent" ? "tag-red" : item.level === "important" ? "tag-blue" : "tag-neutral")}>{levelLabel[item.level] || getUiLabel(item.level)}</span></div><p>{item.body}</p><small>{dateText(item.created_at)} · {item.source_kind}</small></div>
                {!item.read && <button className="icon-button" title="标记已读" onClick={() => void markRead(item.id)}><Check size={16} /></button>}
              </div>)}
              {!notifications.length && <div className="empty-state"><Bell size={24} /><strong>收件箱为空</strong><p>系统产生的审批、任务和运行通知会出现在这里。</p></div>}
            </div>
          </section>

          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><FlaskConical size={17} /><h3>实验与回归门禁</h3></div><button className="primary-button compact" onClick={() => setShowExperimentForm((value) => !value)} disabled={!selectedId}><Plus size={15} />新建实验</button></div>
            <div className="project-toolbar"><ProjectSelect projects={projects} value={selectedId} onChange={setSelectedId} /><span className="muted-inline">实验结果必须满足候选分数不低于基线且样本数不少于 3 才能采纳。</span></div>
            {showExperimentForm && <form className="form-panel embedded-form" onSubmit={createExperiment}>
              <div className="form-grid three">
                <label>实验对象类型<select value={targetKind} onChange={(event) => setTargetKind(event.target.value as Experiment["target_kind"])}><option value="workflow_rule">工作流规则</option><option value="skill">智能体技能</option><option value="context_policy">上下文策略</option></select></label>
                <label>对象 ID<input value={targetId} onChange={(event) => setTargetId(event.target.value)} required /></label>
                <label>实验假设<input value={hypothesis} onChange={(event) => setHypothesis(event.target.value)} required minLength={5} maxLength={3000} /></label>
              </div>
              <div className="form-actions"><button className="primary-button compact" disabled={saving}>创建实验</button></div>
            </form>}
            <div className="experiment-list">
              {experiments.map((experiment) => {
                const draft = runDrafts[experiment.id] || { baseline: "", candidate: "", samples: "3", notes: "" };
                const setDraft = (key: keyof typeof draft, value: string) => setRunDrafts((current) => ({ ...current, [experiment.id]: { ...draft, [key]: value } }));
                return <article className="experiment-card" key={experiment.id}>
                  <div className="experiment-card-head"><div><strong>{experiment.hypothesis}</strong><small>{experiment.target_kind} · {experiment.target_id}</small></div><span className={"status-tag " + (experiment.status === "promoted" ? "tag-green" : experiment.status === "rolled_back" ? "tag-neutral" : experiment.status === "evaluated" ? "tag-blue" : "tag-amber")}>{statusLabel[experiment.status] || getUiLabel(experiment.status)}</span></div>
                  {experiment.status === "proposed" && <div className="experiment-run-form">
                    <label>基线分数<input type="number" min="0" max="100" value={draft.baseline} onChange={(event) => setDraft("baseline", event.target.value)} /></label>
                    <label>候选分数<input type="number" min="0" max="100" value={draft.candidate} onChange={(event) => setDraft("candidate", event.target.value)} /></label>
                    <label>样本数<input type="number" min="1" max="10000" value={draft.samples} onChange={(event) => setDraft("samples", event.target.value)} /></label>
                    <label className="experiment-notes">实验备注<input value={draft.notes} onChange={(event) => setDraft("notes", event.target.value)} /></label>
                    <button className="secondary-button compact" disabled={saving || draft.baseline === "" || draft.candidate === ""} onClick={() => void recordRun(experiment)}>记录评估</button>
                  </div>}
                  {experiment.status === "evaluated" && <div className="experiment-result"><span>基线 {experiment.baseline_score} 分</span><span>候选 {experiment.candidate_score} 分</span><span>样本 {experiment.sample_count}</span><strong>变化 {Number(experiment.results?.delta ?? 0) > 0 ? "+" : ""}{String(experiment.results?.delta ?? 0)}</strong></div>}
                  {experiment.status === "evaluated" && <div className="experiment-actions"><button className="primary-button compact" disabled={saving || !experiment.results?.passed_regression_gate} onClick={() => void resolveExperiment(experiment, "promote")}>采纳实验</button><button className="secondary-button compact" disabled={saving} onClick={() => void resolveExperiment(experiment, "rollback")}>回滚</button></div>}
                  {experiment.owner_note && <p className="form-hint">所有者记录：{experiment.owner_note}</p>}
                </article>;
              })}
              {!experiments.length && <div className="empty-state"><FlaskConical size={24} /><strong>该项目还没有实验</strong><p>创建实验后记录基线、候选分数和样本数，再由所有者决定是否采纳。</p></div>}
            </div>
          </section>
          {scheduleResult && <section className="panel"><div className="panel-heading"><div className="panel-title"><TrendingUp size={17} /><h3>最近一次公平调度结果</h3></div><span className="status-tag tag-green">已执行</span></div><div className="briefing-mini-grid"><div><strong>{scheduleResult.projects_evaluated}</strong><small>评估项目</small></div><div><strong>{scheduleResult.total_routes}</strong><small>路由任务数</small></div></div>{scheduleResult.per_project.map((item) => <div className="schedule-row" key={item.project_id}><strong>{item.project_name}</strong><span>优先级 {item.priority}</span><span>{item.routes.length} 条路由</span></div>)}</section>}
        </>
      )}
    </div>
  );
}

function FolderKanbanIcon() {
  return <FolderKanban size={18} />;
}
