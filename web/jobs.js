// jobs.js — unified long-job tracker (each kind gets its own dock row) + SSE live progress.
import { $, API } from "./api.js";
import { el, toast } from "./ui.js";

const dock = () => $("#jobs-dock");

export function addJob({ id, label, statusFn, parse, onDone }) {
  const chip = el("span", { class: "chip running" }, "running");
  const bar = el("i");
  const prog = el("div", { class: "progress indet" }, bar);
  const line = el("div", { class: "jr-line" }, "…");
  const logPre = el("pre", { hidden: true });
  const x = el("span", { class: "x", title: "閉じる" }, "×");
  const row = el("div", { class: "job-row" }, el("div", { class: "jr-head" }, el("b", {}, label), chip, x), prog, line, logPre);
  let stopped = false;
  x.addEventListener("click", () => { stopped = true; row.remove(); });
  dock().prepend(row);

  async function poll() {
    if (stopped) return;
    try {
      const s = await statusFn(id);
      const p = parse(s);
      if (p.pct != null) { prog.classList.remove("indet"); bar.style.width = Math.round(p.pct * 100) + "%"; }
      line.textContent = p.line || "";
      if (p.log && p.log.length) { logPre.hidden = false; logPre.textContent = p.log.join("\n"); logPre.scrollTop = logPre.scrollHeight; }
      if (["done", "error", "unknown"].includes(p.state)) {
        chip.className = "chip " + (p.state === "done" ? "done" : "error");
        chip.textContent = p.state;
        if (p.state === "done") { bar.style.width = "100%"; prog.classList.remove("indet"); toast(label + " 完了", "ok"); onDone && onDone(s); }
        else { toast(label + " 失敗: " + (s.error || p.state), "error", true); }
        return;
      }
    } catch { /* transient — keep polling */ }
    setTimeout(poll, 4000);
  }
  poll();
  return row;
}

export const parseBible = (s) => ({
  pct: s.total ? (s.done || 0) / s.total : null,
  line: `パネル ${s.done || 0}/${s.total || "?"}` + (s.status && !["generating panels", "done"].includes(s.status) ? ` · ${s.status}` : ""),
  state: s.status === "done" ? "done" : (s.status === "error" ? "error" : (s.status === "unknown" ? "unknown" : "run")),
});

export const parseTrain = (s) => ({
  pct: s.percent != null ? s.percent / 100 : (s.total ? (s.step || 0) / s.total : null),
  line: `${s.status || ""} ${s.step || 0}/${s.total || "?"}` + (s.percent != null ? ` (${s.percent}%)` : "") + (s.lora ? ` → ${s.lora}` : ""),
  state: s.status === "done" ? "done" : (s.status === "error" ? "error" : (s.status === "unknown" ? "unknown" : "run")),
  log: (s.log || []).concat(s.last ? [s.last] : []).slice(-12),
});

// SSE live sampling progress (one channel; generate/variant read Live.text)
export const Live = {
  text: "", subs: new Set(),
  set(t) { this.text = t; this.subs.forEach((f) => f(t)); },
  on(f) { this.subs.add(f); return () => this.subs.delete(f); },
};
export function connectSSE() {
  try {
    const es = new EventSource("/api/progress");
    es.onmessage = (ev) => {
      try {
        const d = JSON.parse(ev.data);
        const v = d.data ? d.data.value : d.value, m = d.data ? d.data.max : d.max;
        if (d.type === "progress" && m) Live.set(`sampling ${v}/${m}`);
        else if (d.type === "execution_success" || d.type === "execution_error" || d.type === "execution_start") Live.set("");
      } catch { /* ignore */ }
    };
    es.onerror = () => {};
  } catch { /* SSE optional */ }
}
