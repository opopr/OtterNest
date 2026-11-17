import "./ExternalInterfacesPage.css";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Activity, ArrowUpRight, Bot, Copy, Globe2, Link2, Network, Plus, RefreshCw, ShieldCheck, Webhook, X } from "lucide-react";
import { Link } from "react-router-dom";
import { getApiOrigin, request } from "../api/client";
import type { Agent, Project } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import ProjectSelect from "../components/ProjectSelect";
import TaskDetailPanel from "../components/TaskDetailPanel";

interface WebhookEndpoint {
  id: string;
  url: string;
  event_prefix: string;
  enabled: boolean;
}
interface WebhookCreated extends WebhookEndpoint {
  signing_secret: string;
}
interface A2ARemote {
  id: string;
  agent_id: string;
  url: string;
  enabled: boolean;
  remote_project_id: string | null;
}
interface A2ARemoteCreated extends A2ARemote {
  secret_env: string;
}
interface DelegationRow {
  id: string;
  project_id: string;
  task_id: string;
  endpoint_id: string;
  status: string;
  remote_task_id: string | null;
  remote_state: string | null;
  attempts: number;
  last_error: string;
  updated_at: string;
}
interface ProjectMember {
  agent_id: string;
  enabled: boolean;
  role: string;
}
interface SystemCapabilities {
  executors: {
    openai_worker: { available: boolean; entrypoint: string };
    codex_worker: { available: boolean; entrypoint: string };
    gemini_worker: { available: boolean; entrypoint: string };
    muse_adapter: { available: boolean; entrypoint: string };
  };
  storage: { backend: string; multipart_enabled: boolean; s3_bucket: string | null };
  vector_search: { enabled: boolean; mode: string; embedding_dim: number };
  kms: { enabled: boolean; provider: string; key_source: string };
}

const errorMessage = (reason: unknown) => reason instanceof Error ? reason.message : "接口操作失败";

export default function ExternalInterfacesPage() {
  const { user } = useAuth();
  const isOwner = user?.kind === "owner";
  const [projects, setProjects] = useState<Project[]>([]);
  const [projectId, setProjectId] = useState("");
  const [agents, setAgents] = useState<Agent[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [webhooks, setWebhooks] = useState<WebhookEndpoint[]>([]);
  const [remotes, setRemotes] = useState<A2ARemote[]>([]);
  const [delegations, setDelegations] = useState<DelegationRow[]>([]);
  const [capabilities, setCapabilities] = useState<SystemCapabilities | null>(null);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [webhookUrl, setWebhookUrl] = useState("");
  const [eventPrefix, setEventPrefix] = useState("task.");
  const [showWebhookForm, setShowWebhookForm] = useState(false);
  const [newWebhookSecret, setNewWebhookSecret] = useState<WebhookCreated | null>(null);
  const [showRemoteForm, setShowRemoteForm] = useState(false);
  const [remoteAgentId, setRemoteAgentId] = useState("");
  const [remoteUrl, setRemoteUrl] = useState("");
  const [remoteProjectId, setRemoteProjectId] = useState("");
  const [newRemote, setNewRemote] = useState<A2ARemoteCreated | null>(null);

  const apiOrigin = getApiOrigin();

  const loadIndex = useCallback(async () => {
    if (!isOwner) { setLoading(false); return; }
    setLoading(true);
    setError("");
    try {
      const [projectRows, agentRows, capData] = await Promise.all([
        request<Project[]>("/projects"),
        request<Agent[]>("/agents"),
        request<SystemCapabilities>("/system/capabilities").catch(() => null),
      ]);
      setProjects(projectRows);
      setAgents(agentRows);
      setCapabilities(capData);
      setProjectId((current) => projectRows.some((p) => p.id === current) ? current : (projectRows[0]?.id || ""));
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setLoading(false); }
  }, [isOwner]);

  const loadConnections = useCallback(async (id: string) => {
    if (!isOwner || !id) {
      setWebhooks([]); setRemotes([]); setMembers([]); setDelegations([]);
      return;
    }
    setLoading(true);
    setError("");
    setWebhooks([]);
    setRemotes([]);
    setMembers([]);
    setDelegations([]);
    try {
      const [hooks, peers, memberships, delegationRows] = await Promise.all([
        request<WebhookEndpoint[]>("/projects/" + encodeURIComponent(id) + "/webhooks"),
        request<A2ARemote[]>("/projects/" + encodeURIComponent(id) + "/a2a/remotes"),
        request<ProjectMember[]>("/projects/" + encodeURIComponent(id) + "/members"),
        request<DelegationRow[]>("/projects/" + encodeURIComponent(id) + "/a2a/delegations?limit=200")
      ]);
      setWebhooks(hooks);
      setRemotes(peers);
      setMembers(memberships);
      setDelegations(delegationRows);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setLoading(false); }
  }, [isOwner]);

  useEffect(() => { void loadIndex(); }, [loadIndex]);
  useEffect(() => { void loadConnections(projectId); }, [loadConnections, projectId]);

  const eligibleRemoteAgents = useMemo(() => agents.filter((agent) =>
    agent.enabled && agent.execution_mode === "remote" &&
    members.some((member) => member.agent_id === agent.id && member.enabled &&
      ["worker", "manager", "reviewer"].includes(member.role)) &&
    !remotes.some((remote) => remote.agent_id === agent.id)
  ), [agents, members, remotes]);

  const createWebhook = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectId) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const created = await request<WebhookCreated>("/projects/" + encodeURIComponent(projectId) + "/webhooks", {
        method: "POST", body: JSON.stringify({ url: webhookUrl.trim(), event_prefix: eventPrefix.trim() })
      });
      setNewWebhookSecret(created);
      setWebhookUrl(""); setEventPrefix("task."); setShowWebhookForm(false);
      await loadConnections(projectId);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const disableWebhook = async (endpoint: WebhookEndpoint) => {
    if (!window.confirm("禁用此 Webhook？待投递事件可能不再发送。")) return;
    setSaving(true); setError("");
    try {
      await request("/webhooks/" + encodeURIComponent(endpoint.id) + "/disable", { method: "POST" });
      await loadConnections(projectId);
      setNotice("Webhook 已禁用。");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const deleteWebhook = async (endpoint: WebhookEndpoint) => {
    if (!window.confirm("确认彻底删除此 Webhook 及其投递历史？")) return;
    setSaving(true); setError("");
    try {
      await request("/webhooks/" + encodeURIComponent(endpoint.id), { method: "DELETE" });
      await loadConnections(projectId);
      setNotice("Webhook 已删除。");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const createRemote = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!projectId || !remoteAgentId) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const created = await request<A2ARemoteCreated>("/projects/" + encodeURIComponent(projectId) + "/a2a/remotes", {
        method: "POST",
        body: JSON.stringify({
          agent_id: remoteAgentId,
          url: remoteUrl.trim(),
          remote_project_id: remoteProjectId.trim() || null
        })
      });
      setNewRemote(created);
      setRemoteUrl(""); setRemoteProjectId(""); setRemoteAgentId(""); setShowRemoteForm(false);
      await loadConnections(projectId);
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const disableRemote = async (endpoint: A2ARemote) => {
    if (!window.confirm("禁用此 A2A 远端？已有委托需要单独核查。")) return;
    setSaving(true); setError("");
    try {
      await request("/a2a/remotes/" + encodeURIComponent(endpoint.id) + "/disable", { method: "POST" });
      await loadConnections(projectId);
      setNotice("A2A 远端已禁用，请检查已有委托。");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const deleteRemote = async (endpoint: A2ARemote) => {
    if (!window.confirm("确认彻底删除此 A2A 远端连接？")) return;
    setSaving(true); setError("");
    try {
      await request("/a2a/remotes/" + encodeURIComponent(endpoint.id), { method: "DELETE" });
      await loadConnections(projectId);
      setNotice("A2A 远端已删除。");
    } catch (reason) { setError(errorMessage(reason)); }
    finally { setSaving(false); }
  };

  const copy = async (value: string) => {
    try { await navigator.clipboard.writeText(value); setNotice("已复制，请妥善保管。"); }
    catch { setError("剪贴板不可用，请手动复制。"); }
  };

  if (!isOwner) return <div className="page-stack">
    <div className="page-heading"><div><h1>外部接口</h1><p>外部连接和签名配置仅所有者可管理。</p></div></div>
    <div className="panel empty-state"><ShieldCheck size={28} /><strong>需要所有者权限</strong></div>
  </div>;

  return <div className="page-stack external-page">
    <div className="page-heading">
      <div><div className="eyebrow">EXTERNAL INTERFACES</div><h1>外部接口</h1>
        <p>统一查看平台对外能力、Webhook 与 A2A 连接；接口存在不代表外部服务已经接通。</p></div>
      <button className="secondary-button compact" onClick={() => { void loadIndex(); if (projectId) void loadConnections(projectId); }} disabled={loading || saving}>
        <RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
    </div>

    {error && <div className="inline-error">{error}<button onClick={() => setError("")} aria-label="关闭错误"><X size={15} /></button></div>}
    {notice && <div className="connection-help">{notice}<button className="text-button" onClick={() => setNotice("")}>关闭</button></div>}

    <div className="external-protocol-grid">
      <section className="panel external-protocol">
        <div className="external-protocol-head"><Globe2 size={20} /><span className="status-tag tag-green">已实现</span></div>
        <h3>REST / OpenAPI</h3><p>统一业务 API、认证与接口文档。能打开文档不代表拥有调用权限。</p>
        <a href={apiOrigin + "/api/docs"} target="_blank" rel="noreferrer" className="secondary-button compact">接口文档<ArrowUpRight size={14} /></a>
      </section>
      <section className="panel external-protocol">
        <div className="external-protocol-head"><Network size={20} /><span className="status-tag tag-amber">需配置执行端</span></div>
        <h3>MCP 工具桥</h3><p>15 个项目级工具，通过执行节点上的 stdio MCP 服务运行，不是可直接访问的 HTTP 接口。</p>
        <code>otternest-mcp --server YOUR_PRIVATE_URL</code>
      </section>
      <section className="panel external-protocol">
        <div className="external-protocol-head"><Link2 size={20} /><span className="status-tag tag-amber">需配置端点</span></div>
        <h3>A2A 入站</h3><p>A2A v0.3 的 Agent Card 和入站入口；Agent Card 需要配置服务端对外地址。</p>
        <a href={apiOrigin + "/.well-known/agent-card.json"} target="_blank" rel="noreferrer" className="secondary-button compact">Agent Card<ArrowUpRight size={14} /></a>
      </section>
      <section className="panel external-protocol">
        <div className="external-protocol-head">
          <Activity size={20} />
          <span className={`status-tag ${capabilities && Object.values(capabilities.executors).some((e) => e.available) ? "tag-green" : "tag-amber"}`}>
            {capabilities && Object.values(capabilities.executors).some((e) => e.available) ? "已就绪" : "待配置密钥/环境"}
          </span>
        </div>
        <h3>外部 AI 执行器</h3>
        <p>已内置 OpenAI Worker、Codex CLI、Google Gemini Worker (<code>otternest-gemini-worker</code>) 与 Muse 适配器 (<code>otternest-muse-worker</code>)。</p>
        {capabilities && <div style={{ display: "flex", flexWrap: "wrap", gap: "6px", marginBottom: "8px", fontSize: "12px" }}>
          <span className={`status-tag ${capabilities.executors.openai_worker.available ? "tag-green" : "tag-amber"}`}>OpenAI {capabilities.executors.openai_worker.available ? "✓" : "未配置"}</span>
          <span className={`status-tag ${capabilities.executors.codex_worker.available ? "tag-green" : "tag-amber"}`}>Codex {capabilities.executors.codex_worker.available ? "✓" : "未安装"}</span>
          <span className={`status-tag ${capabilities.executors.gemini_worker.available ? "tag-green" : "tag-amber"}`}>Gemini {capabilities.executors.gemini_worker.available ? "✓" : "未配置"}</span>
          <span className={`status-tag ${capabilities.executors.muse_adapter.available ? "tag-green" : "tag-amber"}`}>Muse {capabilities.executors.muse_adapter.available ? "✓" : "未配置"}</span>
        </div>}
        <Link to="/agents" className="secondary-button compact">Agent 管理<ArrowUpRight size={14} /></Link>
      </section>
    </div>

    {capabilities && <section className="panel external-project-selector" style={{ gap: "12px", flexWrap: "wrap" }}>
      <div>
        <strong>底层基础设施引擎</strong>
        <p>对象存储、混合向量检索与 KMS 信封加密已在控制面激活。</p>
      </div>
      <div style={{ display: "flex", gap: "8px", flexWrap: "wrap", alignItems: "center" }}>
        <span className="status-tag tag-green">存储: {capabilities.storage.backend.toUpperCase()} (分块上传已启用)</span>
        <span className="status-tag tag-green">向量检索: {capabilities.vector_search.mode} ({capabilities.vector_search.embedding_dim}d)</span>
        <span className="status-tag tag-green">KMS 密钥源: {capabilities.kms.key_source}</span>
      </div>
    </section>}

    <section className="panel external-project-selector">
      <div><strong>项目级接口配置</strong><p>Webhook 与 A2A 远端均隶属于项目，切换项目后显示各自的记录。</p></div>
      <ProjectSelect projects={projects} value={projectId} onChange={(value) => {
        setProjectId(value); setNewWebhookSecret(null); setNewRemote(null); setSelectedTaskId(""); setNotice("");
      }} />
    </section>

    {newWebhookSecret && <section className="panel token-panel" aria-label="Webhook 签名密钥">
      <div className="token-panel-title"><ShieldCheck size={18} /><strong>Webhook 签名密钥（仅此时显示）</strong></div>
      <p>签名密钥只在创建时返回。请在可信环境中配置接收端，不要放入 Google AI Studio 前端或公开仓库。</p>
      <div className="token-value"><code>{newWebhookSecret.signing_secret}</code>
        <button className="secondary-button compact" onClick={() => void copy(newWebhookSecret.signing_secret)}><Copy size={14} />复制</button></div>
      <button className="text-button" onClick={() => setNewWebhookSecret(null)}>我已安全保存</button>
    </section>}

    {newRemote && <section className="panel token-panel" aria-label="A2A 远端认证配置">
      <div className="token-panel-title"><ShieldCheck size={18} /><strong>A2A 远端已登记</strong></div>
      <p>在后端服务环境中配置 <code>{newRemote.secret_env}</code>，值为远端授权令牌。令牌不得发送到浏览器或存储在 OtterNest 数据库中。</p>
      <button className="text-button" onClick={() => setNewRemote(null)}>知道了</button>
    </section>}

    <div className="external-management-grid">
      <section className="panel external-management">
        <div className="panel-heading"><div className="panel-title"><Webhook size={17} /><h3>签名 Webhook</h3></div>
          <div className="heading-actions"><span className="count-chip">{webhooks.length}</span>
            <button className="secondary-button compact" onClick={() => setShowWebhookForm((v) => !v)} disabled={!projectId || saving}><Plus size={14} />添加</button></div></div>
        <p className="form-hint">向允许列表中的 HTTPS 服务器推送项目事件。需要服务端配置签名密钥、允许的主机和 Webhook Worker。</p>
        {showWebhookForm && <form className="form-panel external-form" onSubmit={(event) => void createWebhook(event)}>
          <label>目标 HTTPS URL<input type="url" value={webhookUrl} onChange={(event) => setWebhookUrl(event.target.value)} required placeholder="https://hooks.example.com/otternest" /></label>
          <label>事件前缀<input value={eventPrefix} onChange={(event) => setEventPrefix(event.target.value)} required maxLength={100} placeholder="task." /></label>
          <div className="form-actions"><button className="primary-button compact" disabled={saving || !projectId}>创建 Webhook</button></div>
        </form>}
        {loading && <p className="muted-inline">正在刷新连接…</p>}
        <div className="external-endpoint-list">
          {webhooks.map((hook) => <div className="external-endpoint" key={hook.id}>
            <div><strong>{hook.url}</strong><p>事件：<code>{hook.event_prefix}</code> · ID：{hook.id.slice(0, 8)}</p></div>
            <div className="external-endpoint-actions"><span className={"status-tag " + (hook.enabled ? "tag-green" : "tag-neutral")}>{hook.enabled ? "已启用" : "已禁用"}</span>
              {hook.enabled && <button className="text-button" disabled={saving} onClick={() => void disableWebhook(hook)}>禁用</button>}
              <button className="text-button" disabled={saving} onClick={() => void deleteWebhook(hook)}>删除</button></div>
          </div>)}
          {!loading && webhooks.length === 0 && <div className="empty-mini">当前项目尚未登记 Webhook。</div>}
        </div>
      </section>

      <section className="panel external-management">
        <div className="panel-heading"><div className="panel-title"><Network size={17} /><h3>A2A 远端连接</h3></div>
          <div className="heading-actions"><span className="count-chip">{remotes.length}</span>
            <button className="secondary-button compact" onClick={() => setShowRemoteForm((v) => !v)} disabled={!projectId || saving}><Plus size={14} />添加</button></div></div>
        <p className="form-hint">登记有真实 A2A 能力的远端 Agent。必须先登记 Remote Agent 并加入当前项目，后端还需 HTTPS 主机允许列表和独立远端凭据。</p>
        {showRemoteForm && <form className="form-panel external-form" onSubmit={(event) => void createRemote(event)}>
          <label>项目内 Remote Agent<select value={remoteAgentId} onChange={(event) => setRemoteAgentId(event.target.value)} required>
            <option value="">选择远程 Agent…</option>{eligibleRemoteAgents.map((agent) => <option key={agent.id} value={agent.id}>{agent.name}</option>)}
          </select></label>
          <label>A2A HTTPS URL<input type="url" value={remoteUrl} onChange={(event) => setRemoteUrl(event.target.value)} required placeholder="https://agent.example.com/a2a" /></label>
          <label>远端项目 ID（可选）<input value={remoteProjectId} onChange={(event) => setRemoteProjectId(event.target.value)} maxLength={128} /></label>
          <div className="form-actions"><button className="primary-button compact" disabled={saving || !projectId || !remoteAgentId}>登记远端</button></div>
          {eligibleRemoteAgents.length === 0 && <p className="form-hint">暂无符合条件的 Remote Agent。请先在 Agent 管理登记远程 Agent，并加入此项目。</p>}
        </form>}
        {loading && <p className="muted-inline">正在刷新连接…</p>}
        <div className="external-endpoint-list">
          {remotes.map((remote) => <div className="external-endpoint" key={remote.id}>
            <div><strong>{agents.find((agent) => agent.id === remote.agent_id)?.name || remote.agent_id.slice(0, 8)}</strong>
              <p>{remote.url}</p>{remote.remote_project_id && <p>远端项目：{remote.remote_project_id}</p>}</div>
            <div className="external-endpoint-actions"><span className={"status-tag " + (remote.enabled ? "tag-green" : "tag-neutral")}>{remote.enabled ? "已登记" : "已禁用"}</span>
              {remote.enabled && <button className="text-button" disabled={saving} onClick={() => void disableRemote(remote)}>禁用</button>}
              <button className="text-button" disabled={saving} onClick={() => void deleteRemote(remote)}>删除</button></div>
          </div>)}
          {!loading && remotes.length === 0 && <div className="empty-mini">当前项目尚未登记 A2A 远端。</div>}
        </div>
      </section>
    </div>

    <section className="panel external-delegation-history">
      <div className="panel-heading"><div className="panel-title"><Network size={17} /><h3>A2A 任务委托历史</h3></div>
        <span className="count-chip">{delegations.length}</span></div>
      <p className="form-hint">当前项目最近最多 200 条委托，按最后更新时间排序。项目登记不代表远端执行成功；点击任务详情核对或处理。</p>
      <div className="external-endpoint-list">
        {delegations.map((item) => <article className="external-endpoint" key={item.id}>
          <div><strong>任务 {item.task_id.slice(0, 12)}…</strong>
            <p>远端：{remotes.find((remote) => remote.id === item.endpoint_id)?.url || "已移除的远端记录"}</p>
            <p>远端任务：{item.remote_task_id || "尚未确认"} · 通信尝试 {item.attempts} 次</p>
            <p>更新：{new Date(item.updated_at).toLocaleString("zh-CN", { hour12: false })}</p>
            {item.last_error && <p>最近异常：{item.last_error}</p>}</div>
          <div className="external-endpoint-actions">
            <span className={"status-tag " + (item.status === "unknown" ? "tag-red" : item.status === "submitted" ? "tag-green" : "tag-amber")}>
              {item.status === "unknown" ? "结果未知" : item.status === "pending" ? "待发送" : item.status === "remote" ? "远端处理中" : item.status === "cancel_requested" ? "取消待确认" : item.status === "submitted" ? "待验收" : item.status}
            </span>
            <button className="secondary-button compact" onClick={() => setSelectedTaskId(item.task_id)}>任务详情</button>
          </div>
        </article>)}
        {!loading && delegations.length === 0 && <div className="empty-mini">该项目没有 A2A 委托记录。</div>}
      </div>
    </section>

    <section className="panel external-guide">
      <div className="panel-heading"><div className="panel-title"><Bot size={17} /><h3>第三方平台接入状态</h3></div></div>
      <div className="external-provider-grid">
        <div><strong>OpenAI-compatible 模型 API</strong><span className="status-tag tag-amber">需独立 API Key</span><p>在独立 Worker 环境配置模型服务地址、模型名及密钥。订阅不等于 API 额度。</p></div>
        <div><strong>Codex CLI</strong><span className="status-tag tag-amber">需执行节点登录</span><p>已具备 CLI Worker 适配器；须在受信任的 WSL/Linux 执行节点验证账号及真实任务。</p></div>
        <div><strong>Google AI / Muse</strong><span className="status-tag tag-neutral">未直接接通</span><p>目前不具备自动使用个人订阅的连接器，不能仅凭平台注册信息判断已联机。</p></div>
      </div>
      <p className="form-hint">本页展示的是数据库中的登记状态与后端能力，不是第三方平台实时联网探测；服务可用性仍需端到端验证。</p>
    </section>
    {selectedTaskId && projectId && <TaskDetailPanel
      selection={{ id: selectedTaskId, projectId, projectName: projects.find((p) => p.id === projectId)?.name || "当前项目" }}
      isOwner={isOwner}
      onClose={() => setSelectedTaskId("")}
      onChanged={() => { void loadConnections(projectId); }}
    />}
  </div>;
}
