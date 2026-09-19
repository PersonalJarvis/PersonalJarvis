import { useId } from "react";

import type { FigureArchetype, Palette } from "./figures/figureRecipe";
import type { IllustratedPortraitRecipe } from "./illustratedPortrait";

const GROUNDS = {
  clay: ["#eab293", "#ad654d"], coral: ["#ffac8c", "#dc755f"],
  sky: ["#91c9fa", "#3274b9"], sage: ["#b9dbc4", "#568a79"],
  violet: ["#c7baf0", "#77659e"],
} as const;
const FACES = {
  soft: "M64 27 C85 27 99 40 99 64 C99 87 84 104 64 104 C44 104 29 87 29 64 C29 40 43 27 64 27 Z",
  oval: "M64 25 C86 25 97 42 96 65 C95 89 81 106 64 106 C47 106 33 89 32 65 C31 42 42 25 64 25 Z",
  angular: "M64 27 L85 31 Q98 41 97 63 L92 87 L78 103 L64 107 L50 103 L36 87 L31 63 Q30 41 43 31 Z",
} as const;

function safeColor(value: string | undefined, fallback: string): string {
  return value && /^#[0-9a-fA-F]{6}$/.test(value) ? value : fallback;
}

function Hair({ style, fill }: { style: IllustratedPortraitRecipe["hair"]; fill: string }) {
  switch (style) {
    case "short": return <path d="M30 56 Q24 35 38 25 Q49 13 66 18 Q83 15 96 33 Q102 43 97 54 L91 44 Q80 43 76 34 Q62 45 44 42 Q38 49 30 56 Z" fill={fill} />;
    case "swept": return <path d="M28 58 Q23 34 41 23 Q63 9 83 21 Q103 30 99 51 Q88 43 80 32 Q67 51 36 49 Z" fill={fill} />;
    case "curly": return <g fill={fill}><circle cx="34" cy="38" r="12" /><circle cx="44" cy="27" r="13" /><circle cx="59" cy="24" r="14" /><circle cx="75" cy="24" r="14" /><circle cx="89" cy="32" r="13" /><circle cx="96" cy="45" r="10" /><circle cx="29" cy="50" r="8" /></g>;
    case "bun": return <g fill={fill}><circle cx="88" cy="21" r="18" /><path d="M29 59 Q22 36 37 23 Q50 12 69 18 Q91 18 99 43 L95 57 Q89 43 80 35 Q62 42 38 48 Z" /></g>;
    case "shaved": return <path d="M30 48 Q31 24 61 21 Q91 18 98 48 Q81 34 64 34 Q44 34 30 48 Z" fill={fill} />;
  }
}

function Human({ recipe, palette, faceFill }: { recipe: IllustratedPortraitRecipe; palette: Palette; faceFill: string }) {
  const hair = safeColor(palette.hair, "#382923");
  const eyes = safeColor(palette.eyes, "#252b34");
  const shade = safeColor(palette.skin_shade, "#c88e72");
  const garment = safeColor(palette.primary, "#35455b");
  const trim = safeColor(palette.secondary, "#f4ece5");
  return <>
    <path d="M10 128 Q13 104 36 99 L48 95 L80 95 L92 99 Q115 104 118 128 Z" fill={garment} />
    <path d="M45 97 L64 120 L83 97" fill="none" stroke={trim} strokeWidth="7" strokeLinecap="round" />
    <path d="M52 94 L53 107 Q64 116 75 107 L76 94" fill={shade} />
    <ellipse cx="31" cy="69" rx="7" ry="11" fill={shade} /><ellipse cx="97" cy="69" rx="7" ry="11" fill={shade} />
    <path d={FACES[recipe.face]} fill={faceFill} />
    <ellipse cx="43" cy="75" rx="7" ry="4" fill="#e89182" opacity=".24" /><ellipse cx="85" cy="75" rx="7" ry="4" fill="#e89182" opacity=".24" />
    <path d="M40 53 Q48 48 55 52 M73 52 Q80 48 88 53" fill="none" stroke={hair} strokeWidth="3.4" strokeLinecap="round" />
    <ellipse cx="49" cy="63" rx="7.5" ry="8" fill="#fffaf3" /><ellipse cx="79" cy="63" rx="7.5" ry="8" fill="#fffaf3" />
    <circle cx="50" cy="64" r="4.2" fill={eyes} /><circle cx="78" cy="64" r="4.2" fill={eyes} />
    <circle cx="51.5" cy="62.2" r="1.5" fill="white" /><circle cx="79.5" cy="62.2" r="1.5" fill="white" />
    <path d="M65 66 L61 77 Q64 79 68 77" fill="none" stroke={shade} strokeWidth="2.1" strokeLinecap="round" strokeLinejoin="round" />
    <path d="M53 86 Q64 95 75 86" fill="none" stroke="#8d5145" strokeWidth="2.5" strokeLinecap="round" />
    <Hair style={recipe.hair} fill={hair} />
    {recipe.detail === "glasses" ? <g fill="none" stroke="#eef4f4" strokeWidth="3"><rect x="37" y="54" width="23" height="21" rx="8" /><rect x="68" y="54" width="23" height="21" rx="8" /><path d="M60 60 Q64 57 68 60 M37 60 L30 57 M91 60 L98 57" /></g> : null}
    {recipe.detail === "freckles" ? <g fill="#a7634e" opacity=".75"><circle cx="39" cy="75" r="1.2" /><circle cx="45" cy="78" r="1.2" /><circle cx="51" cy="76" r="1.2" /><circle cx="77" cy="76" r="1.2" /><circle cx="83" cy="78" r="1.2" /><circle cx="89" cy="75" r="1.2" /></g> : null}
    {recipe.detail === "headset" ? <g fill="none" stroke={trim} strokeWidth="3"><path d="M28 67 Q23 28 63 22 Q104 27 100 66" /><rect x="23" y="63" width="10" height="20" rx="4" fill={garment} /><rect x="95" y="63" width="10" height="20" rx="4" fill={garment} /><path d="M100 83 Q97 95 78 95" /><circle cx="77" cy="95" r="3" fill={trim} /></g> : null}
  </>;
}

function Animal({ base, palette, faceFill }: { base: string; palette: Palette; faceFill: string }) {
  const pointy = /fox|wolf|cat|rabbit/i.test(base);
  const fur = safeColor(palette.fur, "#bb825c");
  const eyes = safeColor(palette.eyes, "#273039");
  return <>
    <path d="M12 128 Q17 95 49 94 L79 94 Q111 95 116 128 Z" fill={safeColor(palette.primary, "#344b53")} />
    {pointy ? <g fill={fur}><path d="M30 50 L30 15 L55 38 Z" /><path d="M98 50 L98 15 L73 38 Z" /></g>
      : <g fill={fur}><circle cx="37" cy="35" r="14" /><circle cx="91" cy="35" r="14" /></g>}
    <ellipse cx="64" cy="70" rx="40" ry="42" fill={faceFill} />
    <ellipse cx="47" cy="65" rx="7" ry="8" fill="#fffaf3" /><ellipse cx="81" cy="65" rx="7" ry="8" fill="#fffaf3" />
    <circle cx="48" cy="65" r="4" fill={eyes} /><circle cx="80" cy="65" r="4" fill={eyes} />
    <circle cx="49" cy="63" r="1.4" fill="white" /><circle cx="81" cy="63" r="1.4" fill="white" />
    <ellipse cx="64" cy="88" rx="22" ry="15" fill={safeColor(palette.fur_shade, "#e4b58c")} opacity=".65" />
    <path d="M59 80 Q64 76 69 80 L64 86 Z" fill={eyes} /><path d="M64 86 Q58 94 52 89 M64 86 Q70 94 76 89" fill="none" stroke={eyes} strokeWidth="2.5" strokeLinecap="round" />
  </>;
}

export function IllustratedAgentPortrait({ recipe, palette, archetype, base }: {
  recipe: IllustratedPortraitRecipe; palette: Palette; archetype: FigureArchetype; base: string;
}) {
  const id = useId().replace(/[^a-zA-Z0-9_-]/g, "");
  const [light, dark] = GROUNDS[recipe.backdrop];
  const skin = safeColor(archetype === "quadruped" ? palette.fur : palette.skin, "#e8b08a");
  const shade = safeColor(archetype === "quadruped" ? palette.fur_shade : palette.skin_shade, "#bd8068");
  let face = <Human recipe={recipe} palette={palette} faceFill={`url(#${id}-face)`} />;
  if (archetype === "quadruped") face = <Animal base={base} palette={palette} faceFill={`url(#${id}-face)`} />;
  if (archetype === "spirit") face = <g fill={safeColor(palette.primary, "#eff4ee")}><path d="M27 105 V57 Q27 22 64 22 Q101 22 101 57 V105 L88 96 L76 105 L64 96 L52 105 L40 96 Z" /><ellipse cx="49" cy="63" rx="7" ry="9" fill="#232b31" /><ellipse cx="79" cy="63" rx="7" ry="9" fill="#232b31" /><path d="M53 84 Q64 92 75 84" fill="none" stroke="#232b31" strokeWidth="3" strokeLinecap="round" /></g>;
  return <svg viewBox="0 0 128 128" className="absolute inset-0 h-full w-full" focusable="false" aria-hidden="true">
    <defs>
      <radialGradient id={`${id}-bg`} cx="38%" cy="26%" r="85%"><stop stopColor={light} /><stop offset="1" stopColor={dark} /></radialGradient>
      <linearGradient id={`${id}-face`} x1="0" x2="1" y1="0" y2=".7"><stop stopColor={skin} /><stop offset=".7" stopColor={skin} /><stop offset="1" stopColor={shade} /></linearGradient>
      <clipPath id={`${id}-clip`}><circle cx="64" cy="64" r="64" /></clipPath>
    </defs>
    <g clipPath={`url(#${id}-clip)`}>
      <circle cx="64" cy="64" r="64" fill={`url(#${id}-bg)`} />
      <circle cx="32" cy="26" r="28" fill="white" opacity=".08" />
      {face}
    </g>
  </svg>;
}
