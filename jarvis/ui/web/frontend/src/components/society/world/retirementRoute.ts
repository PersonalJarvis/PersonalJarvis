/**
 * The route half of a retirement: everything the ceremony needs to know about
 * the island before the first frame — where the lead stops, where the body
 * ends up, how the stretcher gets to the mine, and where the bearers come on
 * from.
 *
 * Planned once, when the ceremony starts, so the choreography never re-plans
 * mid-execution. A plan that cannot be made (a body nothing can reach, a mine
 * with no route to it) is `null`, and the caller retires the agent without
 * the show rather than freezing a figure on the plaza.
 */
import { MINE_PORTAL_DZ, MINE_PORTAL_Y } from "./Landmarks";
import {
  buildIsland,
  findPath,
  groundY,
  isWalkable,
  nearestWalkable,
  smoothPath,
  tileToWorld,
  worldToTile,
} from "./islandLayout";
import {
  BEARER_ENTRY_M,
  EXECUTION_RANGE_M,
  MARCH_APPROACH_M,
  TOSS_STANDOFF_M,
  facingHeading,
  firingStand,
  pathLength,
  pointBackFrom,
} from "./retirement";

export interface RetirementPlan {
  /** Where the lead stops to fire, and the heading that puts it face to face. */
  stand: { x: number; z: number; heading: number };
  /** Where the lead walks on from — MARCH_APPROACH_M back along its own route. */
  leadStart: { x: number; z: number; heading: number };
  /** The lead's route from `leadStart` to the stand point, in world metres. */
  leadPath: Array<[number, number]>;
  /** Where the body is, and the heading that has it looking at its executioner. */
  body: { x: number; z: number; heading: number };
  /** From the body to the throwing spot in front of the mine, world metres. */
  carryPath: Array<[number, number]>;
  /** Where the bearers walk on from, and their route in to the body. */
  bearerPath: Array<[number, number]>;
  /** The mouth of the tunnel the body is thrown into, world metres. */
  portal: [number, number, number];
}

/** The tile a point falls on, nudged to the nearest walkable one when it is inside something. */
function walkableTile(x: number, z: number): [number, number] | null {
  const { map } = buildIsland();
  const [tx, tz] = worldToTile(x, z);
  if (isWalkable(map, tx, tz)) return [tx, tz];
  return nearestWalkable(map, tx, tz);
}

/** A smoothed world-metre route between two points, or null when there is none. */
function route(from: [number, number], to: [number, number]): Array<[number, number]> | null {
  const { map } = buildIsland();
  const a = walkableTile(from[0], from[1]);
  const b = walkableTile(to[0], to[1]);
  if (!a || !b) return null;
  const raw = findPath(map, a, b);
  if (!raw) return null;
  return smoothPath(map, raw).map(([tx, tz]) => tileToWorld(tx, tz));
}

/** Drop the tail of a route that is already within `radius` of `target`. */
function trimTail(
  path: Array<[number, number]>,
  target: readonly [number, number],
  radius: number,
): Array<[number, number]> {
  let end = path.length;
  while (end > 1 && Math.hypot(path[end - 1][0] - target[0], path[end - 1][1] - target[1]) < radius) {
    end--;
  }
  return path.slice(0, end);
}

/**
 * Plan the whole ceremony. `bodyAt` is where the condemned figure is standing
 * when the order is given; `leadAt` is where its executioner is.
 */
export function planRetirement(
  bodyAt: readonly [number, number],
  leadAt: readonly [number, number],
): RetirementPlan | null {
  const { map, content } = buildIsland();

  // The body stays exactly where it was ordered to stop — but never inside a
  // building it happened to be crossing.
  const bodyTile = walkableTile(bodyAt[0], bodyAt[1]);
  if (!bodyTile) return null;
  const bodyWorld = isWalkable(map, ...worldToTile(bodyAt[0], bodyAt[1]))
    ? ([bodyAt[0], bodyAt[1]] as [number, number])
    : tileToWorld(bodyTile[0], bodyTile[1]);

  const stand = firingStand(bodyWorld, leadAt);
  const body = {
    x: bodyWorld[0],
    z: bodyWorld[1],
    heading: facingHeading(bodyWorld, [stand.x, stand.z]),
  };

  const leadRoute = route([leadAt[0], leadAt[1]], bodyWorld);
  if (!leadRoute) return null;
  const fullLead: Array<[number, number]> = [
    ...trimTail(leadRoute.slice(1), bodyWorld, EXECUTION_RANGE_M),
    [stand.x, stand.z],
  ];
  // Open with the lead already on its way in, so the walk is a few seconds
  // rather than a march across the island.
  const onset = pointBackFrom(fullLead, MARCH_APPROACH_M);
  const leadStart =
    pathLength(fullLead) > MARCH_APPROACH_M
      ? { x: onset.x, z: onset.z, heading: onset.heading }
      : { x: leadAt[0], z: leadAt[1], heading: onset.heading };
  const leadPath: Array<[number, number]> =
    pathLength(fullLead) > MARCH_APPROACH_M ? fullLead.slice(onset.index) : fullLead;

  // The mine: the throwing spot sits on the forecourt, straight out from the
  // tunnel mouth. The rail head right in front of the portal blocks pathing,
  // so the route ends on the forecourt and the last step is walked free.
  const [mtx, mtz] = content.places.mine.tile;
  const [mx, mz] = tileToWorld(mtx, mtz);
  const portalZ = mz + MINE_PORTAL_DZ;
  const portal: [number, number, number] = [mx, groundY(map, mx, mz) + MINE_PORTAL_Y, portalZ];
  const tossSpot: [number, number] = [mx, portalZ + TOSS_STANDOFF_M];

  const mineRoute = route(bodyWorld, tossSpot);
  if (!mineRoute) return null;
  const carryPath: Array<[number, number]> = [...trimTail(mineRoute, tossSpot, 1.5), tossSpot];

  // The bearers come on from further down the same road they will carry the
  // body back along — they are the mine's crew, so that is where they are.
  const total = pathLength(carryPath);
  const entry = pointBackFrom(carryPath, Math.max(0, total - BEARER_ENTRY_M));
  const bearerPath: Array<[number, number]> = [
    ...carryPath.slice(0, entry.index).reverse(),
    [body.x, body.z],
  ];

  return { stand, leadStart, leadPath, body, carryPath, bearerPath, portal };
}
