import { FolderKanban } from "lucide-react";
import type { Project } from "../api/types";

interface Props {
  projects: Project[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}

export default function ProjectSelect({ projects, value, onChange, disabled = false }: Props) {
  return (
    <label className="project-select">
      <FolderKanban size={15} />
      <select value={value} onChange={(event) => onChange(event.target.value)} disabled={disabled || projects.length === 0}>
        {projects.length === 0 ? <option value="">暂无项目</option> : projects.map((project) => (
          <option key={project.id} value={project.id}>{project.name}</option>
        ))}
      </select>
    </label>
  );
}
