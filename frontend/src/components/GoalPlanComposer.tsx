import { getRiskLabel, getUiLabel } from "../utils/labels";
import { useDraftGuard } from "./DraftGuard";
import { useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, GitBranch, LoaderCircle, LockKeyhole, Sparkles } from "lucide-react";
import { request } from "../api/client";
import { useDialog } from "./AppDialog";

interface PlannedTask {
  title:string;
  instruction:string;
  acceptance:string;
  risk:"R0"|"R1"|"R2"|"R3";
  priority:number;
  sensitivity:string;
  required_capabilities:string[];
  depends_on:number[];
}
interface GoalPlanProposal {
  goal_title:string;
  success_criteria:string;
  priority:number;
  tasks:PlannedTask[];
}
interface GoalPlanDraft {
  id:string;
  project_id:string;
  status:string;
  proposal:GoalPlanProposal;
  expires_at:string;
  confirmation_required:boolean;
}
interface ConfirmedPlan {
  goal:{id:string;title:string;success_criteria:string};
  tasks:Array<{id:string;title:string;status:string}>;
  dependency_count:number;
}
interface Props { projectId:string; onConfirmed:()=>void; }

export default function GoalPlanComposer({projectId,onConfirmed}:Props){
 const [text,setText]=useState("");
 const [draft,setDraft]=useState<GoalPlanDraft|null>(null);
 const [confirmed,setConfirmed]=useState<ConfirmedPlan|null>(null);
 const [generating,setGenerating]=useState(false);
 const [confirming,setConfirming]=useState(false);
 const [error,setError]=useState("");
 const dialog=useDialog();
  useDraftGuard(Boolean(!confirmed && (text.trim() || draft)));

 useEffect(()=>{
  setDraft(null);setConfirmed(null);setError("");setText("");
 },[projectId]);

 const createProposal=async()=>{
  if(text.trim().length<10||!projectId||generating)return;
  setGenerating(true);setError("");setConfirmed(null);
  try{
   const result=await request<GoalPlanDraft>("/goal-plans/proposals",{
    method:"POST",body:JSON.stringify({project_id:projectId,text:text.trim()})
   });
   setDraft(result);
  }catch(reason){setError(reason instanceof Error?reason.message:"生成计划预览失败");}
  finally{setGenerating(false);}
 };

 const confirmPlan=async()=>{
  if(!draft||draft.project_id!==projectId||confirming)return;
  const ok=await dialog.confirm({
   title:"确认创建目标与任务",
   message:`确认创建这个目标及其 ${draft.proposal.tasks.length} 个任务？确认后会写入 PostgreSQL，但不会自动授权、分配智能体或启动执行。`,
   confirmText:"确认并创建"
  });
  if(!ok)return;
  setConfirming(true);setError("");
  try{
   const result=await request<{id:string;status:string;result:ConfirmedPlan}>("/goal-plans/"+encodeURIComponent(draft.id)+"/confirm",{method:"POST"});
   setConfirmed(result.result);setDraft(null);setText("");
   onConfirmed();
  }catch(reason){setError(reason instanceof Error?reason.message:"确认计划失败");}
  finally{setConfirming(false);}
 };

 return <section className="panel goal-plan-composer">
  <div className="panel-heading"><div className="panel-title"><Sparkles size={17}/><h3>AI 多步骤目标规划</h3></div><span className="muted-inline">2–12 个任务 · 人工确认</span></div>
  <div className="goal-plan-intro"><div className="goal-plan-intro-icon"><GitBranch size={19}/></div><div><strong>描述你想达成的长期目标</strong><p>AI 将提出一个目标、拆分任务、验收标准和依赖关系。生成的只是草案，不会启动智能体。</p></div></div>
  <label className="goal-plan-prompt-label">目标描述<textarea rows={3} maxLength={5000} minLength={10} value={text} onChange={event=>setText(event.target.value)} placeholder="例如：构建一个可部署的 SaaS 客户管理系统，先完成需求分析和架构设计，再开发核心功能、测试并准备部署。"/></label>
  <div className="goal-plan-form-foot"><span><LockKeyhole size={13}/>外部模型需由服务器管理员启用。发送内容包括目标描述、项目基本信息及项目智能体的名称和已核验能力。</span><button className="primary-button compact" onClick={()=>void createProposal()} disabled={generating||text.trim().length<10}><Sparkles size={14}/>{generating?"正在规划…":"生成计划预览"}</button></div>
  {error&&<div className="inline-error"><AlertCircle size={15}/>{error}</div>}
  {draft&&draft.project_id===projectId&&<div className="goal-plan-preview">
   <div className="goal-plan-preview-head"><div><span className="eyebrow">计划预览</span><h3>{draft.proposal.goal_title}</h3></div><span className="status-tag tag-amber">待人工确认</span></div>
   <p className="goal-plan-success">{draft.proposal.success_criteria}</p>
   <div className="goal-plan-preview-meta"><span>目标优先级 {draft.proposal.priority}</span><span>{draft.proposal.tasks.length} 个任务</span><span>有效至 {new Date(draft.expires_at).toLocaleTimeString("zh-CN",{hour12:false})}</span></div>
   <div className="goal-plan-task-list">{draft.proposal.tasks.map((task,index)=><article className="goal-plan-task" key={index}>
    <div className="goal-plan-task-index">{String(index+1).padStart(2,"0")}</div>
    <div className="goal-plan-task-content"><div className="goal-plan-task-title"><strong>{task.title}</strong><span className={"status-tag "+(task.risk==="R3"?"tag-red":task.risk==="R2"?"tag-amber":"tag-neutral")}>{getRiskLabel(task.risk)}</span></div>
     <p>{task.instruction}</p><div className="goal-plan-task-acceptance"><strong>验收标准</strong>{task.acceptance}</div>
     <div className="goal-plan-task-tags"><span>优先级 {task.priority}</span><span>{getUiLabel(task.sensitivity)}</span>{task.required_capabilities.map(cap=><code key={cap}>{cap}</code>)}</div>
     {task.depends_on.length>0&&<div className="goal-plan-dependencies"><GitBranch size={12}/>依赖：{task.depends_on.map(dep=>"任务 "+(dep+1)).join("、")}</div>}
    </div>
   </article>)}</div>
   <div className="goal-plan-safety"><LockKeyhole size={14}/><span>确认后仅创建目标、任务、依赖及审计记录。R3 任务仍需单独审批；所有任务尚未分配智能体，也不会自动启动运行实例。</span></div>
   <div className="goal-plan-actions"><button className="secondary-button compact" onClick={()=>setDraft(null)} disabled={confirming}>关闭预览</button><button className="primary-button compact" onClick={()=>void confirmPlan()} disabled={confirming}>{confirming?<LoaderCircle size={14} className="spin"/>:<CheckCircle2 size={14}/>}确认并创建目标与任务</button></div>
  </div>}
  {confirmed&&<div className="goal-plan-confirmed"><CheckCircle2 size={17}/><div><strong>目标及任务已创建</strong><p>{confirmed.goal.title} · {confirmed.tasks.length} 个任务 · {confirmed.dependency_count} 条依赖。任务已进入项目任务体系，等待后续授权和调度。</p></div></div>}
 </section>;
}
