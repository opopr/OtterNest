import ListPagination, { useListPage } from "../components/ListPagination";
import { useScopedRequest } from "../utils/useScopedRequest";
import { useDraftGuard } from "../components/DraftGuard";
import { useProjectSelection } from "../components/ProjectSelection";
import { ErrorNotice } from "../components/PageFeedback";
import { getUiLabel } from "../utils/labels";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { KeyRound, Plus, RefreshCw, ShieldCheck, Trash2 } from "lucide-react";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";
import { useDialog } from "../components/AppDialog";
import ProjectSelect from "../components/ProjectSelect";

interface Member {
  id: string;
  agent_id: string;
  agent_name: string;
  role: string;
  enabled: boolean;
  max_sensitivity: string;
  expires_at: string | null;
}
interface TaskGrant {
  id: string;
  project_id: string;
  task_id: string;
  agent_id: string;
  granted_scopes: string[];
  max_sensitivity: string;
  expires_at: string;
  revoked: boolean;
  created_at: string;
}
const sensitivityLabels: Record<string, string> = { public: "公开", internal: "内部", confidential: "机密", restricted: "受限" };

export default function AccessPage() {
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedProjectId, setSelectedProjectId] = useProjectSelection(projects);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [members, setMembers] = useState<Member[]>([]);
  const [selectedTaskId, setSelectedTaskId] = useState("");
  const [grants, setGrants] = useState<TaskGrant[]>([]);
  const [grantFilter, setGrantFilter] = useState("all");
  const grantStatus = (grant: TaskGrant) => grant.revoked ? "revoked" : new Date(grant.expires_at).getTime() <= Date.now() ? "expired" : "active";
  const visibleGrants = grants.filter(grant => grantFilter === "all" || grantStatus(grant) === grantFilter);
  const grantPage=useListPage(visibleGrants,JSON.stringify([selectedProjectId,selectedTaskId,grantFilter]));
  const [agentId, setAgentId] = useState("");
  const [scopes, setScopes] = useState("task.read, artifact.read");
  const [sensitivity, setSensitivity] = useState("internal");
  const [expires, setExpires] = useState("24");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");
  const dialog = useDialog();

  const selectedProject = useMemo(() => projects.find((item) => item.id === selectedProjectId) || null, [projects, selectedProjectId]);
  const selectedTask = useMemo(() => tasks.find((item) => item.id === selectedTaskId) || null, [tasks, selectedTaskId]);
  const eligibleMembers = members.filter((member) => member.enabled);

  const beginProjectRequest = useScopedRequest(selectedProjectId);
  const beginGrantRequest = useScopedRequest(selectedTaskId);
  const confirmDiscard = useDraftGuard(scopes !== "task.read, artifact.read" || sensitivity !== "internal" || expires !== "24");
  const resetGrant = () => { setScopes("task.read, artifact.read"); setSensitivity("internal"); setExpires("24"); setGrants([]); setGrantFilter("all"); setError(""); };
  const changeProject = async (id: string) => {
    if (saving || id === selectedProjectId || !(await confirmDiscard())) return;
    resetGrant(); setTasks([]); setMembers([]); setSelectedTaskId(""); setAgentId(""); setLoading(true); setSelectedProjectId(id);
  };
  const changeTask = async (id: string) => {
    if (saving || id === selectedTaskId || !(await confirmDiscard())) return;
    resetGrant(); setSelectedTaskId(id);
  };
  const loadProjects = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      setSelectedProjectId((current) => rows.some((item) => item.id === current) ? current : rows[0]?.id || "");
    } catch (reason) { setError(reason instanceof Error ? reason.message : "项目列表读取失败"); }
    finally { setLoading(false); }
  }, []);

  const loadProject = useCallback(async (projectId: string) => {
    const current = beginProjectRequest(projectId); if (!current()) return;
    setLoading(true);
    setError("");
    try {
      const [taskRows, memberRows] = await Promise.all([
        request<Task[]>("/projects/" + encodeURIComponent(projectId) + "/tasks"),
        request<Member[]>("/projects/" + encodeURIComponent(projectId) + "/members")
      ]);
      if (!current()) return;
      setTasks(taskRows);
      setMembers(memberRows);
      setSelectedTaskId((current) => taskRows.some((item) => item.id === current) ? current : taskRows[0]?.id || "");
      setAgentId((current) => memberRows.some((item) => item.agent_id === current && item.enabled) ? current : memberRows.find((item) => item.enabled)?.agent_id || "");
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : "项目任务或成员读取失败"); }
    finally { if (current()) setLoading(false); }
  }, [beginProjectRequest]);

  const loadGrants = useCallback(async (taskId: string) => {
    const current = beginGrantRequest(taskId); if (!current()) return;
    if (!taskId) { setGrants([]); return; }
    try {
      const rows = await request<TaskGrant[]>("/tasks/" + encodeURIComponent(taskId) + "/grants");
      if (current()) setGrants(rows);
    } catch (reason) { if (current()) setError(reason instanceof Error ? reason.message : "任务授权读取失败"); }
  }, [beginGrantRequest]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);
  useEffect(() => { if (selectedProjectId) void loadProject(selectedProjectId); else { setTasks([]); setMembers([]); } }, [selectedProjectId, loadProject]);
  useEffect(() => { void loadGrants(selectedTaskId); }, [selectedTaskId, loadGrants]);

  const createGrant = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedTaskId || !agentId) return;
    setSaving(true);
    setError("");
    try {
      const grantedScopes = scopes.split(",").map((item) => item.trim()).filter(Boolean);
      await request<TaskGrant>("/tasks/" + encodeURIComponent(selectedTaskId) + "/grants", {
        method: "POST",
        body: JSON.stringify({
          agent_id: agentId, granted_scopes: grantedScopes,
          max_sensitivity: sensitivity, expires_in_hours: Number(expires)
        })
      });
      await loadGrants(selectedTaskId);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "创建任务授权失败"); }
    finally { setSaving(false); }
  };

  const revokeGrant = async (grant: TaskGrant) => {
    const confirmed = await dialog.confirm({
      title: "撤销任务临时授权",
      message: "确认撤销该任务的临时授权？已撤销授权不会自动恢复。",
      confirmText: "确认撤销",
      tone: "danger",
    });
    if (!confirmed) return;
    setSaving(true);
    try {
      await request("/tasks/" + encodeURIComponent(grant.task_id) + "/grants/" + encodeURIComponent(grant.id) + "/revoke", { method: "POST" });
      await loadGrants(grant.task_id);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "撤销任务授权失败"); }
    finally { setSaving(false); }
  };

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><h1>任务临时授权</h1></div>
        <div className="heading-actions"><button className="secondary-button" onClick={() => selectedProjectId && void loadProject(selectedProjectId)} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button></div>
      </div>
      <ErrorNotice message={error} onDismiss={() => setError("")} />
      <section className="panel project-toolbar"><div><div className="eyebrow">当前项目</div><strong>{selectedProject?.name || "选择项目"}</strong></div><ProjectSelect projects={projects} value={selectedProjectId} onChange={id=>void changeProject(id)} disabled={saving} /><label className="task-select"><span>授权目标任务</span><select aria-label="授权目标任务" value={selectedTaskId} disabled={saving || loading} onChange={(event) => void changeTask(event.target.value)}><option value="">选择任务…</option>{tasks.map((task) => <option key={task.id} value={task.id}>{task.title}</option>)}</select></label></section>
      <div className="operations-grid">
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><KeyRound size={17} /><h3>创建临时授权</h3></div><span className="muted-inline">仅项目成员可被授权</span></div>
          <form className="form-panel" onSubmit={createGrant}>
            <label>智能体<select value={agentId} onChange={(event) => setAgentId(event.target.value)} required><option value="">选择智能体…</option>{eligibleMembers.map((member) => <option key={member.agent_id} value={member.agent_id}>{member.agent_name} · {getUiLabel(member.role)}</option>)}</select></label>
            <label>授权范围（逗号分隔）<input value={scopes} onChange={(event) => setScopes(event.target.value)} placeholder="task.read, artifact.read" /></label>
            <div className="form-grid"><label>最高敏感级<select value={sensitivity} onChange={(event) => setSensitivity(event.target.value)}><option value="public">公开</option><option value="internal">内部</option><option value="confidential">机密</option><option value="restricted">受限</option></select></label><label>有效期（小时）<input type="number" min="1" max="720" value={expires} onChange={(event) => setExpires(event.target.value)} /></label></div>
            <p className="form-hint">授权只作用于所选任务，不替代项目成员身份，也不会绕过任务租约和其他安全门槛。</p>
            <div className="form-actions"><button className="primary-button compact" disabled={saving || !selectedTaskId || !agentId || eligibleMembers.length === 0}><Plus size={14} />创建授权</button></div>
          </form>
          {!eligibleMembers.length && <p className="form-hint">当前项目没有已启用的成员智能体。请先在项目成员管理中添加智能体。</p>}
        </section>
        <section className="panel">
          <div className="panel-heading"><div className="panel-title"><ShieldCheck size={17} /><h3>当前任务授权</h3></div><span className="count-chip">{grants.length}</span></div>
          {selectedTask && <p className="form-hint">任务：{selectedTask.title}</p>}
          <div className="approval-switch" role="group" aria-label="授权状态">{[{key:"all",label:"全部"},{key:"active",label:"有效"},{key:"expired",label:"已过期"},{key:"revoked",label:"已撤销"}].map(item => <button type="button" key={item.key} aria-pressed={grantFilter === item.key} className={grantFilter === item.key ? "selected" : ""} onClick={() => setGrantFilter(item.key)}>{item.label}</button>)}</div><div className="grant-list">{grantPage.rows.map((grant) => <article className="grant-card" key={grant.id}>
            <div className="grant-card-head"><strong>{members.find((member) => member.agent_id === grant.agent_id)?.agent_name || grant.agent_id.slice(0, 8)}</strong><span className={"status-tag " + (grant.revoked ? "tag-neutral" : new Date(grant.expires_at).getTime() <= Date.now() ? "tag-red" : "tag-green")}>{grant.revoked ? "已撤销" : new Date(grant.expires_at).getTime() <= Date.now() ? "已过期" : "有效"}</span></div>
            <div className="grant-scopes">{grant.granted_scopes.map((scope) => <code key={scope}>{scope}</code>)}</div>
            <small>敏感级上限：{sensitivityLabels[grant.max_sensitivity] || grant.max_sensitivity}</small>
            <small>到期：{new Date(grant.expires_at).toLocaleString("zh-CN", { hour12: false })}</small>
            {!grant.revoked && new Date(grant.expires_at).getTime() > Date.now() && <button className="text-button grant-revoke" disabled={saving} onClick={() => void revokeGrant(grant)}><Trash2 size={13} />撤销授权</button>}
          </article>)}{!visibleGrants.length && <div className="empty-state"><KeyRound size={24} /><strong>{grants.length ? "没有符合条件的授权" : "暂无临时授权"}</strong></div>}</div>
          {!loading && <ListPagination {...grantPage} label="授权分页"/>}
        </section>
      </div>
    </div>
  );
}
