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
 * The middle of the network and a radius that covers the bulk of it.
 *
 * The radius is a PERCENTILE, not a maximum, and that is the whole point. A
 * vault is allowed to contain a page nothing links to; it drifts to the edge
 * of the layout, and framing it along with everything else is what left the
 * readable part of the map occupying a fifth of the screen. Taking the 95th
 * percentile means the network fills the frame and the handful of outliers sit
 * just past the edge — where a slowly orbiting camera brings them back into
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

  let sx = 0;
  let sy = 0;
  let sz = 0;
  for (const point of points) {
    sx += coordinate(point.x);
    sy += coordinate(point.y);
    sz += coordinate(point.z);
  }
  const centre: Vec3 = {
    x: sx / points.length,
    y: sy / points.length,
    z: sz / points.length,
  };

  const distances = points
    .map((point) => {
      const dx = coordinate(point.x) - centre.x;
      const dy = coordinate(point.y) - centre.y;
      const dz = coordinate(point.z) - centre.z;
      return Math.sqrt(dx * dx + dy * dy + dz * dz);
    })
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
