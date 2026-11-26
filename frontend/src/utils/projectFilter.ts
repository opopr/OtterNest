export function filterProjects<T extends {id:string;name:string}>(projects: readonly T[],query:string): T[] {
  const terms=query.trim().toLocaleLowerCase().split(/\s+/).filter(Boolean);
  return projects.filter(project=>terms.every(term=>[project.name,project.id].some(value=>value.toLocaleLowerCase().includes(term))));
}
