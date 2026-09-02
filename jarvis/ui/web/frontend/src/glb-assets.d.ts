/**
 * A `.glb` import resolves to its fingerprinted asset URL — Vite treats the
 * extension as a known asset type, TypeScript does not know it until told.
 * The society's figures ride this (docs/agent-society/character-pipeline.md §4.2).
 */
declare module "*.glb" {
  const src: string;
  export default src;
}
