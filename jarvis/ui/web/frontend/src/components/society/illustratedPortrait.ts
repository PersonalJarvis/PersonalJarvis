/** A compact, local portrait recipe. No image model or network call is involved. */
export const FACE_SHAPES = ["soft", "oval", "angular"] as const;
export const HAIR_STYLES = ["short", "swept", "curly", "bun", "shaved"] as const;
export const FACE_DETAILS = ["none", "glasses", "freckles", "headset"] as const;
export const BACKDROPS = ["clay", "coral", "sky", "sage", "violet"] as const;

export type FaceShape = (typeof FACE_SHAPES)[number];
export type HairStyle = (typeof HAIR_STYLES)[number];
export type FaceDetail = (typeof FACE_DETAILS)[number];
export type Backdrop = (typeof BACKDROPS)[number];

export interface IllustratedPortraitRecipe {
  seed: number;
  face: FaceShape;
  hair: HairStyle;
  detail: FaceDetail;
  backdrop: Backdrop;
}

export function makeIllustratedPortrait(seed: number): IllustratedPortraitRecipe {
  const stable = seed >>> 0;
  return {
    seed: stable,
    face: FACE_SHAPES[stable % FACE_SHAPES.length],
    hair: HAIR_STYLES[(stable >>> 3) % HAIR_STYLES.length],
    detail: FACE_DETAILS[(stable >>> 7) % FACE_DETAILS.length],
    backdrop: BACKDROPS[(stable >>> 11) % BACKDROPS.length],
  };
}

export function serializeIllustratedPortrait(recipe: IllustratedPortraitRecipe): string {
  return `illustrated:v1:${recipe.seed.toString(36)}:${recipe.face}:${recipe.hair}:${recipe.detail}:${recipe.backdrop}`;
}

export function parseIllustratedPortrait(value: string | undefined): IllustratedPortraitRecipe | null {
  if (!value) return null;
  const parts = value.split(":");
  if (parts.length !== 7 || parts[0] !== "illustrated" || parts[1] !== "v1" || !/^[0-9a-z]{1,7}$/.test(parts[2])) {
    return null;
  }
  const seed = Number.parseInt(parts[2], 36);
  if (!Number.isSafeInteger(seed) || seed > 0xffffffff) return null;
  if (!FACE_SHAPES.includes(parts[3] as FaceShape) || !HAIR_STYLES.includes(parts[4] as HairStyle)
    || !FACE_DETAILS.includes(parts[5] as FaceDetail) || !BACKDROPS.includes(parts[6] as Backdrop)) return null;
  return {
    seed,
    face: parts[3] as FaceShape,
    hair: parts[4] as HairStyle,
    detail: parts[5] as FaceDetail,
    backdrop: parts[6] as Backdrop,
  };
}

export function newIllustratedPortrait(): string {
  return serializeIllustratedPortrait(makeIllustratedPortrait(Math.floor(Math.random() * 0x1_0000_0000)));
}

/** Existing and API-created agents get one stable face without a network call. */
export function illustratedPortraitForAgent(agentId: string): string {
  let hash = 2166136261;
  for (let index = 0; index < agentId.length; index++) {
    hash = Math.imul(hash ^ agentId.charCodeAt(index), 16777619) >>> 0;
  }
  return serializeIllustratedPortrait(makeIllustratedPortrait(hash));
}
