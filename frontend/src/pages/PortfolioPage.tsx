import { ErrorNotice, LoadingState } from "../components/PageFeedback";
import { Link } from "react-router-dom";
import ListPagination, { useListPage } from "../components/ListPagination";
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
  const [projectSort, setProjectSort] = useState("priority");
  const [projectQuery, setProjectQuery] = useState("");
  const projectRows = (metrics?.projects || []).filter(row => (projects.find(p => p.id === row.project_id)?.name || row.project_id).toLocaleLowerCase().includes(projectQuery.trim().toLocaleLowerCase())).slice().sort((a,b) => projectSort === "blocked" ? b.blocked_count-a.blocked_count : projectSort === "completion" ? (b.total_tasks ? b.completed_count/b.total_tasks : -1) - (a.total_tasks ? a.completed_count/a.total_tasks : -1) : 0);
  const projectPage = useListPage(projectRows, JSON.stringify([projectSort,projectQuery]));
  const [briefing, setBriefing] = useState<DailyBriefing | null>(null);
  const [notifications, setNotifications] = useState<NotificationItem[]>([]);
  const [notificationFilter, setNotificationFilter] = useState("all");
  const [notificationQuery, setNotificationQuery] = useState("");
  const [notificationProject, setNotificationProject] = useState("all");
  const visibleNotifications = notifications.filter(item => (notificationFilter === "all" || (notificationFilter === "unread" ? !item.read : item.level === "urgent")) && (notificationProject === "all" || (notificationProject === "system" ? !item.project_id : item.project_id === notificationProject)) && [item.title,item.body,item.source_kind].join(" ").toLocaleLowerCase().includes(notificationQuery.trim().toLocaleLowerCase()));
  const notificationPage = useListPage(visibleNotifications, JSON.stringify([notificationFilter,notificationQuery,notificationProject]));
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
        <div><h1>运营分析</h1></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void loadOverview()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => void runSchedule()} disabled={saving}><Play size={15} />执行公平调度</button>
        </div>
      </div>
      <ErrorNotice message={error} onDismiss={() => setError("")} />
      {loading && !metrics ? <LoadingState label="正在汇总运营数据…"/> : (
        <>
          <div className="stat-grid portfolio-stat-grid">
            <div className="stat-card"><div className="stat-icon tone-violet"><FolderKanbanIcon /></div><div className="stat-copy"><span>项目总数</span><strong>{metrics?.project_count ?? 0}</strong></div></div>
            <div className="stat-card"><div className="stat-icon tone-blue"><Activity size={18} /></div><div className="stat-copy"><span>任务总量</span><strong>{metrics?.total_tasks ?? 0}</strong></div></div>
            <div className="stat-card"><div className="stat-icon tone-green"><Check size={18} /></div><div className="stat-copy"><span>已完成任务</span><strong>{metrics?.completed_count ?? 0}</strong><small>{metrics?.total_tasks ? percent((metrics.completed_count || 0) / metrics.total_tasks) : "暂无数据"}</small></div></div>
            <div className="stat-card"><div className="stat-icon tone-amber"><ShieldAlert size={18} /></div><div className="stat-copy"><span>阻塞任务</span><strong>{metrics?.blocked_count ?? 0}</strong></div></div>
            <div className="stat-card"><div className="stat-icon tone-violet"><Bell size={18} /></div><div className="stat-copy"><span>未读通知</span><strong>{unread}</strong><small>其中紧急 {briefing?.unread_urgent_notifications ?? 0} 条</small></div></div>
          </div>

          {metrics && <section className="panel portfolio-distribution"><div className="panel-heading"><div className="panel-title"><Activity size={17}/><h3>任务分布</h3></div><span className="muted-inline">共 {metrics.total_tasks} 项</span></div>
          {metrics.total_tasks > 0 ? <><div className="portfolio-distribution-bar" role="img" aria-label={`已完成 ${metrics.completed_count}，已阻塞 ${metrics.blocked_count}，其他 ${Math.max(0,metrics.total_tasks-metrics.completed_count-metrics.blocked_count)}`}>
            <span className="distribution-completed" style={{flexGrow: Math.max(0,metrics.completed_count)}}/><span className="distribution-blocked" style={{flexGrow: Math.max(0,metrics.blocked_count)}}/><span className="distribution-other" style={{flexGrow: Math.max(0,metrics.total_tasks-metrics.completed_count-metrics.blocked_count)}}/>
          </div><div className="distribution-legend"><Link to="/tasks?status=completed"><i className="distribution-completed"/>已完成 <strong>{metrics.completed_count}</strong></Link><Link to="/tasks?status=blocked"><i className="distribution-blocked"/>已阻塞 <strong>{metrics.blocked_count}</strong></Link><span><i className="distribution-other"/>其他状态 <strong>{Math.max(0,metrics.total_tasks-metrics.completed_count-metrics.blocked_count)}</strong></span></div></> : <div className="empty-mini">暂无任务数据</div>}
          </section>}
          <div className="portfolio-grid">
            <section className="panel">
              <div className="panel-heading"><div className="panel-title"><Gauge size={17} /><h3>项目运行表现</h3></div><span className="muted-inline">{projectRows.length} 个项目</span></div>
              <div className="library-filter-bar"><label>项目<input aria-label="搜索运营项目" value={projectQuery} onChange={e => setProjectQuery(e.target.value)} placeholder="搜索项目名称"/></label><label>排序<select aria-label="项目指标排序" value={projectSort} onChange={e=>setProjectSort(e.target.value)}><option value="priority">默认优先级</option><option value="blocked">阻塞任务数</option><option value="completion">完成率</option></select></label></div>
              <div className="portfolio-project-list">
                {projectPage.rows.map((item) => {
                  const project = projects.find((row) => row.id === item.project_id);
                  const rate = item.total_tasks ? item.completed_count / item.total_tasks : 0;
                  return <div className="portfolio-project-row" key={item.project_id}>
                    <div className="portfolio-project-main"><Link className="portfolio-project-link" to={"/projects?project="+encodeURIComponent(item.project_id)}>{project?.name || item.project_id.slice(0, 8)}</Link><small>{item.completed_count}/{item.total_tasks} 项完成 · {item.blocked_count} 项阻塞</small></div>
                    <div className="portfolio-progress"><span style={{ width: Math.round(rate * 100) + "%" }} /></div>
                    <strong className="portfolio-rate">{item.total_tasks ? Math.round(rate * 100) + "%" : "—"}</strong>
                  </div>;
                })}
                {!projectRows.length && <div className="empty-mini">{metrics?.projects.length ? "没有符合条件的项目" : "尚无项目指标"}</div>}
              </div><ListPagination {...projectPage} label="运营项目分页" />
            </section>

            <section className="panel">
              <div className="panel-heading"><div className="panel-title"><Clock3 size={17} /><h3>每日简报</h3></div><span className="muted-inline">{dateText(briefing?.generated_at)}</span></div>
              <div className="briefing-mini-grid">
                <div><strong>{briefing?.enabled_agents ?? 0}</strong><small>已启用智能体</small></div>
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
            <div className="approval-switch" role="group" aria-label="通知筛选">{[{key:"all",label:"全部"},{key:"unread",label:"未读"},{key:"urgent",label:"紧急"}].map(item => <button type="button" key={item.key} aria-pressed={notificationFilter === item.key} className={notificationFilter === item.key ? "selected" : ""} onClick={() => setNotificationFilter(item.key)}>{item.label}<span>{item.key === "all" ? notifications.length : item.key === "unread" ? unread : notifications.filter(n => n.level === "urgent").length}</span></button>)}</div>
            <div className="library-filter-bar"><label>关键词<input aria-label="搜索通知" value={notificationQuery} onChange={e => setNotificationQuery(e.target.value)} placeholder="搜索通知内容" /></label><label>项目<select aria-label="通知所属项目" value={notificationProject} onChange={e => setNotificationProject(e.target.value)}><option value="all">全部项目</option><option value="system">系统通知</option>{projects.map(p => <option value={p.id} key={p.id}>{p.name}</option>)}</select></label><button type="button" className="text-button" onClick={() => {setNotificationQuery("");setNotificationProject("all");setNotificationFilter("all");}}>清除筛选</button></div><div className="notification-list">
              {notificationPage.rows.map((item) => <div className={"notification-row " + (item.read ? "is-read" : "")} key={item.id}>
                <span className={"notification-dot level-" + item.level} />
                <div className="notification-copy"><div><strong>{item.title}</strong><span className={"status-tag " + (item.level === "urgent" ? "tag-red" : item.level === "important" ? "tag-blue" : "tag-neutral")}>{levelLabel[item.level] || getUiLabel(item.level)}</span></div><p>{item.body}</p><small>{dateText(item.created_at)} · {projects.find(p => p.id === item.project_id)?.name || (item.project_id ? "项目通知" : "系统通知")}</small></div>
                {!item.read && <button className="icon-button" title="标记已读" onClick={() => void markRead(item.id)}><Check size={16} /></button>}
              </div>)}
              {!visibleNotifications.length && <div className="empty-state"><Bell size={24} /><strong>暂无此类通知</strong></div>}
            </div>
            <ListPagination {...notificationPage} label="通知分页" />
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
                  <div className="experiment-card-head"><div><strong>{experiment.hypothesis}</strong><small>{({workflow_rule:"工作流规则",skill:"技能",context_policy:"上下文策略"} as Record<string,string>)[experiment.target_kind] || experiment.target_kind} · {experiment.target_id}</small></div><span className={"status-tag " + (experiment.status === "promoted" ? "tag-green" : experiment.status === "rolled_back" ? "tag-neutral" : experiment.status === "evaluated" ? "tag-blue" : "tag-amber")}>{statusLabel[experiment.status] || getUiLabel(experiment.status)}</span></div>
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
          {scheduleResult && <section className="panel"><div className="panel-heading"><div className="panel-title"><TrendingUp size={17} /><h3>最近一次公平调度结果</h3></div><span className="status-tag tag-green">已执行</span></div><div className="briefing-mini-grid"><div><strong>{scheduleResult.projects_evaluated}</strong><small>评估项目</small></div><div><strong>{scheduleResult.total_routes}</strong><small>分配任务数</small></div></div>{scheduleResult.per_project.map((item) => <div className="schedule-row" key={item.project_id}><strong>{item.project_name}</strong><span>优先级 {item.priority}</span><span>{item.routes.length} 项分配</span></div>)}</section>}
        </>
      )}
    </div>
  );
}

function FolderKanbanIcon() {
  return <FolderKanban size={18} />;
}
