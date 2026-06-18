// api.js — DOM helpers + thin typed wrappers over the backend (unchanged endpoints).
export const $ = (s, el = document) => el.querySelector(s);
export const $$ = (s, el = document) => [...el.querySelectorAll(s)];

export async function api(path, opts = {}) {
  const r = await fetch(path, opts);
  const ct = r.headers.get("content-type") || "";
  const body = ct.includes("json") ? await r.json() : await r.text();
  if (!r.ok) throw new Error((body && body.error) || r.statusText || ("HTTP " + r.status));
  return body;
}
export const postJSON = (path, data) =>
  api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(data) });

export const imgURL = (id) => "/api/image/" + id;
export const baseURL = (name) => "/api/base/" + encodeURIComponent(name);

export const API = {
  gpu: () => api("/api/gpu"),
  sprites: () => api("/api/sprites"),
  loras: () => api("/api/loras"),
  loraCandidates: () => api("/api/lora_candidates"),
  craft: (idea, project) => postJSON("/api/craft_prompt", { idea, ...(project ? { project } : {}) }),
  generate: (b) => postJSON("/api/generate", b),
  variant: (b) => postJSON("/api/variant", b),
  pixelize: (image_id) => postJSON("/api/pixelize", { image_id }),
  transparent: (image_id) => postJSON("/api/transparent", { image_id }),
  fit: (candidate_id, base) => postJSON("/api/fit", { candidate_id, base }),
  adopt: (b) => postJSON("/api/adopt", b),
  uploadMask: (png) => postJSON("/api/upload_mask", { png }),
  sam2: (base, points) => postJSON("/api/sam2_mask", { base, points }),
  bible: (b) => postJSON("/api/bible", b),
  bibleStatus: (id) => api("/api/bible_status/" + id),
  trainStyle: (b) => postJSON("/api/train_lora", b),
  trainChar: (b) => postJSON("/api/train_character", b),
  trainStatus: (id) => api("/api/train_status/" + id),
};
