/** Shipped brand artwork: resolved locally, including official raster favicons. */
const artwork = import.meta.glob("../assets/brands/*.{svg,png,ico}", {
  eager: true,
  query: "?url",
  import: "default",
}) as Record<string, string>;

/** Catalog ids use underscores (`google_cloud`); some lookups hyphenate them. */
export function brandIdVariants(id: string): string[] {
  const compact = id.replace(/-/g, "_");
  const hyphen = id.replace(/_/g, "-");
  return [...new Set([id, compact, hyphen].filter(Boolean))];
}

export function bundledPluginLogo(pluginId: string): string | undefined {
  for (const id of brandIdVariants(pluginId)) {
    for (const extension of ["svg", "png", "ico"]) {
      const url = artwork[`../assets/brands/${id}.${extension}`];
      if (url) return url;
    }
  }
  return undefined;
}

export function isRasterLogo(url: string | undefined): boolean {
  if (!url) return false;
  return (
    /\.(png|ico|jpe?g)(?:\?|$)/i.test(url) ||
    /^data:image\/(png|jpeg|gif|webp)/i.test(url)
  );
}
