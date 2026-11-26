import { useEffect, useMemo, useRef, useState } from "react";
import { FolderKanban, Search, X } from "lucide-react";
import type { Project } from "../api/types";
import { getUiLabel } from "../utils/labels";
import { filterProjects } from "../utils/projectFilter";

interface Props {
  projects: Project[];
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
  label?: string;
}

export default function ProjectSelect({ projects, value, onChange, disabled = false, label = "选择项目" }: Props) {
  const [searching,setSearching] = useState(false);
  const [query,setQuery] = useState("");
  const input = useRef<HTMLInputElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const matches = useMemo(()=>filterProjects(projects,query),[projects,query]);
  const selected = projects.find(project=>project.id===value);
  // Keep the selected option present even when it does not match a search.
  const options = selected && !matches.some(project=>project.id===value) ? [selected,...matches] : matches;
  useEffect(()=>{if(searching) input.current?.focus();},[searching]);
  const closeSearch = () => {setSearching(false);setQuery("");trigger.current?.focus();};
  return <div className="project-picker">
    <div className="project-select">
      <FolderKanban size={15} aria-hidden="true"/>
      <select aria-label={label} value={value} onChange={event=>{onChange(event.target.value);setSearching(false);setQuery("");}} disabled={disabled || !projects.length}>
        {!projects.length ? <option value="">暂无项目</option> : options.map(project=><option key={project.id} value={project.id}>{project.name} · {getUiLabel(project.status)}</option>)}
      </select>
      {projects.length>1 && <button ref={trigger} type="button" className="project-search-toggle" aria-label="搜索项目" aria-expanded={searching} disabled={disabled} onClick={()=>searching?closeSearch():setSearching(true)}><Search size={15}/></button>}
    </div>
    {searching && <div className="project-picker-search">
      <input ref={input} aria-label="项目搜索关键词" value={query} placeholder="项目名称或编号" onChange={event=>setQuery(event.target.value)} onKeyDown={event=>{
        if(event.key==="Escape"){event.preventDefault();closeSearch();}
        if(event.key==="Enter"){event.preventDefault();if(matches.length===1 && !disabled){onChange(matches[0].id);closeSearch();}}
      }}/>
      <button type="button" className="icon-button" aria-label="关闭项目搜索" onClick={closeSearch}><X size={15}/></button>
      <small role="status">{matches.length ? matches.length+" 个匹配项目" : "没有匹配的项目"}</small>
    </div>}
  </div>;
}
