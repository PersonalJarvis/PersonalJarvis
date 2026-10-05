/**
 * A reward's picture for the HUD, the promotion card and the hall screen: the
 * piece itself, drawn from the same colours and ribbons the 3D figure wears —
 * the uniform's coat with its collar, tie and buttons, the cap, the ribbons
 * or the medals — on a dark display tile. Decorative: the name always sits
 * next to it.
 */
import { createContext, useContext, useId } from "react";
import type { RewardId } from "./levelCatalog";
import { uniformStyle, type UniformId } from "./regalia/dress";
import { MEDAL_METALS, RIBBONS, ribbonSpans, type RibbonSpec } from "./regalia/ribbons";

const GOLD = "#d8ab47";
const GOLD_DARK = "#8f6518";

function Uniform({ id }: { id: UniformId }) {
  const u = uniformStyle(id, "captain");
  const open = u.cut === "service" || u.cut === "mess";
  return (
    <g strokeLinejoin="round">
      {/* Sleeves, then the body. */}
      <path d="M13 15 L7 35 L12 36.5 L16 24 Z M35 15 L41 35 L36 36.5 L32 24 Z" fill={u.coat} stroke="#00000055" strokeWidth={0.8} />
      <path d={u.cut === "mess" ? "M14 13 L34 13 L36 33 L24 37 L12 33 Z" : "M14 13 L34 13 L35.5 41 L12.5 41 Z"} fill={u.coat} stroke="#00000055" strokeWidth={0.8} />
      {u.cuffBraid && <path d="M7.6 33 L12.6 34.6 M40.4 33 L35.4 34.6" stroke={u.cuffBraid} strokeWidth={1.6} />}
      {/* The opening: a shirt V with the tie, or a field jacket's stand collar. */}
      {u.cut === "field" ? (
        <>
          <path d="M18 11 h12 v3.5 h-12 z" fill={u.coat} stroke="#00000055" strokeWidth={0.8} />
          <path d="M24 14 V41" stroke="#00000066" strokeWidth={1} />
        </>
      ) : (
        <>
          <path d={open ? "M18.5 13 L24 27 L29.5 13 Z" : "M20.5 13 L24 19 L27.5 13 Z"} fill={u.shirt} />
          {u.tieKind === "bow"
            ? <path d="M21 15 L24 16.5 L27 15 L27 18 L24 16.5 L21 18 Z" fill={u.tie} />
            : <path d={open ? "M23 14 L25 14 L25.6 24.5 L24 26.5 L22.4 24.5 Z" : "M23 14 L25 14 L25.6 30 L24 32 L22.4 30 Z"} fill={u.tie} />}
          {open && <path d="M18.5 13 L24 27 M29.5 13 L24 27" stroke={u.lapels ?? "#00000055"} strokeWidth={u.lapels ? 2.4 : 1} />}
        </>
      )}
      {u.pockets > 0 && [16.5, 27.5].map((x) => <rect key={x} x={x} y={20} width={4} height={1.8} rx={0.4} fill="#00000040" />)}
      {u.pockets === 4 && [16.2, 27.8].map((x) => <rect key={`l${x}`} x={x} y={31} width={4} height={1.8} rx={0.4} fill="#00000040" />)}
      {u.belt && <rect x={12.8} y={34} width={22.4} height={2.2} fill={u.belt} stroke="#00000044" strokeWidth={0.4} />}
      {(open ? [29, 32.5] : [22, 26.5, 31]).map((y) => <circle key={y} cx={24} cy={y} r={0.9} fill={u.buttons} />)}
    </g>
  );
}

function Headwear({ id }: { id: RewardId }) {
  if (id === "headwear_patrol_cap") {
    return (
      <g>
        <path d="M13 16 L35 16 L36 30 L12 30 Z" fill="#5c6248" stroke="#00000055" strokeWidth={0.8} />
        <path d="M11 29 L37 29 L39 33 Q24 36 9 33 Z" fill="#4d523b" stroke="#00000055" strokeWidth={0.8} />
        <path d="M24 19.5 L28 22 L28 24 L24 21.5 L20 24 L20 22 Z" fill={GOLD} />
      </g>
    );
  }
  if (id === "headwear_garrison_cap") {
    return (
      <g>
        <path d="M7 31 Q9 21 24 17 Q39 21 41 31 Z" fill="#1d2742" stroke="#00000066" strokeWidth={0.8} />
        <path d="M8 30.5 Q24 26 40 30.5" stroke={GOLD} strokeWidth={1} fill="none" />
        <path d="M24 17.5 Q33 20 40.5 30" stroke={GOLD} strokeWidth={0.8} fill="none" />
        <circle cx={14} cy={27} r={1.8} fill={GOLD} />
      </g>
    );
  }
  if (id === "headwear_beret") {
    return (
      <g>
        <path d="M9 28 Q8 17 22 15 Q38 14 41 24 Q41 29 33 29 L11 30 Z" fill="#141518" stroke="#ffffff22" strokeWidth={0.8} />
        <rect x={10} y={28.5} width={26} height={3} rx={1.2} fill="#24252a" />
        <path d="M14 18.5 h6 v4 q-3 3 -6 0 z" fill="#24386b" stroke={GOLD} strokeWidth={0.8} />
        <circle cx={17} cy={21} r={1} fill={GOLD} />
      </g>
    );
  }
  return (
    <g>
      <ellipse cx={24} cy={17} rx={17} ry={4.5} fill="#1d2742" stroke="#00000066" strokeWidth={0.8} />
      <path d="M11 18 L13 27 L35 27 L37 18 Z" fill="#1d2742" />
      <rect x={13} y={24} width={22} height={5} fill="#0f1013" />
      <path d="M13.5 28 h21" stroke={GOLD} strokeWidth={1.2} />
      <path d="M12 29 Q24 37 36 29 Z" fill="#0b0c0f" stroke="#ffffff22" strokeWidth={0.6} />
      <path d="M24 18.5 l2.4 1.6 -0.9 2.6 h-3 l-0.9 -2.6 z" fill={GOLD} />
    </g>
  );
}

function Ribbon({ spec, x, y, w, h }: { spec: RibbonSpec; x: number; y: number; w: number; h: number }) {
  return (
    <g>
      {ribbonSpans(spec, w).map(([sx, sw, colour], i) => <rect key={i} x={x + sx} y={y} width={sw + 0.05} height={h} fill={colour} />)}
      <rect x={x} y={y} width={w} height={h} fill="none" stroke="#00000066" strokeWidth={0.4} />
    </g>
  );
}

function Decoration({ id }: { id: RewardId }) {
  const w = 10.5, h = 3.6;
  if (id === "decoration_ribbon_bar") {
    return <g>{[RIBBONS[4], RIBBONS[5], RIBBONS[6]].map((spec, i) => <Ribbon key={spec.id} spec={spec} x={8.25 + i * (w + 0.5)} y={22} w={w} h={h} />)}</g>;
  }
  if (id === "decoration_medals") {
    return (
      <g>
        {RIBBONS.slice(0, 3).map((spec, i) => {
          const x = 12 + i * 9;
          const metal = MEDAL_METALS[spec.medal.metal];
          return (
            <g key={spec.id}>
              <Ribbon spec={spec} x={x} y={10} w={7.5} h={11} />
              <circle cx={x + 3.75} cy={27} r={4.6} fill={metal.face} stroke={metal.edge} strokeWidth={0.9} />
              <circle cx={x + 3.75} cy={27} r={2.4} fill="none" stroke={metal.edge} strokeWidth={0.5} />
            </g>
          );
        })}
      </g>
    );
  }
  const rack = RIBBONS.slice(0, 9);
  const cords = id === "decoration_aiguillette";
  return (
    <g>
      {rack.map((spec, i) => {
        const row = Math.floor(i / 3), col = i % 3;
        return <Ribbon key={spec.id} spec={spec} x={8.25 + col * (w + 0.5)} y={(cords ? 8 : 16) + row * (h + 0.6)} w={w} h={h} />;
      })}
      {cords && (
        <g fill="none" stroke={GOLD} strokeWidth={1.3} strokeLinecap="round">
          <path d="M10 25 Q24 42 38 25" />
          <path d="M10 25 Q24 37 38 25" />
          <path d="M18 30 V40 M29 30 V41" />
          <path d="M18 40 l-1 2.6 h2 z M29 41 l-1 2.6 h2 z" fill={GOLD_DARK} stroke={GOLD_DARK} strokeWidth={0.6} />
        </g>
      )}
    </g>
  );
}

/** The tile a picture sits on: dark display velvet (HUD, promotion card) or light card stock (the white hall screen). */
export const RewardTileTone = createContext<"dark" | "light">("dark");

const TILES = {
  dark: { top: "#2a3456", bottom: "#121831", edge: "#c9a24a55" },
  light: { top: "#ffffff", bottom: "#ece7dc", edge: "#cfc7b5" },
} as const;

export function RewardIcon({ reward, locked = false, size = 30 }: { reward: RewardId; locked?: boolean; size?: number }) {
  const id = useId().replace(/:/g, "");
  const tile = TILES[useContext(RewardTileTone)];
  return (
    <span className="level-reward-icon" data-locked={locked || undefined} aria-hidden style={{ width: size, height: size }}>
      <svg viewBox="0 0 48 48" width={size} height={size}>
        <defs>
          <radialGradient id={`${id}-tile`} cx="0.5" cy="0.3" r="0.8">
            <stop offset="0" stopColor={tile.top} />
            <stop offset="1" stopColor={tile.bottom} />
          </radialGradient>
        </defs>
        <rect x={0.5} y={0.5} width={47} height={47} rx={9} fill={`url(#${id}-tile)`} stroke={tile.edge} strokeWidth={1} />
        <g opacity={locked ? 0.6 : 1} style={locked ? { filter: "grayscale(.75)" } : undefined}>
          {reward.startsWith("uniform_") && <Uniform id={reward as UniformId} />}
          {reward.startsWith("headwear_") && <Headwear id={reward} />}
          {reward.startsWith("decoration_") && <Decoration id={reward} />}
        </g>
      </svg>
    </span>
  );
}
