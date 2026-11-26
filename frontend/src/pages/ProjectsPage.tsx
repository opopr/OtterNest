import EmptyState from "../components/EmptyState";
import ListPagination, { useListPage } from "../components/ListPagination";
import { useProjectSelection } from "../components/ProjectSelection";
import ValidatedForm from "../components/ValidatedForm";
import { ErrorNotice, LoadingState } from "../components/PageFeedback";
import { useDraftGuard, UnsavedDraftNotice } from "../components/DraftGuard";
import { useScopedRequest } from "../utils/useScopedRequest";
import { getUiLabel, TASK_STATUS_LABELS as statusLabels } from "../utils/labels";
import TaskStatusBadge from "../components/TaskStatusBadge";
import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { Eye, FolderKanban, ListChecks, Pencil, Plus, RefreshCw, Search, Target, Trash2, Users, X } from "lucide-react";
import { Link, useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import type { Agent, Project, Task } from "../api/types";
import { useAuth } from "../auth/AuthProvider";
import { useDialog } from "../components/AppDialog";
import TaskDetailPanel, { type TaskDetailSelection } from "../components/TaskDetailPanel";

interface ProjectMember {
  id: string;
  agent_id: string;
  agent_name: string;
  role: "worker" | "manager" | "reviewer" | "advisor";
  enabled: boolean;
  max_sensitivity: string;
}

interface GoalSummary {
  id: string;
  title: string;
  status: string;
}

interface ProjectBriefing {
  task_counts: {
    waiting_dependencies: number;
    waiting_approval: number;
    ready: number;
    running_lease_active: number;
    running_lease_expired_unverified: number;
    submitted: number;
    blocked: number;
    completed: number;
    cancelled: number;
  };
  pending_owner_actions: {
    decisions_proposed: number;
    memories_proposed: number;
    skills_proposed: number;
    side_effects_unreconciled: number;
  };
}


export default function ProjectsPage() {
  const { user } = useAuth();
  const dialog = useDialog();
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedProjectId = searchParams.get("project") || "";
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useProjectSelection(projects, requestedProjectId);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [goals, setGoals] = useState<GoalSummary[]>([]);
  const [briefing, setBriefing] = useState<ProjectBriefing | null>(null);
  const [detailSelection, setDetailSelection] = useState<TaskDetailSelection | null>(null);
  const [newMemberAgentId, setNewMemberAgentId] = useState("");
  const [newMemberRole, setNewMemberRole] = useState<ProjectMember["role"]>("worker");
  const [showMemberForm, setShowMemberForm] = useState(false);
  const [detailTab, setDetailTab] = useState<"tasks" | "members">("tasks");
  const [editingProject, setEditingProject] = useState(false);
  const [editProjectName, setEditProjectName] = useState("");
  const [editProjectDescription, setEditProjectDescription] = useState("");
  const [editProjectPriority, setEditProjectPriority] = useState("50");
  const [loading, setLoading] = useState(true);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState("");
  const [showProjectForm, setShowProjectForm] = useState(false);
  const [showTaskForm, setShowTaskForm] = useState(false);
  const [projectName, setProjectName] = useState("");
  const [projectDescription, setProjectDescription] = useState("");
  const [taskTitle, setTaskTitle] = useState("");
  const [taskInstruction, setTaskInstruction] = useState("");
  const [taskAcceptance, setTaskAcceptance] = useState("");
  const [taskGoalId, setTaskGoalId] = useState("");
  const [taskRisk, setTaskRisk] = useState<"R0" | "R1" | "R2" | "R3">("R1");
  const [taskPriority, setTaskPriority] = useState("50");
  const [taskSensitivity, setTaskSensitivity] = useState<"public" | "internal" | "confidential" | "restricted">("internal");
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");
  const [projectStatusFilter, setProjectStatusFilter] = useState("all");
  const [taskQuery, setTaskQuery] = useState("");
  const [taskStatusFilter, setTaskStatusFilter] = useState("all");
  const [memberQuery, setMemberQuery] = useState("");
  useEffect(() => { setTaskQuery(""); setTaskStatusFilter("all"); setMemberQuery(""); }, [selectedId]);
  const visibleTasks = tasks.filter(task => (taskStatusFilter === "all" || task.status === taskStatusFilter) && [task.title, task.id, task.instruction].join(" ").toLocaleLowerCase().includes(taskQuery.trim().toLocaleLowerCase()));
  const visibleMembers = members.filter(member => [member.agent_name, member.agent_id, getUiLabel(member.role)].join(" ").toLocaleLowerCase().includes(memberQuery.trim().toLocaleLowerCase()));

  const taskPage = useListPage(visibleTasks, JSON.stringify([selectedId,taskQuery,taskStatusFilter]));
  const memberPage = useListPage(visibleMembers, JSON.stringify([selectedId,memberQuery]));
  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedId) || null,
    [projects, selectedId]
  );

  const loadProjects = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const rows = await request<Project[]>("/projects");
      setProjects(rows);
      const nextId = rows.some((row) => row.id === requestedProjectId)
        ? requestedProjectId
        : rows.some((row) => row.id === selectedId) ? selectedId : rows[0]?.id || "";
      setSelectedId(nextId);
      if (nextId && nextId !== requestedProjectId) setSearchParams({ project: nextId }, { replace: true });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目列表读取失败");
    } finally {
      setLoading(false);
    }
  }, [requestedProjectId, selectedId, setSearchParams]);

  useEffect(() => { void loadProjects(); }, [loadProjects]);

  const beginProjectRequest = useScopedRequest(selectedId);
  const resetProjectView = () => { setTasks([]); setMembers([]); setGoals([]); setBriefing(null); setDetailSelection(null); setEditingProject(false); setShowTaskForm(false); setShowMemberForm(false); setTaskTitle(""); setTaskInstruction(""); setTaskAcceptance(""); setTaskGoalId(""); setTaskRisk("R1"); setTaskPriority("50"); setTaskSensitivity("internal"); setNewMemberAgentId(""); setNewMemberRole("worker"); };
  const hasUnsavedDraft = Boolean(taskTitle || taskInstruction || taskAcceptance || taskGoalId || taskRisk !== "R1" || taskPriority !== "50" || taskSensitivity !== "internal" || newMemberAgentId || newMemberRole !== "worker" || (editingProject && selectedProject && (editProjectName !== selectedProject.name || editProjectDescription !== (selectedProject.description || "") || editProjectPriority !== String(selectedProject.priority))));
  const confirmDiscard = useDraftGuard(hasUnsavedDraft);
  const changeProject = async (id: string) => { if (id === selectedId || saving) return; if (!(await confirmDiscard())) return; resetProjectView(); setDetailLoading(true); setError(""); setSelectedId(id); setSearchParams({ project: id }); setDetailTab("tasks"); };

  const loadProjectDetail = useCallback(async (projectId: string) => {
    const isCurrent = beginProjectRequest(projectId);
    if (!isCurrent()) return;
    setDetailLoading(true);
    try {
      const encoded = encodeURIComponent(projectId);
      const [taskRows, memberRows, agentRows, goalRows, briefingRow] = await Promise.all([
        request<Task[]>("/projects/" + encoded + "/tasks"),
        request<ProjectMember[]>("/projects/" + encoded + "/members"),
        request<Agent[]>("/agents").catch(() => [] as Agent[]),
        request<GoalSummary[]>("/projects/" + encoded + "/goals").catch(() => [] as GoalSummary[]),
        request<ProjectBriefing>("/projects/" + encoded + "/briefing").catch(() => null)
      ]);
      if (!isCurrent()) return;
      setTasks(taskRows);
      setMembers(memberRows);
      setAgents(agentRows);
      setGoals(goalRows);
      setBriefing(briefingRow);
    } catch (reason) {
      if (isCurrent()) setError(reason instanceof Error ? reason.message : "项目详情读取失败");
    } finally {
      if (isCurrent()) setDetailLoading(false);
    }
  }, [beginProjectRequest]);

  useEffect(() => {
    if (!selectedId) {
      setTasks([]);
      setMembers([]);
      setGoals([]);
      setBriefing(null);
      return;
    }
    void loadProjectDetail(selectedId);
  }, [selectedId, loadProjectDetail]);

  const createProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setSaving(true);
    setError("");
    try {
      const created = await request<Project>("/projects", {
        method: "POST",
        body: JSON.stringify({ name: projectName, description: projectDescription, priority: 50 })
      });
      setProjectName("");
      setProjectDescription("");
      setShowProjectForm(false);
      setProjects((current) => [created, ...current.filter((project) => project.id !== created.id)]);
      setSelectedId(created.id);
      setSearchParams({ project: created.id });
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建项目失败");
    } finally { setSaving(false); }
  };

  const createTask = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId) return;
    setSaving(true);
    setError("");
    try {
      const parsedPriority = Math.min(100, Math.max(0, Number(taskPriority) || 50));
      await request<Task>("/projects/" + encodeURIComponent(selectedId) + "/tasks", {
        method: "POST",
        body: JSON.stringify({
          title: taskTitle,
          instruction: taskInstruction,
          acceptance: taskAcceptance,
          goal_id: taskGoalId || null,
          risk: taskRisk,
          priority: parsedPriority,
          sensitivity: taskSensitivity,
          dependency_ids: [],
          required_capabilities: []
        })
      });
      setTaskTitle("");
      setTaskInstruction("");
      setTaskAcceptance("");
      setTaskGoalId("");
      setTaskRisk("R1");
      setTaskPriority("50");
      setTaskSensitivity("internal");
      setShowTaskForm(false);
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "创建任务失败");
    } finally { setSaving(false); }
  };

  const availableAgentsToAdd = useMemo(
    () => agents.filter((a) => a.enabled && !a.deleted_at && !members.some((m) => m.agent_id === a.id)),
    [agents, members]
  );

  const addMember = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedId || !newMemberAgentId) return;
    setSaving(true);
    setError("");
    try {
      await request("/projects/" + encodeURIComponent(selectedId) + "/members", {
        method: "POST",
        body: JSON.stringify({ agent_id: newMemberAgentId, role: newMemberRole })
      });
      setNewMemberAgentId("");
      setNewMemberRole("worker");
      setShowMemberForm(false);
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "添加成员失败");
    } finally { setSaving(false); }
  };

  const updateMemberRole = async (member: ProjectMember, role: ProjectMember["role"]) => {
    if (!selectedId || member.role === role) return;
    setSaving(true);
    setError("");
    try {
      await request("/projects/" + encodeURIComponent(selectedId) + "/members/" + encodeURIComponent(member.agent_id), {
        method: "PATCH",
        body: JSON.stringify({ role })
      });
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改成员角色失败");
    } finally { setSaving(false); }
  };

  const removeMember = async (member: ProjectMember) => {
    if (!selectedId) return;
    const confirmed = await dialog.confirm({
      title: "移除项目成员",
      message: `确认将「${member.agent_name}」从当前项目移除？`,
      confirmText: "确认移除",
      tone: "warning"
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      await request("/projects/" + encodeURIComponent(selectedId) + "/members/" + encodeURIComponent(member.agent_id), {
        method: "DELETE"
      });
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "移除成员失败");
    } finally { setSaving(false); }
  };

  const deleteTask = async (task: Task) => {
    if (!selectedId) return;
    const confirmed = await dialog.confirm({
      title: "删除任务",
      message: `确认删除任务「${task.title}」？此操作不可撤销。`,
      confirmText: "永久删除",
      tone: "danger"
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      await request("/tasks/" + encodeURIComponent(task.id), { method: "DELETE" });
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除任务失败");
    } finally { setSaving(false); }
  };

  const startEditProject = () => {
    if (!selectedProject) return;
    setEditProjectName(selectedProject.name);
    setEditProjectDescription(selectedProject.description || "");
    setEditProjectPriority(String(selectedProject.priority ?? 50));
    setEditingProject(true);
  };

  const saveEditProject = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!selectedProject) return;
    setSaving(true);
    setError("");
    try {
      const updated = await request<Project>("/projects/" + encodeURIComponent(selectedProject.id), {
        method: "PATCH",
        body: JSON.stringify({
          name: editProjectName.trim(),
          description: editProjectDescription.trim(),
          priority: Number(editProjectPriority) || 50
        })
      });
      setProjects((cur) => cur.map((p) => p.id === updated.id ? updated : p));
      setEditingProject(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "修改项目失败");
    } finally { setSaving(false); }
  };

  const deleteProject = async () => {
    if (!selectedProject) return;
    const confirmed = await dialog.confirm({
      title: "永久删除项目",
      message: `确认永久删除项目「${selectedProject.name}」及其所有目标、任务与记录？此操作不可撤销。`,
      confirmText: "确认删除项目",
      tone: "danger"
    });
    if (!confirmed) return;
    setSaving(true);
    setError("");
    try {
      await request("/projects/" + encodeURIComponent(selectedProject.id), { method: "DELETE" });
      setEditingProject(false);
      setSelectedId("");
      await loadProjects();
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除项目失败");
    } finally { setSaving(false); }
  };

  const visibleProjects = projects.filter((project) =>
    (projectStatusFilter === "all" || project.status === projectStatusFilter) && (project.name + " " + project.description).toLowerCase().includes(query.trim().toLowerCase())
  );

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div>
          <h1>项目与任务</h1>
        </div>
        <div className="heading-actions">
          <Link to="/goals-workflows" className="secondary-button compact"><Target size={14} />目标与工作流</Link>
          <button className="secondary-button" onClick={() => void loadProjects()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => setShowProjectForm(true)}><Plus size={16} />新建项目</button>
        </div>
      </div>

      <ErrorNotice message={error} onDismiss={() => setError("")} />

      {showProjectForm && (
        <ValidatedForm className="panel form-panel" onSubmit={createProject}>
          <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>创建项目</h3></div><button type="button" className="icon-button" onClick={() => setShowProjectForm(false)}><X size={17} /></button></div>
          <div className="form-grid">
            <label>项目名称<input value={projectName} onChange={(event) => setProjectName(event.target.value)} required maxLength={160} /></label>
            <label>项目说明<input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={3000} /></label>
          </div>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "创建项目"}</button></div>
        </ValidatedForm>
      )}

      <UnsavedDraftNotice dirty={hasUnsavedDraft} />
      <div className="projects-layout">
        <section className="panel project-list-panel">
          <div className="panel-heading"><div className="panel-title"><FolderKanban size={17} /><h3>项目</h3><span className="count-chip">{projects.length}</span></div></div>
          <div className="search-field"><Search size={15} /><input aria-label="搜索项目" placeholder="搜索项目…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          <select className="project-status-filter compact-select" aria-label="项目状态" value={projectStatusFilter} onChange={event => setProjectStatusFilter(event.target.value)}><option value="all">全部项目状态</option><option value="active">运行中</option><option value="paused">已暂停</option><option value="archived">已归档</option></select>
          {loading ? <LoadingState/> : visibleProjects.length === 0 ? (
            <div className="empty-mini">没有匹配的项目</div>
          ) : (
            <div className="project-list">
              {visibleProjects.map((project) => (
                <button key={project.id} className={"project-list-item " + (project.id === selectedId ? "selected" : "")}
                  disabled={saving} aria-pressed={project.id === selectedId} onClick={() => void changeProject(project.id)}>
                  <span className="project-list-icon"><FolderKanban size={17} /></span>
                  <span className="project-list-copy"><strong>{project.name}</strong><small>{getUiLabel(project.status)} · 优先级 {project.priority}</small></span>
                  <span className="project-list-arrow">›</span>
                </button>
              ))}
            </div>
          )}
        </section>

        <section className="project-detail">
          {!selectedProject ? (
            <div className="panel empty-state detail-empty"><FolderKanban size={30} /><strong>选择一个项目</strong><p>或者创建一个新项目，开始组织任务。</p></div>
          ) : (
            <>
              <div className="panel project-detail-header">
                <div className="project-detail-title">
                  <div className="project-avatar large"><FolderKanban size={21} /></div>
                  <div><h2>{selectedProject.name}</h2>{selectedProject.description && <p>{selectedProject.description}</p>}</div>
                </div>
                <div className="flex-row-gap">
                  <span className={"status-tag " + (selectedProject.status === "active" ? "tag-green" : "tag-neutral")}>{getUiLabel(selectedProject.status)}</span>
                  <button className="secondary-button compact" onClick={startEditProject} disabled={saving}><Pencil size={13} />编辑</button>
                  <button className="text-button" onClick={() => void deleteProject()} disabled={saving}><Trash2 size={14} />删除项目</button>
                </div>
              </div>

              {briefing?.task_counts && briefing?.pending_owner_actions && (
                <div className="project-briefing-grid" aria-label="项目执行简报">
                  <div className="project-briefing-tile"><span>待执行</span><strong>{briefing.task_counts.ready ?? 0}</strong></div>
                  <div className="project-briefing-tile"><span>执行中（租约有效）</span><strong>{briefing.task_counts.running_lease_active ?? 0}</strong></div>
                  <div className="project-briefing-tile"><span>待审批 / 待验收</span><strong>{(briefing.task_counts.waiting_approval ?? 0) + (briefing.task_counts.submitted ?? 0)}</strong></div>
                  <div className="project-briefing-tile"><span>阻塞 / 过期租约</span><strong>{(briefing.task_counts.blocked ?? 0) + (briefing.task_counts.running_lease_expired_unverified ?? 0)}</strong></div>
                  <div className="project-briefing-tile"><span>待决策 / 待核实副作用</span><strong>{(briefing.pending_owner_actions.decisions_proposed ?? 0) + (briefing.pending_owner_actions.side_effects_unreconciled ?? 0)}</strong></div>
                </div>
              )}

              {editingProject && (
                <ValidatedForm className="panel form-panel" onSubmit={saveEditProject}>
                  <div className="panel-heading"><div className="panel-title"><Pencil size={16} /><h3>编辑项目</h3></div><button type="button" className="icon-button" onClick={() => setEditingProject(false)}><X size={17} /></button></div>
                  <div className="form-grid three">
                    <label>项目名称<input value={editProjectName} onChange={(e) => setEditProjectName(e.target.value)} required maxLength={160} /></label>
                    <label>项目说明<input value={editProjectDescription} onChange={(e) => setEditProjectDescription(e.target.value)} maxLength={3000} /></label>
                    <label>优先级 (0-100)<input type="number" min={0} max={100} value={editProjectPriority} onChange={(e) => setEditProjectPriority(e.target.value)} /></label>
                  </div>
                  <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "保存修改"}</button></div>
                </ValidatedForm>
              )}

              <div className="workspace-switch" role="group" aria-label="项目内容">
                <button type="button" aria-pressed={detailTab === "tasks"} className={detailTab === "tasks" ? "selected" : ""} onClick={() => setDetailTab("tasks")}>任务 <span>{tasks.length}</span></button>
                <button type="button" aria-pressed={detailTab === "members"} className={detailTab === "members" ? "selected" : ""} onClick={() => setDetailTab("members")}>成员 <span>{members.length}</span></button>
              </div>
              {detailTab === "tasks" && <div className="project-tab-content">
              <div className="section-heading compact-heading">
                <div><h2>任务列表</h2></div>
                <button className="primary-button compact" onClick={() => setShowTaskForm((value) => !value)}><Plus size={15} />新建任务</button>
              </div>

              {showTaskForm && (
                <ValidatedForm className="panel form-panel" onSubmit={createTask}>
                  <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>创建任务</h3></div><button type="button" className="icon-button" onClick={() => setShowTaskForm(false)}><X size={17} /></button></div>
                  <label>任务标题<input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} required maxLength={240} /></label>
                  <div className="form-grid four">
                    <label>关联目标
                      <select value={taskGoalId} onChange={(e) => setTaskGoalId(e.target.value)}>
                        <option value="">不绑定目标</option>
                        {goals.map((g) => <option key={g.id} value={g.id}>{g.title}</option>)}
                      </select>
                    </label>
                    <label>风险等级
                      <select value={taskRisk} onChange={(e) => setTaskRisk(e.target.value as "R0" | "R1" | "R2" | "R3")}>
                        <option value="R0">R0 · 只读/低风险</option>
                        <option value="R1">R1 · 一般执行</option>
                        <option value="R2">R2 · 较高风险</option>
                        <option value="R3">R3 · 高风险/需审批</option>
                      </select>
                    </label>
                    <label>敏感级别
                      <select value={taskSensitivity} onChange={(e) => setTaskSensitivity(e.target.value as "public" | "internal" | "confidential" | "restricted")}>
                        <option value="public">公开</option>
                        <option value="internal">内部</option>
                        <option value="confidential">机密</option>
                        <option value="restricted">受限</option>
                      </select>
                    </label>
                    <label>优先级 (0-100)
                      <input type="number" min={0} max={100} value={taskPriority} onChange={(e) => setTaskPriority(e.target.value)} />
                    </label>
                  </div>
                  <label>执行说明<textarea value={taskInstruction} onChange={(event) => setTaskInstruction(event.target.value)} required maxLength={12000} rows={3} /></label>
                  <label>验收标准<textarea value={taskAcceptance} onChange={(event) => setTaskAcceptance(event.target.value)} required maxLength={6000} rows={2} /></label>
                  <p className="form-hint">R3 高风险任务创建后将自动进入待授权状态，直到所有者审批通过后才可调度执行。</p>
                  <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "创建任务"}</button></div>
                </ValidatedForm>
              )}

              <div className="panel task-table-panel">
                <div className="project-content-filter"><input aria-label="搜索项目任务" placeholder="搜索任务名称或编号" value={taskQuery} onChange={event => setTaskQuery(event.target.value)} /><select aria-label="项目任务状态" value={taskStatusFilter} onChange={event => setTaskStatusFilter(event.target.value)}><option value="all">全部状态</option>{Object.entries(statusLabels).map(([value,label]) => <option key={value} value={value}>{label}</option>)}</select><span className="muted-inline">{visibleTasks.length}/{tasks.length} 项</span></div>
                {detailLoading ? <LoadingState/> : visibleTasks.length === 0 ? (
                  <EmptyState icon={ListChecks} title={tasks.length ? "没有符合条件的任务" : "暂无任务"}/>
                ) : (
                  <div className="task-list">
                    {taskPage.rows.map((task) => (
                      <div
                        className="task-row task-row-clickable"
                        key={task.id}
                        onClick={() => setDetailSelection({ id: task.id, projectId: selectedProject.id, projectName: selectedProject.name })}
                      >
                        <div className="task-status-mark" />
                        <div className="task-main">
                          <strong>{task.title}</strong>
                          <small>{task.id.slice(0, 12)} · {task.risk} · {getUiLabel(task.sensitivity)} · 优先级 {task.priority}</small>
                        </div>
                        <TaskStatusBadge status={task.status}/>
                        <div className="flex-row-gap-sm" onClick={(e) => e.stopPropagation()}>
                          <button
                            type="button"
                            className="secondary-button compact"
                            onClick={() => setDetailSelection({ id: task.id, projectId: selectedProject.id, projectName: selectedProject.name })}
                          >
                            <Eye size={13} />详情
                          </button>
                          {task.status !== "running" && (
                            <button type="button" className="icon-button" title="删除任务" disabled={saving} onClick={() => void deleteTask(task)}>
                              <Trash2 size={14} />
                            </button>
                          )}
                        </div>
                      </div>
                    ))}
                  </div>
                )}
                {!detailLoading && <ListPagination {...taskPage} label="项目任务分页"/>}
              </div>

              </div>}
              {detailTab === "members" && <div className="project-tab-content">
              <div className="section-heading compact-heading">
                <div><h2>项目成员</h2></div>
                <button className="secondary-button compact" onClick={() => setShowMemberForm((v) => !v)}><Users size={15} />添加成员</button>
              </div>

              {showMemberForm && (
                <ValidatedForm className="panel form-panel" onSubmit={addMember}>
                  <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>添加项目成员</h3></div><button type="button" className="icon-button" onClick={() => setShowMemberForm(false)}><X size={17} /></button></div>
                  <div className="form-grid">
                    <label>选择智能体<select value={newMemberAgentId} onChange={(e) => setNewMemberAgentId(e.target.value)} required>
                      <option value="">选择已启用智能体…</option>
                      {availableAgentsToAdd.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.platform})</option>)}
                    </select></label>
                    <label>项目角色<select value={newMemberRole} onChange={(e) => setNewMemberRole(e.target.value as ProjectMember["role"])}>
                      <option value="worker">执行者</option>
                      <option value="manager">管理者（可创建任务）</option>
                      <option value="reviewer">审查者</option>
                      <option value="advisor">顾问</option>
                    </select></label>
                  </div>
                  <div className="form-actions"><button className="primary-button compact" disabled={saving || !newMemberAgentId}>加入项目</button></div>
                </ValidatedForm>
              )}

              <div className="panel task-table-panel">
                <div className="project-content-filter"><input aria-label="搜索项目成员" placeholder="搜索成员或角色" value={memberQuery} onChange={event => setMemberQuery(event.target.value)} /><span className="muted-inline">{visibleMembers.length}/{members.length} 位</span></div>
                {detailLoading ? <LoadingState/> : visibleMembers.length === 0 ? (
                  <EmptyState icon={Users} title={members.length ? "没有符合条件的成员" : "暂无项目成员"}/>
                ) : (
                  <div className="task-list">
                    {memberPage.rows.map((member) => (
                      <div className="task-row flex-row-gap" key={member.id}>
                        <div className="task-main">
                          <strong>{member.agent_name}</strong>
                          <small>{member.agent_id.slice(0, 12)} · 敏感级上限 {getUiLabel(member.max_sensitivity)} · {member.enabled ? "已启用" : "已停用"}</small>
                        </div>
                        <select
                          aria-label={`${member.agent_name} 角色`}
                          value={member.role}
                          disabled={saving}
                          onChange={(e) => void updateMemberRole(member, e.target.value as ProjectMember["role"])}
                          className="compact-select"
                        >
                          <option value="worker">执行者</option>
                          <option value="manager">管理者</option>
                          <option value="reviewer">审查者</option>
                          <option value="advisor">顾问</option>
                        </select>
                        <button className="text-button" disabled={saving} onClick={() => void removeMember(member)} title="移除成员">
                          <Trash2 size={14} />移除
                        </button>
                      </div>
                    ))}
                  </div>
                )}
                {!detailLoading && <ListPagination {...memberPage} label="项目成员分页"/>}
              </div>
              </div>}
            </>
          )}
        </section>
      </div>

      {detailSelection && detailSelection.projectId === selectedId && (
        <TaskDetailPanel
          selection={detailSelection}
          isOwner={user?.kind === "owner"}
          onClose={() => setDetailSelection(null)}
          onChanged={() => { if (selectedId) void loadProjectDetail(selectedId); }}
        />
      )}
    </div>
  );
}

