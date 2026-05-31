// Ambient declarations fuer Vite-spezifische Imports.
// Strict-Mode wuerde sonst meckern dass der Default-Export untyped ist.

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
