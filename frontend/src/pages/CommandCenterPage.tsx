import { ErrorNotice, SuccessNotice } from "../components/PageFeedback";
import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useRef, useState, type FormEvent } from "react";
import { Activity, AlertCircle, CheckCircle2, Clock3, FileCode2, MessageSquare, RefreshCw, Search, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";
import A2UIRenderer, { type A2UIResult } from "../components/A2UIRenderer";
import CommandChatPanel, { type CommandPlan } from "../components/CommandChatPanel";

interface PlanDetail {
  id: string;
  task_id: string;
  project_id: string;
  action: string;
  parameters: Record<string, unknown>;
  expected_version: number;
  status: string;
  expires_at: string;
  confirmed_at?: string | null;
  result?: Record<string, unknown> | null;
}

interface AgUiEvent {
  type: string;
  event_id: number;
  otternest_kind: string;
  subject_id: string;
  timestamp: string | null;
  payload: Record<string, unknown>;
}

interface AgUiSnapshotResponse {
  protocol: string;
  project_id: string;
  state_snapshot: { task_counts: Record<string, number> };
  events: AgUiEvent[];
}


const actionLabels: Record<string, string> = {
  authorize_task: "授权执行",
  review_task: "任务验收",
  cancel_task: "取消任务",
  route_task: "指派任务"
};
const formatTime = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

const sampleA2UI = JSON.stringify(
  [
    {
      type: "card",
      title: "状态快照卡片",
      subtitle: "通过服务端白名单过滤",
      children: [
        { type: "badge", label: "只读视图", tone: "green" },
        { type: "text", text: "任何脚本属性或非白名单节点都会在服务端被剥离。" }
      ]
    }
  ],
  null,
  2
);

export default function CommandCenterPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [taskProjectId, setTaskProjectId] = useState("");
  const [tasksLoading, setTasksLoading] = useState(false);
  const taskRequest = useRef(0);
  const currentProject = useRef(selectedId);
  currentProject.current = selectedId;
  const currentTasks = taskProjectId === selectedId ? tasks : [];
  const [plan, setPlan] = useState<CommandPlan | null>(null);
  const [recentPlanIds, setRecentPlanIds] = useState<string[]>([]);
  const [planLookupId, setPlanLookupId] = useState("");
  const [planLookupResult, setPlanLookupResult] = useState<PlanDetail | null>(null);
  const [agUiData, setAgUiData] = useState<AgUiSnapshotResponse | null>(null);
  const [a2uiInput, setA2uiInput] = useState(sampleA2UI);
  const [a2uiResult, setA2uiResult] = useState<A2UIResult | null>(null);
  const [toolsBusy, setToolsBusy] = useState(false);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedId((current) => rows.some((row) => row.id === current) ? current : rows.find((row) => row.status === "active")?.id || rows[0]?.id || "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目列表读取失败");
    } finally {
      setLoading(false);
    }
  }, []);

  const loadTasks = useCallback(async (projectId: string) => {
    if (projectId !== currentProject.current) return;
    const requestId = ++taskRequest.current;
    if (!projectId) {
      setTaskProjectId("");
      setTasksLoading(false);
      setTasks([]);
      setAgUiData(null);
      return;
    }
    setTasksLoading(true);
    try {
      const [taskRows, agUiRows] = await Promise.all([
        request<Task[]>("/projects/" + encodeURIComponent(projectId) + "/tasks"),
        request<AgUiSnapshotResponse>("/projects/" + encodeURIComponent(projectId) + "/ag-ui/events?limit=25").catch(() => null)
      ]);
      if (requestId !== taskRequest.current || currentProject.current !== projectId) return;
      setTaskProjectId(projectId);
      setTasks(taskRows);
      setAgUiData(agUiRows);
    } catch (reason) {
      if (requestId === taskRequest.current && currentProject.current === projectId) setError(reason instanceof Error ? reason.message : "项目任务读取失败");
    } finally { if (requestId === taskRequest.current) setTasksLoading(false); }
  }, []);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { setPlan(null); setAgUiData(null); setNotice(""); void loadTasks(selectedId); return () => { taskRequest.current += 1; }; }, [selectedId, loadTasks]);

  const handlePlanGenerated = (nextPlan: CommandPlan) => {
    if (nextPlan.project_id !== currentProject.current) return;
    setPlan(nextPlan);
    setPlanLookupId(nextPlan.id);
    setRecentPlanIds((cur) => [nextPlan.id, ...cur.filter((id) => id !== nextPlan.id)].slice(0, 6));
  };

  const confirmPlan = async () => {
    if (!plan || plan.project_id !== currentProject.current) return;
    setSaving(true);
    setError("");
    setNotice("");
    try {
      const confirmed = await request<PlanDetail>("/command/plans/" + encodeURIComponent(plan.id) + "/confirm", { method: "POST", body: "{}" });
      setPlanLookupId(plan.id);
      setPlanLookupResult(confirmed);
      setPlan(null);
      setNotice("操作已确认并提交。任务状态已从服务端重新读取。");
      await loadTasks(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "操作确认失败");
    } finally {
      setSaving(false);
    }
  };

  const lookupPlan = async (event?: FormEvent<HTMLFormElement>, overrideId?: string) => {
    if (event) event.preventDefault();
    const targetId = (overrideId ?? planLookupId).trim();
    if (!targetId) return;
    setToolsBusy(true);
    setError("");
    try {
      const detail = await request<PlanDetail>("/command/plans/" + encodeURIComponent(targetId));
      setPlanLookupResult(detail);
      setPlanLookupId(detail.id);
    } catch (reason) {
      setPlanLookupResult(null);
      setError(reason instanceof Error ? reason.message : "命令计划查询失败");
    } finally {
      setToolsBusy(false);
    }
  };

  const refreshAgUi = async () => {
    if (!selectedId) return;
    setToolsBusy(true);
    setError("");
    try {
      const snapshot = await request<AgUiSnapshotResponse>("/projects/" + encodeURIComponent(selectedId) + "/ag-ui/events?limit=25");
      if (currentProject.current === selectedId) setAgUiData(snapshot);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "读取事件投影失败");
    } finally {
      setToolsBusy(false);
    }
  };

  const validateA2uiPayload = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setToolsBusy(true);
    setError("");
    try {
      const parsed = JSON.parse(a2uiInput);
      const components = Array.isArray(parsed) ? parsed : [parsed];
      const res = await request<A2UIResult>("/a2ui/validate", {
        method: "POST",
        body: JSON.stringify({ components })
      });
      setA2uiResult(res);
    } catch (reason) {
      setA2uiResult(null);
      setError(reason instanceof Error ? reason.message : "A2UI JSON 格式无效或校验失败");
    } finally {
      setToolsBusy(false);
    }
  };

  const targetAgent = typeof plan?.parameters?.target_agent_id === "string" ? plan.parameters.target_agent_id : "";

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          
          <h1>智能指挥中心</h1>
          
        </div>
        <button className="secondary-button" onClick={() => void loadProjects()} disabled={loading}>
          <RefreshCw size={15} className={loading ? "spin" : ""} />刷新项目
        </button>
      </div>

      <ErrorNotice message={error} onDismiss={() => setError("")} />
      <SuccessNotice message={notice} onDismiss={() => setNotice("")} />

      <section className="panel command-project-bar">
        <label>当前项目
          <select aria-label="指挥中心项目" value={selectedId} onChange={(event) => setSelectedId(event.target.value)} disabled={loading || saving || projects.length === 0}>
            {projects.length === 0 && <option value="">暂无项目</option>}
            {projects.map((project) => <option key={project.id} value={project.id}>{project.name} · {getUiLabel(project.status)}</option>)}
          </select>
        </label>
        <div>
          <span className="status-tag tag-neutral">{tasksLoading ? "读取任务中…" : taskProjectId !== selectedId ? "任务尚未读取" : currentTasks.length + " 项任务"}</span>
        </div>
      </section>

      {selectedId ? (
        <CommandChatPanel key={selectedId} projectId={selectedId} tasks={currentTasks} onPlan={handlePlanGenerated} />
      ) : (
        <div className="panel empty-state">
          <MessageSquare size={28} />
          <strong>先创建一个项目</strong>
          
        </div>
      )}

      <details className="panel command-advanced"><summary>高级工具 <span>计划查询 · 事件与组件校验</span></summary>
      <div className="command-tools-grid">
        <section className="panel">
          <div className="panel-heading">
            <div className="panel-title"><Search size={16} /><h3>操作计划查询</h3></div>
            
          </div>
          <form className="form-panel form-panel-flat" onSubmit={(e) => void lookupPlan(e)}>
            <div className="flex-row-gap">
              <input
                className="flex-1"
                value={planLookupId}
                onChange={(e) => setPlanLookupId(e.target.value)}
                aria-label="操作计划编号" placeholder="输入操作计划编号"
              />
              <button type="submit" className="secondary-button compact" disabled={toolsBusy || !planLookupId.trim()}>
                <Search size={13} />查询
              </button>
            </div>
          </form>
          {recentPlanIds.length > 0 && (
            <div className="tag-wrap-list section-spacer-top">
              {recentPlanIds.map((id) => (
                <button
                  key={id}
                  type="button"
                  className="secondary-button compact"
                  onClick={() => { setPlanLookupId(id); void lookupPlan(undefined, id); }}
                >
                  {id.slice(0, 8)}…
                </button>
              ))}
            </div>
          )}
          {planLookupResult && (
            <div className="command-summary">
              <strong>{planLookupResult.id}</strong>
              <span>操作：{actionLabels[planLookupResult.action] || planLookupResult.action} · 状态：{getUiLabel(planLookupResult.status)} · 期望版本：v{planLookupResult.expected_version}</span>
              <span>过期时间：{formatTime(planLookupResult.expires_at)}</span>
              <pre className="json-preview">{JSON.stringify({ parameters: planLookupResult.parameters, result: planLookupResult.result }, null, 2)}</pre>
            </div>
          )}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div className="panel-title"><Activity size={16} /><h3>事件与组件校验</h3></div>
            <button type="button" className="secondary-button compact" disabled={!selectedId || toolsBusy} onClick={() => void refreshAgUi()}>
              <RefreshCw size={13} />刷新投影
            </button>
          </div>
          {agUiData && (
            <div className="tag-wrap-list">
              <span className="status-tag tag-blue">{agUiData.protocol}</span>
              {Object.entries(agUiData.state_snapshot.task_counts).map(([st, count]) => (
                <span className="status-tag tag-neutral" key={st}>{getUiLabel(st)}：{count}</span>
              ))}
            </div>
          )}
          {agUiData && agUiData.events.length > 0 ? (
            <div className="ag-ui-event-list">
              {agUiData.events.slice(-8).reverse().map((ev) => (
                <div className="ag-ui-event-row" key={ev.event_id}>
                  <div className="ag-ui-event-head">
                    <strong>{getUiLabel(ev.type)}</strong>
                    <span className="status-tag tag-neutral">{ev.otternest_kind}</span>
                  </div>
                  <small>#{ev.event_id} · {ev.subject_id.slice(0, 12)} · {ev.timestamp ? formatTime(ev.timestamp) : "—"}</small>
                </div>
              ))}
            </div>
          ) : (
            <div className="empty-mini">暂无 AG-UI 投影事件</div>
          )}

          <form className="form-panel form-panel-flat section-spacer-top" onSubmit={(e) => void validateA2uiPayload(e)}>
            <label>A2UI 组件白名单验证
              <textarea rows={4} value={a2uiInput} onChange={(e) => setA2uiInput(e.target.value)} />
            </label>
            <div className="form-actions">
              <button type="submit" className="secondary-button compact" disabled={toolsBusy}>
                <FileCode2 size={13} />验证并渲染预览
              </button>
            </div>
          </form>
          {a2uiResult && (
            <div className="section-spacer-top">
              <div className="tag-wrap-list">
                <span className={"status-tag " + (a2uiResult.valid ? "tag-green" : "tag-amber")}>
                  {a2uiResult.valid ? "全部通过白名单" : `已剥离 ${a2uiResult.stripped.length} 项`}
                </span>
                <span className="status-tag tag-neutral">节点数 {a2uiResult.node_count}</span>
              </div>
              <A2UIRenderer value={a2uiResult} />
            </div>
          )}
        </section>
      </div>
      </details>

      {plan && (
        <div className="modal-backdrop">
          <section className="confirm-dialog command-plan-dialog" role="dialog" aria-modal="true" aria-labelledby="command-plan-title">
            <div className="panel-heading">
              <div className="panel-title"><ShieldCheck size={18} /><h3 id="command-plan-title">操作预览 · 等待你的确认</h3></div>
              <button className="icon-button" onClick={() => setPlan(null)} aria-label="关闭操作预览"><X size={16} /></button>
            </div>
            <p>此预览尚未执行。确认时服务端会重新验证权限、任务版本、项目状态和操作条件。</p>
            <div className="command-summary">
              <strong>{plan.task_title || plan.task_id}</strong>
              <span>操作：{actionLabels[plan.action] || plan.action}</span>
              <span>任务状态：{getUiLabel(plan.task_status)} · 风险：{plan.task_risk || "—"}</span>
              <span>预览版本：v{plan.expected_version}</span>
              {targetAgent && <span>目标智能体：{targetAgent}</span>}
              <span><Clock3 size={13} />有效期至：{formatTime(plan.expires_at)}</span>
              <span>计划状态：{getUiLabel(plan.status)}</span>
            </div>
            <div className="form-actions">
              <button className="secondary-button" onClick={() => setPlan(null)} disabled={saving}>暂不执行</button>
              <button className="primary-button" onClick={() => void confirmPlan()} disabled={saving}>{saving ? "正在确认…" : "确认并执行"}</button>
            </div>
          </section>
        </div>
      )}
    </div>
  );
}

