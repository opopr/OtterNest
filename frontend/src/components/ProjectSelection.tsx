import { createContext, useContext, useEffect, useRef, useState, type ReactNode, type MutableRefObject } from "react";

const ProjectSelectionContext = createContext<MutableRefObject<string> | null>(null);

/** Remember only a project ID during the authenticated UI session. */
export function ProjectSelectionProvider({children}: {children: ReactNode}) {
  const preferred = useRef("");
  return <ProjectSelectionContext.Provider value={preferred}>{children}</ProjectSelectionContext.Provider>;
}

/** Each workspace retains its own selection and confirms changes independently.
 * Only an ID present in the latest authorized project list becomes the preference.
 */
export function useProjectSelection(projects: readonly {id:string}[], requested = "") {
  const preferred = useContext(ProjectSelectionContext);
  const [selected,setSelected] = useState(() => requested || preferred?.current || "");
  useEffect(() => {
    if (preferred && selected && projects.some(project => project.id === selected)) preferred.current = selected;
  }, [preferred,selected,projects]);
  return [selected,setSelected] as const;
}
