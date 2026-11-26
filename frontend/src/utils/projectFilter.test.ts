import test from "node:test";
import assert from "node:assert/strict";
import { filterProjects } from "./projectFilter";
const projects=[{id:"ab-123",name:"界面验收"},{id:"CD-456",name:"门店管理"}];
test("project filtering handles name, ID, whitespace and case",()=>{
  assert.deepEqual(filterProjects(projects," "),projects);
  assert.deepEqual(filterProjects(projects,"门店"),[projects[1]]);
  assert.deepEqual(filterProjects(projects,"cd-456"),[projects[1]]);
  assert.deepEqual(filterProjects(projects,"验收 ab"),[projects[0]]);
  assert.deepEqual(filterProjects(projects,"不存在"),[]);
});
