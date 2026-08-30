// TwinForge - SVG factory-DAG renderer + live animation.
// Draws the fixed 9-station network, updates node/edge state from the live
// stream, and can light up the backward diagnostic trace with probabilities.

const SVGNS = "http://www.w3.org/2000/svg";
const VW = 1000, VH = 560, PAD = 70;

const STATE_CLASS = {
  working: "st-working", blocked: "st-blocked",
  starved: "st-starved", down: "st-down",
};

export class FactoryGraph {
  constructor(svg) {
    this.svg = svg;
    this.nodeEls = {};
    this.edgeEls = {};
    this.flowEls = {};
    this.layout = null;
  }

  _x(nx) { return PAD + nx * (VW - 2 * PAD); }
  _y(ny) { return PAD + ny * (VH - 2 * PAD); }

  init(layout) {
    this.layout = layout;
    const svg = this.svg;
    svg.setAttribute("viewBox", `0 0 ${VW} ${VH}`);
    svg.innerHTML = "";

    // defs: arrow marker + glow
    const defs = document.createElementNS(SVGNS, "defs");
    defs.innerHTML = `
      <marker id="arrow" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="7"
        markerHeight="7" orient="auto-start-reverse">
        <path d="M0,0 L10,5 L0,10 z" fill="var(--edge)"/>
      </marker>
      <filter id="glow" x="-60%" y="-60%" width="220%" height="220%">
        <feGaussianBlur stdDeviation="5" result="b"/>
        <feMerge><feMergeNode in="b"/><feMergeNode in="SourceGraphic"/></feMerge>
      </filter>`;
    svg.appendChild(defs);

    const st = {};
    layout.stations.forEach(s => st[s.id] = s);

    // edges first (under nodes)
    this.edgeLayer = document.createElementNS(SVGNS, "g");
    svg.appendChild(this.edgeLayer);
    layout.edges.forEach(e => this._makeEdge(e, st));

    // nodes
    this.nodeLayer = document.createElementNS(SVGNS, "g");
    svg.appendChild(this.nodeLayer);
    layout.stations.forEach(s => this._makeNode(s));

    // source/sink labels
    layout.sources.forEach(sid => this._endpointLabel(st[sid], "IN", -1));
    this._endpointLabel(st[layout.sink], "OUT", 1);
  }

  _path(a, b) {
    const x1 = this._x(a.x), y1 = this._y(a.y);
    const x2 = this._x(b.x), y2 = this._y(b.y);
    const mx = (x1 + x2) / 2;
    return `M ${x1+26} ${y1} C ${mx} ${y1}, ${mx} ${y2}, ${x2-30} ${y2}`;
  }

  _makeEdge(e, st) {
    const g = document.createElementNS(SVGNS, "g");
    const d = this._path(st[e.src], st[e.dst]);
    const base = document.createElementNS(SVGNS, "path");
    base.setAttribute("d", d);
    base.setAttribute("class", "edge-base");
    base.setAttribute("marker-end", "url(#arrow)");
    const flow = document.createElementNS(SVGNS, "path");
    flow.setAttribute("d", d);
    flow.setAttribute("class", "edge-flow");
    g.appendChild(base); g.appendChild(flow);
    this.edgeLayer.appendChild(g);
    this.edgeEls[`${e.src}->${e.dst}`] = { g, base, flow, cap: e.capacity };
  }

  _makeNode(s) {
    const g = document.createElementNS(SVGNS, "g");
    g.setAttribute("class", "node");
    g.setAttribute("transform", `translate(${this._x(s.x)},${this._y(s.y)})`);
    const R = 26;
    const ring = document.createElementNS(SVGNS, "circle");
    ring.setAttribute("r", R + 6); ring.setAttribute("class", "node-ring");
    const circ = document.createElementNS(SVGNS, "circle");
    circ.setAttribute("r", R); circ.setAttribute("class", "node-core");
    const id = document.createElementNS(SVGNS, "text");
    id.setAttribute("class", "node-id"); id.setAttribute("dy", "-2"); id.textContent = s.id;
    const nm = document.createElementNS(SVGNS, "text");
    nm.setAttribute("class", "node-name"); nm.setAttribute("y", R + 16);
    nm.textContent = s.name;
    const badge = document.createElementNS(SVGNS, "g");
    badge.setAttribute("class", "wip-badge"); badge.setAttribute("transform", `translate(${R-4},${-R+2})`);
    const bc = document.createElementNS(SVGNS, "circle"); bc.setAttribute("r", 11);
    const bt = document.createElementNS(SVGNS, "text"); bt.setAttribute("class", "wip-text");
    badge.appendChild(bc); badge.appendChild(bt);
    const prob = document.createElementNS(SVGNS, "text");
    prob.setAttribute("class", "prob-label"); prob.setAttribute("y", -R - 10);
    const tier = document.createElementNS(SVGNS, "circle");
    tier.setAttribute("class", "tier-dot"); tier.setAttribute("r", 4);
    tier.setAttribute("cx", -R + 3); tier.setAttribute("cy", -R + 3);
    // risk marker (amber "!" top-right) for the Prevent overlay
    const rm = document.createElementNS(SVGNS, "circle");
    rm.setAttribute("class", "risk-mark"); rm.setAttribute("r", 9);
    rm.setAttribute("cx", R - 2); rm.setAttribute("cy", -R + 2);
    const rmt = document.createElementNS(SVGNS, "text");
    rmt.setAttribute("class", "risk-mark-t"); rmt.setAttribute("x", R - 2); rmt.setAttribute("y", -R + 2);
    rmt.textContent = "!";
    g.append(ring, circ, id, nm, badge, prob, tier, rm, rmt);
    if (s.sensors && s.sensors.length === 0) g.classList.add("dark");
    g.style.cursor = "pointer";
    g.addEventListener("click", () => this.onNodeClick && this.onNodeClick(s.id));
    this.nodeLayer.appendChild(g);
    this.nodeEls[s.id] = { g, ring, circ, badge, wipText: bt, prob };
  }

  setRisk(risks) {   // {sid: prob}; mark stations above threshold
    for (const [sid, n] of Object.entries(this.nodeEls))
      n.g.classList.toggle("at-risk", (risks[sid] || 0) >= 0.35 && (risks[sid] || 0) < 0.95);
  }

  setStatic(on) { this.svg.classList.toggle("static-graph", on); }  // hide flow anim

  _endpointLabel(s, txt, dir) {
    const t = document.createElementNS(SVGNS, "text");
    t.setAttribute("class", "endpoint");
    t.setAttribute("x", this._x(s.x) + dir * 46);
    t.setAttribute("y", this._y(s.y) + 4);
    t.textContent = txt;
    this.nodeLayer.appendChild(t);
  }

  update(live) {
    const stations = live.stations || {};
    for (const [sid, d] of Object.entries(stations)) {
      const n = this.nodeEls[sid]; if (!n) continue;
      n.circ.setAttribute("class", "node-core " + (STATE_CLASS[d.state] || ""));
      const wip = d.queue_in || 0;
      n.wipText.textContent = wip;
      n.badge.style.opacity = wip > 0 ? 1 : 0.25;
      // ring thickness ~ utilization
      n.ring.style.strokeOpacity = 0.15 + 0.85 * (d.utilization || 0);
    }
    const edges = live.edges || {};
    for (const [key, occ] of Object.entries(edges)) {
      const e = this.edgeEls[key]; if (!e) continue;
      const frac = Math.min(1, occ / e.cap);
      e.base.style.strokeWidth = (2 + frac * 6).toFixed(1);
      e.g.classList.toggle("congested", frac > 0.7);
      // flow speed ~ inversely to congestion (slower when jammed)
      e.flow.style.animationDuration = (1.1 + frac * 2.2).toFixed(2) + "s";
      e.flow.style.opacity = occ > 0 ? 0.9 : 0.15;
    }
  }

  setConstraint(sid) {
    for (const [k, n] of Object.entries(this.nodeEls))
      n.g.classList.toggle("constraint", k === sid);
  }

  setForecast(sid) {
    for (const [k, n] of Object.entries(this.nodeEls))
      n.g.classList.toggle("forecast", k === sid && sid);
  }

  // Diagnostic backward trace: highlight edges + node probabilities
  showTrace(traceEdges, posterior, symptom) {
    this.clearTrace();
    for (const [sid, p] of Object.entries(posterior || {})) {
      const n = this.nodeEls[sid]; if (!n) continue;
      n.g.classList.add("in-trace");
      n.prob.textContent = Math.round(p * 100) + "%";
      n.prob.style.opacity = 0.35 + 0.65 * p;
      if (p >= Math.max(...Object.values(posterior))) n.g.classList.add("root-cause");
    }
    (traceEdges || []).forEach(te => {
      const e = this.edgeEls[`${te.src}->${te.dst}`];
      if (e) e.g.classList.add("trace-edge");
    });
    if (symptom) this.nodeEls[symptom]?.g.classList.add("symptom");
  }

  // incremental helpers so app.js can animate the trace step by step
  markSymptom(sid) { this.nodeEls[sid]?.g.classList.add("symptom", "in-trace"); }
  highlightEdge(src, dst) { this.edgeEls[`${src}->${dst}`]?.g.classList.add("trace-edge"); }
  setNodeProb(sid, p, isRoot) {
    const n = this.nodeEls[sid]; if (!n) return;
    n.g.classList.add("in-trace");
    n.prob.textContent = Math.round(p * 100) + "%";
    n.prob.style.opacity = 1;
    if (isRoot) n.g.classList.add("root-cause");
  }

  clearTrace() {
    for (const n of Object.values(this.nodeEls)) {
      n.g.classList.remove("in-trace", "root-cause", "symptom");
      n.prob.textContent = ""; n.prob.style.opacity = 0;
    }
    for (const e of Object.values(this.edgeEls)) e.g.classList.remove("trace-edge");
  }
}
