import type { ReactNode } from "react";
import type { LucideIcon } from "lucide-react";

export default function EmptyState({icon:Icon,title,panel=false,className,action,children}:{icon:LucideIcon;title:string;panel?:boolean;className?:string;action?:{label:string;onClick:()=>void};children?:ReactNode}) {
  return <div className={(panel?"panel ":"")+"empty-state shared-empty-state"+(className?" "+className:"")} role="status"><Icon size={26} aria-hidden="true"/><strong>{title}</strong>{action&&<button type="button" className="secondary-button compact" onClick={action.onClick}>{action.label}</button>}{children}</div>;
}
