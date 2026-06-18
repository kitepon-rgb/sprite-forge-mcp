// ui.js — shared UI primitives + candidate card + gallery + adopt modal.
import { $, API, imgURL } from "./api.js";

export function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (v == null || v === false) continue;
    if (k === "class") e.className = v;
    else if (k === "html") e.innerHTML = v;
    else if (k.startsWith("on") && typeof v === "function") e.addEventListener(k.slice(2), v);
    else e.setAttribute(k, v === true ? "" : v);
  }
  for (const k of kids.flat()) if (k != null && k !== false) e.append(k.nodeType ? k : document.createTextNode(k));
  return e;
}

export function toast(msg, kind = "ok", sticky = false) {
  const t = el("div", { class: "toast " + kind }, msg);
  $("#toasts").append(t);
  if (sticky) t.addEventListener("click", () => t.remove());
  else setTimeout(() => t.remove(), 4200);
  return t;
}

export function modal(headKids, bodyKids) {
  const box = el("div", { class: "modal-box" }, el("div", { class: "modal-head" }, ...headKids), ...bodyKids);
  const m = el("div", { class: "modal" }, box);
  m.addEventListener("click", (e) => { if (e.target === m) m.remove(); });
  document.body.append(m);
  return m;
}

export function emptyState(text, ctaLabel, onCta) {
  const e = el("div", { class: "empty-state" }, el("div", { class: "big" }, "✦"), el("div", {}, text));
  if (ctaLabel) e.append(el("div", {}, el("button", { class: "btn btn-primary", style: "margin-top:12px", onclick: onCta }, ctaLabel)));
  return e;
}

export function auditBadges(c) {
  const a = c.audit || {}; const out = [];
  out.push(el("span", { class: "badge pass " + (a.pass ? "ok" : "bad") }, a.pass ? "PASS" : "FAIL"));
  if ("corners_transparent" in a) out.push(el("span", { class: "badge " + (a.corners_transparent ? "ok" : "bad") }, "四隅透過"));
  if ("canvas_match" in a) out.push(el("span", { class: "badge " + (a.canvas_match ? "ok" : "bad") }, "canvas一致"));
  if (a.bbox_center_delta_px != null) out.push(el("span", { class: "badge " + (a.bbox_center_delta_px <= 1 ? "ok" : "bad") }, "bbox " + (+a.bbox_center_delta_px).toFixed(1) + "px"));
  return out;
}

export function openAdopt(c) {
  const target = el("input", { placeholder: "採用名 (例 hero-damaged)", value: c.base ? c.base.replace(/\.png$/, "") + "-damaged" : "" });
  const pair = el("input", { list: "sprites", placeholder: "pair_with (ダメージ版のベース)", value: c.base ? c.base.replace(/\.png$/, "") : "" });
  const force = el("input", { type: "checkbox" });
  const result = el("div", { class: "adopt-result" });
  const go = el("button", { class: "btn btn-danger" }, "採用（rpgdevへ書き出し）");
  go.addEventListener("click", async () => {
    if (!target.value.trim()) { result.className = "adopt-result bad"; result.textContent = "採用名を入力"; return; }
    go.disabled = true; result.textContent = "採用中…";
    try {
      const r = await API.adopt({ candidate_id: c.id, target_name: target.value.trim(), pair_with: pair.value.trim() || null, force: force.checked });
      if (r.ok) { result.className = "adopt-result ok"; result.textContent = "採用: " + (r.target || target.value); toast("採用しました: " + (r.target || target.value)); }
      else { result.className = "adopt-result bad"; result.textContent = "不採用: " + (r.reason || "gate"); }
    } catch (e) { result.className = "adopt-result bad"; result.textContent = "失敗: " + e.message; }
    finally { go.disabled = false; }
  });
  modal([el("strong", {}, "採用（rpgdevへ・不可逆）"), el("span", { class: "spacer" })],
    [el("div", { class: "panel" },
      el("div", { class: "field" }, el("label", {}, "採用名"), target),
      el("div", { class: "field" }, el("label", {}, "pair_with（任意）"), pair),
      el("label", { class: "toggle" }, force, "強制（ゲート不通過でも書く）"),
      el("div", { class: "cta-bar" }, go), result)]);
}

// createCard(c, {gallery, onBible}) — derive prepends new cards into `gallery`.
export function createCard(c, { gallery, onBible } = {}) {
  const img = el("img", { src: imgURL(c.id), alt: c.id, title: c.id });
  const actions = el("div", { class: "card-actions" });
  const card = el("div", { class: "card" },
    el("div", { class: "thumb checker" }, img),
    el("div", { class: "badges" }, ...auditBadges(c)),
    c.audit && c.audit.reason ? el("p", { class: "reason" }, c.audit.reason) : false,
    actions);

  const derive = (label, fn) => {
    const b = el("button", { class: "btn btn-sm" }, label);
    b.addEventListener("click", async () => {
      b.disabled = true; const o = b.textContent; b.textContent = "…";
      try { const r = await fn(); if (gallery) gallery.prepend(createCard({ id: r.id, kind: "derived", audit: r.audit }, { gallery })); }
      catch (e) { toast("失敗: " + e.message, "error"); }
      finally { b.disabled = false; b.textContent = o; }
    });
    return b;
  };
  actions.append(derive("ピクセル化", () => API.pixelize(c.id)), derive("透過matte", () => API.transparent(c.id)));
  if (onBible && c.kind === "sprite") {            // scoped: only base sprite candidates
    const b = el("button", { class: "btn btn-sm btn-primary" }, "② バイブルへ");
    b.addEventListener("click", () => onBible(c));
    actions.append(b);
  }
  actions.append(el("button", { class: "btn btn-sm", onclick: () => openAdopt(c) }, "採用…"));
  return card;
}

export function renderGallery(container, list, opts = {}) {
  container.innerHTML = "";
  if (!list.length) { container.append(emptyState("まだ候補がありません")); return; }
  list.forEach((c) => container.append(createCard(c, { gallery: container, ...opts })));
}
