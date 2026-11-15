// Whiteboard renderer for Harel statecharts. No dependencies.
// Reads window.CHARTS (model.js); draws to #svg; pan/zoom/drag; saves layout
// per chart in localStorage (best effort) so a nudged board survives reload.
(function () {
  "use strict";
  const NS = "http://www.w3.org/2000/svg";
  const svg = document.getElementById("svg");
  const world = document.getElementById("world");
  const gNodes = document.getElementById("nodes");
  const gEdges = document.getElementById("edges");
  const gLabels = document.getElementById("labels");
  const tabs = document.getElementById("tabs");
  const titleEl = document.getElementById("title");
  const subEl = document.getElementById("subtitle");
  const legend = document.getElementById("legend");

  const ORIGINAL = JSON.parse(JSON.stringify(window.CHARTS));
  const wanted = new URLSearchParams(location.search).get("chart");
  let chartId = (wanted && window.CHARTS[wanted]) ? wanted : Object.keys(window.CHARTS)[0];
  let chart = null;              // live, mutable copy
  let byId = {};
  let view = { k: 1, tx: 0, ty: 0 };
  let showReactions = true;

  const key = (id) => "rp-board:" + id;
  function loadLayout(c) {
    try {
      const saved = JSON.parse(localStorage.getItem(key(chartId)) || "null");
      if (saved) c.nodes.forEach(n => { const p = saved[n.id]; if (p) { n.x = p[0]; n.y = p[1]; } });
    } catch (e) { /* storage unavailable: render from the model */ }
  }
  function saveLayout() {
    try {
      const out = {}; chart.nodes.forEach(n => out[n.id] = [n.x, n.y]);
      localStorage.setItem(key(chartId), JSON.stringify(out));
    } catch (e) { /* ignore */ }
  }

  function el(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    for (const k in attrs) e.setAttribute(k, attrs[k]);
    if (parent) parent.appendChild(e);
    return e;
  }
  function text(x, y, str, cls, parent, anchor) {
    const t = el("text", { x, y, class: cls, "text-anchor": anchor || "middle" }, parent);
    t.textContent = str; return t;
  }

  // ---- geometry -----------------------------------------------------------
  const R = { default: 6, conn: 14 };
  function center(n) {
    if (n.kind === "state" || n.kind === "region") return { x: n.x + n.w / 2, y: n.y + n.h / 2 };
    return { x: n.x, y: n.y };
  }
  function anchor(n, toward) {
    const c = center(n);
    const dx = toward.x - c.x, dy = toward.y - c.y;
    if (n.kind === "default" || n.kind === "conn") {
      const r = R[n.kind], d = Math.hypot(dx, dy) || 1;
      return { x: c.x + dx / d * r, y: c.y + dy / d * r };
    }
    const hw = n.w / 2, hh = n.h / 2;
    if (dx === 0 && dy === 0) return c;
    const sx = hw / Math.abs(dx || 1e-9), sy = hh / Math.abs(dy || 1e-9);
    const s = Math.min(sx, sy);
    return { x: c.x + dx * s, y: c.y + dy * s };
  }
  function depth(n) { let d = 0, p = n.parent; while (p) { d++; p = byId[p] && byId[p].parent; } return d; }
  function descendants(id) {
    const out = [];
    chart.nodes.forEach(n => { let p = n.parent; while (p) { if (p === id) { out.push(n); break; } p = byId[p] && byId[p].parent; } });
    return out;
  }

  // ---- drawing ------------------------------------------------------------
  function drawNode(n) {
    const g = el("g", { class: "node " + n.kind + (n.and ? " and" : ""), "data-id": n.id }, gNodes);
    if (n.kind === "region") {
      el("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 6, class: "region-rect" }, g);
      text(n.x + 10, n.y + 16, n.name, "region-name", g, "start");
      return;
    }
    if (n.kind === "default") { el("circle", { cx: n.x, cy: n.y, r: R.default, class: "default-dot" }, g); return; }
    if (n.kind === "conn") {
      el("circle", { cx: n.x, cy: n.y, r: R.conn, class: "conn-circle conn-" + n.c }, g);
      text(n.x, n.y + 5, n.c, "conn-text", g);
      return;
    }
    // state
    const hasChildren = chart.nodes.some(m => m.parent === n.id);
    el("rect", { x: n.x, y: n.y, width: n.w, height: n.h, rx: 12, class: "state-rect" + (hasChildren ? " composite" : "") }, g);
    const nameY = hasChildren ? n.y + 20 : n.y + 20;
    const nameX = hasChildren ? n.x + 14 : n.x + n.w / 2;
    text(nameX, nameY, n.name, "state-name", g, hasChildren ? "start" : "middle");
    if (n.and && hasChildren) text(n.x + n.w - 12, n.y + 20, "and-state: components separated by dashed boxes", "and-note", g, "end");
    let y = nameY + 16;
    if (n.act) { text(n.x + n.w / 2, y, n.act, "state-act", g); y += 14; }
    if (n.desc) { text(n.x + n.w / 2, y, n.desc, "state-desc", g); y += 14; }
    if (n.reactions && n.reactions.length && showReactions) {
      el("line", { x1: n.x + 10, y1: y - 6, x2: n.x + n.w - 10, y2: y - 6, class: "divider" }, g);
      y += 8;
      n.reactions.forEach(r => { text(n.x + 12, y, r, "reaction", g, "start"); y += 14; });
    }
  }

  function drawEdge(e) {
    const a = byId[e.from], b = byId[e.to];
    if (!a || !b) { console.warn("edge refers to unknown node", e); return; }
    let d, lp;
    if (e.self) {
      // loop off the top-right corner of a rect node
      const x0 = a.x + a.w - 40, y0 = a.y, x1 = a.x + a.w, y1 = a.y + 18;
      d = `M${x0},${y0} C${x0},${y0 - 42} ${x1 + 55},${y1 - 28} ${x1},${y1}`;
      lp = { x: x0 + 10, y: y0 - 36 };
    } else {
      const ca = center(a), cb = center(b);
      let ctrl = { x: (ca.x + cb.x) / 2, y: (ca.y + cb.y) / 2 };
      if (e.bend) {
        const dx = cb.x - ca.x, dy = cb.y - ca.y, L = Math.hypot(dx, dy) || 1;
        ctrl = { x: ctrl.x - dy / L * e.bend, y: ctrl.y + dx / L * e.bend };
      }
      const p1 = anchor(a, ctrl), p2 = anchor(b, ctrl);
      d = e.bend ? `M${p1.x},${p1.y} Q${ctrl.x},${ctrl.y} ${p2.x},${p2.y}` : `M${p1.x},${p1.y} L${p2.x},${p2.y}`;
      lp = e.bend ? { x: 0.25 * p1.x + 0.5 * ctrl.x + 0.25 * p2.x, y: 0.25 * p1.y + 0.5 * ctrl.y + 0.25 * p2.y }
                  : { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    }
    el("path", { d, class: "edge", "marker-end": "url(#arrow)" }, gEdges);
    if (e.label) {
      const lines = e.label.split("\n");
      const x = lp.x + (e.lx || 0), y0 = lp.y + (e.ly || 0) - (lines.length - 1) * 6;
      lines.forEach((ln, i) => text(x, y0 + i * 13, ln, "edge-label", gLabels));
    }
  }

  function render() {
    gNodes.innerHTML = ""; gEdges.innerHTML = ""; gLabels.innerHTML = "";
    byId = {}; chart.nodes.forEach(n => byId[n.id] = n);
    chart.nodes.slice().sort((p, q) => depth(p) - depth(q)).forEach(drawNode);
    chart.edges.forEach(drawEdge);
  }
  function applyView() { world.setAttribute("transform", `translate(${view.tx},${view.ty}) scale(${view.k})`); }

  function bbox() {
    let x0 = Infinity, y0 = Infinity, x1 = -Infinity, y1 = -Infinity;
    chart.nodes.forEach(n => {
      const w = n.w || 0, h = n.h || 0, x = n.kind === "state" || n.kind === "region" ? n.x : n.x - 14, y = n.kind === "state" || n.kind === "region" ? n.y : n.y - 14;
      x0 = Math.min(x0, x); y0 = Math.min(y0, y); x1 = Math.max(x1, x + (w || 28)); y1 = Math.max(y1, y + (h || 28));
    });
    return { x: x0, y: y0, w: x1 - x0, h: y1 - y0 };
  }
  function fit() {
    const bb = bbox(), vw = svg.clientWidth, vh = svg.clientHeight, m = 40;
    view.k = Math.min((vw - 2 * m) / bb.w, (vh - 2 * m) / bb.h, 2);
    view.tx = (vw - bb.w * view.k) / 2 - bb.x * view.k;
    view.ty = (vh - bb.h * view.k) / 2 - bb.y * view.k;
    applyView();
  }

  // ---- interaction ---------------------------------------------------------
  let drag = null;
  function toWorld(ev) { return { x: (ev.clientX - view.tx) / view.k, y: (ev.clientY - view.ty) / view.k }; }
  svg.addEventListener("pointerdown", ev => {
    const g = ev.target.closest && ev.target.closest(".node");
    const p = toWorld(ev);
    if (g) {
      const n = byId[g.getAttribute("data-id")];
      drag = { kind: "node", nodes: [n].concat(descendants(n.id)), last: p };
      g.classList.add("dragging");
    } else {
      drag = { kind: "pan", sx: ev.clientX, sy: ev.clientY, tx: view.tx, ty: view.ty };
    }
    svg.setPointerCapture(ev.pointerId);
  });
  svg.addEventListener("pointermove", ev => {
    if (!drag) return;
    if (drag.kind === "pan") {
      view.tx = drag.tx + ev.clientX - drag.sx; view.ty = drag.ty + ev.clientY - drag.sy; applyView();
    } else {
      const p = toWorld(ev), dx = p.x - drag.last.x, dy = p.y - drag.last.y; drag.last = p;
      drag.nodes.forEach(n => { n.x += dx; n.y += dy; });
      render();
    }
  });
  svg.addEventListener("pointerup", () => { if (drag && drag.kind === "node") saveLayout(); drag = null; });
  svg.addEventListener("wheel", ev => {
    ev.preventDefault();
    const f = Math.exp(-ev.deltaY * 0.0015), k = Math.min(4, Math.max(0.15, view.k * f));
    const r = k / view.k;
    view.tx = ev.clientX - (ev.clientX - view.tx) * r; view.ty = ev.clientY - (ev.clientY - view.ty) * r; view.k = k;
    applyView();
  }, { passive: false });

  // ---- toolbar ---------------------------------------------------------------
  function select(id) {
    chartId = id;
    chart = JSON.parse(JSON.stringify(ORIGINAL[id]));
    loadLayout(chart);
    titleEl.textContent = chart.title; subEl.textContent = chart.subtitle || "";
    [...tabs.children].forEach(b => b.classList.toggle("active", b.dataset.id === id));
    render(); fit();
  }
  Object.keys(ORIGINAL).forEach(id => {
    const b = document.createElement("button"); b.textContent = id; b.dataset.id = id;
    b.onclick = () => select(id); tabs.appendChild(b);
  });
  document.getElementById("fit").onclick = fit;
  document.getElementById("zin").onclick = () => { view.k *= 1.2; applyView(); };
  document.getElementById("zout").onclick = () => { view.k /= 1.2; applyView(); };
  document.getElementById("reset").onclick = () => { try { localStorage.removeItem(key(chartId)); } catch (e) {} select(chartId); };
  document.getElementById("reactions").onclick = (ev) => { showReactions = !showReactions; ev.target.classList.toggle("active", showReactions); render(); };
  document.getElementById("legend-btn").onclick = () => legend.classList.toggle("open");
  document.getElementById("legend-close").onclick = () => legend.classList.remove("open");

  function exportSvg() {
    const bb = bbox(), m = 30;
    const clone = svg.cloneNode(true);
    clone.querySelector("#grid-rect").remove();
    clone.querySelector("#world").setAttribute("transform", "");
    clone.setAttribute("viewBox", `${bb.x - m} ${bb.y - m} ${bb.w + 2 * m} ${bb.h + 2 * m}`);
    clone.setAttribute("width", bb.w + 2 * m); clone.setAttribute("height", bb.h + 2 * m);
    const style = document.createElementNS(NS, "style");
    style.textContent = [...document.styleSheets].map(s => { try { return [...s.cssRules].map(r => r.cssText).join("\n"); } catch (e) { return ""; } }).join("\n");
    clone.insertBefore(style, clone.firstChild);
    el("rect", { x: bb.x - m, y: bb.y - m, width: bb.w + 2 * m, height: bb.h + 2 * m, fill: "#fff" }, clone).parentNode.insertBefore(clone.lastChild, style.nextSibling);
    return { xml: new XMLSerializer().serializeToString(clone), w: bb.w + 2 * m, h: bb.h + 2 * m };
  }
  function download(name, blob) {
    const a = document.createElement("a"); a.href = URL.createObjectURL(blob); a.download = name; a.click();
    setTimeout(() => URL.revokeObjectURL(a.href), 2000);
  }
  document.getElementById("svg-export").onclick = () => download(chartId + ".svg", new Blob([exportSvg().xml], { type: "image/svg+xml" }));
  document.getElementById("png-export").onclick = () => {
    const { xml, w, h } = exportSvg(), img = new Image(), scale = 2;
    img.onload = () => {
      const c = document.createElement("canvas"); c.width = w * scale; c.height = h * scale;
      const ctx = c.getContext("2d"); ctx.fillStyle = "#fff"; ctx.fillRect(0, 0, c.width, c.height);
      ctx.drawImage(img, 0, 0, c.width, c.height);
      c.toBlob(b => download(chartId + ".png", b), "image/png");
    };
    img.src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(xml);
  };
  window.addEventListener("resize", fit);
  window.addEventListener("keydown", ev => { if (ev.key === "f") fit(); if (ev.key === "Escape") legend.classList.remove("open"); });

  select(chartId);
})();
