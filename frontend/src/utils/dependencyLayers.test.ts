import test from "node:test";
import assert from "node:assert/strict";
import { dependencyLayers } from "./dependencyLayers";
const goal = (id: string, ...deps: string[]) => ({id, depends_on: deps.map(id => ({id}))});
test("places independent goals together and dependent goals after predecessors", () => {
  const result = dependencyLayers([goal("c","a","b"),goal("b"),goal("a"),goal("d","c")]);
  assert.deepEqual(result.layers.map(layer => layer.map(n => n.id)), [["b","a"],["c"],["d"]]);
  assert.deepEqual(result.unresolved, []);
});
test("keeps missing references, cycles and their dependents unresolved", () => {
  const result = dependencyLayers([goal("root"),goal("a","b"),goal("b","a"),goal("c","a"),goal("missing","absent")]);
  assert.deepEqual(result.layers.map(layer => layer.map(n=>n.id)), [["root"]]);
  assert.deepEqual(result.unresolved.map(n=>n.id), ["a","b","c","missing"]);
});
test("empty graph is empty", () => assert.deepEqual(dependencyLayers([]), {layers:[],unresolved:[]}));
