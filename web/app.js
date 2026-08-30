// TwinForge front-end. Four IDE workspaces. A single GLOBAL "case" (a flow
// fault you inject) is shared by Telemetry / Prevent / Resolve; Diagnose is
// detached and works on defect cases with its own probability graph.
import { FactoryGraph } from "/web/factory.js";
const $ = s => document.querySelector(s);
const el=(t,c,h)=>{const e=document.createElement(t);if(c)e.className=c;if(h!=null)e.innerHTML=h;return e;};
const pct = v => Math.round(v*100)+"%";
const cap = x => x.charAt(0).toUpperCase()+x.slice(1);

const FLOW_KINDS = { degrade_ramp:"gradual slowdown", degrade_step:"sudden slowdown", station_down:"outage" };

const S = {
  layout:null, meta:null, scenarios:[], interventions:{}, preventActions:{}, complications:{}, stations:[], units:{},
  graph:null, es:null, snap:null, analytics:null,
  tab:"telemetry", speed:40,
  // GLOBAL flow case (shared by telemetry/prevent/resolve). null = healthy.
  case:{station:"S4", kind:"degrade_ramp", magnitude:0.9, complication:"none"},
  defectCase:"s4_tooldrift", sel:null,
};

async function init(){
  S.layout=await (await fetch("/api/layout")).json();
  const sc=await (await fetch("/api/scenarios")).json();
  S.scenarios=sc.scenarios; S.interventions=sc.interventions; S.preventActions=sc.prevent_actions;
  S.complications=sc.complications||{}; S.stations=sc.stations;
  S.units=S.layout.sensor_units||{};
  S.meta=await (await fetch("/api/metrics")).json();
  document.querySelectorAll("#tabs button").forEach(b=>b.onclick=()=>setTab(b.dataset.tab));
  setTab("telemetry");
}
function setTab(t){
  S.tab=t;
  document.querySelectorAll("#tabs button").forEach(b=>b.classList.toggle("active",b.dataset.tab===t));
  document.querySelectorAll(".tabpane").forEach(p=>p.classList.toggle("hidden",p.dataset.tab!==t));
  renderToolbar(t); renderPane(t);
}

// ---------- global case ----------
function caseLabel(){
  if(!S.case) return "Healthy line";
  const k=S.case.kind&&S.case.kind!=="none"?FLOW_KINDS[S.case.kind]:null;
  const c=S.case.complication&&S.case.complication!=="none"?S.complications[S.case.complication].label.toLowerCase():null;
  const parts=[k,c].filter(Boolean).join(" + ")||"health drift";
  return `${S.case.station} · ${parts} · ${Math.round(S.case.magnitude*100)}%`;
}
function caseBody(){ return S.case ? {custom:{station:S.case.station,kind:S.case.kind,magnitude:S.case.magnitude,onset_s:600,complication:S.case.complication||"none"}} : {scenario:"healthy"}; }
function caseStation(){ return S.case?S.case.station:"S4"; }
function stationSensors(sid){ return (S.stations.find(s=>s.id===sid)?.sensors)||[]; }

// ---------- stream ----------
function stopStream(){ if(S.es){S.es.close();S.es=null;} }
function streamURL(extra=""){
  let u=`/api/stream?speed=${S.speed}&emit_every_s=10`;
  if(S.case) u+=`&fault_station=${S.case.station}&fault_kind=${S.case.kind}&fault_mag=${S.case.magnitude}&fault_onset=600&complication=${S.case.complication||"none"}`;
  else u+=`&scenario=healthy`;
  return u+extra;
}
function startStream(extra=""){
  stopStream();
  const es=new EventSource(streamURL(extra)); S.es=es;
  es.onmessage=e=>handleSnap(JSON.parse(e.data));
  es.addEventListener("done",()=>es.close());
}
function handleSnap(snap){
  S.snap=snap;
  if(S.graph) S.graph.update(snap.live);
  updateChips(snap);
  const a=snap.analytics||{};
  if(a.detector){
    S.analytics=a;
    const risks=a.forecast?a.forecast.station_probs:{};
    if(S.graph){ S.graph.setConstraint(a.detector.is_constraint?a.detector.constraint:null); S.graph.setRisk(risks); }
    // light the Prevent tab when something is trending
    const atrisk=Object.values(risks).some(v=>v>=.35&&v<.95);
    document.querySelector('#tabs button[data-tab="prevent"]').classList.toggle("alert", atrisk);
    if(S.tab==="telemetry") renderTelemetryLive();
    if(a.drift&&a.drift.recalibrated) toast("Twin re-calibrated to new throughput");
  }
}
function updateChips(snap){
  $("#s-clock").textContent=`${Math.floor(snap.t/60)}:${String(snap.t%60).padStart(2,"0")}`;
  const det=snap.analytics?.detector, chip=$("#chip-state");
  if(det&&det.is_constraint){ chip.className="status-chip bad"; $("#s-state").textContent=`${det.constraint} constraint`; }
  else { chip.className="status-chip ok"; $("#s-state").textContent="nominal"; }
}

// ---------- toolbars / ribbon ----------
function ribbon(extra=""){
  return `<div class="ribbon" style="flex:1;border:0;padding:0;background:transparent;height:auto">
      <span class="lbl">Case</span>
      <span class="case-chip ${S.case?"fault":""}"><span class="d"></span><b>${caseLabel()}</b></span>
      <button class="btn sm" id="rb-edit">Edit case…</button>
      <span class="sep"></span>
      <label>Speed <input type="range" id="rb-speed" min="10" max="120" step="10" value="${S.speed}" style="width:90px"><span class="mono" id="rb-speedv" style="color:var(--text-2)">${S.speed}×</span></label>
      ${extra}
      <span style="flex:1"></span><button class="btn sm" id="rb-restart">Restart</button></div>`;
}
function wireRibbon(){
  $("#rb-edit").onclick=openCaseModal;
  $("#rb-speed").oninput=e=>{S.speed=+e.target.value;$("#rb-speedv").textContent=S.speed+"×";startStream();};
  $("#rb-restart").onclick=()=>startStream();
}
function renderToolbar(t){
  const tb=$("#toolbar");
  if(t==="telemetry"||t==="prevent"){ tb.innerHTML=ribbon(); wireRibbon(); }
  else if(t==="resolve"){
    const iopts=Object.entries(S.interventions).map(([k,v])=>`<option value="${k}">${v.label}</option>`).join("");
    const sopts=S.stations.map(s=>`<option value="${s.id}">${s.id} · ${s.name}</option>`).join("");
    tb.innerHTML=ribbon(`<span class="sep"></span><label>Fix <select id="rs-kind">${iopts}</select></label><label id="rs-stwrap">at <select id="rs-st">${sopts}</select></label><button class="btn accent sm" id="rs-run">Run resolution</button>`);
    wireRibbon(); $("#rs-st").value=caseStation();
    const k=$("#rs-kind"); if(S.interventions.debottleneck) k.value="debottleneck";
    const tog=()=>$("#rs-stwrap").style.display=S.interventions[k.value].needs_station?"flex":"none"; k.onchange=tog; tog();
    $("#rs-run").onclick=runResolve;
  } else if(t==="diagnose"){
    const defs=S.scenarios.filter(s=>s.kind==="defect").map(s=>`<option value="${s.id}" ${s.id===S.defectCase?"selected":""}>${s.label}</option>`).join("");
    tb.innerHTML=`<span class="tool-title">Defect diagnosis</span><span class="tool-desc">Detached — a QA reject, no throughput signature. Trace it back through telemetry + genealogy.</span>
      <span style="flex:1"></span>
      <label>Defect case <select id="dg-scenario">${defs}</select></label>
      <button class="btn accent sm" id="dg-run">Trace back ←</button>`;
    $("#dg-scenario").onchange=e=>S.defectCase=e.target.value;
    $("#dg-run").onclick=runDefect;
  }
}
function openCaseModal(){
  const sopts=S.stations.map(s=>`<option value="${s.id}">${s.id} · ${s.name}</option>`).join("");
  const kopts=`<option value="none">None (complication only)</option>`+Object.entries(FLOW_KINDS).map(([k,v])=>`<option value="${k}">${cap(v)}</option>`).join("");
  const c=S.case||{station:"S4",kind:"degrade_ramp",magnitude:0.9,complication:"none"};
  modal("Configure case", `
    <p class="dim" style="margin-top:0">Inject a fault into the live line. This is the case that Telemetry, Prevent and Resolve all run against — a <b>simulation of an issue</b>, not a counterfactual.</p>
    <div class="kv"><span class="k">Healthy line</span><button class="btn sm" id="cm-healthy">Set healthy (no fault)</button></div>
    <div class="kv"><span class="k">Station</span><select id="cm-st" style="width:55%">${sopts}</select></div>
    <div class="kv"><span class="k">Primary fault</span><select id="cm-kind" style="width:55%">${kopts}</select></div>
    <div class="kv"><span class="k">Complication <span class="faint">(sensor-specific)</span></span><select id="cm-comp" style="width:55%"></select></div>
    <div class="dim" style="font-size:11.5px;margin:-2px 0 4px">A complication drives a real health signal (heat / vibration) — the leading indicator Prevent can act on. Options depend on the station's sensors.</div>
    <div class="kv"><span class="k">Severity</span><input type="range" id="cm-mag" min="20" max="120" value="${Math.round(c.magnitude*100)}" style="width:50%"><span class="v mono" id="cm-magv">${Math.round(c.magnitude*100)}%</span></div>
    <div style="display:flex;gap:8px;margin-top:14px;justify-content:flex-end"><button class="btn" id="cm-cancel">Cancel</button><button class="btn accent" id="cm-run">Apply & simulate →</button></div>`);
  $("#cm-st").value=c.station; $("#cm-kind").value=(c.kind&&c.kind!=="none")?c.kind:"degrade_ramp";
  const fillComp=()=>{
    const sens=stationSensors($("#cm-st").value);
    const opts=Object.entries(S.complications).filter(([k,v])=>!v.needs||sens.includes(v.needs))
      .map(([k,v])=>`<option value="${k}">${v.label}</option>`).join("");
    $("#cm-comp").innerHTML=opts;
    if([...$("#cm-comp").options].some(o=>o.value===c.complication)) $("#cm-comp").value=c.complication;
  };
  $("#cm-st").onchange=fillComp; fillComp();
  $("#cm-mag").oninput=e=>$("#cm-magv").textContent=e.target.value+"%";
  $("#cm-cancel").onclick=closeModal;
  $("#cm-healthy").onclick=()=>{ S.case=null; applyCase(); };
  $("#cm-run").onclick=()=>{ S.case={station:$("#cm-st").value,kind:$("#cm-kind").value,magnitude:(+$("#cm-mag").value)/100,complication:$("#cm-comp").value}; applyCase(); };
}
function applyCase(){ closeModal(); renderToolbar(S.tab); renderPane(S.tab); }

// ---------- pane dispatch ----------
function renderPane(t){
  if(t==="telemetry"){ renderTelemetry(); startStream(); }
  else if(t==="prevent"){ stopStream(); renderPrevent(); }
  else if(t==="diagnose"){ stopStream(); renderDiagnose(); }
  else if(t==="resolve"){ stopStream(); renderResolve(); }
}

// ================= TELEMETRY =================
function renderTelemetry(){
  $("#pane-telemetry").innerHTML=`
    <div class="stats" id="tl-stats" style="margin-bottom:10px"></div>
    <div class="grid" style="grid-template-columns:1.15fr 1fr;align-items:stretch;height:calc(100vh - 300px);min-height:400px">
      <div class="panel pad0"><div class="head"><span class="title">Process flow</span><span class="aux">live · click a node for detail</span></div>
        <div class="body" style="padding:0;position:relative"><svg id="factory"></svg></div></div>
      <div class="panel pad0"><div class="head"><span class="title">Station telemetry — expected vs actual</span><span class="aux">click a row</span></div>
        <div class="body" style="padding:0"><table class="tele" id="tl-table"></table></div></div>
    </div>
    <div class="panel" style="margin-top:10px"><div class="head"><span class="title">Forecast — risk of constraining at T+5 min (ML)</span><span class="aux mono" id="tl-fcaux"></span></div><div class="body" id="tl-forecast"></div></div>`;
  S.graph=new FactoryGraph(document.querySelector("#pane-telemetry svg")); S.graph.init(S.layout); S.graph.onNodeClick=showStation;
  if(S.snap) renderTelemetryLive();
}
function renderTelemetryLive(){
  const snap=S.snap; if(!snap||S.tab!=="telemetry"||!$("#tl-stats")) return;
  const l=snap.live, det=snap.analytics?.detector, risks=snap.analytics?.forecast?.station_probs||{};
  $("#tl-stats").innerHTML=[
    stat("Throughput",`${l.throughput_uph}`,"uph","acc"),
    stat("Cars built",`${l.produced}`,"",""),
    stat("WIP in line",`${l.wip}`,"",""),
    stat("Constraint",det&&det.is_constraint?det.constraint:"balanced","",det&&det.is_constraint?"r":"g"),
    stat("Case",S.case?S.case.station:"healthy","",S.case?"a":"g"),
  ].join("");
  $("#tl-table").innerHTML=`<thead><tr><th>Station</th><th>State</th><th>Output</th><th>Cycle</th><th>Sensors</th><th></th></tr></thead><tbody>${
    S.layout.stations.map(s=>{ const d=l.stations[s.id]; const risk=risks[s.id]||0;
      const rt=det&&det.constraint===s.id&&det.is_constraint?`<span class="tag oos">constraint</span>`:(risk>=.35&&risk<.95?`<span class="tag risk">risk ${pct(risk)}</span>`:"");
      const sens=(s.sensors.length?s.sensors:["—"]).map(x=>`<span class="${x==="—"?"dark":""}">${x}</span>`).join("");
      return `<tr data-sid="${s.id}" class="${S.sel===s.id?"sel":""}"><td><span class="st">${s.id}</span> <span class="dim" style="font-size:11px">${s.name}</span></td>
        <td><span class="tag ${d.state}">${d.state}</span></td><td>${eva(d.throughput_uph,d.expected.throughput_uph,"uph")}</td>
        <td>${eva(d.eff_ct,d.expected.cycle_time,"s",true)}</td><td><div class="sensors-mini">${sens}</div></td><td>${rt}</td></tr>`;
    }).join("")}</tbody>`;
  $("#tl-table").querySelectorAll("tr[data-sid]").forEach(tr=>tr.onclick=()=>showStation(tr.dataset.sid));
  const fc=snap.analytics?.forecast;
  if(fc){ const mx=Math.max(...fc.ranked.map(r=>r[1]),.001);
    $("#tl-forecast").innerHTML=fc.ranked.slice(0,6).map(([s,pv])=>`<div class="bar"><div class="n">${s}</div><div class="tk"><div class="fl ${pv>=.5?"r":pv>=.3?"a":""}" style="width:${Math.max(3,pv/mx*100)}%"></div></div><div class="p">${pct(pv)}</div></div>`).join("")
      +`<div class="dim" style="font-size:11.5px;margin-top:8px">Amber ⚠ appears on the flow when a station's risk crosses 35% — <b>before</b> it constrains. Open <b>Prevent</b> to act.</div>`;
    $("#tl-fcaux").textContent=`holdout acc ${(S.meta.forecast_holdout_acc*100||0).toFixed(0)}%`;
  }
}
function stat(l,v,u,cls){ return `<div class="stat ${cls||""}"><div class="lbl">${l}</div><div class="val">${v}${u?`<small> ${u}</small>`:""}</div></div>`; }
function eva(a,e,u,inv){ const bad=inv?a>e*1.15:a<e*0.85,warn=inv?a>e*1.05:a<e*0.95;
  return `<span class="eva ${bad?"hi":warn?"warn":""}"><span class="a">${a}${u}</span><span class="e">/ ${e}${u}</span></span>`; }
function showStation(sid){
  S.sel=sid; if(S.tab==="telemetry") renderTelemetryLive();
  const d=S.snap?.live.stations[sid]; if(!d) return; const s=S.layout.stations.find(x=>x.id===sid);
  const flow=[["Throughput",`${d.throughput_uph} uph`,`${d.expected.throughput_uph} uph`],["Cycle time",`${d.eff_ct} s`,`${d.expected.cycle_time} s`],["WIP / queue",`${d.queue_in}`,"—"],["Utilisation",pct(d.utilization),"—"],["State",d.state,"—"]];
  const sens=s.sensors.length?s.sensors.map(n=>{const u=S.units[n]||"";return [cap(n),`${d.channels[n]}${u}`,d.expected[n]!=null?`${d.expected[n]}${u}`:"—"];}):[["—","No internal sensors (manual)","state inferred"]];
  modal(`${sid} · ${s.name}`,`
    <div class="panel"><div class="head"><span class="title">Flow</span></div><div class="body">${flow.map(([k,a,e])=>`<div class="kv"><span class="k">${k}</span><span class="v">${a} <span class="faint">/ ${e}</span></span></div>`).join("")}</div></div>
    <div class="panel" style="margin-top:10px"><div class="head"><span class="title">Sensors — actual / expected</span><span class="aux">${s.instrumentation}</span></div><div class="body">${sens.map(([k,a,e])=>`<div class="kv"><span class="k">${k}</span><span class="v">${a} <span class="faint">/ ${e}</span></span></div>`).join("")}</div></div>`);
}

// ================= PREVENT =================
function renderPrevent(){
  const sopts=S.stations.map(s=>`<option value="${s.id}" ${s.id===caseStation()?"selected":""}>${s.id} · ${s.name}</option>`).join("");
  $("#pane-prevent").innerHTML=`
    <div class="grid" style="grid-template-columns:380px 1fr;align-items:start">
      <div class="panel"><div class="head"><span class="title">Predicted risk · pre-failure</span></div><div class="body">
        <div class="kv"><span class="k">Station</span><select id="pv-st" style="width:55%">${sopts}</select></div><div id="pv-status"><div class="empty">Scoring…</div></div></div></div>
      <div class="panel"><div class="head"><span class="title">Test an intervention · ML sensitivity</span><span class="aux">same model, perturbed telemetry</span></div><div class="body">
        <div id="pv-actions" style="display:flex;flex-wrap:wrap;gap:8px"></div><div id="pv-result" style="margin-top:12px"><div class="empty">Pick an intervention to see risk before vs after.</div></div></div></div>
    </div>`;
  $("#pv-st").onchange=()=>renderPreventStation($("#pv-st").value);
  renderPreventStation($("#pv-st").value);
}
async function renderPreventStation(sid){
  $("#pv-status").innerHTML=`<div class="empty">Scoring…</div>`;
  const st=await post("/api/prevent_status",{...caseBody(),station:sid});
  const r=st.risk,u=S.units;
  const note=st.actionable?`<div class="note a">⚠ <b>${sid}</b> ${nameOf(sid)} is trending to constrain — risk <b>${pct(r)}</b> at T+5 (caught ~min ${st.at_minute}). Line has <b>not failed yet</b>.</div>`
    :(r>0.92?`<div class="note r"><b>${sid}</b> risk ${pct(r)} — already the constraint; use <b>Resolve</b>.</div>`:`<div class="note g"><b>${sid}</b> not trending to fail (risk ${pct(r)}).</div>`);
  const drivers=Object.entries(st.drivers).map(([k,v])=>`<div class="kv"><span class="k">${cap(k)}</span><span class="v">${v}${u[k]||""}</span></div>`).join("");
  $("#pv-status").innerHTML=`${note}<div class="bar" style="margin-top:8px"><div class="n">risk</div><div class="tk"><div class="fl ${r>=.5?"r":r>=.3?"a":"g"}" style="width:${Math.max(3,r*100)}%"></div></div><div class="p">${pct(r)}</div></div>
    <div class="tool-desc" style="margin-top:8px">Driving signals the model reacts to:</div>${drivers||'<div class="dim" style="font-size:12px">Manual station — no internal sensors.</div>'}`;
  const sens=stationSensors(sid);
  const acts=Object.entries(S.preventActions).filter(([k,v])=>!v.needs||sens.includes(v.needs)||(v.needs==="vibration"&&sens.includes("current")));
  $("#pv-actions").innerHTML=acts.map(([k,v])=>`<button class="btn" data-act="${k}" title="${v.desc}">${v.label}</button>`).join("")
    +`<div class="dim" style="font-size:11px;flex-basis:100%;margin-top:2px">Only interventions this station's sensors support are shown${sens.length?"":" (manual station — labour & release only)"}.</div>`;
  $("#pv-actions").querySelectorAll("button").forEach(b=>b.onclick=()=>runPrevent(sid,b.dataset.act));
}
async function runPrevent(sid,action){
  $("#pv-result").innerHTML=`<div class="empty">Re-scoring the model…</div>`;
  const r=await post("/api/prevent_whatif",{...caseBody(),station:sid,action});
  const cls=r.averts?"g":r.delta<0?"a":"r";
  $("#pv-result").innerHTML=`<div class="note ${cls}">${r.averts?"✓ Averts the predicted failure":r.delta<0?"↓ Reduces the risk":"✗ Does not resolve it"} — <b>${r.label}</b> on ${sid}</div>
    <div class="compare"><div class="col"><h4>Predicted risk (do nothing)</h4><div class="bignum">${pct(r.risk_before)}</div></div>
      <div class="col sim"><h4>After ${r.label}</h4><div class="bignum">${pct(r.risk_after)}</div><div class="delta ${r.delta<0?"up":"down"}">${r.delta>0?"+":""}${pct(r.delta)}</div></div></div>
    <div class="dim" style="font-size:11.5px;margin-top:8px">Forecast re-scored on perturbed telemetry at the pre-failure window — <b>not</b> a simulation.</div>`;
}

// ================= DIAGNOSE (defect graph) =================
function renderDiagnose(){
  $("#pane-diagnose").innerHTML=`
    <div class="stats" id="dg-stats" style="margin-bottom:10px"><div class="stat"><div class="lbl">Status</div><div class="val" style="font-size:15px">pick a defect case → Trace back</div></div></div>
    <div class="grid" style="grid-template-columns:1.25fr 1fr;align-items:stretch;height:calc(100vh - 300px);min-height:400px">
      <div class="panel pad0"><div class="head"><span class="title">Root-cause graph — probability per station</span><span class="aux">no flow · ML backtrace</span></div>
        <div class="body" style="padding:0;position:relative"><svg id="factory"></svg>
          <div style="position:absolute;bottom:10px;left:14px;font:11px var(--mono);color:var(--text-3)">edges = defect corridor (path of bad units) · number = P(origin)</div></div></div>
      <div class="panel pad0"><div class="head"><span class="title">Ranked origins — check-first</span></div><div class="body" id="dg-hyps"><div class="empty">Trace a defect case to rank the origins.</div></div></div>
    </div>`;
  S.graph=new FactoryGraph(document.querySelector("#pane-diagnose svg")); S.graph.init(S.layout); S.graph.setStatic(true);
}
async function runDefect(){
  const btn=$("#dg-run"); btn.disabled=true; btn.textContent="Tracing…";
  $("#dg-hyps").innerHTML=`<div class="empty">Re-running the shift, inspecting every unit at QA…</div>`;
  const r=await post("/api/defect_diagnose",{scenario:S.defectCase});
  btn.disabled=false; btn.textContent="Trace back ←";
  // graph: probabilities on nodes + defect corridor edges
  S.graph.clearTrace();
  const post_={}; r.ranked.forEach(([k,p])=>post_[k]=p);
  S.graph.showTrace(r.corridor, post_, S.layout.sink);
  $("#dg-stats").innerHTML=[stat("Defect type",r.attribute,"","acc"),stat("Defective",r.n_defective,"","r"),stat("Defect rate",r.defect_rate_pct,"%","r"),stat("Most likely",`${r.root_cause||"—"}`,"",r.ranked[0]?"acc":"")].join("");
  $("#dg-hyps").innerHTML=`<div style="padding:12px">
    <div class="dim" style="font-size:12px;margin-bottom:6px">A <b>${r.attribute}</b> reject could come from the station that makes it <b>or</b> a downstream station that handled it — probability is distributed, not certain.</div>
    ${r.hypotheses.map((h,i)=>`<div class="hyp ${i===0?"top":""}">
      <div class="r"><div><span class="st">${h.station}<small>${h.station_name}</small></span> <span class="tag" style="color:${h.role==='producer'?'var(--blue)':'var(--amber)'}">${h.role}</span></div><div class="p">${pct(h.probability)}</div></div>
      <div class="ev">${h.channel!=="—"?`<span>${h.channel} <b>${h.dev_pct>0?"+":""}${h.dev_pct}%</b> ${h.out_of_spec?'<span class="tag oos">out of spec</span>':'<span class="tag">in spec</span>'}</span>`:'<span>no matching sensor</span>'}<span>genealogy <b>${h.genealogy_lift}×</b></span></div>
      <div class="ac">${h.action}</div></div>`).join("")}
    <div class="note a" style="margin-top:6px">Containment: <b>${r.containment.count} vehicles</b> passed the most-likely origin (${r.root_cause}) — hold &amp; inspect ${r.containment.first||"—"} … ${r.containment.last||"—"}.</div></div>`;
}

// ================= RESOLVE (counterfactual + flow) =================
function renderResolve(){
  $("#pane-resolve").innerHTML=`
    <div id="rs-summary"><div class="note"><b>Full DES re-simulation</b> — unlike Prevent's instant ML re-score, this re-runs the entire line under identical random draws and takes a moment. Pick a fix in the ribbon and Run.</div></div>
    <div class="grid" style="grid-template-columns:1.1fr 1fr;align-items:stretch;height:calc(100vh - 300px);min-height:400px;margin-top:10px">
      <div class="panel pad0"><div class="head"><span class="title">Simulated line (with fix)</span><span class="aux" id="rs-flowaux">idle — run a resolution</span></div>
        <div class="body" style="padding:0;position:relative"><svg id="factory"></svg></div></div>
      <div class="panel pad0"><div class="head"><span class="title">Current vs simulated</span></div><div class="body" id="rs-compare"><div class="empty">Run a resolution to compare cars built, WIP and lead time — measured on identical draws.</div></div></div>
    </div>`;
  S.graph=new FactoryGraph(document.querySelector("#pane-resolve svg")); S.graph.init(S.layout); if(S.snap) S.graph.update(S.snap.live);
}
async function runResolve(){
  const kind=$("#rs-kind").value, station=S.interventions[kind].needs_station?$("#rs-st").value:null;
  const btn=$("#rs-run"); btn.disabled=true; btn.textContent="Re-simulating…";
  $("#rs-compare").innerHTML=`<div class="empty">Re-simulating both shifts under identical draws…</div>`;
  const r=await post("/api/counterfactual",{...caseBody(),intervention:kind,station});
  btn.disabled=false; btn.textContent="Run resolution";
  const up=r.delta_cars>=0;
  $("#rs-summary").innerHTML=`<div class="note ${r.delta_cars>3?"g":r.delta_cars<-3?"r":"a"}"><b>${up?"+":""}${r.delta_cars} cars</b> over the fault window — ${r.label}${station?" @ "+station:""} vs doing nothing.</div>`;
  $("#rs-compare").innerHTML=`<div style="padding:12px"><div class="compare">
      <div class="col"><h4>Current (do nothing)</h4><div class="bignum">${r.cars_before}<small> cars</small></div><div class="kv"><span class="k">WIP end</span><span class="v">${r.wip_before}</span></div><div class="kv"><span class="k">Lead time</span><span class="v">${r.lead_before??"—"}s</span></div></div>
      <div class="col sim"><h4>Simulated — ${r.label}</h4><div class="bignum">${r.cars_after}<small> cars</small></div><div class="delta ${up?"up":"down"}">${up?"+":""}${r.delta_cars} cars</div><div class="kv"><span class="k">WIP end</span><span class="v">${r.wip_after}</span></div><div class="kv"><span class="k">Lead time</span><span class="v">${r.lead_after??"—"}s</span></div></div></div>
    <div class="section-label" style="margin-top:12px"></div><div class="head" style="border:0;padding:0;margin-bottom:6px"><span class="title">Cumulative cars built</span></div>${chart(r)}
    <div class="chart-legend"><span><i style="background:#555"></i>current</span><span><i style="background:var(--accent)"></i>simulated</span></div></div>`;
  // animate the simulated (fixed) line on the flow graph
  $("#rs-flowaux").innerHTML='<span style="color:var(--green)">▶ simulating the fixed line…</span>';
  startStream(`&intervene=${kind}${station?`&station=${station}`:""}`);
}
function chart(r){ const W=560,H=140,pad=8,b=r.baseline,i=r.intervention_series;
  const mP=Math.max(...b.produced,...i.produced,1),mT=Math.max(...b.t,1);
  const pts=s=>s.t.map((t,k)=>`${pad+(t/mT)*(W-2*pad)},${H-pad-(s.produced[k]/mP)*(H-2*pad)}`).join(" ");
  return `<svg class="chart" viewBox="0 0 ${W} ${H}"><polyline fill="none" stroke="#555" stroke-width="2" points="${pts(b)}"/><polyline fill="none" stroke="var(--accent)" stroke-width="2.5" points="${pts(i)}"/></svg>`;
}

// ---------- utils ----------
async function post(url,body){ return (await (await fetch(url,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)})).json()); }
function modal(t,b){ $("#modal-root").innerHTML=`<div class="modal-bg" id="mbg"><div class="modal"><div class="head"><h3>${t}</h3><span class="x" id="mx">×</span></div><div class="body">${b}</div></div></div>`; $("#mx").onclick=closeModal; $("#mbg").onclick=e=>{if(e.target.id==="mbg")closeModal();}; }
function closeModal(){ $("#modal-root").innerHTML=""; }
function nameOf(sid){ return S.layout.stations.find(s=>s.id===sid)?.name||sid; }
function toast(m){ const t=el("div","toast",m); document.body.appendChild(t); setTimeout(()=>t.remove(),3000); }
init();
