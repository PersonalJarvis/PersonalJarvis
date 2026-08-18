/**
 * Where to put the camera so a 3D graph fills the screen — and how to walk it
 * slowly around the network without ever losing that framing.
 *
 * The renderer's own `zoomToFit` was not enough for either job. It frames
 * EVERY node, so a single page nobody links to shrinks the whole vault to a
 * marble in the middle of a black rectangle; and it hands back no way to keep
 * orbiting from where it left the camera. Both are solved by computing the
 * framing here and driving the camera directly.
 *
 * Everything in this file is pure arithmetic — no three.js, no React — so the
 * rules that decide "this is the middle of the network and this is how far
 * back you stand" are testable without a GPU.
 */

/** A point in graph space. The simulation writes these onto its nodes. */
export interface Vec3 {
  x: number;
  y: number;
  z: number;
}

/** Where the network sits and how big it is. */
export interface Framing {
  centre: Vec3;
  radius: number;
}

/** A camera position expressed as an angle pair and a distance. */
export interface Orbit {
  distance: number;
  /** Rotation in the horizontal plane, radians. */
  azimuth: number;
  /** Height above that plane, radians. ±π/2 is straight down/up. */
  elevation: number;
}

/** Never divide by zero, and never frame a single node from a millimetre away. */
const MIN_RADIUS = 12;

function coordinate(value: number | undefined): number {
  return Number.isFinite(value) ? (value as number) : 0;
}

/**
 * A node farther from the plain average than this many median distances is
 * a stray and has no say in where the pivot is. A ring, a ball, a spread-out
 * cluster all sit within it whole; only a page that drifted off alone falls
 * outside.
 */
const PIVOT_REACH = 1.5;

function meanOf(points: readonly Partial<Vec3>[]): Vec3 {
  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (const point of points) {
    sx += coordinate(point.x);
    sy += coordinate(point.y);
    sz += coordinate(point.z);
  }
  const n = Math.max(1, points.length);
  return { x: sx / n, y: sy / n, z: sz / n };
}

function distanceFrom(centre: Vec3, point: Partial<Vec3>): number {
  const dx = coordinate(point.x) - centre.x;
  const dy = coordinate(point.y) - centre.y;
  const dz = coordinate(point.z) - centre.z;
  return Math.sqrt(dx * dx + dy * dy + dz * dz);
}

/**
 * The middle of the network and a radius that covers the bulk of it.
 *
 * The middle is a TRIMMED mean: the plain average first, then the average of
 * the nodes within reach of it. On a small vault one page nothing links to
 * sits a long way from the rest, and the plain average lands halfway between
 * the two — the camera then circles a point in empty space and the readable
 * cluster swings around it like a bucket on a rope. Averaging again without
 * the stray puts the pivot inside the cluster, so the cluster turns in place
 * and the stray page orbits at the edge, which is what a person means by
 * "rotates around a fixed point".
 *
 * The radius is a PERCENTILE, not a maximum, and that is the other half of
 * the point. Framing the stray along with everything else is what left the
 * readable part of the map occupying a fifth of the screen. Taking the 95th
 * percentile means the network fills the frame and the handful of outliers
 * sit just past the edge — where the orbiting camera brings them back into
 * view anyway, and where a scroll wheel finds them immediately.
 *
 * @param points node positions as the simulation last left them
 * @param percentile share of nodes the radius must cover, 0..1
 * @returns null when there is nothing to frame
 */
export function framingFor(
  points: readonly Partial<Vec3>[],
  percentile = 0.95,
): Framing | null {
  if (points.length === 0) return null;

  const rough = meanOf(points);
  const roughDistances = points.map((point) => distanceFrom(rough, point)).sort((a, b) => a - b);
  const median = roughDistances[Math.floor(roughDistances.length / 2)];
  const reach = median * PIVOT_REACH;
  const near = points.filter((point) => distanceFrom(rough, point) <= reach);
  const centre = meanOf(near.length > 0 ? near : points);

  const distances = points
    .map((point) => distanceFrom(centre, point))
    .sort((a, b) => a - b);

  const share = Math.min(Math.max(percentile, 0), 1);
  const index = Math.min(
    distances.length - 1,
    Math.max(0, Math.ceil(share * distances.length) - 1),
  );
  return { centre, radius: Math.max(distances[index], MIN_RADIUS) };
}

/**
 * Framing around a point the caller CHOSE — the user's own page, say — rather
 * than around the trimmed mean.
 *
 * The maintainer's "main wiki point" (2026-08-18): the vault names one page for
 * the person Jarvis serves and the map should turn around it, so the pages that
 * cluster near it stay near the middle of the screen while everything else
 * sweeps past. Same percentile radius as `framingFor`, measured from the pivot,
 * so a hub that sits off-centre in the cloud still gets a frame that covers
 * the bulk of the network around it.
 *
 * @param pivot the point to turn around (missing coordinates read as 0)
 */
export function framingAround(
  pivot: Partial<Vec3>,
  points: readonly Partial<Vec3>[],
  percentile = 0.95,
): Framing | null {
  if (points.length === 0) return null;
  const centre = {
    x: coordinate(pivot.x),
    y: coordinate(pivot.y),
    z: coordinate(pivot.z),
  };
  const distances = points
    .map((point) => distanceFrom(centre, point))
    .sort((a, b) => a - b);
  const share = Math.min(Math.max(percentile, 0), 1);
  const index = Math.min(
    distances.length - 1,
    Math.max(0, Math.ceil(share * distances.length) - 1),
  );
  return { centre, radius: Math.max(distances[index], MIN_RADIUS) };
}

/**
 * How far back the camera has to stand for a sphere of `radius` to fill
 * `fill` of the frame.
 *
 * The limiting direction is whichever of the two frustum half-angles is
 * narrower — on a wide window that is the vertical one, on a tall one the
 * horizontal — so the network fills the screen without spilling out the short
 * side.
 *
 * @param radius     graph units
 * @param fovDeg     the camera's VERTICAL field of view, degrees
 * @param aspect     viewport width / height
 * @param fill       share of the frame the network should occupy, 0..1
 */
export function orbitDistance(
  radius: number,
  fovDeg: number,
  aspect: number,
  fill = 0.95,
): number {
  const safeAspect = Number.isFinite(aspect) && aspect > 0 ? aspect : 1;
  const halfVertical = (Math.max(fovDeg, 1) * Math.PI) / 360;
  const halfHorizontal = Math.atan(Math.tan(halfVertical) * safeAspect);
  const half = Math.min(halfVertical, halfHorizontal);
  // The sphere subtends `asin(radius / distance)`; asking it to subtend
  // `fill` of the half-angle and solving for distance gives this.
  const target = Math.max(Math.min(fill, 0.99), 0.05) * half;
  return Math.max(radius / Math.sin(target), radius + 1);
}

/** The camera position for an orbit around `centre`. */
export function orbitPoint(centre: Vec3, orbit: Orbit): Vec3 {
  const horizontal = Math.cos(orbit.elevation) * orbit.distance;
  return {
    x: centre.x + Math.cos(orbit.azimuth) * horizontal,
    y: centre.y + Math.sin(orbit.elevation) * orbit.distance,
    z: centre.z + Math.sin(orbit.azimuth) * horizontal,
  };
}

/**
 * The inverse: read an orbit back out of wherever the camera currently is.
 *
 * This is what lets the automatic rotation resume from the user's own viewing
 * angle after they have dragged the map around, instead of snapping back to
 * where it was before they touched it.
 */
export function orbitFrom(centre: Vec3, position: Vec3): Orbit {
  const dx = position.x - centre.x;
  const dy = position.y - centre.y;
  const dz = position.z - centre.z;
  const distance = Math.sqrt(dx * dx + dy * dy + dz * dz);
  if (distance === 0) return { distance: 0, azimuth: 0, elevation: 0 };
  return {
    distance,
    azimuth: Math.atan2(dz, dx),
    elevation: Math.asin(Math.min(Math.max(dy / distance, -1), 1)),
  };
}

/**
 * Sign of the azimuth change per step of the ambient drift.
 *
 * With Y up and the camera looking down at the network, a DECREASING azimuth
 * moves the camera anticlockwise around the pivot as seen from above, so the
 * network turns clockwise on the screen — the direction of a clock hand,
 * which is what the maintainer asked for. `graphCamera.test.ts` projects a
 * node through a look-at camera to pin exactly that, so a sign flip here or
 * in `orbitPoint` fails a test rather than quietly reversing the map.
 */
export const ORBIT_DIRECTION = -1;

/** The next azimuth of the ambient drift after `elapsedMs`. */
export function stepAzimuth(azimuth: number, elapsedMs: number, revolutionMs: number): number {
  return azimuth + ORBIT_DIRECTION * (elapsedMs / revolutionMs) * Math.PI * 2;
}

/**
 * Where a point lands on the screen of a camera at `eye` looking at `centre`
 * with Y up — the same construction as three.js's `lookAt`. Pure arithmetic
 * so the direction of the drift can be tested without a GPU: `x` grows to the
 * right, `y` grows upwards.
 */
export function projectToScreen(eye: Vec3, centre: Vec3, point: Vec3): { x: number; y: number } {
  const back = normalise({ x: eye.x - centre.x, y: eye.y - centre.y, z: eye.z - centre.z });
  const up = { x: 0, y: 1, z: 0 };
  const right = normalise(cross(up, back));
  const screenUp = cross(back, right);
  const rel = { x: point.x - eye.x, y: point.y - eye.y, z: point.z - eye.z };
  return { x: dot(rel, right), y: dot(rel, screenUp) };
}

function cross(a: Vec3, b: Vec3): Vec3 {
  return { x: a.y * b.z - a.z * b.y, y: a.z * b.x - a.x * b.z, z: a.x * b.y - a.y * b.x };
}

function dot(a: Vec3, b: Vec3): number {
  return a.x * b.x + a.y * b.y + a.z * b.z;
}

function normalise(v: Vec3): Vec3 {
  const length = Math.sqrt(dot(v, v)) || 1;
  return { x: v.x / length, y: v.y / length, z: v.z / length };
}

/**
 * Move `current` toward `target` the way a damped thing does: the same share
 * of the remaining gap per unit of time, whatever the frame rate. `tauMs` is
 * the time constant — after one tau, 63 % of the gap is closed; after three,
 * 95 %. This is what lets a re-frame change where the camera should stand
 * without the camera ever jumping there: the drift loop eases toward the new
 * value every frame, and the motion stays continuous.
 */
export function approach(current: number, target: number, elapsedMs: number, tauMs: number): number {
  if (!Number.isFinite(current)) return target;
  if (tauMs <= 0 || elapsedMs <= 0) return current;
  const share = 1 - Math.exp(-elapsedMs / tauMs);
  return current + (target - current) * share;
}

/** `approach`, for a point. */
export function approachPoint(current: Vec3, target: Vec3, elapsedMs: number, tauMs: number): Vec3 {
  return {
    x: approach(current.x, target.x, elapsedMs, tauMs),
    y: approach(current.y, target.y, elapsedMs, tauMs),
    z: approach(current.z, target.z, elapsedMs, tauMs),
  };
}
