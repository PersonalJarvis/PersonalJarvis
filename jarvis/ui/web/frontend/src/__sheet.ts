import { rankArt, svgPoints, FINISH_TONES } from "./components/society/progression/insignia/rankArt";
import { TITLE_IDS } from "./components/society/progression/levelCatalog";
const cells = TITLE_IDS.map((rank) => {
  const art = rankArt(rank);
  const defs = (["gold","silver","cloth"] as const).map((f) => { const t = FINISH_TONES[f]; return `<linearGradient id="${rank}-${f}" x1="0" y1="0" x2="0.35" y2="1"><stop offset="0" stop-color="${t.light}"/><stop offset="0.45" stop-color="${t.mid}"/><stop offset="1" stop-color="${t.dark}"/></linearGradient>`; }).join("");
  const polys = art.parts.map((p) => { const t = FINISH_TONES[p.finish]; return p.detail ? `<polygon points="${svgPoints(p.pts)}" fill="${t.dark}" fill-opacity="0.55"/>` : `<polygon points="${svgPoints(p.pts)}" fill="url(#${rank}-${p.finish})" stroke="${t.edge}" stroke-width="1.1" stroke-linejoin="round"/>`; }).join("");
  return `<div style="display:inline-block;width:180px;text-align:center;margin:6px;font:12px sans-serif"><svg viewBox="-4 -4 108 ${art.h + 8}" width="160" height="150"><defs>${defs}</defs>${polys}</svg><br>${rank}</div>`;
}).join("");
console.log(`<html><body style="background:#e9e6df">${cells}</body></html>`);
