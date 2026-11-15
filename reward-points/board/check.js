// Sanity check for model.js: every edge endpoint is a declared node, ids are unique.
global.window = {};
require("./model.js");
const charts = window.CHARTS;
for (const k in charts) {
  const ids = new Set();
  for (const n of charts[k].nodes) {
    if (ids.has(n.id)) throw new Error(`${k}: duplicate node id ${n.id}`);
    ids.add(n.id);
    if (n.parent && !charts[k].nodes.some(m => m.id === n.parent)) throw new Error(`${k}: ${n.id} has unknown parent ${n.parent}`);
  }
  for (const e of charts[k].edges) {
    if (!ids.has(e.from) || !ids.has(e.to)) throw new Error(`${k}: edge ${e.from} -> ${e.to} refers to an unknown node`);
  }
  console.log(`${k}: ${charts[k].nodes.length} nodes, ${charts[k].edges.length} edges, ok`);
}
