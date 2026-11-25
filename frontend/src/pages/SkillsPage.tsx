import { useScopedRequest } from "../utils/useScopedRequest";
import ListPagination, { useListPage } from "../components/ListPagination";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import {
  AlertCircle, Archive, BookMarked, Check, ClipboardList, Plus,
  RefreshCw, ShieldCheck, X
} from "lucide-react";
import { request } from "../api/client";
import type { Project } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";

interface Skill {
  id: string;
  project_id: string;
  name: string;
  version: string;
  content: string;
  source: string;
  status: string;
  created_at: string;
}
interface SkillEvaluation {
  id: string;
  project_id: string;
  skill_id: string;
  actor_kind: string;
  actor_id: string;
  verdict: "pass" | "fail" | "concern";
  notes: string;
  task_id: string | null;
  created_at: string;
}
const statusLabel: Record<string, string> = { proposed: "待审核", published: "已发布", archived: "已归档" };
const verdictLabel: Record<string, string> = { pass: "通过", fail: "未通过", concern: "有疑虑" };

export default function SkillsPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [skills, setSkills] = useState<Skill[]>([]);
  const [skillQuery, setSkillQuery] = useState("");
  const [skillFilter, setSkillFilter] = useState("all");
  const visibleSkills = useMemo(() => {
    const q = skillQuery.trim().toLocaleLowerCase();
    return skills.filter(skill => (skillFilter === "all" || skill.status === skillFilter) && (!q || [skill.name, skill.version, skill.content, skill.source].some(v => (v || "").toLocaleLowerCase().includes(q))));
  }, [skills, skillQuery, skillFilter]);
  const skillPage = useListPage(visibleSkills, JSON.stringify([selectedId, skillQuery, skillFilter]));
  const [evaluations, setEvaluations] = useState<Record<string, SkillEvaluation[]>>({});
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const [showForm, setShowForm] = useState(false);
  const [name, setName] = useState("");
  const [version, setVersion] = useState("1.0.0");
  const [content, setContent] = useState("");
  const [source, setSource] = useState("");
  const dialog = useDialog();

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedId) || null, [projects, selectedId]);

  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, []);

  const beginProjectRequest = useScopedRequest(selectedId);
  const resetProjectView = () => { setSkills([]); setEvaluations({}); setShowForm(false); setName(""); setVersion("1.0.0"); setContent(""); setSource(""); setSkillQuery(""); setSkillFilter("all"); };
  const changeProject = (id: string) => { if (id === selectedId || saving) return; resetProjectView(); setLoading(true); setError(""); setSelectedId(id); };

  const loadSkills = useCallback(async (projectId: string) => {
    const isCurrent = beginProjectRequest(projectId);
    if (!isCurrent()) return;
    setLoading(true);
    setError("");
    try {
      const rows = await request<Skill[]>("/projects/" + encodeURIComponent(projectId) + "/skills");
      if (!isCurrent()) return;
      setSkills(rows);
      const pairs = await Promise.all(rows.map(async (skill) => {
        try {
          const list = await request<SkillEvaluation[]>("/skills/" + encodeURIComponent(skill.id) + "/evaluations");
          return [skill.id, list] as const;
        } catch {
          return [skill.id, []] as const;
        }
      }));
      if (isCurrent()) setEvaluations(Object.fromEntries(pairs));
    } catch (reason) { if (isCurrent()) setError(reason instanceof Error ? reason.message : "技能列表读取失败"); }
    finally { if (isCurrent()) setLoading(false); }
  }, [beginProjectRequest]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedId) void loadSkills(selectedId); else { setSkills([]); setEvaluations({}); } }, [selectedId, loadSkills]);

  const createSkill = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    setError("");
    try {
      await request<Skill>("/projects/" + encodeURIComponent(selectedId) + "/skills", {
        method: "POST",
        body: JSON.stringify({ name: name.trim(), version: version.trim(), content: content.trim(), source: source.trim() })
      });
      setName(""); setVersion("1.0.0"); setContent(""); setSource(""); setShowForm(false);
      await loadSkills(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "提交技能提案失败"); }
    finally { setSaving(false); }
  };

  const publishSkill = async (skill: Skill) => {
    const confirmed = await dialog.confirm({
      title: "发布技能",
      message: `确认发布技能「${skill.name}」？发布后该项目的智能体才能读取它。`,
      confirmText: "确认发布",
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/skills/" + encodeURIComponent(skill.id) + "/publish", { method: "POST" });
      await loadSkills(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "发布技能失败"); }
    finally { setSaving(false); }
  };

  const evaluateSkill = async (skill: Skill) => {
    const verdict = await dialog.prompt({
      title: `评价技能「${skill.name}」`,
      message: "请选择该技能的评估结论，下一步将填写详细说明。",
      label: "评价结论",
      defaultValue: "pass",
      choices: [
        { value: "pass", label: "通过" },
        { value: "concern", label: "有疑虑" },
        { value: "fail", label: "未通过" },
      ],
      confirmText: "下一步",
    });
    if (!verdict) return;
    const notes = await dialog.prompt({
      title: `填写评价说明（${verdictLabel[verdict] || verdict}）`,
      label: "评价说明（至少 5 个字符）",
      placeholder: "记录执行表现、边界问题或适用条件…",
      multiline: true,
      required: true,
      minLength: 5,
      confirmText: "提交评价",
    });
    if (!notes) return;
    setSaving(true);
    try {
      await request<SkillEvaluation>("/skills/" + encodeURIComponent(skill.id) + "/evaluations", {
        method: "POST", body: JSON.stringify({ verdict, notes, task_id: null })
      });
      await loadSkills(selectedId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "技能评价失败"); }
    finally { setSaving(false); }
  };

  const archiveSkill = async (skill: Skill) => {
    const reason = await dialog.prompt({
      title: `归档技能「${skill.name}」`,
      message: "归档后该技能不再提供给智能体，但会保留历史评价记录。",
      label: "归档原因（至少 5 个字符）",
      placeholder: "说明归档或替代原因…",
      multiline: true,
      required: true,
      minLength: 5,
      confirmText: "确认归档",
    });
    if (!reason) return;
    setSaving(true);
    try {
      await request("/skills/" + encodeURIComponent(skill.id) + "/archive", {
        method: "POST", body: JSON.stringify({ reason })
      });
      await loadSkills(selectedId);
    } catch (cause) { setError(cause instanceof Error ? cause.message : "归档技能 失败"); }
    finally { setSaving(false); }
  };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>技能管理</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedId && void loadSkills(selectedId)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}
      <section className="panel project-toolbar"><div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedId} onChange={changeProject} disabled={saving} /><span className="muted-inline">{skills.length} 个技能版本</span></section>
      <section className="panel">
        <div className="panel-heading"><div className="panel-title"><BookMarked size={17} /><h3>项目技能</h3><span className="count-chip">{visibleSkills.length}</span></div><button className="primary-button compact" onClick={() => setShowForm((value) => !value)} disabled={!selectedId}><Plus size={15} />提交技能提案</button></div>
        <div className="library-filter-bar"><label>关键词<input aria-label="搜索技能" value={skillQuery} onChange={event => setSkillQuery(event.target.value)} placeholder="搜索名称、版本或内容" /></label><label>状态<select aria-label="技能状态" value={skillFilter} onChange={event => setSkillFilter(event.target.value)}><option value="all">全部状态</option><option value="proposed">待发布</option><option value="published">已发布</option><option value="archived">已归档</option></select></label><button className="text-button" type="button" onClick={() => { setSkillQuery(""); setSkillFilter("all"); }}>清除筛选</button></div>
        {showForm && <form className="form-panel embedded-form" onSubmit={createSkill}>
          <div className="form-grid"><label>技能名称<input value={name} onChange={(event) => setName(event.target.value)} required maxLength={120} /></label><label>版本号<input value={version} onChange={(event) => setVersion(event.target.value)} required maxLength={40} /></label></div>
          <label>技能内容<textarea rows={7} value={content} onChange={(event) => setContent(event.target.value)} required maxLength={20000} placeholder="填写智能体可执行的操作步骤、约束和验收要求。提案在发布前不会对项目智能体开放。" /></label>
          <label>来源与依据<input value={source} onChange={(event) => setSource(event.target.value)} required maxLength={3000} /></label>
          <p className="form-hint">同一项目内技能名称与版本组合必须唯一。发布是单独的所有者审批动作。</p>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "提交中…" : "提交提案"}</button></div>
        </form>}
        <div className="skill-grid">
          {loading ? <div className="panel-loading"><div className="spinner" /></div> : skillPage.rows.map((skill) => <article className="skill-card" key={skill.id}>
            <div className="skill-card-head"><div><strong>{skill.name}</strong><small>版本 {skill.version} · {skill.id.slice(0, 8)}</small></div><span className={"status-tag " + (skill.status === "published" ? "tag-green" : skill.status === "proposed" ? "tag-amber" : "tag-neutral")}>{statusLabel[skill.status] || skill.status}</span></div>
            <details className="library-disclosure"><summary>技能内容</summary><pre className="skill-content">{skill.content}</pre><small className="memory-source">来源：{skill.source}</small></details>
            <details className="library-disclosure skill-evaluations"><summary><span><ClipboardList size={13}/> 评价记录</span><span>{(evaluations[skill.id] || []).length} 条</span></summary>{(evaluations[skill.id] || []).map((item) => <div className="skill-evaluation" key={item.id}><span className={"status-tag " + (item.verdict === "pass" ? "tag-green" : item.verdict === "fail" ? "tag-red" : "tag-amber")}>{verdictLabel[item.verdict]}</span><p>{item.notes}</p></div>)}</details>
            <div className="skill-actions">{skill.status === "proposed" && <button className="secondary-button compact" disabled={saving} onClick={() => void publishSkill(skill)}><ShieldCheck size={13} />所有者发布</button>}{skill.status !== "archived" && <button className="text-button" disabled={saving} onClick={() => void evaluateSkill(skill)}><Check size={13} />记录评价</button>}{skill.status !== "archived" && <button className="icon-button" title="归档" disabled={saving} onClick={() => void archiveSkill(skill)}><Archive size={14} /></button>}</div>
          </article>)}
          {!loading && !visibleSkills.length && <div className="empty-state"><BookMarked size={25} /><strong>{skills.length ? "没有符合条件的技能" : "暂无技能"}</strong></div>}
        </div>
        {!loading && <ListPagination {...skillPage} label="技能分页" />}
      </section>
    </div>
  );
}
