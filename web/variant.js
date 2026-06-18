// variant.js — 編集/バリアント (damage edit of an existing sprite) + mask painter (brush/SAM2).
import { API, baseURL } from "./api.js";
import { el, toast, modal, renderGallery } from "./ui.js";
import { state, addCandidates } from "./state.js";
import { Live } from "./jobs.js";

function openMask(base, onApply) {
  const baseImg = el("img", { src: baseURL(base) });
  const canvas = el("canvas");
  const ctx = canvas.getContext("2d");
  let mode = "brush", brush = 36, points = [], drawing = false;
  baseImg.addEventListener("load", () => { canvas.width = baseImg.naturalWidth; canvas.height = baseImg.naturalHeight; });
  const pos = (e) => { const r = canvas.getBoundingClientRect(); return [(e.clientX - r.left) / r.width * canvas.width, (e.clientY - r.top) / r.height * canvas.height]; };
  const paint = (e) => { const [x, y] = pos(e); ctx.fillStyle = "rgba(255,40,40,0.7)"; ctx.beginPath(); ctx.arc(x, y, brush, 0, 7); ctx.fill(); };
  const dot = (x, y) => { ctx.fillStyle = "rgba(0,160,255,0.95)"; ctx.beginPath(); ctx.arc(x, y, 8, 0, 7); ctx.fill(); };
  canvas.addEventListener("pointerdown", (e) => { if (mode === "brush") { drawing = true; paint(e); } else { const [x, y] = pos(e); points.push([Math.round(x), Math.round(y)]); dot(x, y); } });
  canvas.addEventListener("pointermove", (e) => { if (drawing && mode === "brush") paint(e); });
  window.addEventListener("pointerup", () => { drawing = false; });

  const modeSeg = el("div", { class: "seg" },
    el("label", {}, el("input", { type: "radio", name: "mmode", checked: true, onchange: () => { mode = "brush"; } }), "ブラシ"),
    el("label", {}, el("input", { type: "radio", name: "mmode", onchange: () => { mode = "point"; } }), "点(SAM2)"));
  const brushR = el("input", { type: "range", min: 6, max: 90, value: 36, style: "width:120px", oninput: (e) => { brush = +e.target.value; } });
  const clearB = el("button", { class: "btn btn-sm", onclick: () => { ctx.clearRect(0, 0, canvas.width, canvas.height); points = []; } }, "クリア");
  const sam2B = el("button", { class: "btn btn-sm" }, "AIマスク生成");
  const cancelB = el("button", { class: "btn btn-sm" }, "キャンセル");
  const applyB = el("button", { class: "btn btn-sm btn-primary" }, "適用");
  const m = modal([el("strong", {}, "衣装マスク"), el("span", { class: "hint" }, "破りたい衣装を塗る（素肌/炎/髪は塗らない）"),
    el("span", { class: "spacer" }), modeSeg, brushR, clearB, sam2B, cancelB, applyB],
    [el("div", { class: "mask-stage" }, baseImg, canvas)]);
  cancelB.addEventListener("click", () => m.remove());

  sam2B.addEventListener("click", async () => {
    if (!points.length) { toast("点を打ってください（点モード）", "error"); return; }
    sam2B.disabled = true;
    try { const r = await API.sam2(base, points); onApply(r.id, "SAM2"); m.remove(); }
    catch (e) { toast("SAM2失敗: " + e.message, "error"); } finally { sam2B.disabled = false; }
  });
  applyB.addEventListener("click", async () => {
    // build a mask PNG: opaque(white) where painted, transparent elsewhere
    const tmp = el("canvas"); tmp.width = canvas.width; tmp.height = canvas.height;
    const tctx = tmp.getContext("2d");
    const src = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
    const out = tctx.createImageData(canvas.width, canvas.height);
    for (let i = 0; i < src.length; i += 4) { if (src[i + 3] > 10) { out.data[i] = out.data[i + 1] = out.data[i + 2] = out.data[i + 3] = 255; } }
    tctx.putImageData(out, 0, 0);
    applyB.disabled = true;
    try { const r = await API.uploadMask(tmp.toDataURL("image/png")); onApply(r.id, "手描き"); m.remove(); }
    catch (e) { toast("マスク保存失敗: " + e.message, "error"); } finally { applyB.disabled = false; }
  });
}

export function renderVariant(stage) {
  stage.innerHTML = "";
  stage.append(el("div", { class: "step-head" }, el("h1", {}, "編集 / バリアント"),
    el("p", {}, "既存スプライトをダメージ版などに編集（Qwen-Image-Edit）。衣装マスクで bbox 完全一致も可。")));

  const base = el("input", { list: "sprites", value: "hero", placeholder: "ベース（例 hero）" });
  const prompt = el("textarea", { placeholder: "編集指示（例: 衣装だけ破れさせる）" });
  const count = el("input", { type: "number", value: 4, min: 1, max: 8 });
  const denoise = el("input", { type: "number", value: 0.7, min: 0.3, max: 1, step: 0.05 });
  const seed = el("input", { type: "number", placeholder: "(自動)" });
  let maskId = null;
  const maskStat = el("span", { class: "hint" }, "マスク: なし（denoise方式）");
  const maskBtn = el("button", { class: "btn btn-sm", onclick: () => { if (!base.value.trim()) { toast("ベースを入力", "error"); return; } openMask(base.value.trim(), (id, how) => { maskId = id; maskStat.textContent = "マスク: 設定済み(" + how + "・bbox完全一致)"; resetBtn.hidden = false; }); } }, "衣装マスクを描く");
  const resetBtn = el("button", { class: "btn btn-sm btn-ghost", hidden: true, onclick: () => { maskId = null; maskStat.textContent = "マスク: なし（denoise方式）"; resetBtn.hidden = true; } }, "マスク解除");
  const genBtn = el("button", { class: "btn btn-primary" }, "バリアント生成");
  const busy = el("span", { class: "busy", hidden: true });
  const errp = el("div", { class: "err-text", hidden: true });
  const gallery = el("div", { class: "gallery" });

  genBtn.addEventListener("click", async () => {
    if (!base.value.trim() || !prompt.value.trim()) { toast("ベースと指示を入力", "error"); return; }
    errp.hidden = true; genBtn.disabled = true; busy.hidden = false; busy.textContent = "生成中…";
    const off = Live.on((t) => { busy.textContent = "生成中… " + t; });
    try {
      const r = await API.variant({ base: base.value.trim(), prompt: prompt.value.trim(), count: +count.value, denoise: +denoise.value, seed: seed.value.trim() ? +seed.value : null, mask_id: maskId });
      const cands = (r.candidates || []).map((c) => ({ ...c, kind: "variant", base: r.base }));
      addCandidates(cands);
      renderGallery(gallery, cands, {});  // variant cards: no "②へ"
    } catch (e) { errp.hidden = false; errp.textContent = "生成失敗: " + e.message; }
    finally { off(); genBtn.disabled = false; busy.hidden = true; }
  });

  stage.append(el("div", { class: "layout-2" },
    el("div", { class: "panel" },
      el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "ベース"), base), el("div", { class: "field" }, el("label", {}, "案数"), count),
        el("div", { class: "field", title: "0.7=ポーズ忠実&破れ明確" }, el("label", {}, "破れ強さ"), denoise), el("div", { class: "field" }, el("label", {}, "seed"), seed)),
      el("div", { class: "field" }, el("label", {}, "編集指示"), prompt),
      el("div", { class: "inline" }, maskBtn, resetBtn, maskStat),
      el("div", { class: "cta-bar" }, busy, genBtn), errp),
    el("div", {}, el("div", { class: "gallery-toolbar" }, el("strong", {}, "候補"), el("span", { class: "spacer" })), gallery)));
}
