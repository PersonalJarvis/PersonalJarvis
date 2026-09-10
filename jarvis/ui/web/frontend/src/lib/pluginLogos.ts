/** Shipped brand artwork: resolved locally, including official raster favicons. */
const artwork = import.meta.glob("../assets/brands/*.{svg,png,ico}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

export function bundledPluginLogo(pluginId: string): string | undefined {
  for (const extension of ["svg", "png", "ico"]) {
    const url = artwork[`../assets/brands/${pluginId}.${extension}`];
    if (url) return url;
  }
  return undefined;
}
