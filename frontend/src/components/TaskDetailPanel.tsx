import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertCircle, ArrowRight, CheckCircle2, Clock3, GitBranch,
  Plus, RefreshCw, ShieldCheck, Trash2, X
} from "lucide-react";
import { request } from "../api/client";
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

const statusLabels: Record<string,string> = {
  ready:"待执行", waiting_dependencies:"等待依赖", waiting_approval:"等待授权",
  running:"执行中", submitted:"等待验收", blocked:"已阻塞",
  completed:"已完成", cancelled:"已取消", failed:"执行失败"
};
const statusTones: Record<string,string> = {
  ready:"tag-blue", waiting_dependencies:"tag-neutral", waiting_approval:"tag-red",
  running:"tag-blue", submitted:"tag-amber", blocked:"tag-red",
  completed:"tag-green", cancelled:"tag-neutral", failed:"tag-red"
};
const eventLabels: Record<string,string> = {
  "task.created":"任务创建", "task.routed":"分配目标 Agent",
  "task.authorized":"任务已授权", "task.claimed":"Agent 已领取",
  "task.submitted":"提交执行结果", "task.accepted":"验收通过",
  "task.changes_requested":"验收要求修改", "task.blocked":"任务阻塞",
  "task.unblocked":"解除阻塞", "task.cancelled":"任务取消",
  "task.dependency_added":"增加依赖", "task.dependency_removed":"移除依赖",
  "task.dependencies_satisfied":"全部依赖已满足"
};
const formatTime = (value?: string | null) => value ? new Date(value).toLocaleString("zh-CN",{hour12:false}) : "—";
const shortId = (value: string) => value.length > 16 ? value.slice(0,8)+"…"+value.slice(-5) : value;

export default function TaskDetailPanel({selection,isOwner,onClose,onChanged}:Props) {
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
    if(!window.confirm("确认让当前任务等待「"+(predecessor?.title||candidateId)+"」完成后再执行？服务端仍会检查循环依赖和任务状态。"))return;
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
    if(!window.confirm("确认移除对「"+(predecessor?.title||predecessorId)+"」的依赖？这可能使任务提前进入可执行状态。"))return;
    setSaving(true);setError("");
    try{
      await request("/tasks/"+encodeURIComponent(task.id)+"/dependencies/"+encodeURIComponent(predecessorId),{method:"DELETE"});
      await load();
      onChanged();
    }catch(reason){setError(reason instanceof Error?reason.message:"移除依赖失败");}
    finally{setSaving(false);}
  };

  const taskNode = (row:Task,role:"upstream"|"downstream") => (
    <div className="dag-task" key={row.id}>
      <div className="dag-task-title"><strong>{row.title}</strong><span className={"status-tag "+(statusTones[row.status]||"tag-neutral")}>{statusLabels[row.status]||row.status}</span></div>
      <small>{role==="upstream"?"必须先完成":"等待当前任务"}</small>
      <code>{shortId(row.id)}</code>
    </div>
  );

  return <div className="modal-backdrop task-detail-backdrop" onMouseDown={event=>{if(event.target===event.currentTarget)onClose();}}>
    <section className="task-detail-dialog" role="dialog" aria-modal="true" aria-labelledby="task-detail-title">
      <div className="task-detail-header">
        <div><div className="eyebrow">TASK DETAIL · {selection.projectName}</div><h2 id="task-detail-title">{task?.title||"任务详情"}</h2>{task&&<div className="task-detail-badges"><span className={"status-tag "+(statusTones[task.status]||"tag-neutral")}>{statusLabels[task.status]||task.status}</span><span className="status-tag tag-neutral">{task.risk}</span><span className="status-tag tag-neutral">优先级 {task.priority}</span></div>}</div>
        <div className="task-detail-header-actions"><button className="secondary-button compact" onClick={()=>void load()} disabled={loading}><RefreshCw size={14} className={loading?"spin":""}/>刷新</button><button className="icon-button" onClick={onClose} aria-label="关闭任务详情"><X size={17}/></button></div>
      </div>
      {error&&<div className="inline-error"><AlertCircle size={15}/>{error}<button onClick={()=>setError("")}><X size={14}/></button></div>}
      {loading&&!task?<div className="panel-loading"><span className="spinner"/>正在读取任务详情…</div>:task&&<>
        <div className="task-detail-meta">
          <div><small>任务 ID</small><code>{task.id}</code></div>
          <div><small>当前版本</small><strong>v{task.version}</strong></div>
          <div><small>重新执行次数</small><strong>{task.rework_count}</strong></div>
          <div><small>审批状态</small><strong>{task.approval_granted?"已授权":"未授权"}</strong></div>
          <div><small>执行 Agent</small><code>{task.assigned_agent_id?shortId(task.assigned_agent_id):"尚未领取"}</code></div>
          <div><small>目标 Agent</small><code>{task.target_agent_id?shortId(task.target_agent_id):"自动调度"}</code></div>
          <div><small>租约代次</small><strong>{task.lease_epoch}</strong></div>
          <div><small>租约到期</small><strong>{formatTime(task.lease_until)}</strong></div>
          <div><small>完成时间</small><strong>{formatTime(task.completed_at)}</strong></div>
        </div>

        <div className="task-detail-grid">
          <section className="task-detail-section"><h3>任务指令</h3><p className="task-detail-pre">{task.instruction||"暂无指令"}</p></section>
          <section className="task-detail-section"><h3>验收标准</h3><p className="task-detail-pre">{task.acceptance||"暂无验收标准"}</p></section>
          <section className="task-detail-section"><h3>执行结果</h3><p className="task-detail-pre">{task.result_summary||"尚未提交结果"}</p></section>
          <section className="task-detail-section"><h3>失败原因</h3><p className="task-detail-pre">{task.failure_reason||"暂无失败记录"}</p></section>
        </div>

        <section className="task-detail-section task-detail-wide">
          <div className="task-detail-section-head"><div><h3><GitBranch size={16}/>依赖关系 DAG</h3><p>箭头方向表示执行顺序：上游依赖 → 当前任务 → 下游任务。</p></div><span className={"status-tag "+(dependencyInfo?.satisfied?"tag-green":"tag-amber")}>{dependencyInfo?.satisfied?"上游依赖已满足":"仍有依赖未满足"}</span></div>
          <div className="dag-layout">
            <div className="dag-column"><span className="dag-column-label">上游依赖 · {upstream.length}</span>{upstream.length?upstream.map(row=>taskNode(row,"upstream")):<div className="dag-empty">无上游依赖</div>}</div>
            <div className="dag-arrow"><ArrowRight size={19}/></div>
            <div className="dag-current"><span className="dag-column-label">当前任务</span><div className="dag-task dag-task-current"><div className="dag-task-title"><strong>{task.title}</strong><span className={"status-tag "+(statusTones[task.status]||"tag-neutral")}>{statusLabels[task.status]||task.status}</span></div><small>{task.risk} · 版本 {task.version}</small><code>{shortId(task.id)}</code></div></div>
            <div className="dag-arrow"><ArrowRight size={19}/></div>
            <div className="dag-column"><span className="dag-column-label">下游任务 · {downstream.length}</span>{downstream.length?downstream.map(row=>taskNode(row,"downstream")):<div className="dag-empty">暂无下游任务</div>}</div>
          </div>
          {canEditDependencies&&<div className="dependency-editor"><div><strong><ShieldCheck size={15}/>依赖治理</strong><small>仅所有者可修改；任务领取后依赖图将被冻结。</small></div><div className="dependency-editor-controls"><select value={candidateId} onChange={event=>setCandidateId(event.target.value)}><option value="">选择一个上游任务…</option>{candidates.map(row=><option key={row.id} value={row.id}>{row.title} · {statusLabels[row.status]||row.status}</option>)}</select><button className="secondary-button compact" onClick={()=>void addDependency()} disabled={saving||!candidateId}><Plus size={14}/>添加依赖</button></div>
            {upstream.map(row=><div className="dependency-remove-row" key={row.id}><span>{row.title}</span><button className="text-button" onClick={()=>void removeDependency(row.id)} disabled={saving}><Trash2 size={13}/>移除依赖</button></div>)}
          </div>}
          {isOwner&&!canEditDependencies&&<p className="form-hint dependency-lock-note">当前任务状态为「{statusLabels[task.status]||task.status}」，依赖图不可修改。服务端会在领取或进入活动状态后冻结依赖关系。</p>}
        </section>

        <section className="task-detail-section task-detail-wide">
          <div className="task-detail-section-head"><div><h3><Activity size={16}/>关联事件</h3><p>来自项目事件流中 subject_id 与当前任务匹配的记录。</p></div><span className="muted-inline">{events.length} 条</span></div>
          {events.length?<div className="task-event-list">{events.map(event=><div className="task-event-row" key={event.id}><span className="task-event-dot"/><div><strong>{eventLabels[event.kind]||event.kind}</strong><small>{formatTime(event.created_at)} · #{event.id}</small></div></div>)}</div>:<div className="dag-empty">暂时没有关联事件记录。</div>}
          <p className="task-event-note"><Info size={13}/>事件来自项目事件流；当前接口单次最多读取 500 条项目事件，因此超大项目的早期历史可能不完整。</p>
        </section>
      </>}
      <div className="task-detail-footer"><span><Clock3 size={13}/>任务数据以 FastAPI 服务端为准</span><button className="secondary-button compact" onClick={onClose}>关闭</button></div>
    </section>
  </div>;
}
