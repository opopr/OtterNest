import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertCircle, FolderKanban, Plus, RefreshCw, Search, Trash2, Users, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import type { Agent, Project, Task } from "../api/types";

interface ProjectMember {
  id: string;
  agent_id: string;
  agent_name: string;
  role: "worker" | "manager" | "reviewer" | "advisor";
  enabled: boolean;
  max_sensitivity: string;
}

const statusLabels: Record<string, string> = {
  ready: "待执行", running: "执行中", submitted: "待验收",
  waiting_approval: "待审批", blocked: "已阻塞",
  completed: "已完成", cancelled: "已取消"
};

export default function ProjectsPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const requestedProjectId = searchParams.get("project") || "";
  const [projects, setProjects] = useState<Project[]>([]);
  const [selectedId, setSelectedId] = useState(requestedProjectId);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [members, setMembers] = useState<ProjectMember[]>([]);
  const [agents, setAgents] = useState<Agent[]>([]);
  const [newMemberAgentId, setNewMemberAgentId] = useState("");
  const [newMemberRole, setNewMemberRole] = useState<ProjectMember["role"]>("worker");
  const [showMemberForm, setShowMemberForm] = useState(false);
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
  const [saving, setSaving] = useState(false);
  const [query, setQuery] = useState("");

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

  const loadProjectDetail = useCallback(async (projectId: string) => {
    setDetailLoading(true);
    try {
      const [taskRows, memberRows, agentRows] = await Promise.all([
        request<Task[]>("/projects/" + encodeURIComponent(projectId) + "/tasks"),
        request<ProjectMember[]>("/projects/" + encodeURIComponent(projectId) + "/members"),
        request<Agent[]>("/agents").catch(() => [] as Agent[])
      ]);
      setTasks(taskRows);
      setMembers(memberRows);
      setAgents(agentRows);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "项目详情读取失败");
    } finally {
      setDetailLoading(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedId) { setTasks([]); setMembers([]); return; }
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
      await request<Task>("/projects/" + encodeURIComponent(selectedId) + "/tasks", {
        method: "POST",
        body: JSON.stringify({
          title: taskTitle, instruction: taskInstruction, acceptance: taskAcceptance,
          goal_id: null, risk: "R1", priority: 50, sensitivity: "internal",
          dependency_ids: [], required_capabilities: []
        })
      });
      setTaskTitle("");
      setTaskInstruction("");
      setTaskAcceptance("");
      setShowTaskForm(false);
      setTasks(await request<Task[]>("/projects/" + encodeURIComponent(selectedId) + "/tasks"));
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
    if (!selectedId || !window.confirm(`确认将「${member.agent_name}」从当前项目移除？`)) return;
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
    if (!selectedId || !window.confirm(`确认删除任务「${task.title}」？此操作不可撤销。`)) return;
    setSaving(true);
    setError("");
    try {
      await request("/tasks/" + encodeURIComponent(task.id), { method: "DELETE" });
      await loadProjectDetail(selectedId);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "删除任务失败");
    } finally { setSaving(false); }
  };

  const visibleProjects = projects.filter((project) =>
    (project.name + " " + project.description).toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">PROJECT WORKSPACE</div><h1>项目与任务</h1><p>项目和任务的统一入口；目标依赖等高级治理暂由旧版管理台承接。任务状态以服务端记录为准。</p></div>
        <div className="heading-actions">
          <button className="secondary-button" onClick={() => void loadProjects()} disabled={loading}><RefreshCw size={15} className={loading ? "spin" : ""} />刷新</button>
          <button className="primary-button compact" onClick={() => setShowProjectForm(true)}><Plus size={16} />新建项目</button>
        </div>
      </div>

      {error && <div className="inline-error"><AlertCircle size={16} />{error}<button onClick={() => setError("")}><X size={15} /></button></div>}

      {showProjectForm && (
        <form className="panel form-panel" onSubmit={createProject}>
          <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>创建项目</h3></div><button type="button" className="icon-button" onClick={() => setShowProjectForm(false)}><X size={17} /></button></div>
          <div className="form-grid">
            <label>项目名称<input value={projectName} onChange={(event) => setProjectName(event.target.value)} required maxLength={160} /></label>
            <label>项目说明<input value={projectDescription} onChange={(event) => setProjectDescription(event.target.value)} maxLength={3000} /></label>
          </div>
          <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "创建项目"}</button></div>
        </form>
      )}

      <div className="projects-layout">
        <section className="panel project-list-panel">
          <div className="panel-heading"><div className="panel-title"><FolderKanban size={17} /><h3>项目空间</h3><span className="count-chip">{projects.length}</span></div></div>
          <div className="search-field"><Search size={15} /><input placeholder="搜索项目…" value={query} onChange={(event) => setQuery(event.target.value)} /></div>
          {loading ? <div className="panel-loading"><div className="spinner" /></div> : visibleProjects.length === 0 ? (
            <div className="empty-mini">没有匹配的项目</div>
          ) : (
            <div className="project-list">
              {visibleProjects.map((project) => (
                <button key={project.id} className={"project-list-item " + (project.id === selectedId ? "selected" : "")}
                  onClick={() => { setSelectedId(project.id); setSearchParams({ project: project.id }); }}>
                  <span className="project-list-icon"><FolderKanban size={17} /></span>
                  <span className="project-list-copy"><strong>{project.name}</strong><small>{project.status} · 优先级 {project.priority}</small></span>
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
                  <div><div className="eyebrow">PROJECT</div><h2>{selectedProject.name}</h2><p>{selectedProject.description || "暂无项目说明"}</p></div>
                </div>
                <span className={"status-tag " + (selectedProject.status === "active" ? "tag-green" : "tag-neutral")}>{selectedProject.status}</span>
              </div>

              <div className="section-heading compact-heading">
                <div><h2>任务列表</h2><p>{tasks.length} 项任务 · 按创建时间倒序</p></div>
                <button className="primary-button compact" onClick={() => setShowTaskForm((value) => !value)}><Plus size={15} />新建任务</button>
              </div>

              {showTaskForm && (
                <form className="panel form-panel" onSubmit={createTask}>
                  <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>创建任务</h3></div><button type="button" className="icon-button" onClick={() => setShowTaskForm(false)}><X size={17} /></button></div>
                  <label>任务标题<input value={taskTitle} onChange={(event) => setTaskTitle(event.target.value)} required maxLength={240} /></label>
                  <label>执行说明<textarea value={taskInstruction} onChange={(event) => setTaskInstruction(event.target.value)} required maxLength={12000} rows={3} /></label>
                  <label>验收标准<textarea value={taskAcceptance} onChange={(event) => setTaskAcceptance(event.target.value)} required maxLength={6000} rows={2} /></label>
                  <p className="form-hint">默认风险等级 R1、内部敏感级、优先级 50。高风险授权仍由服务端治理规则控制。</p>
                  <div className="form-actions"><button className="primary-button compact" disabled={saving}>{saving ? "保存中…" : "创建任务"}</button></div>
                </form>
              )}

              <div className="panel task-table-panel">
                {detailLoading ? <div className="panel-loading"><div className="spinner" /></div> : tasks.length === 0 ? (
                  <div className="empty-state"><span className="empty-icon">✓</span><strong>这个项目还没有任务</strong><p>创建第一个任务，或从旧版管理台继续管理目标与工作流。</p></div>
                ) : (
                  <div className="task-list">
                    {tasks.map((task) => (
                      <div className="task-row" key={task.id}>
                        <div className="task-status-mark" />
                        <div className="task-main"><strong>{task.title}</strong><small>{task.id.slice(0, 12)} · {task.risk} · 优先级 {task.priority}</small></div>
                        <span className={"status-tag " + (
                          task.status === "completed" ? "tag-green" :
                            task.status === "blocked" || task.status === "waiting_approval" ? "tag-red" :
                              task.status === "running" || task.status === "submitted" ? "tag-blue" : "tag-neutral"
                        )}>{statusLabels[task.status] || task.status}</span>
                        {task.status !== "running" && (
                          <button className="icon-button" title="删除任务" disabled={saving} onClick={() => void deleteTask(task)}>
                            <Trash2 size={14} />
                          </button>
                        )}
                      </div>
                    ))}
                  </div>
                )}
              </div>

              <div className="section-heading compact-heading" style={{ marginTop: "18px" }}>
                <div><h2>项目成员管理</h2><p>{members.length} 位成员 · 支持添加、改角色与移除</p></div>
                <button className="secondary-button compact" onClick={() => setShowMemberForm((v) => !v)}><Users size={15} />添加成员</button>
              </div>

              {showMemberForm && (
                <form className="panel form-panel" onSubmit={addMember}>
                  <div className="panel-heading"><div className="panel-title"><Plus size={17} /><h3>添加项目成员</h3></div><button type="button" className="icon-button" onClick={() => setShowMemberForm(false)}><X size={17} /></button></div>
                  <div className="form-grid">
                    <label>选择 Agent<select value={newMemberAgentId} onChange={(e) => setNewMemberAgentId(e.target.value)} required>
                      <option value="">选择已启用 Agent…</option>
                      {availableAgentsToAdd.map((a) => <option key={a.id} value={a.id}>{a.name} ({a.platform})</option>)}
                    </select></label>
                    <label>项目角色<select value={newMemberRole} onChange={(e) => setNewMemberRole(e.target.value as ProjectMember["role"])}>
                      <option value="worker">worker（执行者）</option>
                      <option value="manager">manager（管理者/可建任务）</option>
                      <option value="reviewer">reviewer（审查者）</option>
                      <option value="advisor">advisor（顾问）</option>
                    </select></label>
                  </div>
                  <div className="form-actions"><button className="primary-button compact" disabled={saving || !newMemberAgentId}>加入项目</button></div>
                </form>
              )}

              <div className="panel task-table-panel">
                {members.length === 0 ? (
                  <div className="empty-state"><Users size={24} /><strong>暂无项目成员</strong><p>点击上方「添加成员」将 Agent 加入当前项目。</p></div>
                ) : (
                  <div className="task-list">
                    {members.map((member) => (
                      <div className="task-row" key={member.id} style={{ gap: "10px" }}>
                        <div className="task-main">
                          <strong>{member.agent_name}</strong>
                          <small>{member.agent_id.slice(0, 12)} · 敏感级上限 {member.max_sensitivity} · {member.enabled ? "已启用" : "已停用"}</small>
                        </div>
                        <select
                          aria-label={`${member.agent_name} 角色`}
                          value={member.role}
                          disabled={saving}
                          onChange={(e) => void updateMemberRole(member, e.target.value as ProjectMember["role"])}
                          style={{ padding: "4px 8px", borderRadius: "6px" }}
                        >
                          <option value="worker">worker</option>
                          <option value="manager">manager</option>
                          <option value="reviewer">reviewer</option>
                          <option value="advisor">advisor</option>
                        </select>
                        <button className="text-button" disabled={saving} onClick={() => void removeMember(member)} title="移除成员">
                          <Trash2 size={14} />移除
                        </button>
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}
        </section>
      </div>
    </div>
  );
}
