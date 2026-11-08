// Colour themes. The CSS for each lives in styles.css under :root[data-theme="<id>"].
export const THEMES = [
  { id: "claude-dark", name: "Claude dark", note: "Warm charcoal, easy on the eyes", sw: ["#262624", "#1f1e1d", "#d97757", "#5b4034"] },
  { id: "claude-light", name: "Claude light", note: "Cream paper, terracotta accent", sw: ["#faf9f5", "#f1efe7", "#c96442", "#e9d7c8"] },
  { id: "soft-slate", name: "Soft slate", note: "Low-contrast blue-grey dark", sw: ["#2a2d33", "#24272c", "#7aa5d8", "#435a73"] },
  { id: "telegram", name: "Telegram dark", note: "The original high-contrast look", sw: ["#0e1621", "#17212b", "#5288c1", "#2b5278"] },
];

const KEY = "sahayi-sim-theme";

export function initialTheme() {
  const fromUrl = new URLSearchParams(window.location.search).get("theme");
  let saved = null;
  try { saved = localStorage.getItem(KEY); } catch { /* storage can be blocked */ }
  const id = [fromUrl, saved].find((t) => THEMES.some((x) => x.id === t)) || "claude-dark";
  return id;
}

export function applyTheme(id) {
  document.documentElement.dataset.theme = id;
  try { localStorage.setItem(KEY, id); } catch { /* ignore */ }
}
