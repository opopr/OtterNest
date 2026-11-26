import { ErrorNotice, LoadingState } from "../components/PageFeedback";
import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useMemo, useState } from "react";
import { Activity, Bot, Copy, Cpu, KeyRound, Monitor, RefreshCw, Search, ShieldCheck, Wifi, WifiOff, XCircle } from "lucide-react";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import { useAuth } from "../auth/AuthProvider";
import { useDialog } from "../components/AppDialog";

interface RuntimeRow {
 id:string; agent_id:string; label:string; environment:string; enabled:boolean;
 status:"idle"|"busy"|"stopped"|string; last_health:Record<string,string>;
 last_seen:string|null; has_token:boolean; online:boolean;
 agent_name:string; agent_platform:string; agent_enabled:boolean;
}
interface RuntimeToken { id:string; agent_id:string; token:string; }
type Filter = "all"|"online"|"offline"|"disabled"|"missing_token";

const stateLabels:Record<string,string>={idle:"空闲",busy:"忙碌",stopped:"已停止"};
const formatTime=(value:string|null)=>value?new Date(value).toLocaleString("zh-CN",{hour12:false}):"从未上报";

export default function RuntimeMonitorPage(){
 const {user}=useAuth();
 const dialog=useDialog();
 const isOwner=user?.kind==="owner";
 const [rows,setRows]=useState<RuntimeRow[]>([]);
 const [loading,setLoading]=useState(true);
 const [saving,setSaving]=useState(false);
 const [error,setError]=useState("");
 const [filter,setFilter]=useState<Filter>("all");
 const [query,setQuery]=useState("");
 const [issuedToken,setIssuedToken]=useState<RuntimeToken|null>(null);

 const load=useCallback(async()=>{
  if(!isOwner){setLoading(false);return;}
  setLoading(true);setError("");
  try{
   setRows(await request<RuntimeRow[]>("/runtimes"));
  }catch(reason){setError(reason instanceof Error?reason.message:"运行实例状态读取失败");}
  finally{setLoading(false);}
 },[isOwner]);

 useEffect(()=>{void load();},[load]);

 const counts=useMemo(()=>({
  total:rows.length,
  online:rows.filter(row=>row.online).length,
  offline:rows.filter(row=>row.enabled&&!row.online).length,
  disabled:rows.filter(row=>!row.enabled).length,
  missingToken:rows.filter(row=>row.enabled&&!row.has_token).length
 }),[rows]);

 const visible=useMemo(()=>{
  const q=query.trim().toLocaleLowerCase();
  return rows.filter(row=>{
   const matchesFilter=filter==="all"||(filter==="online"&&row.online)||(filter==="offline"&&row.enabled&&!row.online)||(filter==="disabled"&&!row.enabled)||(filter==="missing_token"&&row.enabled&&!row.has_token);
   const haystack=[row.agent_name,row.agent_platform,row.label,row.environment,row.id,row.agent_id].join(" ").toLocaleLowerCase();
   return matchesFilter&&(!q||haystack.includes(q));
  }).sort((a,b)=>Number(b.online)-Number(a.online)||a.agent_name.localeCompare(b.agent_name)||a.label.localeCompare(b.label));
 },[rows,filter,query]);

 const rotateToken=async(row:RuntimeRow)=>{
  if(!(await dialog.confirm({title:"轮换运行实例凭据",message:"为「"+row.label+"」轮换独立运行实例凭据？旧凭据将立即失效，必须同步更新对应执行节点的配置。",confirmText:"确认轮换",tone:"warning"})))return;
  setSaving(true);setError("");
  try{
   const token=await request<RuntimeToken>("/agents/"+encodeURIComponent(row.agent_id)+"/runtimes/"+encodeURIComponent(row.id)+"/token",{method:"POST"});
   setIssuedToken(token);
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"运行实例凭据轮换失败");}
  finally{setSaving(false);}
 };

 const disableRuntime=async(row:RuntimeRow)=>{
  if(!(await dialog.confirm({title:"禁用运行实例",message:"确认禁用运行实例「"+row.label+"」？它将不能再提交心跳。该操作不会自动终止外部机器上的进程。",confirmText:"确认禁用",tone:"warning"})))return;
  setSaving(true);setError("");
  try{
   await request("/agents/"+encodeURIComponent(row.agent_id)+"/runtimes/"+encodeURIComponent(row.id)+"/disable",{method:"POST"});
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"禁用运行实例 失败");}
  finally{setSaving(false);}
 };

 const copyToken=async()=>{
  if(!issuedToken)return;
  try{await navigator.clipboard.writeText(issuedToken.token);}
  catch{setError("浏览器未授予剪贴板权限，请手动复制令牌");}
 };

 if(!isOwner)return <div className="page-stack"><div className="page-heading"><div><h1>运行监控</h1><p>统一查看智能体 运行实例和心跳状态。</p></div></div><div className="panel empty-state"><ShieldCheck size={28}/><strong>仅所有者可以查看运行实例 全局状态</strong><p>智能体 运行实例、独立凭据和健康信息属于平台管理数据。</p></div></div>;

 return <div className="page-stack">
  <div className="page-heading"><div><h1>运行监控</h1></div><div className="heading-actions"><button className="secondary-button" onClick={()=>void load()} disabled={loading}><RefreshCw size={15} className={loading?"spin":""}/>刷新状态</button><Link className="secondary-button" to="/agents"><Bot size={15}/>智能体管理</Link></div></div>
  <ErrorNotice message={error} onDismiss={()=>setError("")} onRetry={()=>void load()} busy={loading}/>
  {issuedToken&&<section className="panel token-panel runtime-token-panel">
   <div className="token-panel-title"><KeyRound size={18}/><strong>运行实例凭据已签发：请立即保存</strong></div>
   <p>该令牌只在本次响应中显示一次。旧令牌已立即失效，请同步更新对应执行节点的密钥配置，不要提交到 Git。</p>
   <div className="token-value"><code>{issuedToken.token}</code><button className="secondary-button compact" onClick={()=>void copyToken()}><Copy size={14}/>复制</button></div>
   <button className="text-button" onClick={()=>setIssuedToken(null)}>我已安全保存</button>
  </section>}
  <section className="panel runtime-monitor-panel runtime-workspace">
   <div className="panel-heading"><div className="panel-title"><Activity size={17}/><h3>运行实例</h3><span className="count-chip">{visible.length}</span></div><span className="muted-inline">心跳有效期 180 秒</span></div>
   <div className="task-status-switch runtime-status-switch" role="group" aria-label="运行实例快速筛选">
    {([{key:"all",label:"全部",value:counts.total,icon:Monitor},{key:"online",label:"在线",value:counts.online,icon:Wifi},{key:"offline",label:"离线",value:counts.offline,icon:WifiOff},{key:"missing_token",label:"缺少凭据",value:counts.missingToken,icon:KeyRound},{key:"disabled",label:"已禁用",value:counts.disabled,icon:XCircle}] as const).map(({key,label,value,icon:Icon})=><button key={key} type="button" aria-pressed={filter===key} className={"task-status-filter"+(filter===key?" selected":"")} onClick={()=>setFilter(key)}><Icon size={16}/><span>{label}</span><strong>{value}</strong></button>)}
   </div>
   <div className="runtime-monitor-filters">
    <label className="runtime-search"><Search size={15}/><input value={query} onChange={event=>setQuery(event.target.value)} aria-label="搜索运行实例" placeholder="搜索智能体、实例、环境或编号"/></label>
    <select value={filter} onChange={event=>setFilter(event.target.value as Filter)} aria-label="运行实例状态">
     <option value="all">全部状态</option><option value="online">在线</option><option value="offline">离线</option><option value="disabled">已禁用</option><option value="missing_token">缺少凭据</option>
    </select><button type="button" className="text-button" onClick={()=>{setFilter("all");setQuery("");}}>清除筛选</button>
   </div>
   {loading?<LoadingState label="正在汇总运行实例状态…"/>:visible.length?<div className="runtime-monitor-list">{visible.map(row=><article className="runtime-monitor-card" key={row.id}>
    <div className="runtime-monitor-card-head">
     <div className="runtime-monitor-identity"><div className="runtime-monitor-avatar"><Cpu size={18}/></div><div><strong>{row.label}</strong><small>{row.agent_name} · {row.agent_platform}</small><code>{row.id}</code></div></div>
     <div className="runtime-monitor-badges"><span className={"status-tag "+(!row.enabled?"tag-neutral":row.online?"tag-green":"tag-amber")}>{!row.enabled?"已禁用":row.online?"在线":"离线"}</span><span className="status-tag tag-neutral">{stateLabels[row.status]||getUiLabel(row.status)}</span>{!row.agent_enabled&&<span className="status-tag tag-red">智能体已禁用</span>}</div>
    </div>
    <div className="runtime-monitor-meta">
     <div><small>运行环境</small><strong>{row.environment}</strong></div>
     <div><small>最近心跳</small><strong>{formatTime(row.last_seen)}</strong></div>
     <div><small>独立凭据</small><strong>{row.has_token?"已签发":"未签发"}</strong></div>
    </div>
    <details className="library-disclosure"><summary>健康信息</summary><div className="runtime-health-row">{Object.entries(row.last_health||{}).length?Object.entries(row.last_health).map(([key,value])=><span className="runtime-health-chip" key={key}>{key}：{value}</span>):<span className="muted-inline">暂无健康信息上报</span>}</div></details>
    <div className="runtime-monitor-actions">
     {row.enabled&&<button className="secondary-button compact" disabled={saving} onClick={()=>void rotateToken(row)}><KeyRound size={13}/>{row.has_token?"轮换凭据":"签发凭据"}</button>}
     {row.enabled&&<button className="text-button" disabled={saving} onClick={()=>void disableRuntime(row)}><XCircle size={13}/>禁用运行实例</button>}
    </div>
   </article>)}</div>:<div className="empty-state"><Monitor size={27}/><strong>{rows.length?"没有符合条件的运行实例":"尚未登记运行实例"}</strong><p>{rows.length?"调整搜索条件或状态筛选。":"请先登记智能体，再创建运行实例并签发独立凭据。"}</p>{!rows.length&&<Link className="secondary-button" to="/agents">前往智能体管理</Link>}</div>}
  </section>
  <p className="runtime-monitor-footnote"><Activity size={13}/>在线状态由服务端按最近 180 秒心跳、运行实例启用状态及空闲/忙碌状态计算。</p>
 </div>;
}
