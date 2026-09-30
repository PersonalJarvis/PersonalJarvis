/**
 * The office's chairs, built once as merged geometry so every copy costs a
 * handful of draw calls however much detail a chair carries.
 *
 * - The desk task chair is an ergonomic mesh chair in the Aeron / Gesture
 *   class: a sculpted seat with a waterfall front edge, a curved mesh back in
 *   a black frame with a lumbar pad, T-arms with soft pads, a gas lift in its
 *   sleeve and a polished five-star base on twin-wheel casters. It is exported
 *   as a part list (`TASK_CHAIR_PARTS`) that `DeskInstances` instances per desk.
 * - The meeting chair is a soft-pad conference chair after the Eames
 *   Aluminium Group: stitched leather pads slung between polished side rails
 *   on a four-star base.
 *
 * Both keep the seat top where the seated pose expects it (0.52 m at a desk,
 * 0.50 m at the table) and stay inside a chair's 0.29 m reach from the column.
 */
import {
  BoxGeometry, BufferGeometry, CatmullRomCurve3, CylinderGeometry, DoubleSide, Euler, ExtrudeGeometry, Float32BufferAttribute,
  Matrix4, MeshStandardMaterial, PlaneGeometry, Quaternion, RepeatWrapping, Shape, TubeGeometry, Vector3,
} from "three";
import { mergeVertices, RoundedBoxGeometry } from "three-stdlib";
import { cachedCanvasTexture } from "./canvasMaterials";
import { MAT, matte } from "./OfficeFurniture";
import { OFFICE } from "./officePalette";

/** Chair-only finishes: polished aluminium and the meeting chairs' cognac leather. */
const CHAIR_COLOURS = { aluminium: "#d3d6db", leather: "#9c5f38" } as const;

// ---------------------------------------------------------------------------
// Geometry helpers
// ---------------------------------------------------------------------------

type Vec3 = [number, number, number];

function at(position: Vec3, rotation: Vec3 = [0, 0, 0], scale: Vec3 = [1, 1, 1]): Matrix4 {
  // YXZ: a part's tilt (x) is applied before its turn about the column (y).
  const q = new Quaternion().setFromEuler(new Euler(rotation[0], rotation[1], rotation[2], "YXZ"));
  return new Matrix4().compose(new Vector3(...position), q, new Vector3(...scale));
}

/** Bakes transformed pieces into one non-indexed geometry (positions and normals only). */
function merged(pieces: Array<[BufferGeometry, Matrix4]>): BufferGeometry {
  const positions: number[] = [];
  const normals: number[] = [];
  for (const [source, matrix] of pieces) {
    const piece = source.clone().applyMatrix4(matrix);
    const flat = piece.index ? piece.toNonIndexed() : piece;
    const p = flat.getAttribute("position").array;
    const n = flat.getAttribute("normal").array;
    for (let i = 0; i < p.length; i += 1) { positions.push(p[i]); normals.push(n[i]); }
    piece.dispose();
    if (flat !== piece) flat.dispose();
  }
  const out = new BufferGeometry();
  out.setAttribute("position", new Float32BufferAttribute(positions, 3));
  out.setAttribute("normal", new Float32BufferAttribute(normals, 3));
  out.computeBoundingSphere();
  return out;
}

/** Welds a geometry's seams and recomputes normals, so a bevelled cushion shades soft instead of faceted. */
function smooth(geometry: BufferGeometry): BufferGeometry {
  geometry.deleteAttribute("uv");
  const welded = mergeVertices(geometry, 1e-4);
  welded.clearGroups();
  welded.computeVertexNormals();
  return welded;
}

/** A round tube along a smooth curve through `points`; callers squash it with a scale to make a blade. */
function tube(points: Vec3[], radius: number, closed = false, segments = 32): BufferGeometry {
  const curve = new CatmullRomCurve3(points.map((p) => new Vector3(...p)), closed, "centripetal");
  return new TubeGeometry(curve, segments, radius, 8, closed);
}

const rounded = (w: number, h: number, d: number, r: number) => new RoundedBoxGeometry(w, h, d, 2, r);

// ---------------------------------------------------------------------------
// Shared base pieces
// ---------------------------------------------------------------------------

/** One tapered, flattened spoke, thin end at +y; laid down by the caller. */
const SPOKE = new CylinderGeometry(0.015, 0.026, 1, 8);
const CASTER_WHEEL = new CylinderGeometry(0.03, 0.03, 0.017, 16);

/** A star base: hub, `count` spokes sloping down to `reach`, and a socket at each tip. */
function starBase(count: number, reach: number, hubY: number): Array<[BufferGeometry, Matrix4]> {
  const pieces: Array<[BufferGeometry, Matrix4]> = [[new CylinderGeometry(0.048, 0.064, 0.075, 20), at([0, hubY, 0])]];
  const length = reach - 0.03;
  const tilt = 0.11;
  for (let i = 0; i < count; i += 1) {
    const a = (i * 2 * Math.PI) / count;
    const mid = 0.03 + length / 2;
    pieces.push([SPOKE, at([Math.sin(a) * mid, hubY - 0.012, Math.cos(a) * mid], [Math.PI / 2 + tilt, a, 0], [1, length, 0.62])]);
    pieces.push([new CylinderGeometry(0.019, 0.021, 0.03, 12), at([Math.sin(a) * reach, hubY - 0.035, Math.cos(a) * reach])]);
  }
  return pieces;
}

/** Twin-wheel casters under the tips of a star base, wheels across the spoke. */
function casters(count: number, reach: number): Array<[BufferGeometry, Matrix4]> {
  const pieces: Array<[BufferGeometry, Matrix4]> = [];
  for (let i = 0; i < count; i += 1) {
    const a = (i * 2 * Math.PI) / count;
    const turn = new Matrix4().makeRotationY(a);
    const place = (local: Vec3, rotation: Vec3, geometry: BufferGeometry) =>
      pieces.push([geometry, turn.clone().multiply(at(local, rotation))]);
    // Trailing slightly outboard of the socket, like a real swivel caster.
    for (const x of [-0.013, 0.013]) place([x, 0.03, reach + 0.012], [0, 0, Math.PI / 2], CASTER_WHEEL);
    place([0, 0.052, reach + 0.006], [0, 0, 0], rounded(0.038, 0.03, 0.05, 0.012));
  }
  return pieces;
}

// ---------------------------------------------------------------------------
// Desk task chair (local: seat centre on the floor, sitter faces -z, back towards +z)
// ---------------------------------------------------------------------------

/** Sculpted seat pan: side profile with a dished middle and a waterfall front edge, extruded across. */
function seatCushion(): BufferGeometry {
  // Profile in (z, y): z runs front (-) to back (+).
  const s = new Shape();
  s.moveTo(0.19, 0.466);
  s.lineTo(-0.16, 0.466);
  s.quadraticCurveTo(-0.228, 0.47, -0.236, 0.498);
  s.quadraticCurveTo(-0.232, 0.522, -0.18, 0.522);
  s.quadraticCurveTo(-0.02, 0.5, 0.12, 0.508);
  s.quadraticCurveTo(0.196, 0.514, 0.198, 0.494);
  s.lineTo(0.19, 0.466);
  const depth = 0.43;
  const g = new ExtrudeGeometry(s, { depth, bevelEnabled: true, bevelThickness: 0.028, bevelSize: 0.012, bevelSegments: 3, curveSegments: 8 });
  // Extruded along z; turn it so the profile's first axis becomes local z and the width runs along x.
  g.applyMatrix4(new Matrix4().makeRotationY(-Math.PI / 2));
  g.translate(depth / 2, 0, 0);
  // Round the pan's corners in plan: the sides pull in a little towards the front and back edges.
  const pos = g.getAttribute("position");
  for (let i = 0; i < pos.count; i += 1) {
    const z = pos.getZ(i);
    pos.setX(i, pos.getX(i) * (1 - 0.07 * (z / 0.24) ** 2));
  }
  return smooth(g);
}

/** The backrest surface: a cone segment wrapping the sitter, wider and reclined at the shoulders, rounded corners. */
const BACK = { yb: 0.61, h: 0.5, rb: 0.5, rt: 0.6, arc: 0.84, zb: 0.212 } as const;
function backPoint(s: number, t: number, inset = 0): Vector3 {
  const { yb, h, rb, rt, arc, zb } = BACK;
  const axisZ = zb - rb;
  const theta = (s * arc) / 2;
  const r = rb + (rt - rb) * t - inset;
  const y = yb + t * h - 0.06 * t * s ** 4 + 0.035 * (1 - t) * s ** 4;
  return new Vector3(r * Math.sin(theta), y, axisZ + r * Math.cos(theta));
}

function backPanel(): BufferGeometry {
  const g = new PlaneGeometry(1, 1, 12, 8);
  const pos = g.getAttribute("position");
  for (let i = 0; i < pos.count; i += 1) {
    const p = backPoint(pos.getX(i) * 2, pos.getY(i) + 0.5);
    pos.setXYZ(i, p.x, p.y, p.z);
  }
  g.computeVertexNormals();
  g.computeBoundingSphere();
  return g;
}

function backFrame(): BufferGeometry {
  const points: Vec3[] = [];
  const edge = (from: [number, number], to: [number, number], n: number) => {
    for (let i = 0; i < n; i += 1) {
      const k = i / n;
      const p = backPoint(from[0] + (to[0] - from[0]) * k, from[1] + (to[1] - from[1]) * k);
      points.push([p.x, p.y, p.z]);
    }
  };
  edge([-1, 0], [1, 0], 6);
  edge([1, 0], [1, 1], 6);
  edge([1, 1], [-1, 1], 6);
  edge([-1, 1], [-1, 0], 6);
  return tube(points, 0.015, true, 96);
}

/** A rounded box bent round the backrest's curve at height `t`, standing `inset` in front of the mesh. */
function bentPad(w: number, h: number, d: number, t: number, inset: number): BufferGeometry {
  const g = rounded(w, h, d, Math.min(h, d) / 2.2);
  const pos = g.getAttribute("position");
  const centre = backPoint(0, t, inset);
  const r = BACK.rb + (BACK.rt - BACK.rb) * t - inset;
  const axisZ = BACK.zb - BACK.rb;
  for (let i = 0; i < pos.count; i += 1) {
    const theta = pos.getX(i) / r;
    const rr = r - pos.getZ(i);
    pos.setXYZ(i, rr * Math.sin(theta), centre.y + pos.getY(i), axisZ + rr * Math.cos(theta));
  }
  g.computeVertexNormals();
  return g;
}

/** Fine woven mesh for the backrest, drawn once. */
function meshWeave() {
  const texture = cachedCanvasTexture("chair:mesh-weave", 64, 64, (ctx, w, h) => {
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = "rgba(0,0,0,0.34)";
    for (let y = 0; y < h; y += 4) ctx.fillRect(0, y, w, 1);
    ctx.fillStyle = "rgba(0,0,0,0.18)";
    for (let x = 0; x < w; x += 4) ctx.fillRect(x, 0, 1, h);
  });
  if (texture) {
    texture.wrapS = texture.wrapT = RepeatWrapping;
    texture.repeat.set(9, 10);
  }
  return texture;
}

export const CHAIR_MAT = {
  aluminium: matte(CHAIR_COLOURS.aluminium, { roughness: 0.26, metalness: 0.65 }),
  /** The backrest mesh: see-through weave, both faces lit (the backrest is a single curved sheet). */
  mesh: new MeshStandardMaterial({ color: OFFICE.chairMesh, map: meshWeave(), roughness: 0.9, side: DoubleSide }),
  leather: matte(CHAIR_COLOURS.leather, { roughness: 0.5 }),
};

/** Which finish a task-chair part takes; `DeskInstances` maps frame and fabric onto a desk tone's chair/seat roles. */
export type ChairFinish = "frame" | "fabric" | "metal" | "mesh";

export interface ChairPart {
  finish: ChairFinish;
  geometry: BufferGeometry;
  cast: boolean;
}

const TASK_FRAME = merged([
  // Gas-lift sleeve and the tilt mechanism with its paddle.
  [new CylinderGeometry(0.029, 0.036, 0.23, 16), at([0, 0.225, 0])],
  [rounded(0.22, 0.05, 0.24, 0.015), at([0, 0.438, 0.02])],
  [rounded(0.09, 0.012, 0.024, 0.005), at([0.14, 0.43, -0.04], [0, -0.3, 0])],
  // T-arms: a bar under the seat, a post, and a soft pad on top.
  ...[-1, 1].flatMap((side): Array<[BufferGeometry, Matrix4]> => [
    [rounded(0.2, 0.024, 0.05, 0.008), at([side * 0.175, 0.428, 0.06])],
    [rounded(0.034, 0.25, 0.05, 0.012), at([side * 0.272, 0.54, 0.06])],
    [rounded(0.078, 0.032, 0.26, 0.014), at([side * 0.272, 0.675, 0.03])],
  ]),
  // The backrest's frame, and the spine that carries it from the mechanism.
  [backFrame(), new Matrix4()],
  [tube([[0, 0.44, 0.1], [0, 0.46, 0.2], [0, 0.55, 0.245], [0, 0.7, 0.245]], 0.022), at([0, 0, 0], [0, 0, 0], [1.9, 1, 1])],
  ...casters(5, 0.262),
]);

const TASK_METAL = merged([
  ...starBase(5, 0.262, 0.105),
  // Chrome piston between the sleeve and the mechanism.
  [new CylinderGeometry(0.014, 0.014, 0.09, 12), at([0, 0.38, 0])],
]);

/** The ergonomic desk chair, one entry per draw call. */
export const TASK_CHAIR_PARTS: ChairPart[] = [
  { finish: "metal", geometry: TASK_METAL, cast: true },
  { finish: "frame", geometry: TASK_FRAME, cast: true },
  { finish: "fabric", geometry: seatCushion(), cast: true },
  // Lumbar pad in the seat fabric across the small of the back.
  { finish: "fabric", geometry: bentPad(0.34, 0.07, 0.022, 0.2, 0.014), cast: false },
  { finish: "mesh", geometry: backPanel(), cast: true },
];

/** The task chair outside the instanced desks (a single desk, a showroom): same parts, plain meshes. */
export function TaskChair({ fabric = MAT.chairSeat }: { fabric?: MeshStandardMaterial }) {
  const finishes: Record<ChairFinish, MeshStandardMaterial> = { frame: MAT.chair, fabric, metal: CHAIR_MAT.aluminium, mesh: CHAIR_MAT.mesh };
  return (
    <group>
      {TASK_CHAIR_PARTS.map((part, i) => (
        <mesh key={i} geometry={part.geometry} material={finishes[part.finish]} castShadow={part.cast} receiveShadow />
      ))}
    </group>
  );
}

// ---------------------------------------------------------------------------
// Meeting chair (local: seat centre on the floor, facing +z, backrest on the -z side)
// ---------------------------------------------------------------------------

const RAIL_X = 0.238;
/** The side rail's path: along the seat edge, then sweeping up behind the back pads. */
const RAIL: Vec3[] = [[0, 0.478, 0.235], [0, 0.46, 0.13], [0, 0.455, -0.1], [0, 0.49, -0.215], [0, 0.64, -0.255], [0, 0.82, -0.285], [0, 0.975, -0.31]];
/** Back pads lean with the rail: bottom at (y 0.56, z -0.235), top at (y 0.95, z -0.3). */
const BACK_TILT = Math.atan2(0.065, 0.39);

function meetingPads(): BufferGeometry {
  const pad = rounded(0.44, 0.056, 0.138, 0.026);
  const pieces: Array<[BufferGeometry, Matrix4]> = [
    // Three seat pads; the front one rolls down a touch, a waterfall edge for the knees.
    [pad, at([0, 0.468, 0.145], [0.16, 0, 0])],
    [pad, at([0, 0.472, 0.0])],
    [pad, at([0, 0.47, -0.143], [-0.05, 0, 0])],
  ];
  const backPad = rounded(0.44, 0.1, 0.05, 0.022);
  for (let i = 0; i < 4; i += 1) {
    const t = (i + 0.5) / 4;
    pieces.push([backPad, at([0, 0.56 + t * 0.39, -0.235 - t * 0.065 + 0.02], [BACK_TILT, 0, 0])]);
  }
  return smooth(merged(pieces));
}

const MEETING_METAL = merged([
  ...starBase(4, 0.29, 0.1),
  [new CylinderGeometry(0.024, 0.024, 0.32, 16), at([0, 0.27, 0])],
  // Side rails, flattened into blades, and the spreaders that tie them together.
  ...[-1, 1].map((side): [BufferGeometry, Matrix4] => [tube(RAIL, 0.02, false, 40), at([side * RAIL_X, 0, 0], [0, 0, 0], [0.55, 1, 1])]),
  [new BoxGeometry(RAIL_X * 2, 0.02, 0.05), at([0, 0.44, -0.02])],
  [new BoxGeometry(RAIL_X * 2, 0.03, 0.02), at([0, 0.8, -0.3], [BACK_TILT, 0, 0])],
  // Arm posts rising off the rails.
  ...[-1, 1].map((side): [BufferGeometry, Matrix4] => [
    tube([[0, 0.46, -0.1], [0, 0.56, -0.08], [0, 0.645, -0.03]], 0.014, false, 16), at([side * (RAIL_X + 0.018), 0, 0], [0, 0, 0], [0.7, 1, 1]),
  ]),
]);

const MEETING_DARK = merged([
  // Backing sheet behind the pads (it shows as the seams between them) and the tilt housing.
  [new BoxGeometry(0.45, 0.012, 0.44), at([0, 0.445, 0])],
  [new BoxGeometry(0.45, 0.4, 0.012), at([0, 0.755, -0.281], [BACK_TILT, 0, 0])],
  [rounded(0.16, 0.05, 0.18, 0.015), at([0, 0.42, -0.01])],
  // Arm pads and the glides under the base.
  ...[-1, 1].map((side): [BufferGeometry, Matrix4] => [rounded(0.07, 0.03, 0.27, 0.013), at([side * (RAIL_X + 0.018), 0.655, 0.02])]),
  ...Array.from({ length: 4 }, (_, i): [BufferGeometry, Matrix4] => {
    const a = (i * Math.PI) / 2;
    return [new CylinderGeometry(0.024, 0.03, 0.05, 14), at([Math.sin(a) * 0.29, 0.025, Math.cos(a) * 0.29])];
  }),
]);

const MEETING_PADS = meetingPads();

/** A soft-pad conference chair centred on its seat, facing +z (backrest on the -z side). */
export function MeetingChair() {
  return (
    <group>
      <mesh geometry={MEETING_METAL} material={CHAIR_MAT.aluminium} castShadow receiveShadow />
      <mesh geometry={MEETING_DARK} material={MAT.chair} castShadow receiveShadow />
      <mesh geometry={MEETING_PADS} material={CHAIR_MAT.leather} castShadow receiveShadow />
    </group>
  );
}
