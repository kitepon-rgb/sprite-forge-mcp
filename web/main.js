// main.js — boot, hash router, stepper, shared loads (GPU / sprites / loras / SSE).
import { $, API } from "./api.js";
import { state, emit, subscribe } from "./state.js";
import { el } from "./ui.js";
import { connectSSE } from "./jobs.js";
import { renderGenerate } from "./gen.js";
import { renderBible } from "./bible.js";
import { renderCharLora, renderSettings } from "./lora.js";
import { renderVariant } from "./variant.js";

const STEPS = [
  { key: "step1", n: "1", label: "素体生成", render: (s) => renderGenerate(s, "base") },
  { key: "step2", n: "2", label: "キャラバイブル", render: renderBible },
  { key: "step3", n: "3", label: "キャラLoRA", render: renderCharLora },
  { key: "step4", n: "4", label: "活用/共有", render: (s) => renderGenerate(s, "activate") },
];
const ALT = [
  { key: "edit", label: "編集/バリアント", render: renderVariant },
  { key: "settings", label: "設定/LoRA", render: renderSettings },
];
const ALL = Object.fromEntries([...STEPS, ...ALT].map((r) => [r.key, r]));

function renderStepper(active) {
  const bar = $("#stepper"); bar.innerHTML = "";
  const ai = STEPS.findIndex((s) => s.key === active);
  STEPS.forEach((s, i) => {
    const cls = "step" + (s.key === active ? " is-current" : (ai >= 0 && i < ai ? " is-done" : ""));
    const step = el("div", { class: cls, onclick: () => { location.hash = "#/" + s.key; } }, el("span", { class: "n" }, s.n), s.label);
    bar.append(step);
    if (i < STEPS.length - 1) bar.append(el("span", { class: "rail" }));
  });
  bar.append(el("span", { class: "sep" }));
  ALT.forEach((a) => bar.append(el("div", { class: "step alt" + (a.key === active ? " is-current" : ""), onclick: () => { location.hash = "#/" + a.key; } }, a.label)));
}

function route() {
  const key = location.hash.replace(/^#\/?/, "") || "step1";
  const r = ALL[key] || STEPS[0];
  renderStepper(r.key);
  try { r.render($("#stage")); } catch (e) { $("#stage").innerHTML = ""; $("#stage").append(el("div", { class: "err-text" }, "画面エラー: " + e.message)); console.error(e); }
}

async function pollGpu() {
  try {
    const g = await API.gpu();
    const pill = $("#gpu");
    if (g.comfy_up) { pill.className = "pill ok"; pill.textContent = `GPU: ${(g.device || "").replace(/^cuda:0 /, "")} · ${g.vram_used_mb}/${g.vram_total_mb}MB`; }
    else { pill.className = "pill err"; pill.textContent = "GPU: ComfyUI未接続"; }
  } catch { $("#gpu").className = "pill err"; $("#gpu").textContent = "GPU: ?"; }
  setTimeout(pollGpu, 15000);
}

async function loadLists() {
  try { state.sprites = (await API.sprites()).sprites || []; } catch {}
  try { state.loras = (await API.loras()).loras || []; } catch {}
  const dl = $("#sprites"); dl.innerHTML = ""; state.sprites.forEach((n) => dl.append(el("option", { value: n.replace(/\.png$/, "") })));
  emit();
}

window.addEventListener("hashchange", route);
connectSSE();
pollGpu();
loadLists().then(route);
