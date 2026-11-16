import { useCallback, useEffect, useMemo, useState, type FormEvent } from "react";
import { AlertCircle, FolderKanban, Plus, RefreshCw, Search, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { request } from "../api/client";
import type { Project, Task } from "../api/types";

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

  useEffect(() => {
    if (!selectedId) { setTasks([]); return; }
    let active = true;
    setDetailLoading(true);
    request<Task[]>("/projects/" + encodeURIComponent(selectedId) + "/tasks")
      .then((rows) => { if (active) setTasks(rows); })
      .catch((reason) => { if (active) setError(reason instanceof Error ? reason.message : "任务读取失败"); })
      .finally(() => { if (active) setDetailLoading(false); });
    return () => { active = false; };
  }, [selectedId]);

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
      setSelectedId(created.id);
      setSearchParams({ project: created.id });
      await loadProjects();
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

  const visibleProjects = projects.filter((project) =>
    (project.name + " " + project.description).toLowerCase().includes(query.toLowerCase())
  );

  return (
    <div className="page-stack">
      <div className="page-heading">
        <div><div className="eyebrow">PROJECT WORKSPACE</div><h1>项目与任务</h1><p>项目、目标和任务的统一入口。任务状态以 PostgreSQL 中的服务端记录为准。</p></div>
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
