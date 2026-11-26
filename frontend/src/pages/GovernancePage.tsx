import { ErrorNotice } from "../components/PageFeedback";
import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle, Bot, Check, CheckCircle2, ClipboardCheck, FileSearch, MessageSquare,
  RefreshCw, SearchCheck, ShieldCheck, Users, X, XCircle
} from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";
import CommandChatPanel from "../components/CommandChatPanel";

interface Decision {
  id: string;
  question: string;
  resolution: string;
  rationale: string;
  status: string;
}
interface EvidenceCheck {
  reference: string;
  status: "linked" | "unresolved" | "unsupported" | string;
  factual_claim_verified?: boolean;
}
interface Review {
  id: string;
  agent_id: string;
  stance: "support" | "oppose" | "alternative";
  reasoning: string;
  evidence: string[];
  evidence_checks: EvidenceCheck[];
  evidence_verified: boolean;
  created_at: string;
}
interface Deliberation {
  decision_id: string;
  project_id: string;
  round: number;
  status: "open" | "closed";
  opened_at: string;
  closed_at: string | null;
  closing_note: string;
  first_round: Review[];
  rebuttals: Array<{
    id: string;
    agent_id: string;
    stance: string;
    reasoning: string;
    evidence: string[];
    evidence_checks?: EvidenceCheck[];
  }>;
}
interface AutoPolicy {
  decision_id: string;
  reviewer_ids: string[];
  owner_approval_required: boolean;
  max_rounds: number;
}
interface ProjectMember {
  id: string;
  agent_id: string;
  agent_name: string;
  role: string;
  enabled: boolean;
}
interface CommandPlan {
  id: string;
  task_id: string;
  project_id: string;
  action: string;
  parameters: Record<string, unknown>;
  expected_version: number;
  status: string;
  expires_at: string;
  confirmation_required: boolean;
  task_title?: string;
  task_status?: string;
  task_risk?: string;
  result?: Record<string, unknown> | null;
}

const taskStatus: Record<string, string> = { waiting_approval: "等待授权", submitted: "等待验收" };
const stanceLabel: Record<string, string> = { support: "支持", oppose: "反对", alternative: "替代方案" };
const evidenceStatusLabel: Record<string, string> = {
  linked: "已关联项目记录",
  unresolved: "未匹配或未发布",
  unsupported: "格式不支持"
};
const timeText = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export default function GovernancePage() {
  const dialog = useDialog();
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [selectedDecisionId, setSelectedDecisionId] = useState("");
  const [reviews, setReviews] = useState<Review[]>([]);
  const [deliberation, setDeliberation] = useState<Deliberation | null>(null);
  const [autoPolicy, setAutoPolicy] = useState<AutoPolicy | null>(null);
  const [policySelection, setPolicySelection] = useState<string[]>([]);
  const [evidenceInput, setEvidenceInput] = useState("");
  const [evidenceResult, setEvidenceResult] = useState<{ checks: EvidenceCheck[]; content_verification: string } | null>(null);
  const [resolutionNote, setResolutionNote] = useState("");
  const [closingNote, setClosingNote] = useState("");
  const [plan, setPlan] = useState<CommandPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"tasks" | "decisions" | "command" | "chat">("tasks");
  const [approvalFilter, setApprovalFilter] = useState("all");
  const [commandText, setCommandText] = useState("");

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);
  const selectedDecision = useMemo(() => decisions.find((item) => item.id === selectedDecisionId) || null, [decisions, selectedDecisionId]);
  const pendingTasks = tasks.filter((task) => task.status === "waiting_approval" || task.status === "submitted");
  const dissentCount = reviews.filter((review) => review.stance !== "support").length +
    (deliberation?.rebuttals || []).filter((reply) => reply.stance !== "support").length;

  const memberNameMap = useMemo(() => {
    const map = new Map<string, string>();
    members.forEach((m) => map.set(m.agent_id, m.agent_name));
    return map;
  }, [members]);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, []);

  const loadProjectData = useCallback(async (projectId: string) => {
    setDetailLoading(true);
    setError("");
    try {
      const encoded = encodeURIComponent(projectId);
      const [rawTasks, rawDecisions, rawMembers] = await Promise.all([
        request<Task[]>("/projects/" + encoded + "/tasks"),
        request<Decision[]>("/projects/" + encoded + "/decisions"),
        request<ProjectMember[]>("/projects/" + encoded + "/members").catch(() => [] as ProjectMember[])
      ]);
      const taskRows = Array.isArray(rawTasks) ? rawTasks : [];
      const decisionRows = Array.isArray(rawDecisions) ? rawDecisions : [];
      const memberRows = Array.isArray(rawMembers) ? rawMembers : [];
      setTasks(taskRows);
      setDecisions(decisionRows);
      setMembers(memberRows);
      setSelectedDecisionId((current) => decisionRows.some((item) => item.id === current) ? current : decisionRows.find((item) => item.status === "proposed")?.id || decisionRows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "治理事项读取失败"); }
    finally { setDetailLoading(false); }
  }, []);

  const loadDecision = useCallback(async (decisionId: string) => {
    if (!decisionId) {
      setReviews([]);
      setDeliberation(null);
      setAutoPolicy(null);
      setEvidenceResult(null);
      setEvidenceInput("");
      return;
    }
    setDetailLoading(true);
    setEvidenceResult(null);
    setPolicySelection([]);
    try {
      const encoded = encodeURIComponent(decisionId);
      const [rawReviews, delibRow, policyRow] = await Promise.all([
        request<Review[]>("/decisions/" + encoded + "/reviews"),
        request<Deliberation>("/decisions/" + encoded + "/deliberation").catch(() => null),
        request<AutoPolicy>("/decisions/" + encoded + "/deliberation/auto-policy").catch(() => null)
      ]);
      const reviewRows = Array.isArray(rawReviews) ? rawReviews : [];
      setReviews(reviewRows);
      setDeliberation(delibRow);
      setAutoPolicy(policyRow);
      const collectedRefs = Array.from(
        new Set([
          ...reviewRows.flatMap((r) => Array.isArray(r.evidence) ? r.evidence : []),
          ...(Array.isArray(delibRow?.rebuttals) ? delibRow.rebuttals : []).flatMap((r) => Array.isArray(r.evidence) ? r.evidence : [])
        ])
      );
      setEvidenceInput(collectedRefs.join("\n"));
    } catch (reason) { setError(reason instanceof Error ? reason.message : "决策审查信息读取失败"); }
    finally { setDetailLoading(false); }
  }, []);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedId) void loadProjectData(selectedId); else { setTasks([]); setDecisions([]); setMembers([]); } }, [selectedId, loadProjectData]);
  useEffect(() => { void loadDecision(selectedDecisionId); }, [selectedDecisionId, loadDecision]);

  const createTaskPlan = async (task: Task, action: "authorize_task" | "review_task", accepted?: boolean) => {
    let reason = "";
    if (action === "review_task" && accepted === false) {
      const input = await dialog.prompt({
        title: "退回任务返工",
        message: `请填写退回「${task.title}」的具体原因。`,
        label: "退回原因",
        placeholder: "例如：请补充单元测试或修正输出格式…",
        required: true,
        minLength: 1,
        multiline: true,
        confirmText: "生成退回预览"
      });
      if (input === null) return;
      reason = input;
    }
    setSaving(true);
    setError("");
    try {
      const nextPlan = await request<CommandPlan>("/command/plans", {
        method: "POST",
        body: JSON.stringify({
          task_id: task.id, action,
          ...(action === "review_task" ? { accepted, reason } : {})
        })
      });
      setPlan(nextPlan);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "生成命令预览失败"); }
    finally { setSaving(false); }
  };

  const confirmPlan = async () => {
    if (!plan) return;
    const confirmed = await dialog.confirm({
      title: "执行治理命令",
      message: "确认执行这条已预览的治理命令？服务端会再次校验任务版本与状态。",
      confirmText: "确认执行",
      tone: "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      await request<CommandPlan>("/command/plans/" + encodeURIComponent(plan.id) + "/confirm", { method: "POST" });
      setPlan(null);
      if (selectedId) await loadProjectData(selectedId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "命令确认失败；任务可能已变化，请重新预览"); }
    finally { setSaving(false); }
  };

  const interpretCommand = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId || commandText.trim().length < 4) return;
    setSaving(true);
    setError("");
    try {
      const preview = await request<CommandPlan & { interpretation: { mode: string; llm_interpreted: boolean; requires_explicit_confirmation: boolean } }>("/command/interpret", {
        method: "POST",
        body: JSON.stringify({ project_id: selectedId, text: commandText.trim() })
      });
      setPlan(preview);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "命令无法安全解释；没有创建执行计划");
    } finally { setSaving(false); }
  };

  const inspectEvidence = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedDecision) return;
    const references = evidenceInput
      .split(/[\n,]+/)
      .map((s) => s.trim())
      .filter(Boolean)
      .slice(0, 20);
    if (!references.length) {
      setError("请至少输入一条证据引用（格式如 artifact:<uuid> 或 memory:<uuid>）。");
      return;
    }
    setSaving(true);
    setError("");
    try {
      const res = await request<{ checks: EvidenceCheck[]; content_verification: string }>(
        "/decisions/" + encodeURIComponent(selectedDecision.id) + "/evidence/inspect",
        { method: "POST", body: JSON.stringify({ references }) }
      );
      setEvidenceResult(res);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "证据引用核查失败");
    } finally {
      setSaving(false);
    }
  };

  const togglePolicyMember = (agentId: string) => {
    setPolicySelection((cur) => {
      if (cur.includes(agentId)) return cur.filter((id) => id !== agentId);
      if (cur.length >= 8) return cur;
      return [...cur, agentId];
    });
  };

  const saveAutoPolicy = async () => {
    if (!selectedDecision || policySelection.length < 2) return;
    setSaving(true);
    setError("");
    try {
      const created = await request<AutoPolicy>(
        "/decisions/" + encodeURIComponent(selectedDecision.id) + "/deliberation/auto-policy",
        { method: "POST", body: JSON.stringify({ reviewer_ids: policySelection }) }
      );
      setAutoPolicy(created);
      setPolicySelection([]);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "配置自动协商审查名单失败");
    } finally {
      setSaving(false);
    }
  };

  const openSecondRound = async () => {
    if (!selectedDecision) return;
    const confirmed = await dialog.confirm({
      title: "开启第二轮独立讨论",
      message: "确认开启第二轮独立讨论？开启后不能创建第三轮。",
      confirmText: "开启第二轮"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request<Deliberation>("/decisions/" + encodeURIComponent(selectedDecision.id) + "/deliberation/open", { method: "POST" });
      await loadDecision(selectedDecision.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "开启第二轮失败"); }
    finally { setSaving(false); }
  };

  const closeSecondRound = async () => {
    if (!selectedDecision || closingNote.trim().length < 10) return;
    setSaving(true);
    try {
      await request<Deliberation>("/decisions/" + encodeURIComponent(selectedDecision.id) + "/deliberation/close", {
        method: "POST", body: JSON.stringify({ reason: closingNote.trim() })
      });
      setClosingNote("");
      await loadDecision(selectedDecision.id);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "关闭第二轮失败"); }
    finally { setSaving(false); }
  };

  const resolveDecision = async () => {
    if (!selectedDecision) return;
    if (deliberation?.status === "open") {
      setError("第二轮讨论尚未关闭，不能完成决策审批。");
      return;
    }
    if (dissentCount > 0 && resolutionNote.trim().length < 10) {
      setError("存在独立异议，请填写至少 10 个字符的所有者处理说明。");
      return;
    }
    const confirmed = await dialog.confirm({
      title: "所有者最终裁决",
      message: "确认由所有者完成该决策？独立审查意见不会自动替代所有者审批。",
      confirmText: "批准并裁决",
      tone: "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/decisions/" + encodeURIComponent(selectedDecision.id) + "/resolve", {
        method: "POST", body: JSON.stringify({ owner_note: resolutionNote.trim() })
      });
      setResolutionNote("");
      await loadProjectData(selectedId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "决策审批失败"); }
    finally { setSaving(false); }
  };

  const eligibleReviewers = members.filter((m) => m.enabled);

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>审批与治理</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadProjectData(selectedId)} disabled={loading || detailLoading}><RefreshCw size={15} className={loading || detailLoading ? "spin" : ""} />刷新</button></div>
      </div>
      <ErrorNotice message={error} onDismiss={() => setError("")} />
      <section className="panel governance-toolbar">
        <div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "尚未选择项目"}</strong></div>
        <ProjectSelect projects={projects} value={selectedId} onChange={setSelectedId} />
        <div className="governance-summary"><span><strong>{pendingTasks.length}</strong> 待处理任务</span><span><strong>{decisions.filter((item) => item.status === "proposed").length}</strong> 待审决策</span></div>
      </section>

      <div className="governance-tabs">
        <button className={tab === "tasks" ? "selected" : ""} onClick={() => setTab("tasks")}><ClipboardCheck size={16} />任务审批 <span>{pendingTasks.length}</span></button>
        <button className={tab === "decisions" ? "selected" : ""} onClick={() => setTab("decisions")}><ShieldCheck size={16} />决策审查 <span>{decisions.filter((item) => item.status === "proposed").length}</span></button>
        <button className={tab === "command" ? "selected" : ""} onClick={() => setTab("command")}><MessageSquare size={16} />受限命令</button>
        <button className={tab === "chat" ? "selected" : ""} onClick={() => setTab("chat")}><Bot size={16} />多轮指挥</button>
      </div>

      {tab === "tasks" ? (
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><ClipboardCheck size={17} /><h3>待授权与待验收任务</h3></div><span className="muted-inline">所有写操作先预览，再由所有者确认</span></div>
          <div className="approval-switch" role="group" aria-label="审批状态">{[{key:"all",label:"全部待办"},{key:"waiting_approval",label:"待授权"},{key:"submitted",label:"待验收"}].map(item => <button type="button" key={item.key} aria-pressed={approvalFilter === item.key} className={approvalFilter === item.key ? "selected" : ""} onClick={() => setApprovalFilter(item.key)}>{item.label}<span>{item.key === "all" ? pendingTasks.length : pendingTasks.filter(task => task.status === item.key).length}</span></button>)}</div>
          <div className="governance-task-list">
            {pendingTasks.filter(task => approvalFilter === "all" || task.status === approvalFilter).map((task) => <article className="governance-task" key={task.id}>
              <div className="governance-task-main"><div><strong>{task.title}</strong><span className={"status-tag " + (task.status === "waiting_approval" ? "tag-red" : "tag-blue")}>{taskStatus[task.status]}</span></div><p>{task.id} · {task.risk} · 优先级 {task.priority}</p><small>{task.instruction}</small></div>
              <div className="governance-task-actions">
                {task.status === "waiting_approval" ? <button className="primary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "authorize_task")}><ShieldCheck size={14} />生成授权预览</button> : <>
                  <button className="primary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "review_task", true)}><Check size={14} />验收通过预览</button>
                  <button className="secondary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "review_task", false)}><XCircle size={14} />退回返工</button>
                </>}
              </div>
            </article>)}
            {!pendingTasks.some(task => approvalFilter === "all" || task.status === approvalFilter) && <div className="empty-state"><CheckCircle2 size={26} /><strong>暂无此类待办</strong></div>}
          </div>
        </section>
      ) : tab === "decisions" ? (
        <div className="governance-decision-layout">
          <section className="panel decision-list-panel">
            <div className="panel-heading"><div className="panel-title"><FileSearch size={17} /><h3>决策列表</h3></div><span className="count-chip">{decisions.length}</span></div>
            {decisions.map((decision) => <button key={decision.id} className={"decision-list-item " + (decision.id === selectedDecisionId ? "selected" : "")} onClick={() => setSelectedDecisionId(decision.id)}>
              <span className="decision-list-copy"><strong>{decision.question}</strong><small>{decision.status === "proposed" ? "待所有者裁决" : "已批准"}</small></span>
              <span className={"status-tag " + (decision.status === "proposed" ? "tag-amber" : "tag-green")}>{decision.status === "proposed" ? "待审" : "已批"}</span>
            </button>)}
            {!decisions.length && <div className="empty-mini">该项目暂无决策记录</div>}
          </section>
          <section className="panel decision-detail-panel">
            {!selectedDecision ? <div className="empty-state"><MessageSquare size={26} /><strong>选择一项决策</strong><p>查看独立审查意见和讨论过程。</p></div> : <>
              <div className="panel-heading"><div className="panel-title"><MessageSquare size={17} /><h3>决策详情</h3></div><span className={"status-tag " + (selectedDecision.status === "proposed" ? "tag-amber" : "tag-green")}>{selectedDecision.status === "proposed" ? "待裁决" : "已批准"}</span></div>
              <div className="decision-question">{selectedDecision.question}</div>
              <div className="decision-answer-grid"><div><small>建议方案</small><p>{selectedDecision.resolution}</p></div><div><small>决策依据</small><p>{selectedDecision.rationale}</p></div></div>

              <div className="auto-policy-panel">
                <div className="decision-subheading">
                  <strong><Users size={14} /> 自动协商审查名单（Auto-Policy）</strong>
                  <span>{autoPolicy ? `已固定 ${autoPolicy.reviewer_ids.length} 位审查者 · 最多 ${autoPolicy.max_rounds} 轮` : "未配置"}</span>
                </div>
                {autoPolicy ? (
                  <div className="auto-policy-roster">
                    {autoPolicy.reviewer_ids.map((id) => (
                      <span className="status-tag tag-blue" key={id}>{memberNameMap.get(id) || id.slice(0, 8)}</span>
                    ))}
                  </div>
                ) : selectedDecision.status === "proposed" && reviews.length === 0 ? (
                  <>
                    <p className="form-hint">在第一轮审查开始前，可指定 2–8 名项目内已启用的 智能体 组成自动法定协商名单。</p>
                    {eligibleReviewers.length < 2 ? (
                      <p className="form-hint">当前项目已启用成员不足 2 位，请先在项目管理页添加成员。</p>
                    ) : (
                      <>
                        <div className="auto-policy-roster">
                          {eligibleReviewers.map((m) => (
                            <label className="auto-policy-chip" key={m.agent_id}>
                              <input
                                type="checkbox"
                                checked={policySelection.includes(m.agent_id)}
                                onChange={() => togglePolicyMember(m.agent_id)}
                              />
                              <span>{m.agent_name} ({getUiLabel(m.role)})</span>
                            </label>
                          ))}
                        </div>
                        <div className="form-actions">
                          <button type="button" className="secondary-button compact" disabled={saving || policySelection.length < 2} onClick={() => void saveAutoPolicy()}>
                            保存协商名单（已选 {policySelection.length}/8）
                          </button>
                        </div>
                      </>
                    )}
                  </>
                ) : (
                  <p className="form-hint">首轮审查已开始或决策已归档，未设置固定协商名单。</p>
                )}
              </div>

              <div className="decision-subheading"><strong>第一轮独立审查</strong><span>{reviews.length} 条意见</span></div>
              {reviews.map((review) => <article className="review-card" key={review.id}>
                <div className="review-card-head"><strong>{memberNameMap.get(review.agent_id) || `Agent ${review.agent_id.slice(0, 8)}`}</strong><span className={"status-tag " + (review.stance === "support" ? "tag-green" : review.stance === "oppose" ? "tag-red" : "tag-amber")}>{stanceLabel[review.stance]}</span></div>
                <p>{review.reasoning}</p>
                {review.evidence.length > 0 && (
                  <div className="evidence-list">
                    <small>证据引用（来源校验仅供参考，不证明事实真伪）</small>
                    {review.evidence.map((item) => {
                      const check = review.evidence_checks?.find((c) => c.reference === item);
                      return <code key={item}>{item}{check?.status ? ` · ${evidenceStatusLabel[check.status] || check.status}` : ""}</code>;
                    })}
                  </div>
                )}
                <small className="muted-inline">{timeText(review.created_at)}</small>
              </article>)}
              {!reviews.length && <p className="form-hint">尚无 智能体 独立审查意见。只有注册并加入该项目的 智能体 可以提交审查。</p>}

              <form className="auto-policy-panel" onSubmit={inspectEvidence}>
                <div className="decision-subheading">
                  <strong><SearchCheck size={14} /> 证据链引用核查</strong>
                  <span>支持 artifact / memory / decision / task 引用</span>
                </div>
                <label className="field-label">
                  待核查引用列表（每行或逗号分隔，最多 20 条）
                  <textarea
                    rows={2}
                    value={evidenceInput}
                    onChange={(e) => setEvidenceInput(e.target.value)}
                    placeholder="例如：artifact:00000000-0000-0000-0000-000000000000"
                  />
                </label>
                <div className="form-actions">
                  <button type="submit" className="secondary-button compact" disabled={saving || !evidenceInput.trim()}>
                    <SearchCheck size={14} />核查项目引用存在性
                  </button>
                </div>
                {evidenceResult && (
                  <div className="evidence-check-list">
                    <small className="muted-inline">内容真伪核验：{evidenceResult.content_verification === "not_performed" ? "未自动核验（仅检查项目内存在性与发布状态）" : evidenceResult.content_verification}</small>
                    {evidenceResult.checks.map((c, idx) => (
                      <div className="evidence-check-row" key={idx}>
                        <code>{c.reference}</code>
                        <span className={"status-tag " + (c.status === "linked" ? "tag-green" : c.status === "unresolved" ? "tag-amber" : "tag-red")}>
                          {evidenceStatusLabel[c.status] || c.status}
                        </span>
                      </div>
                    ))}
                  </div>
                )}
              </form>

              {deliberation && <div className="deliberation-panel">
                <div className="decision-subheading"><strong>第二轮讨论</strong><span className={"status-tag " + (deliberation.status === "open" ? "tag-blue" : "tag-neutral")}>{deliberation.status === "open" ? "讨论中" : "已关闭"}</span></div>
                <p className="form-hint">第二轮最多一轮，不会自动批准决策。所有者仍需最终裁决。</p>
                {deliberation.rebuttals.map((reply) => <article className="review-card" key={reply.id}><div className="review-card-head"><strong>{memberNameMap.get(reply.agent_id) || `Agent ${reply.agent_id.slice(0, 8)}`}</strong><span className="status-tag tag-neutral">{stanceLabel[reply.stance] || reply.stance}</span></div><p>{reply.reasoning}</p></article>)}
                {deliberation.status === "open" && selectedDecision.status === "proposed" && <div className="form-panel"><label>关闭说明（至少 10 个字符）<textarea rows={2} value={closingNote} onChange={(event) => setClosingNote(event.target.value)} /></label><button className="secondary-button compact" disabled={saving || closingNote.trim().length < 10} onClick={() => void closeSecondRound()}>关闭第二轮</button></div>}
                {deliberation.closing_note && <p className="form-hint">关闭说明：{deliberation.closing_note}</p>}
              </div>}
              {!deliberation && selectedDecision.status === "proposed" && reviews.length >= 2 && <div className="deliberation-callout"><div><strong>满足开启第二轮的最低审查人数</strong><p>至少两名独立审查者已提交意见。开启后不能再创建第三轮。</p></div><button className="secondary-button compact" disabled={saving} onClick={() => void openSecondRound()}>开启第二轮</button></div>}
              {selectedDecision.status === "proposed" && <div className="decision-resolution">
                <label>所有者最终处理说明{dissentCount > 0 ? "（存在异议，至少 10 个字符）" : "（可选）"}<textarea rows={3} value={resolutionNote} onChange={(event) => setResolutionNote(event.target.value)} placeholder="记录对独立意见的回应、证据权衡与最终决策理由…" /></label>
                <div className="decision-resolution-foot"><span className="muted-inline">异议 / 替代意见：{dissentCount} 条</span><button className="primary-button compact" disabled={saving || deliberation?.status === "open" || (dissentCount > 0 && resolutionNote.trim().length < 10)} onClick={() => void resolveDecision()}><ShieldCheck size={15} />所有者最终裁决</button></div>
              </div>}
            </>}
          </section>
        </div>
      ) : tab === "command" ? (
        <section className="panel command-input-panel">
          <div className="panel-heading"><div className="panel-title"><MessageSquare size={17} /><h3>受限自然语言命令</h3></div><span className="status-tag tag-blue">本地规则解析</span></div>
          <p className="form-hint">该入口只支持后端白名单中的中文命令模板，不会调用外部模型，也不会直接执行操作。解析成功后会创建与任务审批相同的版本化命令预览。</p>
          <form className="form-panel" onSubmit={interpretCommand}>
            <label>输入命令<textarea rows={3} value={commandText} onChange={(event) => setCommandText(event.target.value)} required minLength={4} maxLength={3500} placeholder="例如：验收通过任务「完成首页 React 改造」" /></label>
            <div className="command-examples">
              <span>批准任务「任务名称」</span>
              <span>取消任务「任务名称」</span>
              <span>验收通过任务「任务名称」</span>
              <span>验收驳回任务「任务名称」原因：需要补充测试</span>
              <span>指派任务「任务名称」给「智能体 名称」</span>
            </div>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || !selectedId || commandText.trim().length < 4}>解析并生成预览</button></div>
          </form>
          <div className="command-safety-note"><ShieldCheck size={16} /><span>命令只会生成预览。你仍需在预览弹窗中检查任务、操作、版本和风险，再点击确认执行。</span></div>
        </section>
      ) : (
        <CommandChatPanel key={selectedId} projectId={selectedId} tasks={tasks} onPlan={setPlan} />
      )}

      {plan && <div className="modal-backdrop"><section className="panel command-preview">
        <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17} /><h3>命令执行预览</h3></div><button className="icon-button" onClick={() => setPlan(null)}><X size={17} /></button></div>
        <div className="command-preview-warning"><AlertCircle size={18} /><span>此操作必须由所有者明确确认。预览有效期至 {timeText(plan.expires_at)}，任务版本为 {plan.expected_version}。</span></div>
        <div className="command-preview-grid"><div><small>任务</small><strong>{plan.task_title || plan.task_id}</strong></div><div><small>操作</small><strong>{getUiLabel(plan.action)}</strong></div><div><small>当前状态</small><strong>{getUiLabel(plan.task_status)}</strong></div><div><small>风险等级</small><strong>{plan.task_risk}</strong></div></div>
        <pre className="json-preview">{JSON.stringify(plan.parameters, null, 2)}</pre>
        <div className="form-actions"><button className="secondary-button" onClick={() => setPlan(null)}>取消</button><button className="primary-button" disabled={saving} onClick={() => void confirmPlan()}>{saving ? "执行中…" : "确认执行命令"}</button></div>
      </section></div>}
    </div>
  );
}

