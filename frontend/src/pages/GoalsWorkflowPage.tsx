import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle, ArrowUp, GitBranch, ListChecks, Plus, RefreshCw,
  Workflow, X, Zap
} from "lucide-react";
import { request } from "../api/client";
import type { Project } from "../api/types";
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
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [board, setBoard] = useState<GoalBoard | null>(null);
  const [rules, setRules] = useState<WorkflowRule[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showGoalForm, setShowGoalForm] = useState(false);
  const [goalTitle, setGoalTitle] = useState("");
  const [goalCriteria, setGoalCriteria] = useState("");
  const [goalPriority, setGoalPriority] = useState("50");
  const [showRuleForm, setShowRuleForm] = useState(false);
  const [ruleName, setRuleName] = useState("");
  const [eventPrefix, setEventPrefix] = useState("task.completed");
  const [actionKind, setActionKind] = useState<WorkflowRule["action_kind"]>("notify_inbox");
  const [actionTitle, setActionTitle] = useState("");
  const [maxDepth, setMaxDepth] = useState("3");
  const [cooldown, setCooldown] = useState("60");
  const [dependencySelections, setDependencySelections] = useState<Record<string, string>>({});
  const [tab, setTab] = useState<"goals" | "rules">("goals");

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

  const loadProjectData = useCallback(async (projectId: string) => {
    setLoading(true);
    setError("");
    try {
      const [goalBoard, workflowRules, workflowRuns] = await Promise.all([
        request<GoalBoard>("/projects/" + encodeURIComponent(projectId) + "/goal-board"),
        request<WorkflowRule[]>("/projects/" + encodeURIComponent(projectId) + "/workflow-rules"),
        request<WorkflowRun[]>("/projects/" + encodeURIComponent(projectId) + "/workflow-runs?limit=50")
      ]);
      setBoard(goalBoard);
      setRules(workflowRules);
      setRuns(workflowRuns);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "目标与工作流读取失败"); }
    finally { setLoading(false); }
  }, []);

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
      const input = window.prompt("完成说明（至少 5 个字符）");
      if (!input || input.trim().length < 5) return;
      note = input.trim();
      if (!goal.can_complete) {
        setError("当前目标尚不可完成：" + goal.completion_blockers.join("、"));
        return;
      }
    }
    if (!window.confirm("确认将目标「" + goal.title + "」设置为" + goalStatus[status] + "？")) return;
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
    if (!window.confirm("确认移除这条目标依赖？")) return;
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

  const evaluateRules = async () => {
    if (!window.confirm("将评估该项目最近 50 条事件，并按已启用规则执行动作（可能创建任务或通知）。继续？")) return;
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
        <div><div className="eyebrow">GOALS & AUTOMATION</div><h1>目标与工作流</h1><p>目标完成门槛、跨目标依赖、事件触发规则与执行记录。</p></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadProjectData(selectedId)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      <section className="panel project-toolbar"><div><div className="eyebrow">PROJECT WORKSPACE</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedId} onChange={setSelectedId} /><span className="muted-inline">{board?.goals.length ?? 0} 个目标 · {rules.length} 条规则 · {runs.length} 条运行记录</span></section>
      <div className="governance-tabs">
        <button className={tab === "goals" ? "selected" : ""} onClick={() => setTab("goals")}><GitBranch size={16} />目标依赖图 <span>{board?.goals.length ?? 0}</span></button>
        <button className={tab === "rules" ? "selected" : ""} onClick={() => setTab("rules")}><Workflow size={16} />工作流规则 <span>{rules.length}</span></button>
      </div>
      {loading && !board ? <div className="panel-loading"><div className="spinner" />正在读取目标和工作流…</div> : tab === "goals" ? (
        <div className="page-stack">
        <GoalPlanComposer projectId={selectedId} onConfirmed={()=>void loadProjectData(selectedId)} />
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><GitBranch size={17} /><h3>目标与前置条件</h3></div><button className="primary-button compact" onClick={() => setShowGoalForm((value) => !value)} disabled={!selectedId}><Plus size={15} />新建目标</button></div>
          {showGoalForm && <form className="form-panel embedded-form" onSubmit={createGoal}>
            <label>目标名称<input value={goalTitle} onChange={(event) => setGoalTitle(event.target.value)} required maxLength={240} /></label>
            <label>成功标准<textarea rows={3} value={goalCriteria} onChange={(event) => setGoalCriteria(event.target.value)} required maxLength={5000} /></label>
            <label>优先级（0–100）<input type="number" min="0" max="100" value={goalPriority} onChange={(event) => setGoalPriority(event.target.value)} /></label>
            <div className="form-actions"><button className="primary-button compact" disabled={saving}>创建目标</button></div>
          </form>}
          <div className="goal-board">
            {(board?.goals || []).map((goal, index) => {
              const predecessorOptions = (board?.goals || []).filter((item) => item.id !== goal.id && !goal.depends_on.some((edge) => edge.id === item.id));
              const progress = goal.task_count ? Math.round(goal.completed_task_count / goal.task_count * 100) : 0;
              return <article className="goal-card" key={goal.id}>
                <div className="goal-card-top"><span className="goal-number">{String(index + 1).padStart(2, "0")}</span><span className={"status-tag " + (goal.status === "completed" ? "tag-green" : goal.status === "paused" ? "tag-neutral" : "tag-blue")}>{goalStatus[goal.status] || goal.status}</span></div>
                <h3>{goal.title}</h3><p>{goal.success_criteria}</p>
                <div className="goal-progress-label"><span>关联任务 {goal.completed_task_count}/{goal.task_count}</span><strong>{progress}%</strong></div><div className="progress-track"><span style={{ width: progress + "%" }} /></div>
                <div className="goal-dependencies"><strong>前置目标</strong>{goal.depends_on.map((dependency) => <div className="goal-dependency" key={dependency.id}><ArrowUp size={13} /><span>{dependency.title}</span><span className={"status-tag " + (dependency.status === "completed" ? "tag-green" : "tag-neutral")}>{goalStatus[dependency.status] || dependency.status}</span><button className="icon-button" title="移除依赖" onClick={() => void removeDependency(goal, dependency.id)}><X size={14} /></button></div>)}{!goal.depends_on.length && <small>无前置目标</small>}
                  {goal.status !== "completed" && predecessorOptions.length > 0 && <div className="dependency-add"><select value={dependencySelections[goal.id] || ""} onChange={(event) => setDependencySelections((current) => ({ ...current, [goal.id]: event.target.value }))}><option value="">选择前置目标…</option>{predecessorOptions.map((item) => <option key={item.id} value={item.id}>{item.title}</option>)}</select><button className="secondary-button compact" disabled={!dependencySelections[goal.id] || saving} onClick={() => void addDependency(goal)}>添加</button></div>}
                </div>
                {goal.completion_blockers.length > 0 && <div className="goal-blockers">完成门槛：{goal.completion_blockers.map((item) => item === "prerequisite_goals_incomplete" ? "前置目标未完成" : "关联任务未全部验收").join("、")}</div>}
                <div className="goal-card-actions">{goal.status === "active" && <button className="text-button" onClick={() => void changeGoalStatus(goal, "paused")}>暂停</button>}{goal.status === "paused" && <button className="text-button" onClick={() => void changeGoalStatus(goal, "active")}>恢复</button>}{goal.status !== "completed" && <button className="secondary-button compact" disabled={!goal.can_complete || saving} onClick={() => void changeGoalStatus(goal, "completed")}>完成目标</button>}</div>
              </article>;
            })}
            {!board?.goals.length && <div className="empty-state"><GitBranch size={25} /><strong>尚未定义目标</strong><p>创建目标后，可以为目标添加前置依赖。系统会阻止循环依赖和不满足完成条件的目标被标记为完成。</p></div>}
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
                <div className="workflow-rule-icon"><Zap size={18} /></div><div className="workflow-rule-main"><div><strong>{rule.name}</strong><span className={"status-tag " + (rule.enabled ? "tag-green" : "tag-neutral")}>{rule.enabled ? "已启用" : "已停用"}</span></div><p><code>{rule.event_prefix}</code> → {rule.action_kind === "create_task" ? "创建跟进任务" : "发送收件箱通知"}</p><small>已触发 {rule.triggered_count} 次 · 最大链深度 {rule.max_chain_depth} · 冷却 {rule.cooldown_seconds}s</small></div><button className="secondary-button compact" disabled={saving} onClick={() => void toggleRule(rule)}>{rule.enabled ? "停用" : "启用"}</button>
              </article>)}
              {!rules.length && <div className="empty-state"><Workflow size={24} /><strong>尚未配置工作流规则</strong><p>添加事件前缀规则，系统会在显式评估时按约束执行。</p></div>}
            </div>
          </section>
          <section className="panel">
            <div className="panel-heading"><div className="panel-title"><ListChecks size={17} /><h3>工作流运行记录</h3></div><span className="muted-inline">最近 50 条</span></div>
            <div className="workflow-run-list">{runs.map((run) => <div className="workflow-run-row" key={run.id}><span className={"status-tag " + (run.status === "executed" ? "tag-green" : run.status === "failed" ? "tag-red" : "tag-neutral")}>{runStatus[run.status] || run.status}</span><div><strong>{run.detail || "事件规则执行"}</strong><small>事件 #{run.event_id} · 链深度 {run.chain_depth} · {new Date(run.created_at).toLocaleString("zh-CN", { hour12: false })}</small></div><span className="muted-inline">{run.created_task_id ? "任务已创建" : ""}</span></div>)}{!runs.length && <div className="empty-mini">暂无工作流运行记录</div>}</div>
          </section>
        </div>
      )}
    </div>
  );
}
