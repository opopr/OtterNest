import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, ArrowUpRight, CheckCircle2, Link2, RefreshCw, ShieldCheck, ShieldAlert } from "lucide-react";
import { Link } from "react-router-dom";
import { request } from "../api/client";
import "./TaskExecutionSafety.css";

interface SideEffect {
  id: string;
  idempotency_key: string;
  target_system: string;
  description: string;
  status: string;
  agent_id: string;
  lease_epoch: number;
  evidence_summary: string;
  owner_note: string;
  updated_at: string;
}
interface RemoteEndpoint {
  id: string;
  agent_id: string;
  url: string;
  enabled: boolean;
  remote_project_id: string | null;
}
interface Delegation {
  id: string;
  task_id: string;
  project_id: string;
  endpoint_id: string;
  status: string;
  remote_task_id: string | null;
  remote_state: string | null;
  attempts: number;
  last_error: string;
  updated_at: string;
}
type Outcome = "verified_executed" | "verified_not_executed";

interface Props {
  taskId: string;
  projectId: string;
  taskStatus: string;
  taskRisk: string;
  isOwner: boolean;
  onChanged: () => void;
}

const sideEffectLabels: Record<string, string> = {
  pending: "结果待确认",
  reported_executed: "执行者报告已执行",
  unknown: "外部结果未知",
  verified_executed: "已核实执行",
  verified_not_executed: "已核实未执行"
};
const delegationLabels: Record<string, string> = {
  pending: "本地排队，尚未确认发送",
  remote: "远端执行中",
  unknown: "发送结果未知，禁止重发",
  cancel_requested: "已请求取消，等待远端确认",
  canceled: "已取消",
  submitted: "远端提交，等待本地验收",
  failed: "远端执行失败",
  changes_requested: "需要修改",
  released: "已解除绑定",
};
const errorText = (reason: unknown) => reason instanceof Error ? reason.message : "请求失败";
const when = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export default function TaskExecutionSafety({ taskId, projectId, taskStatus, taskRisk, isOwner, onChanged }: Props) {
  const [effects, setEffects] = useState<SideEffect[]>([]);
  const [endpoints, setEndpoints] = useState<RemoteEndpoint[]>([]);
  const [delegation, setDelegation] = useState<Delegation | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");

  const [effectId, setEffectId] = useState("");
  const [outcome, setOutcome] = useState<Outcome | "">("");
  const [evidenceNote, setEvidenceNote] = useState("");
  const [endpointId, setEndpointId] = useState("");
  const [remoteTaskId, setRemoteTaskId] = useState("");

  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const [ledger, peerRows, current] = await Promise.all([
        request<SideEffect[]>("/tasks/" + encodeURIComponent(taskId) + "/side-effects"),
        isOwner ? request<RemoteEndpoint[]>("/projects/" + encodeURIComponent(projectId) + "/a2a/remotes") : Promise.resolve([]),
        isOwner ? request<Delegation | null>("/tasks/" + encodeURIComponent(taskId) + "/a2a/delegation") : Promise.resolve(null),
      ]);
      setEffects(ledger);
      setEndpoints(peerRows);
      setDelegation(current);
    } catch (reason) {
      setError(errorText(reason));
      setEffects([]);
      setEndpoints([]);
      setDelegation(null);
    } finally { setLoading(false); }
  }, [taskId, projectId, isOwner]);

  useEffect(() => { void load(); }, [load]);

  const mutate = async (action: () => Promise<unknown>, success: string) => {
    setSaving(true); setError(""); setNotice("");
    try {
      await action();
      await load();
      setNotice(success);
      onChanged();
    } catch (reason) { setError(errorText(reason)); }
    finally { setSaving(false); }
  };

  const openReconciliation = (id: string) => {
    setEffectId((current) => current === id ? "" : id);
    setOutcome("");
    setEvidenceNote("");
    setNotice("");
  };

  const reconcileEffect = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!effectId || !outcome || evidenceNote.trim().length < 5) return;
    const effect = effects.find((item) => item.id === effectId);
    if (!effect) return;
    const action = outcome === "verified_executed" ? "确已执行" : "确认未执行";
    if (!window.confirm("确认已通过外部系统的权威记录查证“" + effect.target_system + "”的结果为【" + action + "】？\n\n该结论会写入审计台账，不能仅凭 OtterNest 超时或没有回包判断。")) return;
    void mutate(
      () => request("/side-effects/" + encodeURIComponent(effect.id) + "/reconcile", {
        method: "POST", body: JSON.stringify({ status: outcome, note: evidenceNote.trim() })
      }),
      "人工对账结论已记录。"
    ).then(() => { setEffectId(""); setOutcome(""); setEvidenceNote(""); });
  };

  const queueDelegation = () => {
    if (!endpointId) return;
    const peer = endpoints.find((item) => item.id === endpointId);
    if (!peer || !window.confirm("确认将此任务交给外部 A2A Agent？\n\n远端：" + peer.url + "\n\n后端会验证项目状态、任务风险、Agent 资格和凭据。请求提交后不能当作尚未发送而直接重试。")) return;
    void mutate(
      () => request("/tasks/" + encodeURIComponent(taskId) + "/a2a/delegate", {
        method: "POST", body: JSON.stringify({ endpoint_id: endpointId })
      }),
      "A2A 委托已进入待处理队列；尚不代表远端已接单。"
    ).then(() => setEndpointId(""));
  };

  const cancelDelegation = () => {
    if (!delegation || !window.confirm("确认请求取消远端委托？\n\n对于已到远端的任务，取消请求不等于取消成功。必须等待远端确认，不能据此重复执行。")) return;
    void mutate(
      () => request("/a2a/delegations/" + encodeURIComponent(delegation.id) + "/cancel", { method: "POST" }),
      "取消请求已记录。请继续检查远端状态。"
    );
  };

  const releaseDelegation = () => {
    if (!delegation || !window.confirm("只允许对已确认终止且阻塞的委托解除绑定。确定继续？\n\n历史映射会保留以便审计。解除绑定不代表可以不经检查重复外部副作用。")) return;
    void mutate(
      () => request("/a2a/delegations/" + encodeURIComponent(delegation.id) + "/release", { method: "POST" }),
      "远端委托绑定已解除，历史记录仍保留。"
    );
  };

  const reconcileDelegation = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!delegation || !remoteTaskId.trim()) return;
    if (!window.confirm("确认已在远端系统核实该任务 ID：\n" + remoteTaskId.trim() + "\n\n此操作只建立已核实映射，不重新发送任务。")) return;
    void mutate(
      () => request("/a2a/delegations/" + encodeURIComponent(delegation.id) + "/reconcile", {
        method: "POST", body: JSON.stringify({ remote_task_id: remoteTaskId.trim() })
      }),
      "远端任务 ID 已记录，后端将根据可用的轮询 Worker 继续核对。"
    ).then(() => setRemoteTaskId(""));
  };

  const unresolvedEffects = effects.filter((effect) => ["pending", "unknown", "reported_executed"].includes(effect.status));
  const selected = effects.find((effect) => effect.id === effectId);
  const canDelegate = isOwner && taskStatus === "ready" && (taskRisk === "R0" || taskRisk === "R1") && !delegation;
  const canCancel = Boolean(delegation && (
    (delegation.status === "pending" && taskStatus === "ready") ||
    (delegation.status === "remote" && delegation.remote_task_id && taskStatus === "running")
  ));
  const canRelease = Boolean(delegation && ["failed", "changes_requested"].includes(delegation.status) && taskStatus === "blocked");
  const canReconcileRemote = Boolean(delegation?.status === "unknown" && ["ready", "running"].includes(taskStatus));

  return <>
    <section className="task-detail-section task-detail-wide safety-ops-section">
      <div className="task-detail-section-head">
        <div><h3><ShieldCheck size={16} />外部副作用台账</h3>
          <p>由执行 Agent 在任务租约内登记和报告；所有者仅核对真实外部结果。</p></div>
        <span className={"status-tag " + (unresolvedEffects.length ? "tag-amber" : "tag-green")}>{effects.length} 条记录 · {unresolvedEffects.length} 条待核实</span>
      </div>
      {error && <div className="inline-error"><AlertTriangle size={14} />{error}</div>}
      {notice && <div className="success-notice"><CheckCircle2 size={14} />{notice}<button onClick={() => setNotice("")}>关闭</button></div>}
      {unresolvedEffects.length > 0 && <p className="safety-warning"><AlertTriangle size={15} />外部结果尚未核实。不可仅凭超时或请求失败判定未执行，更不可直接重发可能产生副作用的操作。</p>}
      <div className="safety-items">
        {effects.map((effect) => {
          const reviewed = ["verified_executed", "verified_not_executed"].includes(effect.status);
          return <article className="safety-item" key={effect.id}>
            <div className="safety-item-head">
              <div><strong>{effect.target_system}</strong><code>{effect.idempotency_key}</code></div>
              <span className={"status-tag " + (reviewed ? "tag-green" : effect.status === "unknown" ? "tag-red" : "tag-amber")}>{sideEffectLabels[effect.status] || effect.status}</span>
            </div>
            <p>{effect.description}</p>
            <small>Agent {effect.agent_id.slice(0, 8)} · 租约 #{effect.lease_epoch} · {when(effect.updated_at)}</small>
            {effect.evidence_summary && <p><b>执行者证据：</b>{effect.evidence_summary}</p>}
            {effect.owner_note && <p><b>所有者对账：</b>{effect.owner_note}</p>}
            {isOwner && !reviewed && <button className="secondary-button compact" disabled={saving} onClick={() => openReconciliation(effect.id)}>
              {effectId === effect.id ? "收起核实表单" : "人工核实外部结果"}</button>}
          </article>;
        })}
      </div>
      {!loading && effects.length === 0 && <div className="dag-empty">此任务暂无已登记的外部副作用。没有台账不构成外部操作未发生的证明。</div>}
      {isOwner && selected && <form className="safety-form" onSubmit={reconcileEffect}>
        <strong>核实「{selected.target_system}」的真实结果</strong>
        <p>请先在目标系统检查权威交易记录、作业日志或任务执行证据，然后选择结论。</p>
        <label>人工核实结论<select value={outcome} required onChange={(event) => setOutcome(event.target.value as Outcome | "")}>
          <option value="">请选择核实结果…</option>
          <option value="verified_executed">确认已经执行</option>
          <option value="verified_not_executed">确认没有执行</option>
        </select></label>
        <label>外部证据与对账备注<textarea rows={3} value={evidenceNote} minLength={5} maxLength={3000} required
          onChange={(event) => setEvidenceNote(event.target.value)} placeholder="说明查询了哪个外部系统、记录 ID、返回状态及人工核实依据…" /></label>
        <div className="form-actions"><button className="primary-button compact" disabled={saving || !outcome || evidenceNote.trim().length < 5}>提交人工对账</button>
          <button className="secondary-button compact" type="button" onClick={() => openReconciliation(effectId)}>取消</button></div>
      </form>}
    </section>

    {isOwner && <section className="task-detail-section task-detail-wide safety-ops-section">
      <div className="task-detail-section-head">
        <div><h3><Link2 size={16} />A2A 任务委托</h3><p>只允许 R0/R1 的就绪任务向项目内已登记、具备核验能力的远端 Agent 委托。</p></div>
        <button className="secondary-button compact" disabled={saving || loading} onClick={() => void load()}><RefreshCw size={13} />刷新状态</button>
      </div>
      {delegation ? <div className="safety-item">
        <div className="safety-item-head">
          <div><strong>{delegationLabels[delegation.status] || delegation.status}</strong>
            <small>委托 ID：{delegation.id}</small></div>
          <span className={"status-tag " + (delegation.status === "unknown" ? "tag-red" : ["released", "submitted", "canceled"].includes(delegation.status) ? "tag-green" : "tag-amber")}>{delegation.status}</span>
        </div>
        <div className="safety-delegation-meta">
          <span>远端任务 ID：<code>{delegation.remote_task_id || "未知 / 尚未确认"}</code></span>
          <span>远端状态：{delegation.remote_state || "尚未返回"}</span>
          <span>远端通信尝试：{delegation.attempts}</span>
          <span>最后更新：{when(delegation.updated_at)}</span>
        </div>
        {delegation.last_error && <p className="safety-warning"><AlertTriangle size={13} />{delegation.last_error}</p>}
        {delegation.status === "unknown" && <p className="safety-warning"><ShieldAlert size={15} />远端可能已接受请求但回包丢失。禁止重新发起委托；必须先核实远端任务 ID。</p>}
        <div className="safety-actions">
          {canCancel && <button className="secondary-button compact" disabled={saving} onClick={cancelDelegation}>请求取消委托</button>}
          {canRelease && <button className="secondary-button compact" disabled={saving} onClick={releaseDelegation}>解除终止委托绑定</button>}
        </div>
        {canReconcileRemote && <form className="safety-form" onSubmit={reconcileDelegation}>
          <label>已在远端核实的任务 ID<input value={remoteTaskId} maxLength={128} required
            onChange={(event) => setRemoteTaskId(event.target.value)} placeholder="输入经过核实的远端任务 ID" /></label>
          <button className="primary-button compact" disabled={saving || !remoteTaskId.trim()}>记录映射并交由 Worker 核对</button>
        </form>}
        {delegation.status === "cancel_requested" && <p className="form-hint">仅已提出取消请求，不能将此任务视为已取消或可安全重发。</p>}
        {delegation.status === "submitted" && <p className="form-hint">远端成果已提交。请在任务中心走所有者验收流程，不允许自动批准。</p>}
      </div> : <div className="safety-form">
        <p>当前任务尚无 A2A 委托。远端注册由<Link to="/interfaces" className="safety-inline-link">外部接口</Link>管理，必须提前配置主机允许列表和服务器端凭据。</p>
        <label>可用的 A2A 远端<select value={endpointId} disabled={!canDelegate || saving} onChange={(event) => setEndpointId(event.target.value)}>
          <option value="">选择已登记远端…</option>
          {endpoints.filter((endpoint) => endpoint.enabled).map((endpoint) => <option key={endpoint.id} value={endpoint.id}>{endpoint.url}</option>)}
        </select></label>
        <button className="primary-button compact" disabled={!canDelegate || !endpointId || saving} onClick={queueDelegation}>明确授权并加入委托队列</button>
        {!canDelegate && <p className="form-hint">仅当前状态为「待执行」且风险 R0/R1 的任务可发起新委托；已有委托不会被覆盖。</p>}
        {!endpoints.some((endpoint) => endpoint.enabled) && <Link to="/interfaces" className="secondary-button compact safety-link-button">前往登记远端 Agent<ArrowUpRight size={13} /></Link>}
      </div>}
      <p className="form-hint safety-footer-note">登记或排队 ≠ 远端已执行；只有独立的 A2A Worker 能与远端通信，且所有状态需以服务端台账和实际远端确认结果为准。</p>
    </section>}
  </>;
}
