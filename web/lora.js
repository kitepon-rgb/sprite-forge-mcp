// lora.js — Step ③ キャラLoRA (from a bible) + 設定/LoRA (style-LoRA training).
import { API, baseURL } from "./api.js";
import { el, toast } from "./ui.js";
import { state, emit, setCharLora } from "./state.js";
import { addJob, parseTrain } from "./jobs.js";

async function reloadLoras() { try { state.loras = (await API.loras()).loras || []; emit(); } catch {} }

export function renderCharLora(stage) {
  stage.innerHTML = "";
  stage.append(el("div", { class: "step-head" }, el("h1", {}, "③ キャラLoRA"),
    el("p", {}, "②で作ったバイブルのパネルを教材に、そのキャラのLoRAを学習（ComfyUI停止でクリーンGPU・~20〜30分）。")));

  const bible = el("input", { value: state.pendingCharBible || (state.bibles[0] && state.bibles[0].name) || "", placeholder: "バイブル名" });
  const name = el("input", { placeholder: "出力LoRA名（既定 char-<バイブル名>）" });
  const trig = el("input", { placeholder: "(自動 sf<name>)" });
  const steps = el("input", { type: "number", value: 1500 });
  const start = el("button", { class: "btn btn-primary" }, "キャラLoRA学習を開始");
  const hint = el("div", { class: "hint" }, state.bibles.length ? "利用可能バイブル: " + state.bibles.map((b) => b.name).join(", ") : "まず②でバイブルを生成してください");

  start.addEventListener("click", async () => {
    const bn = bible.value.trim();
    if (!bn) { toast("バイブル名を入力", "error"); return; }
    start.disabled = true;
    try {
      const r = await API.trainChar({ bible_name: bn, trigger: trig.value.trim() || undefined, name: name.value.trim() || undefined, steps: +steps.value });
      toast("キャラLoRA学習 開始: " + r.name);
      addJob({
        id: r.job_id, label: "char-lora · " + r.name, statusFn: API.trainStatus, parse: parseTrain,
        onDone: (s) => { if (s.lora) { setCharLora(s.lora, s.trigger || r.trigger); reloadLoras(); toast("キャラLoRA配置完了: " + s.lora + " → ④で活用可"); } },
      });
    } catch (e) { toast("開始失敗: " + e.message, "error", true); }
    finally { start.disabled = false; }
  });

  stage.append(el("div", { class: "panel" },
    el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "バイブル"), bible),
      el("div", { class: "field" }, el("label", {}, "LoRA名"), name), el("div", { class: "field" }, el("label", {}, "トリガー"), trig),
      el("div", { class: "field" }, el("label", {}, "steps"), steps)),
    hint, el("div", { class: "cta-bar" }, start)));
  if (state.charLora) stage.append(el("div", { class: "banner" }, "学習済み: " + state.charLora.lora_name + "（" + state.charLora.trigger + "）",
    el("span", { class: "spacer" }), el("button", { class: "btn btn-sm", onclick: () => location.hash = "#/step4" }, "④ 活用 →")));
  state.pendingCharBible = null;
}

export function renderSettings(stage) {
  stage.innerHTML = "";
  stage.append(el("div", { class: "step-head" }, el("h1", {}, "設定 / LoRA"),
    el("p", {}, "画風LoRA（ハウス画風）の学習と、導入済みLoRAの確認。")));

  const grid = el("div", { class: "train-grid" });
  const nameI = el("input", { value: "sprite-style-v2" }), trigI = el("input", { value: "sfrpg" }), stepsI = el("input", { type: "number", value: 1500 });
  const countSpan = el("span", { class: "hint" }, "");
  const updateCount = () => { const ks = [...grid.querySelectorAll(".tk")]; countSpan.textContent = ks.filter((c) => c.checked).length + "/" + ks.length + "枚"; };
  (async () => {
    try {
      const { candidates } = await API.loraCandidates();
      candidates.forEach((c) => {
        const ck = el("input", { type: "checkbox", class: "tk", checked: true });
        const cap = el("input", { class: "tcap", value: c.desc });
        const tc = el("div", { class: "tc" }, el("img", { src: baseURL(c.stem) }),
          el("label", { class: "trow" }, ck, " " + c.stem), cap);
        tc.dataset.stem = c.stem; grid.append(tc);
      });
      updateCount();
    } catch (e) { grid.append(el("p", { class: "hint" }, "候補取得失敗: " + e.message)); }
  })();
  grid.addEventListener("change", updateCount);
  const all = el("button", { class: "btn btn-sm", onclick: () => { grid.querySelectorAll(".tk").forEach((c) => c.checked = true); updateCount(); } }, "全選択");
  const none = el("button", { class: "btn btn-sm", onclick: () => { grid.querySelectorAll(".tk").forEach((c) => c.checked = false); updateCount(); } }, "全解除");
  const start = el("button", { class: "btn btn-primary" }, "画風LoRA学習を開始");
  start.addEventListener("click", async () => {
    const sprites = [...grid.querySelectorAll(".tc")].filter((t) => t.querySelector(".tk").checked)
      .map((t) => ({ stem: t.dataset.stem, desc: t.querySelector(".tcap").value.trim() }));
    if (!sprites.length) { toast("学習画像を選択", "error"); return; }
    start.disabled = true;
    try {
      const r = await API.trainStyle({ sprites, name: nameI.value.trim() || "sprite-style", trigger: trigI.value.trim() || "sfrpg", steps: +stepsI.value });
      toast("画風LoRA学習 開始: " + r.name);
      addJob({ id: r.job_id, label: "style-lora · " + r.name, statusFn: API.trainStatus, parse: parseTrain, onDone: () => reloadLoras() });
    } catch (e) { toast("開始失敗: " + e.message, "error", true); }
    finally { start.disabled = false; }
  });

  stage.append(el("div", { class: "panel" },
    el("div", { class: "section-title" }, "画風LoRA 学習セット（チェックした画像で学習）"),
    el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "出力名"), nameI),
      el("div", { class: "field" }, el("label", {}, "トリガー"), trigI), el("div", { class: "field" }, el("label", {}, "steps"), stepsI),
      all, none, countSpan),
    grid, el("div", { class: "cta-bar" }, start)));
  stage.append(el("div", { class: "panel", style: "margin-top:16px" },
    el("div", { class: "section-title" }, "導入済みLoRA"),
    el("div", { class: "hint" }, state.loras.length ? state.loras.join(", ") : "（なし／読み込み中）")));
}
