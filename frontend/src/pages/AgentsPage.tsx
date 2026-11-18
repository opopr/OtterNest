import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  Bot, Copy, Cpu, Edit3, KeyRound, Plus, Power, RefreshCw, RotateCcw, ShieldCheck,
  Terminal, Trash2, X, XCircle
} from "lucide-react";
import { request } from "../api/client";
import type { Agent } from "../api/types";
import { useDialog } from "../components/AppDialog";

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
  const dialog = useDialog();
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
  const [editingAgent, setEditingAgent] = useState<Agent | null>(null);
  const [editName, setEditName] = useState("");
  const [editPlatform, setEditPlatform] = useState("");
  const [editExecutionMode, setEditExecutionMode] = useState<Agent["execution_mode"]>("manual");
  const [editCapabilities, setEditCapabilities] = useState("");
  const [runtimes, setRuntimes] = useState<Runtime[]>([]);
  const [showRuntimeForm, setShowRuntimeForm] = useState(false);
  const [runtimeLabel, setRuntimeLabel] = useState("");
  const [runtimeEnvironment, setRuntimeEnvironment] = useState("local");
  const [verifiedCapabilities, setVerifiedCapabilities] = useState("");
  const [error, setError] = useState("");
  const [saving, setSaving] = useState(false);
  const [tab, setTab] = useState<"active" | "trash">("active");

  const activeAgents = useMemo(() => agents.filter((agent) => !agent.deleted_at), [agents]);
  const trashedAgents = useMemo(() => agents.filter((agent) => Boolean(agent.deleted_at)), [agents]);
  const visibleAgents = tab === "active" ? activeAgents : trashedAgents;
  const selectedAgent = useMemo(() => agents.find((agent) => agent.id === selectedAgentId) || null, [agents, selectedAgentId]);

  const loadAgents = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Agent[]>("/agents?include_deleted=true");
      setAgents(rows);
      setError("");
      setSelectedAgentId((current) => rows.some((agent) => agent.id === current && !agent.deleted_at) ? current : "");
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

  const startEditAgent = (agent: Agent) => {
    setEditingAgent(agent);
    setEditName(agent.name);
    setEditPlatform(agent.platform);
    setEditExecutionMode(agent.execution_mode);
    setEditCapabilities(agent.declared_capabilities.join(", "));
  };

  const updateAgent = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!editingAgent) return;
    setSaving(true);
    setError("");
    try {
      const caps = editCapabilities.split(",").map((item) => item.trim()).filter(Boolean);
      await request<Agent>("/agents/" + encodeURIComponent(editingAgent.id), {
        method: "PATCH",
        body: JSON.stringify({
          name: editName.trim(),
          platform: editPlatform.trim(),
          execution_mode: editExecutionMode,
          capabilities: caps,
        })
      });
      setEditingAgent(null);
      await loadAgents();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "更新 Agent 失败");
    } finally { setSaving(false); }
  };

  const rotateAgentToken = async (agent: Agent) => {
    if (!window.confirm(`确认重新生成并轮换 Agent「${agent.name}」的主令牌？旧令牌将立即失效！`)) return;
    setSaving(true);
    setError("");
    try {
      const res = await request<{ id: string; token: string }>("/agents/" + encodeURIComponent(agent.id) + "/token", {
        method: "POST"
      });
      setCreatedToken(res.token);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "重新生成令牌失败");
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
    if (!(await dialog.confirm({ title: "签发 / 轮换 Runtime 凭据", message: "为该 Runtime 签发或轮换独立凭据？旧凭据会立即失效。", confirmText: "确认签发", tone: "warning" }))) return;
    setSaving(true);
    try {
      const result = await request<RuntimeToken>("/agents/" + encodeURIComponent(selectedAgent.id) + "/runtimes/" + encodeURIComponent(runtime.id) + "/token", { method: "POST" });
      setRuntimeToken(result);
      await loadRuntimes(selectedAgent.id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Runtime 凭据签发失败"); }
    finally { setSaving(false); }
  };

  const disableRuntime = async (runtime: Runtime) => {
    if (!selectedAgent || !(await dialog.confirm({ title: "确认禁用 Runtime", message: "确认禁用该 Runtime？它将不能再提交心跳。", confirmText: "确认禁用", tone: "warning" }))) return;
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
    if (!(await dialog.confirm({ title: "保存能力核验", message: "确认保存能力核验结果？这表示你已在外部验证这些能力确实可用。", confirmText: "保存核验" }))) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(selectedAgent.id) + "/verified-capabilities", {
        method: "PUT", body: JSON.stringify({ capabilities: values })
      });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "能力核验更新失败"); }
    finally { setSaving(false); }
  };

  const disableAgent = async (agent: Agent) => {
    if (!(await dialog.confirm({ title: "停用 Agent", message: `确认停用 Agent「${agent.name}」？它将无法继续领取新任务或鉴权。`, confirmText: "确认停用", tone: "warning" }))) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(agent.id) + "/disable", { method: "POST" });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "停用 Agent 失败"); }
    finally { setSaving(false); }
  };

  const enableAgent = async (agent: Agent) => {
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(agent.id) + "/enable", { method: "POST" });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "启用 Agent 失败"); }
    finally { setSaving(false); }
  };

  const deleteAgent = async (agent: Agent) => {
    if (!(await dialog.confirm({ title: "移入回收站", message: `确认删除 Agent「${agent.name}」并移入回收站？它将被立即停用，稍后可在回收站恢复。`, confirmText: "移入回收站", tone: "warning" }))) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(agent.id), { method: "DELETE" });
      if (selectedAgentId === agent.id) setSelectedAgentId("");
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "删除 Agent 失败"); }
    finally { setSaving(false); }
  };

  const restoreAgent = async (agent: Agent) => {
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(agent.id) + "/restore", { method: "POST" });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "恢复 Agent 失败"); }
    finally { setSaving(false); }
  };

  const permanentDeleteAgent = async (agent: Agent) => {
    if (!(await dialog.confirm({ title: "永久删除 Agent", message: `警告：确认彻底永久删除 Agent「${agent.name}」？此操作不可恢复。`, confirmText: "永久删除", tone: "danger" }))) return;
    setSaving(true);
    try {
      await request("/agents/" + encodeURIComponent(agent.id) + "/permanent", { method: "DELETE" });
      await loadAgents();
    } catch (reason) { setError(reason instanceof Error ? reason.message : "彻底删除 Agent 失败"); }
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

      {editingAgent && (
        <form className="panel form-panel" onSubmit={updateAgent}>
          <div className="panel-heading"><div className="panel-title"><Edit3 size={17} /><h3>编辑 Agent：{editingAgent.name}</h3></div><button type="button" className="icon-button" onClick={() => setEditingAgent(null)}><X size={17} /></button></div>
          <div className="form-grid three">
            <label>Agent 名称<input value={editName} onChange={(event) => setEditName(event.target.value)} required maxLength={120} /></label>
            <label>平台 / 环境<input value={editPlatform} onChange={(event) => setEditPlatform(event.target.value)} required maxLength={80} /></label>
            <label>执行方式<select value={editExecutionMode} onChange={(event) => setEditExecutionMode(event.target.value as Agent["execution_mode"])}><option value="manual">手动</option><option value="polling">轮询 Runner</option><option value="remote">远程接入</option></select></label>
          </div>
          <label>能力声明（逗号分隔）<input value={editCapabilities} onChange={(event) => setEditCapabilities(event.target.value)} placeholder="python, browser, filesystem" /></label>
          <div className="form-actions" style={{ display: "flex", gap: "8px" }}>
            <button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "保存修改"}</button>
            <button type="button" className="secondary-button compact" onClick={() => setEditingAgent(null)}>取消</button>
          </div>
        </form>
      )}

      <div className="governance-tabs">
        <button className={tab === "active" ? "selected" : ""} onClick={() => setTab("active")}>
          <Bot size={16} />已登记 Agent <span>{activeAgents.length}</span>
        </button>
        <button className={tab === "trash" ? "selected" : ""} onClick={() => { setTab("trash"); setSelectedAgentId(""); }}>
          <Trash2 size={16} />回收站 <span>{trashedAgents.length}</span>
        </button>
      </div>

      <div className="agent-grid">
        {loading ? <div className="panel-loading"><div className="spinner" /></div> : visibleAgents.length === 0 ? (
          <div className="panel empty-state">
            {tab === "active" ? <Bot size={30} /> : <Trash2 size={30} />}
            <strong>{tab === "active" ? "尚未登记 Agent" : "回收站为空"}</strong>
            <p>{tab === "active" ? "创建一个身份后，再配置项目成员关系与执行环境。" : "已删除的 Agent 会暂存在这里，支持随时恢复或彻底删除。"}</p>
          </div>
        ) : visibleAgents.map((agent) => (
          <article className={"panel agent-card " + (selectedAgentId === agent.id ? "agent-card-selected" : "")} key={agent.id}>
            <div className="agent-card-head">
              <div className="agent-avatar"><Bot size={20} /></div>
              <span className={"status-tag " + (agent.deleted_at ? "tag-neutral" : agent.enabled ? "tag-green" : "tag-amber")}>
                {agent.deleted_at ? "已删除" : agent.enabled ? "已启用" : "已停用"}
              </span>
            </div>
            <h3>{agent.name}</h3><p>{agent.platform} · {agent.execution_mode}</p>
            <div className="agent-capabilities"><span>声明能力 {agent.declared_capabilities.length}</span><span>已核验 {agent.verified_capabilities.length}</span></div>
            <div className="capability-list">
              {agent.verified_capabilities.length > 0
                ? agent.verified_capabilities.map((capability) => <span className="capability-tag verified" key={capability}><ShieldCheck size={12} />{capability}</span>)
                : <span className="muted-inline">暂无已核验能力</span>}
            </div>
            <small className="agent-id">ID · {agent.id}</small>
            {tab === "active" ? (
              <div className="card-action-row">
                <button className="secondary-button compact agent-detail-button flex-1" onClick={() => setSelectedAgentId((current) => current === agent.id ? "" : agent.id)}>
                  <Cpu size={14} />{selectedAgentId === agent.id ? "收起" : "环境与能力"}
                </button>
                <button className="secondary-button compact" disabled={saving} onClick={() => startEditAgent(agent)} title="编辑基本信息">
                  <Edit3 size={14} />编辑
                </button>
                <button className="secondary-button compact" disabled={saving || !agent.enabled} onClick={() => void rotateAgentToken(agent)} title="重置/重新生成主令牌">
                  <KeyRound size={14} />重置Token
                </button>
                {agent.enabled ? (
                  <button className="secondary-button compact" disabled={saving} onClick={() => void disableAgent(agent)} title="停用">
                    <XCircle size={14} />停用
                  </button>
                ) : (
                  <button className="secondary-button compact" disabled={saving} onClick={() => void enableAgent(agent)} title="启用">
                    <Power size={14} />启用
                  </button>
                )}
                <button className="text-button" disabled={saving} onClick={() => void deleteAgent(agent)} title="移入回收站">
                  <Trash2 size={14} />删除
                </button>
              </div>
            ) : (
              <div className="card-action-row-lg">
                <button className="primary-button compact flex-1" disabled={saving} onClick={() => void restoreAgent(agent)}>
                  <RotateCcw size={14} />恢复
                </button>
                <button className="text-button" disabled={saving} onClick={() => void permanentDeleteAgent(agent)}>
                  <Trash2 size={14} />彻底删除
                </button>
              </div>
            )}
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
          {selectedAgent.enabled && <div className="agent-disable-row"><span className="form-hint">停用 Agent 会阻止新分配；它不会自动终止外部机器上的进程。</span><button className="text-button" disabled={saving} onClick={() => void disableAgent(selectedAgent)}><XCircle size={14} />停用整个 Agent</button></div>}
        </section>
      )}
    </div>
  );
}
