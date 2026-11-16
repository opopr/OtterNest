import { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Clock3, MessageSquare, RefreshCw, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";
import CommandChatPanel, { type CommandPlan } from "../components/CommandChatPanel";

const actionLabels:Record<string,string>={
 authorize_task:"授权执行",review_task:"任务验收",cancel_task:"取消任务",route_task:"指派任务"
};
const formatTime=(value:string)=>new Date(value).toLocaleString("zh-CN",{hour12:false});

export default function CommandCenterPage(){
 const [projects,setProjects]=useState<Project[]>([]);
 const [selectedId,setSelectedId]=useState("");
 const [tasks,setTasks]=useState<Task[]>([]);
 const [plan,setPlan]=useState<CommandPlan|null>(null);
 const [loading,setLoading]=useState(true);
 const [saving,setSaving]=useState(false);
 const [error,setError]=useState("");
 const [notice,setNotice]=useState("");

 const loadProjects=useCallback(async()=>{
  setLoading(true);setError("");
  try{
   const rows=await request<Project[]>("/projects");
   setProjects(rows);
   setSelectedId(current=>rows.some(row=>row.id===current)?current:rows.find(row=>row.status==="active")?.id||rows[0]?.id||"");
  }catch(reason){setError(reason instanceof Error?reason.message:"项目列表读取失败");}
  finally{setLoading(false);}
 },[]);

 const loadTasks=useCallback(async(projectId:string)=>{
  if(!projectId){setTasks([]);return;}
  try{setTasks(await request<Task[]>("/projects/"+encodeURIComponent(projectId)+"/tasks"));}
  catch(reason){setError(reason instanceof Error?reason.message:"项目任务读取失败");}
 },[]);

 useEffect(()=>{void loadProjects();},[loadProjects]);
 useEffect(()=>{void loadTasks(selectedId);},[selectedId,loadTasks]);

 const confirmPlan=async()=>{
  if(!plan)return;
  setSaving(true);setError("");setNotice("");
  try{
   await request("/command/plans/"+encodeURIComponent(plan.id)+"/confirm",{method:"POST",body:"{}"});
   setPlan(null);
   setNotice("操作已确认并提交。任务状态已从服务端重新读取。");
   await loadTasks(selectedId);
  }catch(reason){setError(reason instanceof Error?reason.message:"操作确认失败");}
  finally{setSaving(false);}
 };

 const targetAgent=typeof plan?.parameters?.target_agent_id==="string"?plan.parameters.target_agent_id:"";

 return <div className="page-stack">
  <div className="page-heading">
   <div><div className="eyebrow">OWNER-CONTROLLED COMMANDS</div><h1>AI 指挥中心</h1><p>通过规则模板或服务器端模型提出任务操作建议；任何授权、指派、验收和取消都必须经过你的确认。</p></div>
   <button className="secondary-button" onClick={()=>void loadProjects()} disabled={loading}><RefreshCw size={15} className={loading?"spin":""}/>刷新项目</button>
  </div>
  {error&&<div className="inline-error"><AlertCircle size={15}/>{error}<button onClick={()=>setError("")}><X size={14}/></button></div>}
  {notice&&<div className="success-notice"><CheckCircle2 size={15}/>{notice}<button onClick={()=>setNotice("")}><X size={14}/></button></div>}
  <section className="panel command-project-bar">
   <label>当前项目<select value={selectedId} onChange={event=>setSelectedId(event.target.value)} disabled={loading||projects.length===0}>
    {projects.length===0&&<option value="">暂无项目</option>}
    {projects.map(project=><option key={project.id} value={project.id}>{project.name} · {project.status}</option>)}
   </select></label>
   <div><span className="status-tag tag-neutral">{tasks.length} 项任务上下文</span><span className="muted-inline">模型模式最多读取 50 项最新任务</span></div>
  </section>
  {selectedId?<CommandChatPanel projectId={selectedId} tasks={tasks} onPlan={setPlan}/>:<div className="panel empty-state"><MessageSquare size={28}/><strong>先创建一个项目</strong><p>指挥中心需要项目任务上下文，才能生成有约束的操作预览。</p></div>}
  {plan&&<div className="modal-backdrop"><section className="confirm-dialog command-plan-dialog" role="dialog" aria-modal="true" aria-labelledby="command-plan-title">
   <div className="panel-heading"><div className="panel-title"><ShieldCheck size={18}/><h3 id="command-plan-title">操作预览 · 等待你的确认</h3></div><button className="icon-button" onClick={()=>setPlan(null)} aria-label="关闭操作预览"><X size={16}/></button></div>
   <p>此预览尚未执行。确认时服务端会重新验证权限、任务版本、项目状态和操作条件。</p>
   <div className="command-summary">
    <strong>{plan.task_title||plan.task_id}</strong>
    <span>操作：{actionLabels[plan.action]||plan.action}</span>
    <span>任务状态：{plan.task_status||"—"} · 风险：{plan.task_risk||"—"}</span>
    <span>预览版本：v{plan.expected_version}</span>
    {targetAgent&&<span>目标 Agent：{targetAgent}</span>}
    <span><Clock3 size={13}/>有效期至：{formatTime(plan.expires_at)}</span>
    <span>计划状态：{plan.status}</span>
   </div>
   <div className="form-actions"><button className="secondary-button" onClick={()=>setPlan(null)} disabled={saving}>暂不执行</button><button className="primary-button" onClick={()=>void confirmPlan()} disabled={saving}>{saving?"正在确认…":"确认并执行"}</button></div>
  </section></div>}
 </div>;
}
