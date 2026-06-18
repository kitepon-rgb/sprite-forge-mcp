// state.js — tiny observable store shared across step screens (fixes cross-step reuse).
const subs = new Set();
export const state = {
  candidates: new Map(),  // id -> {id, kind, audit, base?}
  bibles: [],             // [{name}]
  loras: [],              // installed lora filenames
  sprites: [],            // rpgdev sprite names
  charLora: null,         // {lora_name, trigger} after a char-LoRA finishes
};
export function emit() { subs.forEach((f) => f(state)); }
export function subscribe(f) { subs.add(f); return () => subs.delete(f); }

export function addCandidates(list = []) {
  list.forEach((c) => state.candidates.set(c.id, c));
  emit();
}
export function spriteCandidates() {
  return [...state.candidates.values()].filter((c) => c.kind === "sprite");
}
export function addBible(name) {
  if (name && !state.bibles.some((b) => b.name === name)) { state.bibles.push({ name }); emit(); }
}
export function setCharLora(lora_name, trigger) { state.charLora = { lora_name, trigger }; emit(); }
