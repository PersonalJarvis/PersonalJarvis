/**
 * Military headwear for the toy figure, built in its head group's space
 * (head centre `TOY.head.y` above the neck pivot, the face towards +z):
 *
 * - patrol cap: a soft flat-topped field cap with a short bill and the rank
 *   sewn on the front;
 * - garrison cap: the folded side cap in Army blue with gold piping, worn
 *   tilted to the right, the rank pinned at the left front (a unit crest for
 *   enlisted ranks);
 * - beret: black wool pulled down over the right ear, a shield-shaped flash
 *   over the left eye carrying the officer's rank or the unit crest;
 * - service cap: the peaked dress cap with a black band, a glossy visor, a
 *   gold chin strap and the gold eagle; field-grade officers get one row of
 *   gold oak leaves on the visor, general officers two.
 *
 * The figure hides its hair under a hat except for the band at the nape.
 */
import { CylinderGeometry, MeshStandardMaterial, SphereGeometry } from "three";
import { RoundedBoxGeometry } from "three-stdlib";
import { TOY } from "../../office/toyFigureModel";
import { RANK_INFO, type RankId, type RewardId } from "../levelCatalog";
import { CAP_BADGE_ART, CREST_ART, rankArt } from "../insignia/rankArt";
import { ArtPiece3D, RankInsignia3D, REGALIA_MATERIALS } from "./insignia3d";

const HD = TOY.head;

export type HeadwearId = Extract<RewardId, `headwear_${string}`>;

const GEO = {
  cyl: new CylinderGeometry(1, 1, 1, 48),
  crown: new CylinderGeometry(1.12, 1, 1, 48),
  sphere: new SphereGeometry(1, 40, 24),
  bill: new RoundedBoxGeometry(1, 1, 1, 3, 0.2),
  /** The front half of a disc: a visor. */
  visor: new CylinderGeometry(1, 1, 1, 40, 1, false, -Math.PI / 2, Math.PI),
  prism: new CylinderGeometry(1, 1, 1, 3),
};

const cloth = new Map<string, MeshStandardMaterial>();
function fabric(colour: string, roughness = 0.92): MeshStandardMaterial {
  const key = `${colour}|${roughness}`;
  let m = cloth.get(key);
  if (!m) { m = new MeshStandardMaterial({ color: colour, roughness }); cloth.set(key, m); }
  return m;
}
const PATENT = new MeshStandardMaterial({ color: "#0c0d10", roughness: 0.12, metalness: 0.2 });

/** The rank pinned on a cap: officers wear their pin, enlisted ranks the unit crest (or their sewn rank on the patrol cap). */
function CapPin({ rank, width }: { rank: RankId; width: number }) {
  const officer = RANK_INFO[rank].tier === "officer" || RANK_INFO[rank].tier === "general";
  if (officer) {
    const art = rankArt(rank);
    // Stars stacked for the shoulder are pinned side by side on a cap.
    const sideways = RANK_INFO[rank].tier === "general" && rank !== "general_of_the_army";
    return <group rotation={[0, 0, sideways ? Math.PI / 2 : 0]}><RankInsignia3D rank={rank} width={sideways ? (width * art.w) / art.h * 2.2 : width} /></group>;
  }
  return <ArtPiece3D id="crest" art={CREST_ART} width={width * 0.9} />;
}

function PatrolCap({ rank }: { rank: RankId }) {
  const shell = fabric("#5c6248");
  const sewn = rankArt(rank).mount === "sleeve";
  return (
    <group position={[0, HD.y + 0.14, 0]} rotation={[-0.1, 0, 0]}>
      <mesh geometry={GEO.crown} material={shell} position={[0, 0.07, 0]} scale={[HD.rx + 0.02, 0.17, HD.rz + 0.03]} castShadow />
      <mesh geometry={GEO.cyl} material={fabric("#4d523b")} position={[0, -0.005, 0]} scale={[HD.rx + 0.026, 0.035, HD.rz + 0.036]} />
      <mesh geometry={GEO.bill} material={shell} position={[0, -0.01, HD.rz + 0.08]} rotation={[0.22, 0, 0]} scale={[0.3, 0.022, 0.13]} castShadow />
      <group position={[0, 0.07, HD.rz + 0.034]}>
        {sewn ? <RankInsignia3D rank={rank} width={0.085} wrapRadius={0.3} /> : <CapPin rank={rank} width={0.06} />}
      </group>
    </group>
  );
}

function GarrisonCap({ rank }: { rank: RankId }) {
  const blue = fabric("#1d2742");
  return (
    <group position={[-0.02, HD.y + HD.ry - 0.06, 0]} rotation={[0, 0, 0.16]}>
      {/* The folded body: a wedge lying front to back, its ridge on top. */}
      <mesh geometry={GEO.prism} material={blue} rotation={[Math.PI / 2, 0, Math.PI]} position={[0, 0.045, 0]} scale={[0.16, 0.52, 0.095]} castShadow />
      <mesh geometry={GEO.cyl} material={blue} position={[0, 0, 0]} scale={[0.21, 0.04, HD.rz + 0.02]} />
      {/* Gold piping along the ridge and the curtain's edge. */}
      <mesh material={REGALIA_MATERIALS.braid} position={[0, 0.095, 0]}>
        <boxGeometry args={[0.01, 0.008, 0.5]} />
      </mesh>
      {[-1, 1].map((sx) => (
        <mesh key={sx} material={REGALIA_MATERIALS.braid} position={[sx * 0.135, 0.022, 0]} rotation={[0, 0, sx * 0.55]}>
          <boxGeometry args={[0.006, 0.006, 0.48]} />
        </mesh>
      ))}
      <group position={[0.11, 0.035, 0.17]} rotation={[0, 0.5, 0.5]}>
        <CapPin rank={rank} width={0.045} />
      </group>
    </group>
  );
}

function Beret({ rank }: { rank: RankId }) {
  const wool = fabric("#121316", 0.98);
  return (
    <group position={[0, HD.y + 0.1, 0]}>
      {/* Head band, then the crown pulled over the right side (-x). */}
      <mesh geometry={GEO.cyl} material={fabric("#1b1c20", 0.85)} position={[0, 0.02, 0]} scale={[HD.rx + 0.022, 0.04, HD.rz + 0.026]} />
      <mesh geometry={GEO.sphere} material={wool} position={[-0.05, 0.12, -0.01]} rotation={[0.05, 0, 0.32]}
        scale={[HD.rx + 0.07, 0.1, HD.rz + 0.06]} castShadow />
      {/* The flash over the left eye: a navy shield edged in gold. */}
      <group position={[0.1, 0.09, HD.rz + 0.005]} rotation={[-0.15, 0.32, 0]}>
        <mesh material={REGALIA_MATERIALS.braid} scale={[0.072, 0.082, 0.006]}>
          <sphereGeometry args={[0.5, 20, 12]} />
        </mesh>
        <mesh material={fabric("#24386b", 0.8)} position={[0, 0, 0.002]} scale={[0.064, 0.074, 0.006]}>
          <sphereGeometry args={[0.5, 20, 12]} />
        </mesh>
        <group position={[0, 0, 0.006]}><CapPin rank={rank} width={0.038} /></group>
      </group>
    </group>
  );
}

/** Rows of gold oak leaves on the visor: none below major, one for field grade, two for general officers. */
function visorRows(rank: RankId): number {
  const info = RANK_INFO[rank];
  if (info.tier === "general") return 2;
  if (rank === "major" || rank === "lieutenant_colonel" || rank === "colonel") return 1;
  return 0;
}

function ServiceCap({ rank }: { rank: RankId }) {
  const crown = fabric("#1d2742", 0.8);
  const rows = visorRows(rank);
  const vr = HD.rx + 0.03;
  return (
    <group position={[0, HD.y + 0.12, 0]} rotation={[-0.06, 0, 0]}>
      <mesh geometry={GEO.cyl} material={fabric("#0f1013", 0.6)} position={[0, 0.04, 0]} scale={[HD.rx + 0.024, 0.085, HD.rz + 0.03]} castShadow />
      <mesh geometry={GEO.crown} material={crown} position={[0, 0.12, -0.01]} scale={[HD.rx + 0.07, 0.075, HD.rz + 0.085]} castShadow />
      <mesh geometry={GEO.cyl} material={crown} position={[0, 0.16, -0.012]} scale={[HD.rx + 0.085, 0.022, HD.rz + 0.1]} castShadow />
      {/* The glossy visor, angled down over the brow. */}
      <mesh geometry={GEO.visor} material={PATENT} position={[0, 0.0, 0.03]} rotation={[0.38, 0, 0]} scale={[vr, 0.012, HD.rz + 0.1]} castShadow />
      {Array.from({ length: rows }, (_, row) => (
        <group key={row} position={[0, 0.004 - row * 0.018, 0.03]} rotation={[0.38, 0, 0]}>
          {Array.from({ length: 9 }, (_, i) => {
            const a = -0.95 + (i / 8) * 1.9;
            const r = HD.rz + 0.06 - row * 0.03;
            return (
              <mesh key={i} material={REGALIA_MATERIALS.gold} position={[Math.sin(a) * vr * (r / (HD.rz + 0.1)), 0.008, Math.cos(a) * r]}
                rotation={[0, a + 0.6, 0]} scale={[0.02, 0.004, 0.009]}>
                <sphereGeometry args={[1, 10, 6]} />
              </mesh>
            );
          })}
        </group>
      ))}
      {/* The gold chin strap across the band, buttoned at each side. */}
      <mesh material={REGALIA_MATERIALS.braid} position={[0, 0.015, HD.rz + 0.034]}>
        <boxGeometry args={[0.32, 0.016, 0.006]} />
      </mesh>
      {[-1, 1].map((sx) => (
        <mesh key={sx} material={REGALIA_MATERIALS.gold} position={[sx * 0.17, 0.015, HD.rz - 0.02]}>
          <sphereGeometry args={[0.012, 10, 8]} />
        </mesh>
      ))}
      <group position={[0, 0.115, HD.rz + 0.09]} rotation={[-0.12, 0, 0]}>
        <ArtPiece3D id="cap-badge" art={CAP_BADGE_ART} width={0.11} />
      </group>
    </group>
  );
}

/** A headwear piece for a figure of the given rank, in head-group space. */
export function Headwear({ kind, rank }: { kind: HeadwearId; rank: RankId }) {
  if (kind === "headwear_patrol_cap") return <PatrolCap rank={rank} />;
  if (kind === "headwear_garrison_cap") return <GarrisonCap rank={rank} />;
  if (kind === "headwear_beret") return <Beret rank={rank} />;
  return <ServiceCap rank={rank} />;
}
