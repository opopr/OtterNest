import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle, Check, CheckCircle2, ClipboardCheck, FileSearch, MessageSquare,
  RefreshCw, ShieldCheck, X, XCircle
} from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";
import ProjectSelect from "../components/ProjectSelect";

interface Decision {
  id: string;
  question: string;
  resolution: string;
  rationale: string;
  status: string;
}
interface Review {
  id: string;
  agent_id: string;
  stance: "support" | "oppose" | "alternative";
  reasoning: string;
  evidence: string[];
  evidence_checks: Array<{ reference: string; status?: string; detail?: string }>;
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
  rebuttals: Array<{ id: string; agent_id: string; stance: string; reasoning: string; evidence: string[] }>;
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
const timeText = (value: string) => new Date(value).toLocaleString("zh-CN", { hour12: false });

export default function GovernancePage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [tasks, setTasks] = useState<Task[]>([]);
  const [decisions, setDecisions] = useState<Decision[]>([]);
  const [selectedDecisionId, setSelectedDecisionId] = useState("");
  const [reviews, setReviews] = useState<Review[]>([]);
  const [deliberation, setDeliberation] = useState<Deliberation | null>(null);
  const [resolutionNote, setResolutionNote] = useState("");
  const [closingNote, setClosingNote] = useState("");
  const [plan, setPlan] = useState<CommandPlan | null>(null);
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [tab, setTab] = useState<"tasks" | "decisions" | "command">("tasks");
  const [commandText, setCommandText] = useState("");

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);
  const selectedDecision = useMemo(() => decisions.find((item) => item.id === selectedDecisionId) || null, [decisions, selectedDecisionId]);
  const pendingTasks = tasks.filter((task) => task.status === "waiting_approval" || task.status === "submitted");
  const dissentCount = reviews.filter((review) => review.stance !== "support").length +
    (deliberation?.rebuttals || []).filter((reply) => reply.stance !== "support").length;

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
      const [taskRows, decisionRows] = await Promise.all([
        request<Task[]>("/projects/" + encodeURIComponent(projectId) + "/tasks"),
        request<Decision[]>("/projects/" + encodeURIComponent(projectId) + "/decisions")
      ]);
      setTasks(taskRows);
      setDecisions(decisionRows);
      setSelectedDecisionId((current) => decisionRows.some((item) => item.id === current) ? current : decisionRows.find((item) => item.status === "proposed")?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "治理事项读取失败"); }
    finally { setDetailLoading(false); }
  }, []);

  const loadDecision = useCallback(async (decisionId: string) => {
    if (!decisionId) { setReviews([]); setDeliberation(null); return; }
    setDetailLoading(true);
    try {
      const reviewRows = await request<Review[]>("/decisions/" + encodeURIComponent(decisionId) + "/reviews");
      setReviews(reviewRows);
      try {
        setDeliberation(await request<Deliberation>("/decisions/" + encodeURIComponent(decisionId) + "/deliberation"));
      } catch {
        setDeliberation(null);
      }
    } catch (reason) { setError(reason instanceof Error ? reason.message : "决策审查信息读取失败"); }
    finally { setDetailLoading(false); }
  }, []);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedId) void loadProjectData(selectedId); else { setTasks([]); setDecisions([]); } }, [selectedId, loadProjectData]);
  useEffect(() => { void loadDecision(selectedDecisionId); }, [selectedDecisionId, loadDecision]);

  const createTaskPlan = async (task: Task, action: "authorize_task" | "review_task", accepted?: boolean) => {
    let reason = "";
    if (action === "review_task" && accepted === false) {
      const input = window.prompt("请填写退回原因（不能为空）");
      if (!input || !input.trim()) return;
      reason = input.trim();
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
    if (!window.confirm("确认执行这条已预览的治理命令？服务端会再次校验任务版本与状态。")) return;
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

  const openSecondRound = async () => {
    if (!selectedDecision) return;
    if (!window.confirm("确认开启第二轮独立讨论？开启后不能创建第三轮。")) return;
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
    if (!window.confirm("确认由所有者完成该决策？独立审查意见不会自动替代所有者审批。")) return;
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

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">OWNER GOVERNANCE</div><h1>指挥中心与审批</h1><p>高风险授权、任务验收、独立决策审查与所有者最终裁决。</p></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadProjectData(selectedId)} disabled={loading || detailLoading}><RefreshCw size={15} className={loading || detailLoading ? "spin" : ""} />刷新</button></div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      <section className="panel governance-toolbar">
        <div><div className="eyebrow">CURRENT PROJECT</div><strong>{selectedProject?.name || "尚未选择项目"}</strong></div>
        <ProjectSelect projects={projects} value={selectedId} onChange={setSelectedId} />
        <div className="governance-summary"><span><strong>{pendingTasks.length}</strong> 待处理任务</span><span><strong>{decisions.filter((item) => item.status === "proposed").length}</strong> 待审决策</span></div>
      </section>

      <div className="governance-tabs">
        <button className={tab === "tasks" ? "selected" : ""} onClick={() => setTab("tasks")}><ClipboardCheck size={16} />任务审批 <span>{pendingTasks.length}</span></button>
        <button className={tab === "decisions" ? "selected" : ""} onClick={() => setTab("decisions")}><ShieldCheck size={16} />决策审查 <span>{decisions.filter((item) => item.status === "proposed").length}</span></button>
        <button className={tab === "command" ? "selected" : ""} onClick={() => setTab("command")}><MessageSquare size={16} />自然语言命令</button>
      </div>

      {tab === "tasks" ? (
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><ClipboardCheck size={17} /><h3>待授权与待验收任务</h3></div><span className="muted-inline">所有写操作先预览，再由所有者确认</span></div>
          <div className="governance-task-list">
            {pendingTasks.map((task) => <article className="governance-task" key={task.id}>
              <div className="governance-task-main"><div><strong>{task.title}</strong><span className={"status-tag " + (task.status === "waiting_approval" ? "tag-red" : "tag-blue")}>{taskStatus[task.status]}</span></div><p>{task.id} · {task.risk} · 优先级 {task.priority}</p><small>{task.instruction}</small></div>
              <div className="governance-task-actions">
                {task.status === "waiting_approval" ? <button className="primary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "authorize_task")}><ShieldCheck size={14} />生成授权预览</button> : <>
                  <button className="primary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "review_task", true)}><Check size={14} />验收通过预览</button>
                  <button className="secondary-button compact" disabled={saving} onClick={() => void createTaskPlan(task, "review_task", false)}><XCircle size={14} />退回返工</button>
                </>}
              </div>
            </article>)}
            {!pendingTasks.length && <div className="empty-state"><CheckCircle2 size={26} /><strong>没有待处理任务</strong><p>该项目当前没有等待授权或验收的任务。</p></div>}
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
              <div className="decision-subheading"><strong>第一轮独立审查</strong><span>{reviews.length} 条意见</span></div>
              {reviews.map((review) => <article className="review-card" key={review.id}>
                <div className="review-card-head"><strong>Agent {review.agent_id.slice(0, 8)}</strong><span className={"status-tag " + (review.stance === "support" ? "tag-green" : review.stance === "oppose" ? "tag-red" : "tag-amber")}>{stanceLabel[review.stance]}</span></div>
                <p>{review.reasoning}</p>
                {review.evidence.length > 0 && <div className="evidence-list"><small>证据引用（尚未自动验证）</small>{review.evidence.map((item) => <code key={item}>{item}</code>)}</div>}
                <small className="muted-inline">{timeText(review.created_at)}</small>
              </article>)}
              {!reviews.length && <p className="form-hint">尚无 Agent 独立审查意见。只有注册并加入该项目的 Agent 可以提交审查。</p>}
              {deliberation && <div className="deliberation-panel">
                <div className="decision-subheading"><strong>第二轮讨论</strong><span className={"status-tag " + (deliberation.status === "open" ? "tag-blue" : "tag-neutral")}>{deliberation.status === "open" ? "讨论中" : "已关闭"}</span></div>
                <p className="form-hint">第二轮最多一轮，不会自动批准决策。所有者仍需最终裁决。</p>
                {deliberation.rebuttals.map((reply) => <article className="review-card" key={reply.id}><div className="review-card-head"><strong>Agent {reply.agent_id.slice(0, 8)}</strong><span className="status-tag tag-neutral">{stanceLabel[reply.stance] || reply.stance}</span></div><p>{reply.reasoning}</p></article>)}
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
      ) : (
        <section className="panel command-input-panel">
          <div className="panel-heading"><div className="panel-title"><MessageSquare size={17} /><h3>受限自然语言命令</h3></div><span className="status-tag tag-blue">模板解析 · 非 LLM</span></div>
          <p className="form-hint">该入口只支持后端白名单中的中文命令模板，不会调用外部模型，也不会直接执行操作。解析成功后会创建与任务审批相同的版本化命令预览。</p>
          <form className="form-panel" onSubmit={interpretCommand}>
            <label>输入命令<textarea rows={3} value={commandText} onChange={(event) => setCommandText(event.target.value)} required minLength={4} maxLength={3500} placeholder="例如：验收通过任务「完成首页 React 改造」" /></label>
            <div className="command-examples">
              <span>批准任务「任务名称」</span>
              <span>取消任务「任务名称」</span>
              <span>验收通过任务「任务名称」</span>
              <span>验收驳回任务「任务名称」原因：需要补充测试</span>
              <span>指派任务「任务名称」给「Agent 名称」</span>
            </div>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || !selectedId || commandText.trim().length < 4}>解析并生成预览</button></div>
          </form>
          <div className="command-safety-note"><ShieldCheck size={16} /><span>命令只会生成预览。你仍需在预览弹窗中检查任务、操作、版本和风险，再点击确认执行。</span></div>
        </section>
      )}

      {plan && <div className="modal-backdrop"><section className="panel command-preview">
        <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17} /><h3>命令执行预览</h3></div><button className="icon-button" onClick={() => setPlan(null)}><X size={17} /></button></div>
        <div className="command-preview-warning"><AlertCircle size={18} /><span>此操作必须由所有者明确确认。预览有效期至 {timeText(plan.expires_at)}，任务版本为 {plan.expected_version}。</span></div>
        <div className="command-preview-grid"><div><small>任务</small><strong>{plan.task_title || plan.task_id}</strong></div><div><small>操作</small><strong>{plan.action}</strong></div><div><small>当前状态</small><strong>{plan.task_status}</strong></div><div><small>风险等级</small><strong>{plan.task_risk}</strong></div></div>
        <pre className="json-preview">{JSON.stringify(plan.parameters, null, 2)}</pre>
        <div className="form-actions"><button className="secondary-button" onClick={() => setPlan(null)}>取消</button><button className="primary-button" disabled={saving} onClick={() => void confirmPlan()}>{saving ? "执行中…" : "确认执行命令"}</button></div>
      </section></div>}
    </div>
  );
}
