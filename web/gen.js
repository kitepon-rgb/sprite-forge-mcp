// gen.js — Step ① 素体生成 and Step ④ 活用. Shared generate form + AI prompt-craft + gallery.
import { API } from "./api.js";
import { el, toast, renderGallery, emptyState } from "./ui.js";
import { state, addCandidates, spriteCandidates } from "./state.js";
import { Live } from "./jobs.js";

function loraOptions(selected) {
  return [el("option", { value: "" }, "（画風LoRA既定）"),
    ...state.loras.map((n) => el("option", { value: n, selected: n === selected || false }, n))];
}
function triggerFor(name) {
  const m = /^char-(.+)\.safetensors$/i.exec(name || "");
  if (m) return "sf" + m[1].replace(/[^a-z0-9]/gi, "");
  if (/sprite-style-v2/i.test(name)) return "sfrpg";
  if (/sprite-style-v1/i.test(name)) return "sf4spirit";
  return "";
}

export function renderGenerate(stage, mode /* "base" | "activate" */) {
  const isAct = mode === "activate";
  stage.innerHTML = "";
  stage.append(el("div", { class: "step-head" },
    el("h1", {}, isAct ? "④ 活用 / 共有" : "① 素体生成"),
    el("p", {}, isAct ? "学習したキャラLoRAで、このキャラを好きなポーズ・衣装で生成 → 採用/共有。"
      : "コンセプトから新規キャラの素体を生成。良い1枚を選んで「② バイブルへ」。")));

  // --- left: form ---
  const idea = el("input", { placeholder: "ラフ発想（例: 炎の魔法使いの女の子、ツインテール、強気）" });
  const project = el("input", { placeholder: "(任意) プロジェクトcwd", style: "max-width:160px" });
  const craftBtn = el("button", { class: "btn btn-primary" }, "AIでプロンプト生成 (Opus)");
  const prompt = el("textarea", { placeholder: "生成プロンプト（AI生成 or 直接入力・編集可）" });
  craftBtn.addEventListener("click", async () => {
    if (!idea.value.trim()) { toast("ラフ発想を入力してください", "error"); return; }
    const o = craftBtn.innerHTML; craftBtn.disabled = true; craftBtn.innerHTML = '<span class="spin"></span>生成中';
    try { const r = await API.craft(idea.value.trim(), project.value.trim() || undefined); prompt.value = r.prompt; toast("プロンプト生成: " + r.model); }
    catch (e) { toast("プロンプト生成失敗: " + e.message, "error", true); }
    finally { craftBtn.disabled = false; craftBtn.innerHTML = o; }
  });
  const craft = el("div", { class: "craft" },
    el("div", { class: "craft-row" }, el("div", { class: "field" }, el("label", {}, "ラフ発想"), idea), project, craftBtn),
    el("div", { class: "pill-note" }, "claude -p / codex を cwd=プロジェクトで実行（追加課金なし・プロジェクト文脈付き）"));

  const w = el("input", { type: "number", value: 1024 }), h = el("input", { type: "number", value: 1024 });
  const count = el("input", { type: "number", value: 4, min: 1, max: 8 }), seed = el("input", { type: "number", placeholder: "(自動)" });
  const styleToggle = el("input", { type: "checkbox", checked: !isAct });
  const loraSel = el("select", {}, ...loraOptions(isAct && state.charLora ? state.charLora.lora_name : ""));
  const trig = el("input", { value: isAct && state.charLora ? state.charLora.trigger : "", placeholder: "(自動)", style: "max-width:120px" });
  const strength = el("input", { type: "number", value: 0.65, min: 0, max: 1.2, step: 0.05 });
  loraSel.addEventListener("change", () => { trig.value = triggerFor(loraSel.value); });
  const ctrl = el("input", { list: "sprites", placeholder: "(なし) 例 hero" }), ctrlStr = el("input", { type: "number", value: 0.55, min: 0, max: 1, step: 0.05 });

  const poseRow = isAct ? el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "ポーズ プリセット"),
    el("div", { class: "inline" }, ...["standing", "walking", "running", "casting a spell", "battle stance", "sitting"].map((p) =>
      el("button", { class: "btn btn-sm btn-ghost", onclick: () => { prompt.value = (prompt.value ? prompt.value.replace(/,?\s*(standing|walking|running|casting a spell|battle stance|sitting)\b/gi, "") : "") + ", " + p; } }, p))))) : false;

  const bgSel = el("select", {}, el("option", { value: "auto" }, "背景:自動(淡色→緑)"), el("option", { value: "grey" }, "背景:グレー"), el("option", { value: "green" }, "背景:緑"), el("option", { value: "magenta" }, "背景:マゼンタ"));
  const genBtn = el("button", { class: "btn btn-primary" }, isAct ? "このキャラを生成" : "素体を生成");
  const busy = el("span", { class: "busy", hidden: true });
  const errp = el("div", { class: "err-text", hidden: true });

  const form = el("div", { class: "panel" },
    craft,
    el("div", { class: "field" }, el("label", {}, "プロンプト"), prompt),
    el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "幅"), w), el("div", { class: "field" }, el("label", {}, "高"), h),
      el("div", { class: "field" }, el("label", {}, "案数"), count), el("div", { class: "field" }, el("label", {}, "seed"), seed),
      el("div", { class: "field", title: "淡色(白/水色)キャラは緑背景の方が透過がきれい" }, el("label", {}, "背景"), bgSel)),
    el("div", { class: "row" }, el("label", { class: "toggle" }, styleToggle, "画風LoRA"),
      el("div", { class: "field" }, el("label", {}, "LoRA選択"), loraSel), el("div", { class: "field" }, el("label", {}, "トリガー"), trig),
      el("div", { class: "field" }, el("label", {}, "LoRA強さ"), strength)),
    poseRow,
    el("details", { class: "disclosure" }, el("summary", {}, "詳細：ControlNet 参照ポーズ"),
      el("div", { class: "row", style: "margin-top:8px" }, el("div", { class: "field" }, el("label", {}, "参照スプライト"), ctrl), el("div", { class: "field" }, el("label", {}, "Ctrl強さ"), ctrlStr))),
    el("div", { class: "cta-bar" }, busy, genBtn), errp);

  // --- right: gallery ---
  const gallery = el("div", { class: "gallery" });
  const galWrap = el("div", {}, el("div", { class: "gallery-toolbar" }, el("strong", {}, "候補"), el("span", { class: "spacer" })), gallery);
  const refresh = () => renderGallery(gallery, isAct ? [...state.candidates.values()].filter((c) => c.kind !== "variant") : spriteCandidates(),
    isAct ? {} : { onBible: (c) => { state.pendingBibleSource = c.id; location.hash = "#/step2"; } });
  refresh();

  genBtn.addEventListener("click", async () => {
    if (!prompt.value.trim()) { toast("プロンプトを入力（AI生成も可）", "error"); return; }
    errp.hidden = true; genBtn.disabled = true; busy.hidden = false; busy.textContent = "生成中…";
    const off = Live.on((t) => { busy.textContent = "生成中… " + t; });
    try {
      const picked = loraSel.value.trim();
      const body = {
        prompt: prompt.value.trim(), width: +w.value, height: +h.value, count: +count.value,
        style_lora: !picked && styleToggle.checked, lora_strength: +strength.value, bg: bgSel.value,
      };
      if (picked) { body.lora_name = picked; body.lora_trigger = trig.value.trim(); }
      else if (isAct && state.charLora) { body.lora_name = state.charLora.lora_name; body.lora_trigger = state.charLora.trigger; }
      if (seed.value.trim()) body.seed = +seed.value;
      if (ctrl.value.trim()) { body.control_base = ctrl.value.trim(); body.control_strength = +ctrlStr.value; }
      const r = await API.generate(body);
      if (body.bg === "auto" && r.bg && r.bg !== "grey") toast("淡色と判定 → 背景「" + r.bg + "」で生成", "ok");
      addCandidates((r.candidates || []).map((c) => ({ ...c, kind: "sprite" })));
      refresh();
    } catch (e) { errp.hidden = false; errp.textContent = "生成失敗: " + e.message; }
    finally { off(); genBtn.disabled = false; busy.hidden = true; }
  });

  stage.append(el("div", { class: "layout-2" }, form, galWrap));
}
