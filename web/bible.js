// bible.js — Step ② キャラバイブル. Pick a base (① candidate or rpgdev sprite) → generate → sheet.
import { API, imgURL } from "./api.js";
import { el, toast } from "./ui.js";
import { state, addBible, spriteCandidates } from "./state.js";
import { addJob, parseBible } from "./jobs.js";

export function renderBible(stage) {
  stage.innerHTML = "";
  stage.append(el("div", { class: "step-head" }, el("h1", {}, "② キャラバイブル"),
    el("p", {}, "素体（①の候補 or 既存スプライト）から、ターンアラウンド＋表情＋衣装などのモデルシートを生成。")));

  let selected = state.pendingBibleSource || null;  // candidate id
  const cands = spriteCandidates();
  const thumbWrap = el("div", { class: "gallery", style: "grid-template-columns:repeat(auto-fill,minmax(150px,1fr))" });
  function renderThumbs() {
    thumbWrap.innerHTML = "";
    if (!cands.length) { thumbWrap.append(el("p", { class: "hint" }, "①の素体候補がありません（既存スプライトを使うか、①で生成）")); return; }
    cands.forEach((c) => {
      const card = el("div", { class: "card", style: "cursor:pointer;border-color:" + (c.id === selected ? "var(--accent)" : "var(--line)") },
        el("div", { class: "thumb checker", style: "min-height:150px;max-height:200px" }, el("img", { src: imgURL(c.id), style: "max-height:200px" })));
      card.addEventListener("click", () => { selected = c.id; existing.value = ""; renderThumbs(); });
      thumbWrap.append(card);
    });
  }
  renderThumbs();

  const existing = el("input", { list: "sprites", placeholder: "既存スプライト名 (例 hero)" });
  existing.addEventListener("input", () => { if (existing.value.trim()) { selected = null; renderThumbs(); } });
  const name = el("input", { placeholder: "キャラ名（ファイル/タイトル・英数字推奨）", value: "" });
  const attr = el("input", { placeholder: "属性/肩書（任意 例 Fire Mage）" });
  const genBtn = el("button", { class: "btn btn-primary" }, "バイブル生成");
  const out = el("div", { style: "margin-top:16px" });

  genBtn.addEventListener("click", async () => {
    const nm = name.value.trim();
    if (!nm) { toast("キャラ名を入力", "error"); return; }
    if (!selected && !existing.value.trim()) { toast("素体（①候補）か既存スプライトを選んでください", "error"); return; }
    genBtn.disabled = true;
    try {
      const body = { name: nm, attr: attr.value.trim() };
      if (selected) body.candidate_id = selected; else body.source = existing.value.trim();
      const r = await API.bible(body);
      toast("バイブル生成開始: " + nm);
      addJob({
        id: r.job_id, label: "bible · " + nm, statusFn: API.bibleStatus, parse: parseBible,
        onDone: (s) => { addBible(s.name); showResult(s.name); },
      });
    } catch (e) { toast("開始失敗: " + e.message, "error", true); }
    finally { genBtn.disabled = false; }
  });

  function showResult(nm) {
    out.innerHTML = "";
    out.append(el("div", { class: "section-title" }, "完成: " + nm),
      el("div", { class: "sheet-viewer" },
        el("div", { class: "share" },
          el("a", { href: "/api/bible_html/" + encodeURIComponent(nm), target: "_blank", class: "btn btn-sm" }, "共有HTMLを開く"),
          el("button", { class: "btn btn-sm btn-primary", onclick: () => { state.pendingCharBible = nm; location.hash = "#/step3"; } }, "③ このバイブルでキャラLoRA →")),
        el("img", { src: "/api/bible_image/" + encodeURIComponent(nm) + "?t=" + Date.now() })));
  }

  stage.append(el("div", { class: "panel" },
    el("div", { class: "section-title" }, "素体を選ぶ（①の候補）"), thumbWrap,
    el("div", { class: "section-title" }, "または既存スプライト"),
    el("div", { class: "row" }, el("div", { class: "field" }, el("label", {}, "既存"), existing),
      el("div", { class: "field" }, el("label", {}, "キャラ名"), name), el("div", { class: "field" }, el("label", {}, "属性"), attr)),
    el("div", { class: "cta-bar" }, genBtn)), out);

  state.pendingBibleSource = null;  // consumed
}
