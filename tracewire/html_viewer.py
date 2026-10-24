"""tracewire.html_viewer -- renders an interactive HTML timeline viewer.

The viewer is a single self-contained HTML file (no network dependencies).
It draws the execution as a *wire* -- a horizontal timeline -- with one
marker per control point.  Clicking a marker shows the program state at
that point as if you were debugging.  Marker sets can be toggled:

    visible   -- unchecking hides the set's markers and simplifies the
                 wire between the remaining ones (straight segment).
    important -- shows a pair of back/forward buttons that jump between
                 the markers of that set.
"""

from __future__ import annotations

import html
import json
import os

_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
  :root { --ink:#1a2230; --muted:#66707f; --line:#d8dee8; --accent:#2563eb;
          --green:#16a34a; --amber:#d97706; --red:#dc2626; --bg:#f7f9fc; }
  * { box-sizing:border-box; }
  body { margin:0; font:14px/1.5 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
         background:var(--bg); color:var(--ink); }
  header { padding:12px 16px; background:#fff; border-bottom:1px solid var(--line);
           display:flex; flex-wrap:wrap; gap:8px 20px; align-items:baseline; }
  header h1 { font-size:16px; margin:0; }
  header .meta { color:var(--muted); font-size:12px; }
  main { display:flex; gap:16px; padding:16px; flex-wrap:wrap; }
  section { background:#fff; border:1px solid var(--line); border-radius:8px;
            padding:12px; min-width:280px; }
  section h2 { font-size:13px; margin:0 0 8px; color:var(--muted); text-transform:uppercase;
               letter-spacing:.04em; }
  #timeline { flex:1 1 640px; }
  #wire-wrap { overflow-x:auto; }
  svg { display:block; width:100%; height:120px; }
  .marker { cursor:pointer; }
  .marker:hover { r:7; }
  .marker.selected { stroke:#000; stroke-width:2; }
  #inspector { flex:1 1 300px; max-height:70vh; overflow:auto; }
  table { border-collapse:collapse; width:100%; font-size:12px; }
  th,td { text-align:left; padding:3px 6px; border-bottom:1px solid var(--line);
          vertical-align:top; word-break:break-all; }
  th { color:var(--muted); font-weight:600; }
  .changed { color:var(--amber); }
  #sets { flex:1 1 260px; }
  .set-row { display:flex; align-items:center; gap:8px; padding:5px 0;
             border-bottom:1px dashed var(--line); }
  .set-row label { display:flex; align-items:center; gap:4px; font-size:12px; }
  .set-row .count { color:var(--muted); font-size:11px; margin-left:auto; }
  .nav { display:flex; gap:4px; }
  .nav button { border:1px solid var(--line); background:#fff; border-radius:4px;
                cursor:pointer; font-size:11px; padding:1px 7px; }
  .nav button:hover { background:#eef2f8; }
  #source { flex:1 1 100%; }
  #source pre { margin:0; font-size:12px; overflow-x:auto; }
  .src-line { white-space:pre; }
  .src-line.hit { background:#e8f0fe; }
  .src-line.hit.selected { background:#dbeafe; outline:1px solid var(--accent); }
  .src-no { display:inline-block; width:3.2em; color:var(--muted); user-select:none;
            text-align:right; margin-right:10px; }
  footer { padding:10px 16px; color:var(--muted); font-size:11px; }
</style>
</head>
<body>
<header>
  <h1>TraceWire &mdash; execution timeline</h1>
  <span class="meta" id="meta"></span>
</header>
<main>
  <section id="timeline">
    <h2>Wire &middot; click a marker to inspect state</h2>
    <div id="wire-wrap"><svg id="svg" viewBox="0 0 1000 120"></svg></div>
  </section>
  <section id="sets">
    <h2>Marker sets</h2>
    <div id="sets-list"></div>
  </section>
  <section id="inspector">
    <h2 id="insp-head">Inspector</h2>
    <div id="insp-body"><span style="color:var(--muted)">click a marker on the wire</span></div>
  </section>
  <section id="source">
    <h2>Source</h2>
    <pre id="src-pre"></pre>
  </section>
</main>
<footer>TraceWire &middot; self-contained viewer &middot; trace: __TRACEFILE__</footer>
<script>
const DATA = __DATA__;
const model = DATA.model;
const sets = DATA.sets;
const markerSets = DATA.markerSets;   // per marker: array of set indices
let selected = -1;
let svg, wirePath, markerNodes = [], setControls = {};

const SVG_NS = "http://www.w3.org/2000/svg";
function svgEl(tag, attrs) {
  const el = document.createElementNS(SVG_NS, tag);
  for (const k in attrs) el.setAttribute(k, attrs[k]);
  return el;
}

function wireY(i) {   // gentle zigzag so the wire is legible
  return 60 + ((i % 2) ? 16 : -16);
}

function rebuild() {
  svg = document.getElementById("svg");
  svg.innerHTML = "";
  markerNodes = [];
  const n = model.markers.length;
  document.getElementById("meta").textContent =
    model.source + " \u00b7 " + n + " control points \u00b7 " + sets.length + " sets";

  if (n === 0) return;

  const visible = model.markers.map((m, i) =>
    markerSets[i].some(si => sets[si] && !sets[si].visible) ? false : true);
  const xOf = i => 20 + (n === 1 ? 0 : i * (960 / (n - 1)));

  // wire: through visible markers only; hidden markers simplify to straight
  const pts = [];
  for (let i = 0; i < n; i++) if (visible[i]) pts.push([xOf(i), wireY(i)]);
  let d;
  if (pts.length >= 2) {
    d = "M" + pts.map(p => p[0].toFixed(1) + "," + p[1].toFixed(1)).join(" L");
  } else if (pts.length === 1) {
    d = "M" + pts[0][0].toFixed(1) + "," + pts[0][1].toFixed(1);
  } else {
    d = "M20,60 L980,60";
  }
  const w = svgEl("path", { d: d, fill: "none", stroke: "#94a3b8",
                           "stroke-width": 2, "stroke-linecap": "round" });
  svg.appendChild(w);

  // invisible markers: tiny dots so the simplification is visible
  for (let i = 0; i < n; i++) {
    if (!visible[i]) {
      svg.appendChild(svgEl("circle", { cx: xOf(i), cy: wireY(i), r: 1.6,
                                        fill: "#cbd5e1" }));
    }
  }
  // visible markers
  const palette = ["#2563eb", "#16a34a", "#d97706", "#dc2626", "#0891b2",
                   "#7c3aed", "#db2777", "#65a30d", "#ea580c", "#0f766e"];
  for (let i = 0; i < n; i++) {
    if (!visible[i]) continue;
    const m = model.markers[i];
    const color = markerSets[i].length ? palette[markerSets[i][0] % palette.length]
                                       : "#94a3b8";
    const c = svgEl("circle", { class: "marker", cx: xOf(i), cy: wireY(i),
                                r: m.t === 4 ? 6.5 : 4.5, fill: color,
                                "data-i": i });
    if (m.t === 4) {   // exception: ring
      svg.appendChild(svgEl("circle", { cx: xOf(i), cy: wireY(i), r: 8.5,
                                        fill: "none", stroke: "#dc2626",
                                        "stroke-width": 1.5 }));
    }
    c.addEventListener("click", () => select(i));
    svg.appendChild(c);
    markerNodes.push(c);
  }
}

function select(i) {
  selected = i;
  const m = model.markers[i];
  // highlight on wire
  svg.querySelectorAll(".marker").forEach(el => el.classList.remove("selected"));
  if (markerNodes[i]) markerNodes[i].classList.add("selected");
  // inspector
  const setsHere = markerSets[i].map(si => sets[si].name);
  document.getElementById("insp-head").textContent =
    "Marker #" + i + " \u00b7 " + m.f + "() line " + m.l;
  let rows = "<table><tr><th>variable</th><th>type</th><th>value</th></tr>";
  const keys = Object.keys(m.v);
  if (!keys.length) rows += "<tr><td colspan=3 style='color:var(--muted)'>no locals</td></tr>";
  for (const k of keys) {
    const changed = (k in m.ch);
    rows += "<tr><td" + (changed ? " class='changed' title='changed here'" : "") +
            ">" + esc(k) + "</td><td>" + esc(m.ty[k] || "?") + "</td><td>" +
            esc(JSON.stringify(m.v[k])) + "</td></tr>";
  }
  rows += "</table>";
  rows += "<p style='font-size:12px;color:var(--muted)'>in sets: " +
          (setsHere.length ? esc(setsHere.join(", ")) : "none") + "</p>";
  if (m.t === 4) rows += "<p style='color:var(--red)'>exception control point</p>";
  document.getElementById("insp-body").innerHTML = rows;
  // source highlight
  const src = document.getElementById("src-pre").children;
  if (src && src[m.l - 1]) {
    for (const el of src) el.classList.remove("hit", "selected");
    src[m.l - 1].classList.add("hit");
    src[m.l - 1].classList.add("selected");
    src[m.l - 1].scrollIntoView({ block: "center" });
  }
}

function esc(s) {
  return String(s).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
}

function renderSets() {
  const wrap = document.getElementById("sets-list");
  wrap.innerHTML = "";
  sets.forEach((s, si) => {
    const row = document.createElement("div");
    row.className = "set-row";
    const vis = document.createElement("label");
    vis.innerHTML = "<input type='checkbox'> visible";
    vis.firstChild.checked = s.visible;
    vis.firstChild.addEventListener("change", () => { sets[si].visible = vis.firstChild.checked; rebuild(); });
    const imp = document.createElement("label");
    imp.innerHTML = "<input type='checkbox'> important";
    imp.firstChild.checked = s.important;
    imp.firstChild.addEventListener("change", () => { sets[si].important = imp.firstChild.checked; renderSets(); });
    const nav = document.createElement("span");
    nav.className = "nav";
    if (s.important) {
      const prev = document.createElement("button"); prev.textContent = "\u2190";
      const next = document.createElement("button"); next.textContent = "\u2192";
      prev.title = "previous " + s.name; next.title = "next " + s.name;
      prev.addEventListener("click", () => jump(si, -1));
      next.addEventListener("click", () => jump(si, +1));
      nav.appendChild(prev); nav.appendChild(next);
    }
    const cnt = document.createElement("span");
    cnt.className = "count";
    cnt.textContent = markerSets.filter(a => a.indexOf(si) >= 0).length + " markers";
    const name = document.createElement("span");
    name.textContent = s.name;
    row.appendChild(vis); row.appendChild(imp); row.appendChild(name);
    row.appendChild(nav); row.appendChild(cnt);
    wrap.appendChild(row);
  });
}

function jump(si, dir) {
  let best = -1;
  const n = model.markers.length;
  if (selected < 0) {
    best = dir > 0 ? 0 : n - 1;
  } else {
    if (dir > 0) {
      for (let i = selected + 1; i < n; i++)
        if (markerSets[i].indexOf(si) >= 0) { best = i; break; }
    } else {
      for (let i = selected - 1; i >= 0; i--)
        if (markerSets[i].indexOf(si) >= 0) { best = i; break; }
    }
  }
  if (best >= 0) select(best);
}

function renderSource() {
  const pre = document.getElementById("src-pre");
  if (!model.sourceText) { pre.textContent = "(source file not available)"; return; }
  const lines = model.sourceText.split("\\n");
  let out = "";
  for (let i = 0; i < lines.length; i++) {
    out += "<span class='src-line'><span class='src-no'>" + (i + 1) + "</span>" +
           esc(lines[i]) + "</span>\\n";
  }
  pre.innerHTML = out;
}

renderSource();
renderSets();
rebuild();
</script>
</body>
</html>
"""


def render_html(model, store, out_path: str, title: str = None) -> None:
    """Render *model* (a TraceModel) plus *store* sets into *out_path*."""
    payload = _build_payload(model, store)
    page = (_TEMPLATE
            .replace("__TITLE__", html.escape(title or "TraceWire execution timeline"))
            .replace("__DATA__", payload)
            .replace("__TRACEFILE__", html.escape(os.path.basename(model.reader.srcfile))))
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(page)


def _json_safe(value):
    """Make a traced value safe to inline into the page's JSON payload.

    Traces can legitimately contain ``bytes`` (the format has a BYTES tag),
    which ``json.dumps`` refuses with ``TypeError: Object of type bytes is not
    JSON serializable`` - rendering the viewer for any program that so much as
    touched a byte string used to crash outright.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (bytes, bytearray)):
        try:
            return bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            return repr(bytes(value))
    return repr(value)


def _build_payload(model, store):
    set_list = []
    for s in store.sets.values():
        set_list.append({"name": s.name, "visible": s.visible, "important": s.important})
    membership = [[si for si, s in enumerate(store.sets.values()) if s.contains(m)]
                  for m in model.markers]
    markers_payload = []
    for m in model.markers:
        markers_payload.append({
            "i": m.index, "f": m.function, "l": m.line, "t": m.evtype,
            "v": {k: _json_safe(v) for k, v in m.variables.items()},
            "ch": {k: _json_safe(v) for k, v in m.changed.items()},
            "ty": m.types,
        })
    source_text = ""
    try:
        if os.path.exists(model.source):
            with open(model.source, "r", encoding="utf-8", errors="replace") as fh:
                source_text = fh.read()
    except OSError:
        pass
    data = {
        "model": {"source": model.source, "sourceText": source_text,
                  "markers": markers_payload},
        "sets": set_list,
        "markerSets": membership,
    }
    return json.dumps(data, ensure_ascii=False).replace("</", "<\\/")
