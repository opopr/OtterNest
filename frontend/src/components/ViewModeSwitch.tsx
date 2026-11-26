import { useEffect,useState } from "react";
import { LayoutGrid,List,Table2 } from "lucide-react";
type ViewMode = "list"|"table"|"cards";
export function useDisplayPreference(key:string,initial:ViewMode) {
  const [value,setValue]=useState<ViewMode>(()=>{try{const saved=localStorage.getItem(key);return saved==="list"||saved==="table"||saved==="cards"?saved:initial;}catch{return initial;}});
  useEffect(()=>{try{localStorage.setItem(key,value);}catch{}},[key,value]);
  return [value,setValue] as const;
}
export function ViewModeSwitch({value,onChange,label,alternate}:{value:ViewMode;onChange:(value:ViewMode)=>void;label:string;alternate:"table"|"list"}) {
  const options:ViewMode[]=alternate==="table"?["list","table"]:["cards","list"];
  return <div className="view-mode-switch" role="group" aria-label={label}>{options.map(mode=>{
    const Icon=mode==="cards"?LayoutGrid:mode==="table"?Table2:List;
    return <button key={mode} type="button" aria-pressed={mode===value} onClick={()=>onChange(mode)}><Icon size={14} aria-hidden="true"/>{mode==="cards"?"卡片":mode==="table"?"表格":"列表"}</button>;
  })}</div>;
}
