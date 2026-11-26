import { useEffect, useState, type ReactNode } from "react";
import { Settings2 } from "lucide-react";

export interface DataColumn<T> {
  id: string;
  label: string;
  required?: boolean;
  numeric?: boolean;
  render: (row:T) => ReactNode;
}

/** Only column identifiers are persisted, never row data. Required identity/status/action columns cannot be hidden. */
export default function DataTable<T>({rows,columns,rowKey,label,preferenceKey,rowClassName,className}:{
  rows: readonly T[]; columns: readonly DataColumn<T>[]; rowKey:(row:T)=>string;
  label:string; preferenceKey:string; rowClassName?:string; className?:string;
}) {
  const [hidden,setHidden]=useState<string[]>(()=>{
    try { const raw:unknown=JSON.parse(localStorage.getItem(preferenceKey)||"[]"); return Array.isArray(raw)?raw.filter((value):value is string=>typeof value==="string"):[]; }catch{return [];}
  });
  useEffect(()=>{try{localStorage.setItem(preferenceKey,JSON.stringify(hidden));}catch{}},[preferenceKey,hidden]);
  const visible=columns.filter(column=>column.required||!hidden.includes(column.id));
  const optional=columns.filter(column=>!column.required);
  return <div className="data-table-workspace">
    {!!optional.length && <details className="table-column-settings"><summary><Settings2 size={15} aria-hidden="true"/>显示列</summary>
      <div className="table-column-options" role="group" aria-label={label+"显示列"}>
        {optional.map(column=><label key={column.id}><input type="checkbox" checked={!hidden.includes(column.id)} onChange={event=>setHidden(current=>event.target.checked?current.filter(id=>id!==column.id):[...current,column.id])}/>{column.label}</label>)}
        <button type="button" className="text-button" onClick={()=>setHidden([])}>恢复默认列</button>
      </div>
    </details>}
    <div className="data-table-scroll" role="region" aria-label={label} tabIndex={0}>
      <table className={"data-table "+(className||"")}><thead><tr>{visible.map(column=><th key={column.id} scope="col" className={column.numeric?"numeric-cell":undefined}>{column.label}</th>)}</tr></thead>
      <tbody>{rows.map(row=><tr key={rowKey(row)} className={rowClassName}>{visible.map(column=><td key={column.id} className={column.numeric?"numeric-cell":undefined}>{column.render(row)}</td>)}</tr>)}</tbody></table>
    </div>
  </div>;
}
