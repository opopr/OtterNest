/** Groups goals by dependency depth; unresolved or cyclic references are never presented as ready. */
export function dependencyLayers<T extends { id: string; depends_on: readonly { id: string }[] }>(nodes: readonly T[]) {
  const known = new Set(nodes.map(node => node.id));
  const remaining = new Map(nodes.map(node => [node.id, node]));
  const placed = new Set<string>();
  const layers: T[][] = [];
  while (remaining.size) {
    const layer = [...remaining.values()].filter(node => node.depends_on.every(dep => known.has(dep.id) && placed.has(dep.id)));
    if (!layer.length) break;
    layers.push(layer);
    for (const node of layer) { remaining.delete(node.id); placed.add(node.id); }
  }
  return { layers, unresolved: [...remaining.values()] };
}
