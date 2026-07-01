// Ambient declarations for Vite-specific imports.
// Otherwise strict mode would complain that the default export is untyped.

declare module "*.svg?raw" {
  const content: string;
  export default content;
}

declare module "*.css" {
  const content: string;
  export default content;
}

declare module "*.png" {
  const url: string;
  export default url;
}

declare module "*.riv?url" {
  const url: string;
  export default url;
}
