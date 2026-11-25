import { useScopedRequest } from "../utils/useScopedRequest";
import { dependencyLayers } from "../utils/dependencyLayers";
import ListPagination, { useListPage } from "../components/ListPagination";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle, ArrowUp, GitBranch, ListChecks, Pencil, Plus, RefreshCw,
  Trash2, Workflow, X, Zap
} from "lucide-react";
import { request } from "../api/client";
import type { Project } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";
import GoalPlanComposer from "../components/GoalPlanComposer";

interface GoalDependency {
  id: string;
  title: string;
  status: string;
}
interface Goal {
  id: string;
  project_id: string;
  title: string;
  success_criteria: string;
  priority: number;
  status: string;
  depends_on: GoalDependency[];
  dependencies_satisfied: boolean;
  task_count: number;
  completed_task_count: number;
  open_task_count: number;
  can_complete: boolean;
  completion_blockers: string[];
}
interface GoalBoard { project_id: string; goals: Goal[]; }
interface WorkflowRule {
  id: string;
  project_id: string;
  name: string;
  event_prefix: string;
  condition_status: string | null;
  action_kind: "create_task" | "notify_inbox";
  action_config: Record<string, unknown>;
  max_chain_depth: number;
  cooldown_seconds: number;
  enabled: boolean;
  triggered_count: number;
  last_triggered_at: string | null;
}
interface WorkflowRun {
  id: string;
  project_id: string;
  rule_id: string;
  event_id: number;
  correlation_id: string | null;
  chain_depth: number;
  status: string;
  created_task_id: string | null;
  detail: string;
  created_at: string;
}
const goalStatus: Record<string, string> = { active: "进行中", paused: "已暂停", completed: "已完成" };
const runStatus: Record<string, string> = { executed: "已执行", skipped: "已跳过", failed: "失败" };

export default function GoalsWorkflowPage() {
  const dialog = useDialog();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [board, setBoard] = useState<GoalBoard | null>(null);
  const [rules, setRules] = useState<WorkflowRule[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [runFilter, setRunFilter] = useState("all");
  const runPage = useListPage(runs.filter(run => runFilter === "all" || run.status === runFilter), JSON.stringify([selectedId,runFilter]));
  const goalGraph = useMemo(() => dependencyLayers(board?.goals || []), [board]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showGoalForm, setShowGoalForm] = useState(false);
  const [goalTitle, setGoalTitle] = useState("");
  const [goalCriteria, setGoalCriteria] = useState("");
  const [goalPriority, setGoalPriority] = useState("50");
  const [editingGoal, setEditingGoal] = useState<Goal | null>(null);
  const [editGoalTitle, setEditGoalTitle] = useState("");
  const [editGoalCriteria, setEditGoalCriteria] = useState("");
  const [editGoalPriority, setEditGoalPriority] = useState("50");
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleName, setRuleName] = useState("");
  const [eventPrefix, setEventPrefix] = useState("task.completed");
  const [actionKind, setActionKind] = useState<WorkflowRule["action_kind"]>("notify_inbox");
  const [actionTitle, setActionTitle] = useState("");
  const [maxDepth, setMaxDepth] = useState("3");
  const [cooldown, setCooldown] = useState("60");
  const [dependencySelections, setDependencySelections] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<"goals" | "rules">("goals");
  const [goalQuery, setGoalQuery] = useState("");
  const [goalFilter, setGoalFilter] = useState("all");
  const shownGoals = (board?.goals || []).filter(goal => (goalFilter === "all" || (goalFilter === "waiting" ? !goal.dependencies_satisfied && goal.status !== "completed" : goal.status === goalFilter)) && [goal.title,goal.success_criteria,goal.id].join(" ").toLocaleLowerCase().includes(goalQuery.trim().toLocaleLowerCase()));
  const [focusedGoal, setFocusedGoal] = useState("");
  const revealGoal = (id: string) => { setGoalQuery(""); setGoalFilter("all"); setFocusedGoal(id); };
  useEffect(() => { if (focusedGoal) { const node = document.getElementById("goal-card-" + focusedGoal); node?.scrollIntoView({block:"nearest"}); node?.focus({preventScroll:true}); } }, [focusedGoal, goalQuery, goalFilter]);
  useEffect(() => { setGoalQuery(""); setGoalFilter("all"); setFocusedGoal(""); }, [selectedId]);

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, []);

  const beginProjectRequest = useScopedRequest(selectedId);
  const resetProjectView = () => { setBoard(null); setRules([]); setRuns([]); setEditingGoal(null); setShowGoalForm(false); setShowRuleForm(false); setGoalTitle(""); setGoalCriteria(""); setGoalPriority("50"); setRuleName(""); setActionTitle(""); setEventPrefix("task.completed"); setActionKind("notify_inbox"); setMaxDepth("3"); setCooldown("60"); setDependencySelections({}); setRunFilter("all"); };
  const changeProject = (id: string) => { if (id === selectedId || saving) return; resetProjectView(); setLoading(true); setError(""); setSelectedId(id); };

  const loadProjectData = useCallback(async (projectId: string) => {
    const isCurrent = beginProjectRequest(projectId);
    if (!isCurrent()) return;
    setLoading(true);
    setError("");
    try {
      const [goalBoard, workflowRules, workflowRuns] = await Promise.all([
        request<GoalBoard>("/projects/" + encodeURIComponent(projectId) + "/goal-board"),
        request<WorkflowRule[]>("/projects/" + encodeURIComponent(projectId) + "/workflow-rules"),
        request<WorkflowRun[]>("/projects/" + encodeURIComponent(projectId) + "/workflow-runs?limit=50")
      ]);
      if (!isCurrent()) return;
      setBoard(goalBoard);
      setRules(workflowRules);
      setRuns(workflowRuns);
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "目标与工作流读取失败"); }
    finally { if (isCurrent()) setLoading(false); }
  }, [beginProjectRequest]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedId) void loadProjectData(selectedId); else { setBoard(null); setRules([]); setRuns([]); } }, [selectedId, loadProjectData]);

  const createGoal = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    try {
      await request("/projects/" + encodeURIComponent(selectedId) + "/goals", {
        method: "POST",
        body: JSON.stringify({ title: goalTitle.trim(), success_criteria: goalCriteria.trim(), priority: Number(goalPriority) })
      });
      setGoalTitle(""); setGoalCriteria(""); setGoalPriority("50"); setShowGoalForm(false);
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建目标失败"); }
    finally { setSaving(false); }
  };

  const changeGoalStatus = async (goal: Goal, status: "active" | "paused" | "completed") => {
    let note = "";
    if (status === "completed") {
      if (!goal.can_complete) {
        setError("当前目标尚不可完成：" + goal.completion_blockers.join("、"));
        return;
      }
      const input = await dialog.prompt({
        title: `完成目标「${goal.title}」`,
        message: "确认所有前置目标和关联任务已完成验收，并填写目标完成说明。",
        label: "完成说明（至少 5 个字符）",
        placeholder: "总结交付成果与验收结论…",
        required: true,
        minLength: 5,
        multiline: true,
        confirmText: "确认完成"
      });
      if (input === null) return;
      note = input;
    } else {
      const confirmed = await dialog.confirm({
        title: "更新目标状态",
        message: "确认将目标「" + goal.title + "」设置为" + goalStatus[status] + "？",
        confirmText: "确认更新"
      });
      if (!confirmed) return;
    }
    setSaving(true);
    try {
      await request("/goals/" + encodeURIComponent(goal.id) + "/status", {
        method: "POST", body: JSON.stringify({ status, note })
      });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "更新目标状态失败"); }
    finally { setSaving(false); }
  };

  const addDependency = async (goal: Goal) => {
    const predecessorId = dependencySelections[goal.id];
    if (!predecessorId) return;
    setSaving(true);
    try {
      await request("/goals/" + encodeURIComponent(goal.id) + "/dependencies", {
        method: "POST", body: JSON.stringify({ predecessor_id: predecessorId })
      });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "添加目标依赖失败"); }
    finally { setSaving(false); }
  };

  const removeDependency = async (goal: Goal, predecessorId: string) => {
    const confirmed = await dialog.confirm({
      title: "移除目标依赖",
      message: "确认移除这条前置目标依赖？",
      confirmText: "确认移除",
      tone: "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/goals/" + encodeURIComponent(goal.id) + "/dependencies/" + encodeURIComponent(predecessorId), { method: "DELETE" });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "移除目标依赖失败"); }
    finally { setSaving(false); }
  };

  const createRule = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    try {
      const actionConfig = actionKind === "create_task" ? { title: actionTitle.trim() } : {};
      await request<WorkflowRule>("/projects/" + encodeURIComponent(selectedId) + "/workflow-rules", {
        method: "POST",
        body: JSON.stringify({
          name: ruleName.trim(), event_prefix: eventPrefix.trim(), condition_status: null,
          action_kind: actionKind, action_config: actionConfig,
          max_chain_depth: Number(maxDepth), cooldown_seconds: Number(cooldown)
        })
      });
      setRuleName(""); setActionTitle(""); setShowRuleForm(false);
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建工作流规则失败"); }
    finally { setSaving(false); }
  };

  const toggleRule = async (rule: WorkflowRule) => {
    setSaving(true);
    try {
      await request<WorkflowRule>("/workflow-rules/" + encodeURIComponent(rule.id) + "/toggle", { method: "POST" });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "切换规则状态失败"); }
    finally { setSaving(false); }
  };

  const deleteRule = async (rule: WorkflowRule) => {
    const confirmed = await dialog.confirm({
      title: "删除工作流规则",
      message: `确认删除工作流规则「${rule.name}」？`,
      confirmText: "确认删除",
      tone: "danger"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/workflow-rules/" + encodeURIComponent(rule.id), { method: "DELETE" });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除规则失败"); }
    finally { setSaving(false); }
  };

  const startEditGoal = (goal: Goal) => {
    setEditingGoal(goal);
    setEditGoalTitle(goal.title);
    setEditGoalCriteria(goal.success_criteria);
    setEditGoalPriority(String(goal.priority));
  };

  const saveEditGoal = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingGoal || !editGoalTitle.trim() || !editGoalCriteria.trim()) return;
    setSaving(true);
    try {
      await request("/goals/" + encodeURIComponent(editingGoal.id), {
        method: "PATCH",
        body: JSON.stringify({
          title: editGoalTitle.trim(),
          success_criteria: editGoalCriteria.trim(),
          priority: Math.min(100, Math.max(0, Number(editGoalPriority) || 50))
        })
      });
      setEditingGoal(null);
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "修改目标失败"); }
    finally { setSaving(false); }
  };

  const deleteGoal = async (goal: Goal) => {
    const confirmed = await dialog.confirm({
      title: "删除目标",
      message: `确认删除目标「${goal.title}」？关联的非运行任务将保留但解除绑定。`,
      confirmText: "永久删除",
      tone: "danger"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/goals/" + encodeURIComponent(goal.id), { method: "DELETE" });
      if (editingGoal?.id === goal.id) setEditingGoal(null);
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除目标失败"); }
    finally { setSaving(false); }
  };

  const evaluateRules = async () => {
    const confirmed = await dialog.confirm({
      title: "评估事件工作流规则",
      message: "将评估该项目最近 50 条事件，并按已启用规则执行动作（可能创建任务或通知）。继续？",
      confirmText: "立即评估",
      tone: "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/projects/" + encodeURIComponent(selectedId) + "/workflow-rules/evaluate?limit=50", { method: "POST" });
      await loadProjectData(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "规则评估失败"); }
    finally { setSaving(false); }
  };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>目标与工作流</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadProjectData(selectedId)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      <section className="panel project-toolbar"><div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedId} onChange={changeProject} disabled={saving} /><span className="muted-inline">{board?.goals?.length ?? 0} 个目标 · {rules.length} 条规则 · {runs.length} 条运行记录</span></section>
      <div className="governance-tabs">
        <button className={tab === "goals" ? "selected" : ""} onClick={() => setTab("goals")}><GitBranch size={16} />目标依赖图 <span>{board?.goals?.length ?? 0}</span></button>
        <button className={tab === "rules" ? "selected" : ""} onClick={() => setTab("rules")}><Workflow size={16} />工作流规则 <span>{rules.length}</span></button>
      </div>
      {loading ? <div className="panel-loading"><div className="spinner" />正在读取目标和工作流…</div> : tab === "goals" ? (
        <div className="page-stack">
        <details className="panel maintenance-disclosure"><summary>智能目标规划</summary><GoalPlanComposer key={selectedId} projectId={selectedId} onConfirmed={()=>void loadProjectData(selectedId)} /></details>
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><GitBranch size={17} /><h3>目标与前置条件</h3></div><button className="primary-button compact" onClick={() => setShowGoalForm((value) => !value)} disabled={!selectedId}><Plus size={15} />新建目标</button></div>
          {showGoalForm && <form className="form-panel embedded-form" onSubmit={createGoal}>
            <label>目标名称<input value={goalTitle} onChange={(event) => setGoalTitle(event.target.value)} required maxLength={240} /></label>
            <label>成功标准<textarea rows={3} value={goalCriteria} onChange={(event) => setGoalCriteria(event.target.value)} required maxLength={5000} /></label>
            <label>优先级（0–100）<input type="number" min="0" max="100" value={goalPriority} onChange={(event) => setGoalPriority(event.target.value)} /></label>
            <div className="form-actions"><button className="primary-button compact" disabled={saving}>创建目标</button></div>
          </form>}
          {editingGoal && <form className="form-panel embedded-form" onSubmit={saveEditGoal}>
            <div className="panel-heading"><div className="panel-title"><Pencil size={15} /><h3>编辑目标：{editingGoal.title}</h3></div><button type="button" className="icon-button" onClick={() => setEditingGoal(null)}><X size={16} /></button></div>
            <div className="form-grid">
              <label>目标名称<input value={editGoalTitle} onChange={(e) => setEditGoalTitle(e.target.value)} required maxLength={240} /></label>
              <label>优先级（0–100）<input type="number" min="0" max="100" value={editGoalPriority} onChange={(e) => setEditGoalPriority(e.target.value)} /></label>
            </div>
            <label>成功标准<textarea rows={3} value={editGoalCriteria} onChange={(e) => setEditGoalCriteria(e.target.value)} required maxLength={5000} /></label>
            <div className="form-actions"><button type="button" className="secondary-button compact" onClick={() => setEditingGoal(null)}>取消</button><button className="primary-button compact" disabled={saving}>保存修改</button></div>
          </form>}
          {Boolean(board?.goals.length) && <section className="goal-layer-board" aria-label="目标依赖概览">
            <div className="goal-layer-heading"><strong>目标依赖顺序</strong><span className="muted-inline">按前置依赖分层，不代表执行状态</span></div>
            <div className="goal-layer-grid">{goalGraph.layers.map((layer,index) => <div className="goal-layer" key={index}><span className="goal-layer-label">第 {index+1} 层</span>{layer.map(goal => <button type="button" key={goal.id} className="goal-layer-node" onClick={() => revealGoal(goal.id)}><strong>{goal.title}</strong><span className={"status-tag " + (goal.status === "completed" ? "tag-green" : !goal.dependencies_satisfied ? "tag-amber" : "tag-neutral")}>{goal.status === "completed" ? "已完成" : !goal.dependencies_satisfied ? "等待前置目标" : goalStatus[goal.status] || goal.status}</span></button>)}</div>)}</div>
            {goalGraph.unresolved.length > 0 && <div className="inline-error">无法确定依赖顺序：{goalGraph.unresolved.map(goal=>goal.title).join("、")}</div>}
          </section>}
          <div className="library-filter-bar"><label>目标<input aria-label="搜索目标" value={goalQuery} onChange={e=>{setGoalQuery(e.target.value);setFocusedGoal("");}} placeholder="搜索目标名称或成功标准"/></label><label>状态<select aria-label="目标状态" value={goalFilter} onChange={e=>{setGoalFilter(e.target.value);setFocusedGoal("");}}><option value="all">全部状态</option><option value="active">进行中</option><option value="waiting">等待前置目标</option><option value="paused">已暂停</option><option value="completed">已完成</option></select></label><span className="muted-inline">{shownGoals.length} 个目标</span></div><div className="goal-board">
            {shownGoals.map((goal, index) => {
              const predecessorOptions = (board?.goals || []).filter((item) => item.id !== goal.id && !goal.depends_on.some((edge) => edge.id === item.id));
              const progress = goal.task_count ? Math.round(goal.completed_task_count / goal.task_count * 100) : 0;
              return <article tabIndex={-1} className={"goal-card" + (focusedGoal === goal.id ? " goal-card-focused" : "")} id={"goal-card-" + goal.id} key={goal.id}>
                <div className="goal-card-top"><span className="goal-number">{String(index + 1).padStart(2, "0")}</span><span className={"status-tag " + (goal.status === "completed" ? "tag-green" : goal.status === "paused" ? "tag-neutral" : "tag-blue")}>{goalStatus[goal.status] || goal.status}</span></div>
                <h3>{goal.title}</h3><details className="library-disclosure"><summary>成功标准</summary><p>{goal.success_criteria}</p></details>
                <div className="goal-progress-label"><span>关联任务 {goal.completed_task_count}/{goal.task_count}</span><strong>{progress}%</strong></div><div className="progress-track"><span style={{ width: progress + "%" }} /></div>
                <div className="goal-dependencies"><strong>前置目标</strong>{goal.depends_on.map((dependency) => <div className="goal-dependency" key={dependency.id}><ArrowUp size={13} /><span>{dependency.title}</span><span className={"status-tag " + (dependency.status === "completed" ? "tag-green" : "tag-neutral")}>{goalStatus[dependency.status] || dependency.status}</span><button className="icon-button" title="移除依赖" onClick={() => void removeDependency(goal, dependency.id)}><X size={14} /></button></div>)}{!goal.depends_on.length && <small>无前置目标</small>}
                  {goal.status !== "completed" && predecessorOptions.length > 0 && <div className="dependency-add"><select value={dependencySelections[goal.id] || ""} onChange={(event) => setDependencySelections((current) => ({ ...current, [goal.id]: event.target.value }))}><option value="">选择前置目标…</option>{predecessorOptions.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button className="secondary-button compact" disabled={!dependencySelections[goal.id] || saving} onClick={() => void addDependency(goal)}>添加</button></div>}
                </div>
                {goal.completion_blockers.length > 0 && <div className="goal-blockers">完成门槛：{goal.completion_blockers.map((item) => item === "prerequisite_goals_incomplete" ? "前置目标未完成" : "关联任务未全部验收").join("、")}</div>}
                <div className="goal-card-actions">{goal.status === "active" && <button className="text-button" onClick={() => void changeGoalStatus(goal, "paused")}>暂停</button>}{goal.status === "paused" && <button className="text-button" onClick={() => void changeGoalStatus(goal, "active")}>恢复</button>}{goal.status !== "completed" && <button className="secondary-button compact" disabled={!goal.can_complete || saving} onClick={() => void changeGoalStatus(goal, "completed")}>完成目标</button>}<button className="text-button" disabled={saving} onClick={() => startEditGoal(goal)}><Pencil size={13} />编辑</button><button className="text-button" disabled={saving} onClick={() => void deleteGoal(goal)}><Trash2 size={13} />删除</button></div>
              </article>;
            })}
            {!shownGoals.length && <div className="empty-state"><GitBranch size={25} /><strong>{board?.goals.length ? "没有符合条件的目标" : "尚未定义目标"}</strong></div>}
          </div>
        </section>
        </div>
      ) : (
        <div className="page-stack">
          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><Workflow size={17} /><h3>事件驱动工作流</h3></div><div className="heading-actions"><button className="secondary-button compact" onClick={() => void evaluateRules()} disabled={!selectedId || saving}><Zap size={14} />评估规则</button><button className="primary-button compact" onClick={() => setShowRuleForm((value) => !value)}><Plus size={14} />新建规则</button></div></div>
            {showRuleForm && <form className="form-panel embedded-form" onSubmit={createRule}>
              <div className="form-grid">
                <label>规则名称<input value={ruleName} onChange={(event) => setRuleName(event.target.value)} required maxLength={160} /></label>
                <label>事件前缀<input value={eventPrefix} onChange={(event) => setEventPrefix(event.target.value)} required pattern="[a-z0-9_.*-]+" placeholder="task.completed" /></label>
                <label>触发动作<select value={actionKind} onChange={(event) => setActionKind(event.target.value as WorkflowRule["action_kind"])}><option value="notify_inbox">发送通知</option><option value="create_task">创建跟进任务</option></select></label>
                {actionKind === "create_task" && <label>自动创建任务标题<input value={actionTitle} onChange={(event) => setActionTitle(event.target.value)} required /></label>}
                <label>最大链深度<input type="number" min="1" max="5" value={maxDepth} onChange={(event) => setMaxDepth(event.target.value)} /></label>
                <label>冷却时间（秒）<input type="number" min="0" max="86400" value={cooldown} onChange={(event) => setCooldown(event.target.value)} /></label>
              </div>
              <p className="form-hint">规则采用事件前缀匹配，并受最大链深度、冷却时间与重复事件去重保护。</p>
              <div className="form-actions"><button className="primary-button compact" disabled={saving}>保存规则</button></div>
            </form>}
            <div className="workflow-rule-list">
              {rules.map((rule) => <article className="workflow-rule-card" key={rule.id}>
                <div className="workflow-rule-icon"><Zap size={18} /></div><div className="workflow-rule-main"><div><strong>{rule.name}</strong><span className={"status-tag " + (rule.enabled ? "tag-green" : "tag-neutral")}>{rule.enabled ? "已启用" : "已停用"}</span></div><p><code>{rule.event_prefix}</code> → {rule.action_kind === "create_task" ? "创建跟进任务" : "发送收件箱通知"}</p><small>已触发 {rule.triggered_count} 次 · 最大链深度 {rule.max_chain_depth} · 冷却 {rule.cooldown_seconds}s</small></div><div className="flex-row-gap-sm"><button className="secondary-button compact" disabled={saving} onClick={() => void toggleRule(rule)}>{rule.enabled ? "停用" : "启用"}</button><button className="text-button" disabled={saving} onClick={() => void deleteRule(rule)}><Trash2 size={14} />删除</button></div>
              </article>)}
              {!rules.length && <div className="empty-state"><Workflow size={24} /><strong>尚未配置工作流规则</strong><p>添加事件前缀规则，系统会在显式评估时按约束执行。</p></div>}
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><ListChecks size={17} /><h3>工作流运行记录</h3></div><span className="muted-inline">最近 50 条</span></div>
            <div className="approval-switch" role="group" aria-label="工作流运行状态">{[{key:"all",label:"全部"},{key:"executed",label:"已执行"},{key:"skipped",label:"已跳过"},{key:"failed",label:"失败"}].map(item=><button type="button" key={item.key} aria-pressed={runFilter===item.key} className={runFilter===item.key?"selected":""} onClick={()=>setRunFilter(item.key)}>{item.label}</button>)}</div><div className="workflow-run-list">{runPage.rows.map((run) => <div className="workflow-run-row" key={run.id}><span className={"status-tag " + (run.status === "executed" ? "tag-green" : run.status === "failed" ? "tag-red" : "tag-neutral")}>{runStatus[run.status] || run.status}</span><div><strong>{run.detail || "事件规则执行"}</strong><small>事件 #{run.event_id} · 链深度 {run.chain_depth} · {new Date(run.created_at).toLocaleString("zh-CN", { hour12: false })}</small></div><span className="muted-inline">{run.created_task_id ? "任务已创建" : ""}</span></div>)}{!runPage.total && <div className="empty-mini">暂无符合条件的运行记录</div>}</div><ListPagination {...runPage} label="工作流分页"/>
          </section>
        </div>
      )}
    </div>
  );
}
