export interface AuthStatus {
  account_configured: boolean;
  totp_enabled: boolean;
  token_configured: boolean;
}
export interface OwnerActor { kind: string; id: string; }
export interface Project {
  id: string; name: string; description: string; status: string; priority: number;
}
export interface Agent {
  id: string; name: string; platform: string;
  execution_mode: "manual" | "polling" | "remote";
  enabled: boolean; declared_capabilities: string[]; verified_capabilities: string[];
  deleted_at?: string | null;
}
export interface Task {
  id: string; project_id: string; goal_id: string | null; title: string;
  instruction: string; acceptance: string; risk: "R0" | "R1" | "R2" | "R3";
  status: string; assigned_agent_id: string | null; priority: number;
  sensitivity: string; result_summary: string | null; failure_reason: string | null;
  rework_count: number; required_capabilities: string[];
}
export interface ProjectMetric {
  project_id: string; total_tasks: number; completed_count: number; blocked_count: number;
  rework_task_count: number; acceptance_pass_rate: number | null; rework_rate: number | null;
}
export interface PortfolioMetrics {
  project_count: number; total_tasks: number; completed_count: number;
  blocked_count: number; projects: ProjectMetric[];
}
export interface BriefingProject {
  project_id: string; name: string; status: string; priority: number;
  task_total: number; completed: number; running: number;
  proposed_memories: number; proposed_decisions: number; proposed_skills: number;
}
export interface BriefingTask {
  project_id: string; task_id: string; title: string; failure_reason?: string | null;
}
export interface DailyBriefing {
  generated_at: string; enabled_agents: number; unread_notifications: number;
  unread_urgent_notifications: number; pending_r3_tasks: BriefingTask[];
  pending_review_tasks: BriefingTask[]; blocked_tasks: BriefingTask[];
  projects: BriefingProject[];
}
export interface SystemMode {
  mode: "normal" | "degraded" | "readonly_protect";
  reason: string; updated_at: string | null;
}
