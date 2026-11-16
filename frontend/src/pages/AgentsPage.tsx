import { useCallback, useEffect, useState, type FormEvent } from "react";
import { Bot, Copy, Plus, RefreshCw, ShieldCheck, X } from "lucide-react";
import { request } from "../api/client";
import type { Agent } from "../api/types";

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("local");
  const [executionMode, setExecutionMode] = useState<Agent["execution_mode"]>("manual");
  const [createdToken, setCreatedToken] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      setAgents(await request<Agent[]>("/agents"));
      setError("");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Agent 列表读取失败");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => { void loadAgents(); }, [loadAgents]);

  const createAgent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const created = await request<Agent & { token: string }>("/agents", {
        method: "POST",
        body: JSON.stringify({ name, platform, execution_mode: executionMode, capabilities: [] })
      });
      setCreatedToken(created.token);
      setName("");
      setPlatform("local");
      setExecutionMode("manual");
      setShowForm(false);
      await loadAgents();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建 Agent 失败");
    } finally { setSaving(false); }
  };

  const copyToken = async () => {
    try { await navigator.clipboard.writeText(createdToken); }
    catch { setError("浏览器未授予剪贴板权限，请手动复制令牌"); }
  };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">AGENT REGISTRY</div><h1>Agent 管理</h1><p>登记执行身份、运行方式与能力声明。启用状态不等于当前在线。</p></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void loadAgents()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => setShowForm(true)}><Plus size={16} />登记 Agent</button>
        </div>
      </div>

      {error && <div className="inline-error">{error}<button onClick={() => setError("")}><X size={15} /></button></div>}

      {createdToken && (
        <div className="panel token-panel">
          <div className="token-panel-title"><ShieldCheck size={18} /><strong>Agent 已创建：请立即保存令牌</strong></div>
          <p>令牌只在创建时返回一次。离开本页或刷新后不会再次显示，请只交给可信 Runner 使用。</p>
          <div className="token-value"><code>{createdToken}</code><button className="secondary-button compact" onClick={() => void copyToken()}><Copy size={15} />复制</button></div>
          <button className="text-button" onClick={() => setCreatedToken("")}>我已安全保存</button>
        </div>
      )}

      {showForm && (
        <form className="panel form-panel" onSubmit={createAgent}>
          <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>登记新的 Agent</h3></div><button type="button" className="icon-button" onClick={() => setShowForm(false)}><X size={17} /></button></div>
          <div className="form-grid three">
            <label>Agent 名称<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} /></label>
            <label>平台 / 环境<input value={platform} onChange={(event) => setPlatform(event.target.value)} required maxLength={80} /></label>
            <label>执行方式<select value={executionMode} onChange={(event) => setExecutionMode(event.target.value as Agent["execution_mode"])}><option value="manual">手动</option><option value="polling">轮询 Runner</option><option value="remote">远程接入</option></select></label>
          </div>
          <p className="form-hint">能力初始为空。能力声明和所有者独立核验是两个不同状态，不能仅凭注册自动获得可信能力。</p>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "创建中…" : "创建 Agent"}</button></div>
        </form>
      )}

      <div className="agent-grid">
        {loading ? <div className="panel-loading"><div className="spinner" /></div> : agents.length === 0 ? (
          <div className="panel empty-state"><Bot size={30} /><strong>尚未登记 Agent</strong><p>创建一个身份后，再配置项目成员关系与执行环境。</p></div>
        ) : agents.map((agent) => (
          <article className="panel agent-card" key={agent.id}>
            <div className="agent-card-head"><div className="agent-avatar"><Bot size={20} /></div><span className={"status-tag " + (agent.enabled ? "tag-green" : "tag-neutral")}>{agent.enabled ? "已启用" : "已禁用"}</span></div>
            <h3>{agent.name}</h3><p>{agent.platform} · {agent.execution_mode}</p>
            <div className="agent-capabilities"><span>声明能力 {agent.declared_capabilities.length}</span><span>已核验 {agent.verified_capabilities.length}</span></div>
            <div className="capability-list">
              {agent.verified_capabilities.length > 0
                ? agent.verified_capabilities.map((capability) => <span className="capability-tag verified" key={capability}><ShieldCheck size={12} />{capability}</span>)
                : <span className="muted-inline">暂无已核验能力</span>}
            </div>
            <small className="agent-id">ID · {agent.id}</small>
          </article>
        ))}
      </div>
    </div>
  );
}
