import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot, Copy, Cpu, KeyRound, Plus, RefreshCw, ShieldCheck,
  Terminal, X, XCircle
} from "lucide-react";
import { request } from "../api/client";
import type { Agent } from "../api/types";

interface Runtime {
  id: string;
  agent_id: string;
  label: string;
  environment: string;
  enabled: boolean;
  status: "idle" | "busy" | "stopped" | string;
  last_health: Record<string, string>;
  last_seen: string | null;
  has_token: boolean;
  online: boolean;
}
interface RuntimeToken { id: string; agent_id: string; token: string; }

export default function AgentsPage() {
  const [agents, setAgents] = useState<Agent[]>([]);
  const [loading, setLoading] = useState(true);
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [platform, setPlatform] = useState("local");
  const [executionMode, setExecutionMode] = useState<Agent["execution_mode"]>("manual");
  const [capabilities, setCapabilities] = useState("");
  const [createdToken, setCreatedToken] = useState("");
  const [runtimeToken, setRuntimeToken] = useState<RuntimeToken | null>(null);
  const [selectedAgentId, setSelectedAgentId] = useState("");
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [showRuntimeForm, setShowRuntimeForm] = useState(false);
  const [runtimeLabel, setRuntimeLabel] = useState("");
  const [runtimeEnvironment, setRuntimeEnvironment] = useState("local");
  const [verifiedCapabilities, setVerifiedCapabilities] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);

  const selectedAgent = useMemo(() => agents.find((agent) => agent.id === selectedAgentId) || null, [agents, selectedAgentId]);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Agent[]>("/agents");
      setAgents(rows);
      setError("");
      setSelectedAgentId((current) => rows.some((agent) => agent.id === current) ? current : "");
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Agent 列表读取失败");
    } finally { setLoading(false); }
  }, []);

  const loadRuntimes = useCallback(async (agentId: string) => {
    if (!agentId) { setRuntimes([]); return; }
    try {
      setRuntimes(await request<Runtime[]>("/agents/" + encodeURIComponent(agentId) + "/runtimes"));
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Runtime 列表读取失败");
    }
  }, []);

  useEffect(() => { void loadAgents(); }, [loadAgents]);
  useEffect(() => {
    if (selectedAgent) {
      setVerifiedCapabilities(selectedAgent.verified_capabilities.join(", "));
      void loadRuntimes(selectedAgent.id);
    } else setRuntimes([]);
  }, [selectedAgent, loadRuntimes]);

  const createAgent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const declared = capabilities.split(",").map((item) => item.trim()).filter(Boolean);
      const created = await request<Agent & { token: string }>("/agents", {
        method: "POST",
        body: JSON.stringify({ name, platform, execution_mode: executionMode, capabilities: declared })
      });
      setCreatedToken(created.token);
      setName("");
      setPlatform("local");
      setExecutionMode("manual");
      setCapabilities("");
      setShowForm(false);
      await loadAgents();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建 Agent 失败");
    } finally { setSaving(false); }
  };

  const createRuntime = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedAgent) return;
    setSaving(true);
    try {
      await request<Runtime>("/agents/" + encodeURIComponent(selectedAgent.id) + "/runtimes", {
        method: "POST", body: JSON.stringify({ label: runtimeLabel.trim(), environment: runtimeEnvironment.trim() })
      });
      setRuntimeLabel("");
      setRuntimeEnvironment("local");
      setShowRuntimeForm(false);
      await loadRuntimes(selectedAgent.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "注册 Runtime 失败"); }
    finally { setSaving(false); }
  };

  const issueRuntimeToken = async (runtime: Runtime) => {
    if (!selectedAgent) return;
    if (!window.confirm("为该 Runtime 签发或轮换独立凭据？旧凭据会立即失效。")) return;
    setSaving(true);
    try {
      const result = await request<RuntimeToken>("/agents/" + encodeURIComponent(selectedAgent.id) + "/runtimes/" + encodeURIComponent(runtime.id) + "/token", { method: "POST" });
      setRuntimeToken(result);
      await loadRuntimes(selectedAgent.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Runtime 凭据签发失败"); }
    finally { setSaving(false); }
  };

  const disableRuntime = async (runtime: Runtime) => {
    if (!selectedAgent || !window.confirm("确认禁用该 Runtime？它将不能再提交心跳。")) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(selectedAgent.id) + "/runtimes/" + encodeURIComponent(runtime.id) + "/disable", { method: "POST" });
      await loadRuntimes(selectedAgent.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "禁用 Runtime 失败"); }
    finally { setSaving(false); }
  };

  const saveVerifiedCapabilities = async () => {
    if (!selectedAgent) return;
    const values = verifiedCapabilities.split(",").map((item) => item.trim()).filter(Boolean);
    if (values.some((item) => !selectedAgent.declared_capabilities.includes(item))) {
      setError("只能核验 Agent 已声明的能力。");
      return;
    }
    if (!window.confirm("确认保存能力核验结果？这表示你已在外部验证这些能力确实可用。")) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(selectedAgent.id) + "/verified-capabilities", {
        method: "PUT", body: JSON.stringify({ capabilities: values })
      });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "能力核验更新失败"); }
    finally { setSaving(false); }
  };

  const disableAgent = async () => {
    if (!selectedAgent || !window.confirm("确认禁用 Agent？相关 Runtime 不会被自动终止，请单独检查外部进程。")) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(selectedAgent.id) + "/disable", { method: "POST" });
      setSelectedAgentId("");
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "禁用 Agent 失败"); }
    finally { setSaving(false); }
  };

  const copyToken = async (token: string) => {
    try { await navigator.clipboard.writeText(token); }
    catch { setError("浏览器未授予剪贴板权限，请手动复制令牌"); }
  };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">AGENT REGISTRY & RUNTIME</div><h1>Agent 管理</h1><p>管理 Agent 身份、独立 Runtime 凭据、健康心跳和能力核验。启用状态不等于当前在线。</p></div>
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
          <div className="token-value"><code>{createdToken}</code><button className="secondary-button compact" onClick={() => void copyToken(createdToken)}><Copy size={15} />复制</button></div>
          <button className="text-button" onClick={() => setCreatedToken("")}>我已安全保存</button>
        </div>
      )}

      {runtimeToken && (
        <div className="panel token-panel">
          <div className="token-panel-title"><KeyRound size={18} /><strong>Runtime 独立凭据：请立即保存</strong></div>
          <p>此凭据只授权给指定 Runtime。轮换后旧凭据立即失效，不要把它当作 Agent 主令牌。</p>
          <div className="token-value"><code>{runtimeToken.token}</code><button className="secondary-button compact" onClick={() => void copyToken(runtimeToken.token)}><Copy size={15} />复制</button></div>
          <button className="text-button" onClick={() => setRuntimeToken(null)}>我已安全保存</button>
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
          <label>能力声明（逗号分隔）<input value={capabilities} onChange={(event) => setCapabilities(event.target.value)} placeholder="python, browser, filesystem" /></label>
          <p className="form-hint">能力声明不等于可信能力。所有者必须独立核验后，Agent 才能被分配要求该能力的任务。</p>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "创建中…" : "创建 Agent"}</button></div>
        </form>
      )}

      <div className="agent-grid">
        {loading ? <div className="panel-loading"><div className="spinner" /></div> : agents.length === 0 ? (
          <div className="panel empty-state"><Bot size={30} /><strong>尚未登记 Agent</strong><p>创建一个身份后，再配置项目成员关系与执行环境。</p></div>
        ) : agents.map((agent) => (
          <article className={"panel agent-card " + (selectedAgentId === agent.id ? "agent-card-selected" : "")} key={agent.id}>
            <div className="agent-card-head"><div className="agent-avatar"><Bot size={20} /></div><span className={"status-tag " + (agent.enabled ? "tag-green" : "tag-neutral")}>{agent.enabled ? "已启用" : "已禁用"}</span></div>
            <h3>{agent.name}</h3><p>{agent.platform} · {agent.execution_mode}</p>
            <div className="agent-capabilities"><span>声明能力 {agent.declared_capabilities.length}</span><span>已核验 {agent.verified_capabilities.length}</span></div>
            <div className="capability-list">
              {agent.verified_capabilities.length > 0
                ? agent.verified_capabilities.map((capability) => <span className="capability-tag verified" key={capability}><ShieldCheck size={12} />{capability}</span>)
                : <span className="muted-inline">暂无已核验能力</span>}
            </div>
            <small className="agent-id">ID · {agent.id}</small>
            <button className="secondary-button compact agent-detail-button" onClick={() => setSelectedAgentId((current) => current === agent.id ? "" : agent.id)}><Cpu size={14} />{selectedAgentId === agent.id ? "收起运行环境" : "运行环境与能力"}</button>
          </article>
        ))}
      </div>

      {selectedAgent && (
        <section className="panel runtime-panel">
          <div className="panel-heading"><div className="panel-title"><Terminal size={17} /><h3>{selectedAgent.name} · Runtime 与能力治理</h3></div><button className="icon-button" onClick={() => setSelectedAgentId("")}><X size={16} /></button></div>
          <div className="runtime-capability-panel">
            <div><strong>所有者核验能力</strong><p>只可从声明能力中选择。请先在平台外完成实际能力测试。</p></div>
            <label>已核验能力（逗号分隔）<input value={verifiedCapabilities} onChange={(event) => setVerifiedCapabilities(event.target.value)} placeholder="从声明能力中选择" /></label>
            <button className="secondary-button compact" disabled={saving} onClick={() => void saveVerifiedCapabilities()}>保存核验</button>
          </div>
          <div className="panel-heading runtime-heading"><div className="panel-title"><Cpu size={16} /><h3>独立运行实例</h3><span className="count-chip">{runtimes.length}</span></div><button className="primary-button compact" onClick={() => setShowRuntimeForm((value) => !value)} disabled={!selectedAgent.enabled}><Plus size={14} />注册 Runtime</button></div>
          {showRuntimeForm && <form className="form-panel embedded-form" onSubmit={createRuntime}><div className="form-grid"><label>实例标签<input value={runtimeLabel} onChange={(event) => setRuntimeLabel(event.target.value)} required maxLength={120} placeholder="oracle-arm-runner-01" /></label><label>运行环境<input value={runtimeEnvironment} onChange={(event) => setRuntimeEnvironment(event.target.value)} required maxLength={160} placeholder="Oracle ARM / WSL2 / local" /></label></div><div className="form-actions"><button className="primary-button compact" disabled={saving}>创建 Runtime</button></div></form>}
          <div className="runtime-list">{runtimes.map((runtime) => <article className="runtime-card" key={runtime.id}>
            <div className="runtime-card-head"><div><strong>{runtime.label}</strong><small>{runtime.environment} · {runtime.id.slice(0, 12)}</small></div><span className={"status-tag " + (runtime.online ? "tag-green" : runtime.enabled ? "tag-amber" : "tag-neutral")}>{!runtime.enabled ? "已禁用" : runtime.online ? "在线" : "离线"}</span></div>
            <div className="runtime-meta"><span>状态：{runtime.status}</span><span>最近心跳：{runtime.last_seen ? new Date(runtime.last_seen).toLocaleString("zh-CN", { hour12: false }) : "从未上报"}</span><span>独立凭据：{runtime.has_token ? "已签发" : "未签发"}</span></div>
            {Object.keys(runtime.last_health || {}).length > 0 && <pre className="json-preview">{JSON.stringify(runtime.last_health, null, 2)}</pre>}
            <div className="runtime-actions">{runtime.enabled && <button className="secondary-button compact" disabled={saving} onClick={() => void issueRuntimeToken(runtime)}><KeyRound size={13} />签发 / 轮换凭据</button>}{runtime.enabled && <button className="text-button" disabled={saving} onClick={() => void disableRuntime(runtime)}><XCircle size={13} />禁用 Runtime</button>}</div>
          </article>)}{!runtimes.length && <div className="empty-mini">尚未登记 Runtime。创建 Runtime 后，Runner 使用独立凭据定期上报心跳。</div>}</div>
          {selectedAgent.enabled && <div className="agent-disable-row"><span className="form-hint">禁用 Agent 会阻止新分配；它不会自动终止外部机器上的进程。</span><button className="text-button" disabled={saving} onClick={() => void disableAgent()}><XCircle size={14} />禁用整个 Agent</button></div>}
        </section>
      )}
    </div>
  );
}
