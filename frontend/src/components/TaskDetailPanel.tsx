import { getRiskLabel, TASK_STATUS_LABELS as statusLabels } from "../utils/labels";
import TaskStatusBadge from "./TaskStatusBadge";
import ValidatedForm from "./ValidatedForm";
import { useDraftGuard, UnsavedDraftNotice } from "./DraftGuard";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Activity, AlertCircle, ArrowRight, Clock3, GitBranch, Info,
  Pencil, Plus, RefreshCw, ShieldCheck, Trash2, X
} from "lucide-react";
import { request } from "../api/client";
import { useDialog } from "./AppDialog";
import TaskExecutionSafety from "./TaskExecutionSafety";
import TaskContinuityPanel from "./TaskContinuityPanel";
import type { Task } from "../api/types";

interface TaskDetail extends Task {
  goal_id: string | null;
  lease_epoch: number;
  lease_until: string | null;
  version: number;
  correlation_id: string;
  completed_at: string | null;
  target_agent_id: string | null;
  approval_granted: boolean;
}
interface GraphEdge { task_id: string; depends_on_id: string; }
interface TaskGraph { tasks: Task[]; dependencies: GraphEdge[]; }
interface DependencyInfo { task_id: string; depends_on: string[]; satisfied: boolean; }
interface ProjectEvent { id: number; kind: string; subject_id: string; payload: Record<string, unknown>; created_at: string; }
export interface TaskDetailSelection { id: string; projectId: string; projectName: string; }

interface Props {
  selection: TaskDetailSelection;
  isOwner: boolean;
  onClose: () => void;
  onChanged: () => void;
}

const eventLabels: Record<string,string> = {
  "task.created":"任务创建", "task.routed":"分配目标智能体",
  "task.authorized":"任务已授权", "task.claimed":"智能体已领取",
  "task.submitted":"提交执行结果", "task.accepted":"验收通过",
  "task.changes_requested":"验收要求修改", "task.blocked":"任务阻塞",
  "task.unblocked":"解除阻塞", "task.cancelled":"任务取消",
  "task.dependency_added":"增加依赖", "task.dependency_removed":"移除依赖",
  "task.dependencies_satisfied":"全部依赖已满足"
};
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : "—";
const shortId = (value: string) => value.length > 16 ? value.slice(0,8)+"…"+value.slice(-5) : value;

export default function TaskDetailPanel({selection,isOwner,onClose,onChanged}:Props) {
  const dialog = useDialog();
  const panelRef = useRef<HTMLElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus({ preventScroll: true });
    return () => {
      if (previous?.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);
  const [task,setTask] = useState<TaskDetail|null>(null);
  const [graph,setGraph] = useState<TaskGraph|null>(null);
  const [dependencyInfo,setDependencyInfo] = useState<DependencyInfo|null>(null);
  const [events,setEvents] = useState<ProjectEvent[]>([]);
  const [loading,setLoading] = useState(true);
  const [saving,setSaving] = useState(false);
  const [error,setError] = useState("");
  const [candidateId,setCandidateId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [detail,dag,dependencies,projectEvents] = await Promise.all([
        request<TaskDetail>("/tasks/"+encodeURIComponent(selection.id)),
        request<TaskGraph>("/projects/"+encodeURIComponent(selection.projectId)+"/task-graph"),
        request<DependencyInfo>("/tasks/"+encodeURIComponent(selection.id)+"/dependencies"),
        request<ProjectEvent[]>("/projects/"+encodeURIComponent(selection.projectId)+"/events?limit=500")
      ]);
      setTask(detail);
      setGraph(dag);
      setDependencyInfo(dependencies);
      setEvents(projectEvents.filter(event=>event.subject_id===selection.id).sort((a,b)=>b.id-a.id));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "任务详情读取失败");
    } finally {
      setLoading(false);
    }
  },[selection.id,selection.projectId]);

  useEffect(()=>{void load();},[load]);

  const upstream = useMemo(()=>{
    if(!task||!graph)return [];
    const ids = new Set(graph.dependencies.filter(edge=>edge.task_id===task.id).map(edge=>edge.depends_on_id));
    return graph.tasks.filter(row=>ids.has(row.id));
  },[task,graph]);

  const downstream = useMemo(()=>{
    if(!task||!graph)return [];
    const ids = new Set(graph.dependencies.filter(edge=>edge.depends_on_id===task.id).map(edge=>edge.task_id));
    return graph.tasks.filter(row=>ids.has(row.id));
  },[task,graph]);

  const descendants = useMemo(()=>{
    if(!task||!graph)return new Set<string>();
    const found = new Set<string>([task.id]);
    let changed = true;
    while(changed){
      changed = false;
      for(const edge of graph.dependencies){
        if(found.has(edge.depends_on_id)&&!found.has(edge.task_id)){
          found.add(edge.task_id);
          changed = true;
        }
      }
    }
    found.delete(task.id);
    return found;
  },[task,graph]);

  const candidates = useMemo(()=>{
    if(!task||!graph||!dependencyInfo)return [];
    const current = new Set(dependencyInfo.depends_on);
    return graph.tasks.filter(row=>row.id!==task.id&&!current.has(row.id)&&!descendants.has(row.id));
  },[task,graph,dependencyInfo,descendants]);

  const canEditDependencies = Boolean(isOwner&&task&&["ready","waiting_dependencies","waiting_approval"].includes(task.status));

  const addDependency = async () => {
    if(!task||!candidateId)return;
    const predecessor = graph?.tasks.find(row=>row.id===candidateId);
    const confirmed = await dialog.confirm({
      title: "添加任务依赖",
      message: "确认让当前任务等待「"+(predecessor?.title||candidateId)+"」完成后再执行？服务端仍会检查循环依赖和任务状态。",
      confirmText: "添加依赖"
    });
    if(!confirmed)return;
    setSaving(true);setError("");
    try{
      await request("/tasks/"+encodeURIComponent(task.id)+"/dependencies",{
        method:"POST",body:JSON.stringify({predecessor_id:candidateId})
      });
      setCandidateId("");
      await load();
      onChanged();
    }catch(reason){setError(reason instanceof Error?reason.message:"添加依赖失败");}
    finally{setSaving(false);}
  };

  const removeDependency = async (predecessorId:string) => {
    if(!task)return;
    const predecessor = graph?.tasks.find(row=>row.id===predecessorId);
    const confirmed = await dialog.confirm({
      title: "移除任务依赖",
      message: "确认移除对「"+(predecessor?.title||predecessorId)+"」的依赖？这可能使任务提前进入可执行状态。",
      confirmText: "确认移除",
      tone: "warning"
    });
    if(!confirmed)return;
    setSaving(true);setError("");
    try{
      await request("/tasks/"+encodeURIComponent(task.id)+"/dependencies/"+encodeURIComponent(predecessorId),{method:"DELETE"});
      await load();
      onChanged();
    }catch(reason){setError(reason instanceof Error?reason.message:"移除依赖失败");}
    finally{setSaving(false);}
  };

  const deleteTask = async () => {
    if (!task) return;
    const confirmed = await dialog.confirm({
      title: "删除任务",
      message: `确认删除任务「${task.title}」？此操作不可撤销。`,
      confirmText: "永久删除",
      tone: "danger"
    });
    if (!confirmed) return;
    setSaving(true); setError("");
    try {
      await request("/tasks/" + encodeURIComponent(task.id), { method: "DELETE" });
      onChanged();
      onClose();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除任务失败");
    } finally {
      setSaving(false);
    }
  };

  const [editingTask, setEditingTask] = useState(false);
  const [editTitle, setEditTitle] = useState("");
  const [editInstruction, setEditInstruction] = useState("");
  const [editAcceptance, setEditAcceptance] = useState("");
  const [editRisk, setEditRisk] = useState<"R0" | "R1" | "R2" | "R3">("R1");
  const [editPriority, setEditPriority] = useState("50");

  const editDirty = Boolean(editingTask && task && (editTitle !== task.title || editInstruction !== task.instruction || editAcceptance !== task.acceptance || editRisk !== task.risk || editPriority !== String(task.priority)));
  useDraftGuard(editDirty);
  const confirmEditDiscard = async () => !editDirty || await dialog.confirm({
    title: "放弃任务修改？", message: "尚未保存的任务修改将丢失。", confirmText: "放弃修改", cancelText: "继续编辑", tone: "warning"
  });
  const closeDetails = async () => {
    if (saving || !(await confirmEditDiscard())) return;
    onClose();
  };
  const closeEditor = async () => {
    if (saving || !(await confirmEditDiscard())) return;
    setEditingTask(false);
    closeRef.current?.focus({preventScroll:true});
  };
  const startEditTask = () => {
    if (!task || editingTask || saving) return;
    setEditTitle(task.title);
    setEditInstruction(task.instruction);
    setEditAcceptance(task.acceptance);
    setEditRisk(task.risk);
    setEditPriority(String(task.priority));
    setEditingTask(true);
  };

  const saveEditTask = async () => {
    if (!task || saving) return;
    const priority = Number(editPriority);
    if (!editTitle.trim() || !editPriority.trim() || !Number.isInteger(priority) || priority < 0 || priority > 100) {
      setError("请填写任务标题，并输入 0 至 100 的整数优先级。");
      return;
    }
    setSaving(true); setError("");
    try {
      await request("/tasks/" + encodeURIComponent(task.id), {
        method: "PATCH",
        body: JSON.stringify({
          title: editTitle.trim(),
          instruction: editInstruction.trim(),
          acceptance: editAcceptance.trim(),
          risk: editRisk,
          priority
        })
      });
      setEditingTask(false);
      await load();
      onChanged();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改任务失败");
    } finally {
      setSaving(false);
    }
  };

  const [dependencyView, setDependencyView] = useState<"all"|"waiting">("all");
  const visibleUpstream = dependencyView === "waiting" ? upstream.filter(row=>row.status!=="completed") : upstream;
  const taskNode = (row:Task,role:"upstream"|"downstream") => (
    <div className="dag-task" key={row.id}>
      <div className="dag-task-title"><strong>{row.title}</strong><TaskStatusBadge status={row.status}/></div>
      <small>{role==="upstream"?"必须先完成":"等待当前任务"}</small>
      <code>{shortId(row.id)}</code>
    </div>
  );

  return <div className="modal-backdrop task-detail-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)void closeDetails();}}>
    <section ref={panelRef} className="task-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="task-detail-title" onKeyDown={event => {
      // Nested confirmation dialogs handle their own keyboard events in the capture phase.
      if (event.key === "Escape") {
        event.stopPropagation();
        event.preventDefault(); if (!saving) void closeDetails();
      } else if (event.key === "Tab") {
        const items = Array.from(panelRef.current?.querySelectorAll<HTMLElement>(
          'button, input, textarea, select, a[href], summary, [tabindex]'
        ) || []).filter(el => el.tabIndex >= 0 && !el.matches(":disabled") && el.getClientRects().length > 0 && getComputedStyle(el).visibility === "visible");
        if (!items.length) { event.preventDefault(); return; }
        const first = items[0], last = items[items.length - 1];
        if (event.shiftKey ? document.activeElement === first : document.activeElement === last) {
          event.preventDefault();
          (event.shiftKey ? last : first).focus();
        }
      }
    }}>
      <div className="task-detail-header">
        <div><div className="eyebrow">{selection.projectName}</div><h2 id="task-detail-title">{task?.title||"任务详情"}</h2>{task&&<div className="task-detail-badges"><TaskStatusBadge status={task.status}/><span className="status-tag tag-neutral">{getRiskLabel(task.risk)}</span><span className="status-tag tag-neutral">优先级 {task.priority}</span></div>}</div>
        <div className="task-detail-header-actions"><button className="secondary-button compact" onClick={()=>void load()} disabled={loading}><RefreshCw size={14} className={loading?"spin":""}/>刷新</button><button ref={closeRef} type="button" className="icon-button" onClick={()=>void closeDetails()} aria-label="关闭任务详情"><X size={17}/></button></div>
      </div>
      {error&&<div className="inline-error" role="alert"><AlertCircle size={15}/>{error}<button type="button" aria-label="关闭错误提示" onClick={()=>setError("")}><X size={14}/></button></div>}
      {loading&&!task?<div className="panel-loading"><span className="spinner"/>正在读取任务详情…</div>:task&&<>
        <div className="task-detail-grid">
          <section className="task-detail-section"><h3>任务指令</h3><p className="task-detail-pre">{task.instruction||"暂无指令"}</p></section>
          <section className="task-detail-section"><h3>验收标准</h3><p className="task-detail-pre">{task.acceptance||"暂无验收标准"}</p></section>
          <section className="task-detail-section"><h3>执行结果</h3><p className="task-detail-pre">{task.result_summary||"尚未提交结果"}</p></section>
          {task.failure_reason && <section className="task-detail-section task-failure-section"><h3>失败原因</h3><p className="task-detail-pre">{task.failure_reason}</p></section>}
        </div>

        <details className="task-detail-disclosure"><summary>执行信息与任务标识</summary>
        <div className="task-detail-meta">
          <div><small>任务 ID</small><code>{task.id}</code></div>
          <div><small>当前版本</small><strong>v{task.version}</strong></div>
          <div><small>重新执行次数</small><strong>{task.rework_count}</strong></div>
          <div><small>审批状态</small><strong>{task.approval_granted?"已授权":"未授权"}</strong></div>
          <div><small>执行智能体</small><code>{task.assigned_agent_id?shortId(task.assigned_agent_id):"尚未领取"}</code></div>
          <div><small>目标智能体</small><code>{task.target_agent_id?shortId(task.target_agent_id):"自动调度"}</code></div>
          <div><small>租约代次</small><strong>{task.lease_epoch}</strong></div>
          <div><small>租约到期</small><strong>{formatTime(task.lease_until)}</strong></div>
          <div><small>完成时间</small><strong>{formatTime(task.completed_at)}</strong></div>
        </div>

        </details>

        {editingTask && (
          <section className="task-detail-section task-detail-wide">
            <div className="task-detail-section-head"><h3><Pencil size={15}/>编辑任务</h3><button type="button" className="icon-button" aria-label="关闭任务编辑" disabled={saving} onClick={()=>void closeEditor()}><X size={15}/></button></div>
            <UnsavedDraftNotice dirty={editDirty}/>
            <ValidatedForm className="form-panel form-panel-flat" onSubmit={event=>{event.preventDefault();void saveEditTask();}}>
              <div className="form-grid three">
                <label>任务标题<input value={editTitle} onChange={(e) => setEditTitle(e.target.value)} required maxLength={240}/></label>
                <label>风险等级<select value={editRisk} onChange={(e) => setEditRisk(e.target.value as "R0"|"R1"|"R2"|"R3")}><option value="R0">{getRiskLabel("R0")}</option><option value="R1">{getRiskLabel("R1")}</option><option value="R2">{getRiskLabel("R2")}</option><option value="R3">{getRiskLabel("R3")}</option></select></label>
                <label>优先级<input type="number" min={0} max={100} step={1} required value={editPriority} onChange={(e) => setEditPriority(e.target.value)}/></label>
              </div>
              <label>执行说明<textarea rows={3} value={editInstruction} onChange={(e) => setEditInstruction(e.target.value)}/></label>
              <label>验收标准<textarea rows={2} value={editAcceptance} onChange={(e) => setEditAcceptance(e.target.value)}/></label>
              <div className="form-actions"><button type="submit" className="primary-button compact" disabled={saving}>保存任务修改</button></div>
            </ValidatedForm>
          </section>
        )}

        <details className="task-detail-disclosure" open><summary>任务依赖关系</summary><section className="task-detail-section task-detail-wide">
          <div className="task-detail-section-head"><div><h3><GitBranch size={16}/>依赖图</h3><p>箭头方向表示执行顺序：上游依赖 → 当前任务 → 下游任务。</p></div><span className={"status-tag "+(dependencyInfo?.satisfied?"tag-green":"tag-amber")}>{dependencyInfo?.satisfied?"上游依赖已满足":"仍有依赖未满足"}</span></div>
          <div className="approval-switch dependency-switch" role="group" aria-label="上游依赖筛选"><button type="button" aria-pressed={dependencyView==="all"} className={dependencyView==="all"?"selected":""} onClick={()=>setDependencyView("all")}>全部依赖 <span>{upstream.length}</span></button><button type="button" aria-pressed={dependencyView==="waiting"} className={dependencyView==="waiting"?"selected":""} onClick={()=>setDependencyView("waiting")}>未完成 <span>{upstream.filter(row=>row.status!=="completed").length}</span></button></div><div className="dag-layout">
            <div className="dag-column"><span className="dag-column-label">上游依赖 · {upstream.length}</span>{visibleUpstream.length?visibleUpstream.map(row=>taskNode(row,"upstream")):<div className="dag-empty">{dependencyView==="waiting"?"无未完成上游任务":"无上游依赖"}</div>}</div>
            <div className="dag-arrow"><ArrowRight size={19}/></div>
            <div className="dag-current"><span className="dag-column-label">当前任务</span><div className="dag-task dag-task-current"><div className="dag-task-title"><strong>{task.title}</strong><TaskStatusBadge status={task.status}/></div><small>{getRiskLabel(task.risk)} · 版本 {task.version}</small><code>{shortId(task.id)}</code></div></div>
            <div className="dag-arrow"><ArrowRight size={19}/></div>
            <div className="dag-column"><span className="dag-column-label">下游任务 · {downstream.length}</span>{downstream.length?downstream.map(row=>taskNode(row,"downstream")):<div className="dag-empty">暂无下游任务</div>}</div>
          </div>
          {canEditDependencies&&<div className="dependency-editor"><div><strong><ShieldCheck size={15}/>依赖治理</strong><small>仅所有者可修改；任务领取后依赖图将被冻结。</small></div><div className="dependency-editor-controls"><select aria-label="上游任务" value={candidateId} onChange={event=>setCandidateId(event.target.value)}><option value="">选择一个上游任务…</option>{candidates.map(row=><option key={row.id} value={row.id}>{row.title} · {statusLabels[row.status]||row.status}</option>)}</select><button className="secondary-button compact" onClick={()=>void addDependency()} disabled={saving||!candidateId}><Plus size={14}/>添加依赖</button></div>
            {upstream.map(row=><div className="dependency-remove-row" key={row.id}><span>{row.title}</span><button className="text-button" onClick={()=>void removeDependency(row.id)} disabled={saving}><Trash2 size={13}/>移除依赖</button></div>)}
          </div>}
          {isOwner&&!canEditDependencies&&<p className="form-hint dependency-lock-note">当前任务状态为「{statusLabels[task.status]||task.status}」，依赖图不可修改。服务端会在领取或进入活动状态后冻结依赖关系。</p>}
        </section></details>

        <details className="task-detail-disclosure" open><summary>执行安全与外部操作</summary><TaskExecutionSafety taskId={task.id} projectId={selection.projectId} taskStatus={task.status} taskRisk={task.risk} isOwner={isOwner} onChanged={() => { void load(); onChanged(); }} />
        </details>
        <details className="task-detail-disclosure" open><summary>执行连续性</summary><TaskContinuityPanel taskId={task.id} taskStatus={task.status} isOwner={isOwner} onChanged={() => { void load(); onChanged(); }} />

        </details>
        <details className="task-detail-disclosure"><summary>关联事件 · {events.length} 条</summary><section className="task-detail-section task-detail-wide">
          <div className="task-detail-section-head"><div><h3><Activity size={16}/>事件记录</h3></div><span className="muted-inline">{events.length} 条</span></div>
          {events.length?<div className="task-event-list">{events.map(event=><div className="task-event-row" key={event.id}><span className="task-event-dot"/><div><strong>{eventLabels[event.kind]||event.kind}</strong><small>{formatTime(event.created_at)} · #{event.id}</small></div></div>)}</div>:<div className="dag-empty">暂时没有关联事件记录。</div>}
          <p className="task-event-note"><Info size={13}/>事件来自项目事件流；当前接口单次最多读取 500 条项目事件，因此事件量超过 500 条时，后续记录可能未包含，列表不保证覆盖完整任务历史。</p>
        </section></details>
      </>}
      <div className="task-detail-footer">
        <span><Clock3 size={13}/>任务数据以服务端为准</span>
        <div className="flex-row-gap">
          {isOwner && task && !["running", "completed", "cancelled"].includes(task.status) && (
            <button className="secondary-button compact" disabled={saving || editingTask} onClick={startEditTask}>
              <Pencil size={13}/>编辑任务
            </button>
          )}
          {isOwner && task && task.status !== "running" && (
            <button className="text-button" disabled={saving || editingTask} onClick={() => void deleteTask()}>
              <Trash2 size={14}/>删除任务
            </button>
          )}
          <button type="button" className="secondary-button compact" onClick={()=>void closeDetails()}>关闭</button>
        </div>
      </div>
    </section>
  </div>;
}
