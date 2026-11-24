import { useCallback, useEffect, useMemo, useState } from "react";
import {
  Activity, AlertCircle, Bot, Copy, Cpu, KeyRound,
  Monitor, RefreshCw, Search, ShieldCheck, Wifi, WifiOff, X, XCircle
} from "lucide-react";
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

const stateLabels:Record<string,string>={idle:"空闲",busy:"忙碌",stopped:"已停止",monitoring:"仅监控"};
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
  }catch(reason){setError(reason instanceof Error?reason.message:"Runtime 状态读取失败");}
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
  if(!(await dialog.confirm({title:"轮换 Runtime 凭据",message:"为「"+row.label+"」轮换独立 Runtime 凭据？旧凭据将立即失效，必须同步更新对应 Runner 的配置。",confirmText:"确认轮换",tone:"warning"})))return;
  setSaving(true);setError("");
  try{
   const token=await request<RuntimeToken>("/agents/"+encodeURIComponent(row.agent_id)+"/runtimes/"+encodeURIComponent(row.id)+"/token",{method:"POST"});
   setIssuedToken(token);
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"Runtime 凭据轮换失败");}
  finally{setSaving(false);}
 };

 const disableRuntime=async(row:RuntimeRow)=>{
  if(!(await dialog.confirm({title:"禁用 Runtime",message:"确认禁用 Runtime「"+row.label+"」？它将不能再提交心跳。该操作不会自动终止外部机器上的进程。",confirmText:"确认禁用",tone:"warning"})))return;
  setSaving(true);setError("");
  try{
   await request("/agents/"+encodeURIComponent(row.agent_id)+"/runtimes/"+encodeURIComponent(row.id)+"/disable",{method:"POST"});
   await load();
  }catch(reason){setError(reason instanceof Error?reason.message:"禁用 Runtime 失败");}
  finally{setSaving(false);}
 };

 const copyToken=async()=>{
  if(!issuedToken)return;
  try{await navigator.clipboard.writeText(issuedToken.token);}
  catch{setError("浏览器未授予剪贴板权限，请手动复制令牌");}
 };

 if(!isOwner)return <div className="page-stack"><div className="page-heading"><div><div className="eyebrow">RUNTIME OBSERVABILITY</div><h1>Runtime 监控</h1><p>统一查看 Agent 运行实例和心跳状态。</p></div></div><div className="panel empty-state"><ShieldCheck size={28}/><strong>仅所有者可以查看 Runtime 全局状态</strong><p>Agent 运行实例、独立凭据和健康信息属于平台管理数据。</p></div></div>;

 return <div className="page-stack">
  <div className="page-heading"><div><div className="eyebrow">RUNTIME OBSERVABILITY</div><h1>Runtime 监控</h1><p>跨 Agent 汇总运行实例、180 秒心跳在线状态、健康信息和独立凭据。</p></div><div className="heading-actions"><button className="secondary-button" onClick={()=>void load()} disabled={loading}><RefreshCw size={15} className={loading?"spin":""}/>刷新状态</button><Link className="secondary-button" to="/agents"><Bot size={15}/>Agent 管理</Link></div></div>
  {error&&<div className="inline-error"><AlertCircle size={16}/>{error}<button onClick={()=>setError("")}><X size={14}/></button></div>}
  {issuedToken&&<section className="panel token-panel runtime-token-panel">
   <div className="token-panel-title"><KeyRound size={18}/><strong>Runtime 凭据已签发：请立即保存</strong></div>
   <p>该令牌只在本次响应中显示一次。旧令牌已立即失效，请同步更新对应 Runner 的密钥配置，不要提交到 Git。</p>
   <div className="token-value"><code>{issuedToken.token}</code><button className="secondary-button compact" onClick={()=>void copyToken()}><Copy size={14}/>复制</button></div>
   <button className="text-button" onClick={()=>setIssuedToken(null)}>我已安全保存</button>
  </section>}
  <div className="stat-grid runtime-stats">
   <div className="stat-card"><div className="stat-icon tone-violet"><Monitor size={18}/></div><div className="stat-copy"><span>运行实例</span><strong>{counts.total}</strong><small>所有已登记 Runtime</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-green"><Wifi size={18}/></div><div className="stat-copy"><span>在线</span><strong>{counts.online}</strong><small>最近心跳在 180 秒内</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-amber"><WifiOff size={18}/></div><div className="stat-copy"><span>离线</span><strong>{counts.offline}</strong><small>已启用但未在线</small></div></div>
   <div className="stat-card"><div className="stat-icon tone-blue"><KeyRound size={18}/></div><div className="stat-copy"><span>缺少凭据</span><strong>{counts.missingToken}</strong><small>需要签发独立令牌</small></div></div>
  </div>
  <section className="panel runtime-monitor-panel">
   <div className="panel-heading"><div className="panel-title"><Activity size={17}/><h3>运行实例列表</h3><span className="count-chip">{visible.length}</span></div><span className="muted-inline">在线状态由 FastAPI 服务端计算</span></div>
   <div className="runtime-monitor-filters">
    <label className="runtime-search"><Search size={15}/><input value={query} onChange={event=>setQuery(event.target.value)} placeholder="搜索 Agent、实例、环境或 ID"/></label>
    <select value={filter} onChange={event=>setFilter(event.target.value as Filter)} aria-label="按 Runtime 状态筛选">
     <option value="all">全部状态</option><option value="online">在线</option><option value="offline">离线</option><option value="disabled">已禁用</option><option value="missing_token">缺少凭据</option>
    </select>
   </div>
   {loading?<div className="panel-loading"><span className="spinner"/>正在汇总 Runtime 状态…</div>:visible.length?<div className="runtime-monitor-list">{visible.map(row=><article className="runtime-monitor-card" key={row.id}>
    <div className="runtime-monitor-card-head">
     <div className="runtime-monitor-identity"><div className="runtime-monitor-avatar"><Cpu size={18}/></div><div><strong>{row.label}</strong><small>{row.agent_name} · {row.agent_platform}</small><code>{row.id}</code></div></div>
     <div className="runtime-monitor-badges"><span className={"status-tag "+(!row.enabled?"tag-neutral":row.online?"tag-green":"tag-amber")}>{!row.enabled?"已禁用":row.online?"在线":"离线"}</span><span className="status-tag tag-neutral">{stateLabels[row.status]||row.status}</span>{!row.agent_enabled&&<span className="status-tag tag-red">Agent 已禁用</span>}</div>
    </div>
    <div className="runtime-monitor-meta">
     <div><small>运行环境</small><strong>{row.environment}</strong></div>
     <div><small>最近心跳</small><strong>{formatTime(row.last_seen)}</strong></div>
     <div><small>独立凭据</small><strong>{row.has_token?"已签发":"未签发"}</strong></div>
    </div>
    <div className="runtime-health-row"><span className="runtime-health-label">健康信息</span>{Object.entries(row.last_health||{}).length?Object.entries(row.last_health).map(([key,value])=><span className="runtime-health-chip" key={key}>{key}：{value}</span>):<span className="muted-inline">暂无健康信息上报</span>}</div>
    <div className="runtime-monitor-actions">
     {row.enabled&&<button className="secondary-button compact" disabled={saving} onClick={()=>void rotateToken(row)}><KeyRound size={13}/>{row.has_token?"轮换凭据":"签发凭据"}</button>}
     {row.enabled&&<button className="text-button" disabled={saving} onClick={()=>void disableRuntime(row)}><XCircle size={13}/>禁用 Runtime</button>}
    </div>
   </article>)}</div>:<div className="empty-state"><Monitor size={27}/><strong>{rows.length?"没有符合条件的 Runtime":"尚未登记 Runtime"}</strong><p>{rows.length?"调整搜索条件或状态筛选。":"先在 Agent 管理中登记 Agent，再为它创建 Runtime 并签发独立凭据。"}</p>{!rows.length&&<Link className="secondary-button" to="/agents">前往 Agent 管理</Link>}</div>}
  </section>
  <p className="runtime-monitor-footnote"><Activity size={13}/>在线状态由服务端按最近 180 秒心跳、Runtime 启用状态和 idle/busy 状态计算；本页面不会自行推断或伪造在线状态。刷新可重新读取服务器快照。</p>
 </div>;
}
