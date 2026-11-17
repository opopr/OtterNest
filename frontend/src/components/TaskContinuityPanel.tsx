import { useCallback, useEffect, useState, type FormEvent } from "react";
import { AlertTriangle, ArchiveRestore, CheckCircle2, ClipboardCheck, Download, RefreshCw, ShieldAlert } from "lucide-react";
import { request } from "../api/client";
import "./TaskContinuityPanel.css";

interface Checkpoint {
  id: string;
  task_id: string;
  agent_id: string;
  lease_epoch: number;
  step_number: number;
  summary: string;
  next_steps: string;
  artifact_ids: string[];
  verification_status: string;
  owner_note: string;
  created_at: string;
  verified_at: string | null;
}
interface HandoffBundle {
  task: { id: string; title: string; status: string; version: number; lease_epoch: number };
  latest_verified_checkpoint: Checkpoint | null;
  latest_self_reported_checkpoint: Checkpoint | null;
  dependencies: { depends_on: string[]; satisfied: boolean };
  artifacts: Array<{ id: string; name: string; review_status: string }>;
  side_effects: Array<{ id: string; status: string; target_system: string }>;
  handoff_boundary: string;
  [key: string]: unknown;
}
type ReviewStatus = "owner_verified" | "rejected";
interface Props { taskId: string; taskStatus: string; isOwner: boolean; onChanged: () => void; }
const time = (date: string | null) => date ? new Date(date).toLocaleString("zh-CN", { hour12: false }) : "—";
const labels: Record<string, string> = {
  self_reported: "Agent 自报，未经核验",
  owner_verified: "所有者已核验",
  rejected: "所有者已驳回"
};
const message = (reason: unknown) => reason instanceof Error ? reason.message : "操作失败";

export default function TaskContinuityPanel({ taskId, taskStatus, isOwner, onChanged }: Props) {
  const [checkpoints, setCheckpoints] = useState<Checkpoint[]>([]);
  const [handoff, setHandoff] = useState<HandoffBundle | null>(null);
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [handoffBusy, setHandoffBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [selectedId, setSelectedId] = useState("");
  const [reviewStatus, setReviewStatus] = useState<ReviewStatus>("owner_verified");
  const [reviewNote, setReviewNote] = useState("");
  const [revokeReason, setRevokeReason] = useState("");
  const [understandFencing, setUnderstandFencing] = useState(false);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      setCheckpoints(await request<Checkpoint[]>("/tasks/" + encodeURIComponent(taskId) + "/checkpoints"));
    } catch (reason) { setError(message(reason)); setCheckpoints([]); }
    finally { setLoading(false); }
  }, [taskId]);

  useEffect(() => { void load(); setHandoff(null); }, [load]);

  const verifyCheckpoint = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isOwner || !selectedId || (reviewStatus === "rejected" && reviewNote.trim().length < 5)) return;
    const selected = checkpoints.find((cp) => cp.id === selectedId);
    if (!selected || selected.verification_status !== "self_reported") return;
    if (!window.confirm("确认" + (reviewStatus === "owner_verified" ? "核验通过" : "驳回") + "这条 Agent 检查点？\n\n核验只确认这一条记录，不证明模型对话状态或外部进程已经安全迁移。")) return;
    setSaving(true); setError(""); setNotice("");
    try {
      await request("/checkpoints/" + encodeURIComponent(selectedId) + "/verify", {
        method: "POST", body: JSON.stringify({ status: reviewStatus, note: reviewNote.trim() })
      });
      setSelectedId(""); setReviewStatus("owner_verified"); setReviewNote("");
      await load(); setHandoff(null);
      setNotice("检查点审核已记录。"); onChanged();
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const revokeLease = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!isOwner || taskStatus !== "running" || !understandFencing || revokeReason.trim().length < 5) return;
    if (!window.confirm("高风险操作：撤销旧租约只会隔离旧 epoch 的控制平面提交，不能停止外部进程或撤销已发生的副作用。\n\n非 R3 任务可能重新进入可调度状态，务必已在执行节点人工核实进程状态。确定继续？")) return;
    setSaving(true); setError(""); setNotice("");
    try {
      const result = await request<{ remote_execution_stopped_verified: boolean; lease_epoch: number }>(
        "/tasks/" + encodeURIComponent(taskId) + "/revoke-lease", {
          method: "POST", body: JSON.stringify({ reason: revokeReason.trim(), target_agent_id: null })
        }
      );
      setRevokeReason(""); setUnderstandFencing(false);
      await load(); setHandoff(null);
      setNotice("旧租约已隔离，新 epoch 为 " + result.lease_epoch + "。外部执行已停止：未验证（服务端返回 " + String(result.remote_execution_stopped_verified) + "）。");
      onChanged();
    } catch (reason) { setError(message(reason)); }
    finally { setSaving(false); }
  };

  const loadHandoff = async () => {
    setHandoffBusy(true); setError("");
    try { setHandoff(await request<HandoffBundle>("/tasks/" + encodeURIComponent(taskId) + "/handoff")); }
    catch (reason) { setError(message(reason)); }
    finally { setHandoffBusy(false); }
  };

  const downloadHandoff = () => {
    if (!handoff || !isOwner) return;
    if (!window.confirm("交接包包含任务指令、已批准决策与副作用状态。仅保存到可信设备，不要上传公共仓库或未经授权的外部 AI。确定导出吗？")) return;
    const json = JSON.stringify(handoff, null, 2);
    const url = URL.createObjectURL(new Blob([json], { type: "application/json;charset=utf-8" }));
    const a = document.createElement("a");
    a.href = url;
    a.download = "otternest-handoff-" + taskId + ".json";
    document.body.appendChild(a); a.click(); a.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
  };

  const selected = checkpoints.find((cp) => cp.id === selectedId);
  return <section className="task-detail-section task-detail-wide continuity-section">
    <div className="task-detail-section-head">
      <div><h3><ClipboardCheck size={16} />检查点与故障交接</h3><p>仅由持有效租约的 Agent 创建检查点；所有者负责核验、撤销旧租约及审查交接内容。</p></div>
      <button className="secondary-button compact" disabled={loading || saving} onClick={() => void load()}><RefreshCw size={13} />刷新</button>
    </div>
    {error && <div className="inline-error"><AlertTriangle size={14} />{error}</div>}
    {notice && <div className="success-notice"><CheckCircle2 size={14} />{notice}<button onClick={() => setNotice("")}>关闭</button></div>}
    <div className="continuity-checkpoint-list">
      {checkpoints.map((cp) => <article className="continuity-checkpoint" key={cp.id}>
        <div className="continuity-checkpoint-title">
          <strong>租约 #{cp.lease_epoch} · 步骤 {cp.step_number}</strong>
          <span className={"status-tag " + (cp.verification_status === "owner_verified" ? "tag-green" : cp.verification_status === "rejected" ? "tag-red" : "tag-amber")}>{labels[cp.verification_status] || cp.verification_status}</span>
        </div>
        <p>{cp.summary}</p>
        {cp.next_steps && <p><b>后续步骤：</b>{cp.next_steps}</p>}
        {cp.artifact_ids.length > 0 && <p><b>关联成果：</b>{cp.artifact_ids.length} 个</p>}
        {cp.owner_note && <p><b>审核备注：</b>{cp.owner_note}</p>}
        <small>提交 Agent {cp.agent_id.slice(0, 8)} · {time(cp.created_at)}</small>
        {isOwner && cp.verification_status === "self_reported" && <button className="secondary-button compact" disabled={saving}
          onClick={() => { setSelectedId(cp.id); setReviewStatus("owner_verified"); setReviewNote(""); }}>审核检查点</button>}
      </article>)}
    </div>
    {loading && <div className="dag-empty">正在读取检查点…</div>}
    {!loading && checkpoints.length === 0 && <div className="dag-empty">暂无执行者提交的检查点。</div>}
    {isOwner && selected && <form className="continuity-form" onSubmit={(event) => void verifyCheckpoint(event)}>
      <strong>审核租约 #{selected.lease_epoch} · 步骤 {selected.step_number}</strong>
      <label>审核结论<select value={reviewStatus} onChange={(event) => setReviewStatus(event.target.value as ReviewStatus)}>
        <option value="owner_verified">核验通过</option><option value="rejected">驳回</option></select></label>
      <label>审核依据与备注<textarea rows={3} value={reviewNote} onChange={(event) => setReviewNote(event.target.value)} maxLength={2000}
        minLength={reviewStatus === "rejected" ? 5 : undefined} required={reviewStatus === "rejected"} placeholder="记录核验方式、可信成果或需要修正的问题…" /></label>
      <div className="form-actions"><button className="primary-button compact" disabled={saving || (reviewStatus === "rejected" && reviewNote.trim().length < 5)}>提交审核</button>
        <button type="button" className="secondary-button compact" onClick={() => setSelectedId("")}>取消</button></div>
    </form>}

    <div className="continuity-operations">
      <div className="continuity-ops-heading"><ArchiveRestore size={16} /><strong>故障交接包</strong></div>
      <p>交接包会同时标注「最近已核验」和「最新自报」的检查点，且包含依赖、成果和外部副作用状态。模型内部会话与真实进程状态不会自动转移。</p>
      <button className="secondary-button compact" disabled={handoffBusy} onClick={() => void loadHandoff()}><RefreshCw size={13} />{handoffBusy ? "读取中…" : "查看当前交接包"}</button>
      {handoff && <div className="continuity-handoff">
        <div className="continuity-handoff-summary">
          <div><span>任务版本</span><strong>v{handoff.task.version}</strong></div>
          <div><span>最新已核验检查点</span><strong>{handoff.latest_verified_checkpoint ? "租约 #" + handoff.latest_verified_checkpoint.lease_epoch + " · 步骤 " + handoff.latest_verified_checkpoint.step_number : "无"}</strong></div>
          <div><span>最新自报检查点</span><strong>{handoff.latest_self_reported_checkpoint ? "未核实" : "无"}</strong></div>
          <div><span>依赖状态</span><strong>{handoff.dependencies.satisfied ? "已满足" : "未满足"}</strong></div>
          <div><span>成果数</span><strong>{handoff.artifacts.length}</strong></div>
          <div><span>外部副作用数</span><strong>{handoff.side_effects.length}</strong></div>
        </div>
        <p className="continuity-boundary"><ShieldAlert size={14} />交接包只代表服务端已记录事实，不保证外部副作用已撤销、旧执行节点已停止或上下文完全可迁移。</p>
        {isOwner && <button className="secondary-button compact" onClick={downloadHandoff}><Download size={14} />导出授权交接 JSON</button>}
      </div>}
    </div>

    {isOwner && taskStatus === "running" && <form className="continuity-form continuity-revoke" onSubmit={(event) => void revokeLease(event)}>
      <div className="continuity-ops-heading"><ShieldAlert size={16} /><strong>撤销旧执行租约</strong></div>
      <p className="continuity-boundary"><AlertTriangle size={14} />此操作只使旧 epoch 无法在控制平面提交，不会终止远端进程。服务端也不验证远端已停止；非 R3 任务可能重新进入调度。</p>
      <label>撤销原因（至少 5 字符）<textarea rows={2} value={revokeReason} maxLength={2000} required minLength={5}
        onChange={(event) => setRevokeReason(event.target.value)} placeholder="说明旧节点故障、外部执行状态核查结果及撤销原因…" /></label>
      <label className="continuity-checkbox"><input type="checkbox" checked={understandFencing} onChange={(event) => setUnderstandFencing(event.target.checked)} />
        <span>我理解租约隔离不等于进程终止，已确认外部状态并评估重复副作用风险。</span></label>
      <button className="secondary-button compact" disabled={saving || !understandFencing || revokeReason.trim().length < 5}>撤销租约（高风险）</button>
    </form>}
  </section>;
}
