import { useCallback, useEffect, useMemo, useState } from "react";
import { AlertCircle, CheckCircle2, ClipboardCheck, Filter, RefreshCw, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";

interface Row extends Task { project_name: string; }
interface Plan { id:string; task_id:string; action:string; status:string; expires_at:string; expected_version:number; task_title?:string; }
const labels:Record<string,string>={ready:"待执行",waiting_dependencies:"等待依赖",waiting_approval:"等待授权",running:"执行中",submitted:"等待验收",blocked:"已阻塞",completed:"已完成",cancelled:"已取消",failed:"执行失败"};
const tones:Record<string,string>={ready:"tag-blue",waiting_dependencies:"tag-neutral",waiting_approval:"tag-red",running:"tag-blue",submitted:"tag-amber",blocked:"tag-red",completed:"tag-green",cancelled:"tag-neutral",failed:"tag-red"};
const risks:Record<string,string>={R0:"低风险",R1:"一般风险",R2:"较高风险",R3:"高风险"};
const time=(v:string)=>new Date(v).toLocaleString("zh-CN",{hour12:false});

export default function TaskCenterPage(){
 const [projects,setProjects]=useState<Project[]>([]),[tasks,setTasks]=useState<Row[]>([]);
 const [project,setProject]=useState("all"),[status,setStatus]=useState("all"),[risk,setRisk]=useState("all"),[query,setQuery]=useState("");
 const [loading,setLoading]=useState(true),[saving,setSaving]=useState(false),[error,setError]=useState(""),[plan,setPlan]=useState<Plan|null>(null);
 const load=useCallback(async()=>{
  setLoading(true);setError("");
  try{
   const ps=await request<Project[]>("/projects");setProjects(ps);
   const groups=await Promise.all(ps.map(async p=>({p,rows:await request<Task[]>("/projects/"+encodeURIComponent(p.id)+"/tasks")})));
   setTasks(groups.flatMap(g=>g.rows.map(t=>({...t,project_name:g.p.name}))));
  }catch(e){setError(e instanceof Error?e.message:"任务加载失败");}finally{setLoading(false);}
 },[]);
 useEffect(()=>{void load();},[load]);
 const rows=useMemo(()=>{
  const q=query.trim().toLocaleLowerCase();
  return tasks.filter(t=>(project==="all"||t.project_id===project)&&(status==="all"||t.status===status)&&(risk==="all"||t.risk===risk)&&(!q||[t.title,t.instruction,t.project_name,t.id].some(v=>v.toLocaleLowerCase().includes(q))))
   .sort((a,b)=>{const rank=(t:Row)=>t.status==="waiting_approval"?0:t.status==="submitted"?1:t.status==="blocked"?2:3;return rank(a)-rank(b)||b.priority-a.priority;});
 },[tasks,project,status,risk,query]);
 const counts={all:tasks.length,approval:tasks.filter(t=>t.status==="waiting_approval").length,review:tasks.filter(t=>t.status==="submitted").length,blocked:tasks.filter(t=>t.status==="blocked").length};
 const preview=async(t:Row,action:"authorize_task"|"review_task")=>{
  setSaving(true);setError("");setPlan(null);
  try{const body=action==="authorize_task"?{task_id:t.id,action}:{task_id:t.id,action,accepted:true};setPlan(await request<Plan>("/command/plans",{method:"POST",body:JSON.stringify(body)}));}
  catch(e){setError(e instanceof Error?e.message:"无法生成命令预览");}finally{setSaving(false);}
 };
 const confirm=async()=>{
  if(!plan)return;setSaving(true);setError("");
  try{await request<Plan>("/command/plans/"+encodeURIComponent(plan.id)+"/confirm",{method:"POST",body:"{}"});setPlan(null);await load();}
  catch(e){setError(e instanceof Error?e.message:"确认失败");}finally{setSaving(false);}
 };
 return <div className="page-stack">
  <div className="page-heading"><div><div className="eyebrow">TASK OPERATIONS</div><h1>任务中心</h1><p>跨项目汇总任务状态；授权与验收均通过服务端命令预览执行。</p></div><button className="secondary-button" onClick={()=>void load()} disabled={loading}><RefreshCw size={15} className={loading?"spin":""}/>刷新</button></div>
  {error&&<div className="inline-error"><AlertCircle size={16}/>{error}<button onClick={()=>setError("")}><X size={14}/></button></div>}
  <div className="stat-grid task-center-stats">
   <div className="stat-card"><div className="stat-icon tone-violet"><ClipboardCheck size={18}/></div><div className="stat-copy"><span>全部任务</span><strong>{counts.all}</strong><small>所有可访问项目</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-amber"><ShieldCheck size={18}/></div><div className="stat-copy"><span>等待授权</span><strong>{counts.approval}</strong><small>需要所有者审批</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-blue"><CheckCircle2 size={18}/></div><div className="stat-copy"><span>等待验收</span><strong>{counts.review}</strong><small>等待所有者验收</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-green"><AlertCircle size={18}/></div><div className="stat-copy"><span>阻塞任务</span><strong>{counts.blocked}</strong><small>需要排查原因</small></div></div>
  </div>
  <section className="panel task-center-panel"><div className="panel-heading"><div className="panel-title"><Filter size={17}/><h3>筛选任务</h3></div><span className="muted-inline">{rows.length} 条结果</span></div>
   <div className="task-filters">
    <label>项目<select value={project} onChange={e=>setProject(e.target.value)}><option value="all">全部项目</option>{projects.map(p=><option key={p.id} value={p.id}>{p.name}</option>)}</select></label>
    <label>状态<select value={status} onChange={e=>setStatus(e.target.value)}><option value="all">全部状态</option>{Object.entries(labels).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select></label>
    <label>风险等级<select value={risk} onChange={e=>setRisk(e.target.value)}><option value="all">全部等级</option>{Object.entries(risks).map(([k,v])=><option key={k} value={k}>{k} · {v}</option>)}</select></label>
    <label>关键词<input value={query} onChange={e=>setQuery(e.target.value)} placeholder="任务名称、项目或 ID"/></label>
   </div>
  </section>
  <section className="panel task-center-panel"><div className="panel-heading"><div className="panel-title"><ClipboardCheck size={17}/><h3>任务列表</h3></div><span className="muted-inline">待授权、待验收和阻塞任务优先</span></div>
   {loading?<div className="panel-loading"><span className="spinner"/>正在汇总项目任务…</div>:rows.length?<div className="task-center-list">{rows.map(t=><article className="task-center-row" key={t.id}>
    <div className="task-center-main"><div className="task-center-title"><strong>{t.title}</strong><span className={"status-tag "+(tones[t.status]||"tag-neutral")}>{labels[t.status]||t.status}</span><span className="status-tag tag-neutral">{t.risk}</span></div>
     <p>{t.project_name} · 优先级 {t.priority} · {t.sensitivity}</p>
     <small>{t.result_summary||t.failure_reason||t.instruction}</small><small>任务 ID：{t.id}</small>
    </div><div className="task-center-actions">
     {t.status==="waiting_approval"&&<button className="secondary-button compact" disabled={saving} onClick={()=>void preview(t,"authorize_task")}><ShieldCheck size={14}/>预览授权</button>}
     {t.status==="submitted"&&<button className="secondary-button compact" disabled={saving} onClick={()=>void preview(t,"review_task")}><CheckCircle2 size={14}/>预览验收</button>}
    </div></article>)}</div>:<div className="empty-state"><ClipboardCheck size={24}/><strong>没有符合条件的任务</strong><p>调整筛选条件，或先在项目中创建任务。</p></div>}
  </section>
  {plan&&<div className="modal-backdrop"><section className="confirm-dialog" role="dialog" aria-modal="true" aria-labelledby="command-title">
   <div className="panel-heading"><div className="panel-title"><ShieldCheck size={18}/><h3 id="command-title">确认任务操作</h3></div><button className="icon-button" onClick={()=>setPlan(null)}><X size={16}/></button></div>
   <p>服务端已生成限时命令预览。请核对操作，再明确确认。</p>
   <div className="command-summary"><strong>{plan.task_title||tasks.find(t=>t.id===plan.task_id)?.title||"任务"}</strong><span>操作：{plan.action==="authorize_task"?"授权执行":"验收通过"}</span><span>预览状态：{plan.status}</span><span>任务版本：{plan.expected_version}</span><span>有效期：{time(plan.expires_at)}</span></div>
   <div className="form-actions"><button className="secondary-button" onClick={()=>setPlan(null)} disabled={saving}>取消</button><button className="primary-button" onClick={()=>void confirm()} disabled={saving}>{saving?"正在确认…":"确认并执行"}</button></div>
  </section></div>}
 </div>;
}
