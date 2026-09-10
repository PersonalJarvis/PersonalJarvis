import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { expect, it } from "vitest";
import { bundledPluginLogo } from "./pluginLogos";

const catalog = JSON.parse(readFileSync(resolve(
  __dirname, "../../../../../marketplace/seed_catalog.json",
), "utf8")) as { plugins: { id: string }[] };

it.each(catalog.plugins.map((plugin) => plugin.id))("ships local artwork for %s", (id) => {
  const logo = bundledPluginLogo(id);
  expect(logo).toBeTruthy();
  expect(logo).not.toMatch(/^https?:/);
});

it.each(["onenote", "onedrive", "azure", "google_cloud", "stripe", "cloudflare", "agentmail"])(
  "never substitutes an external glyph for the reported missing %s brand", (id) => {
    expect(bundledPluginLogo(id)).toMatch(/(?:\.svg|\.png|\.ico|^data:)/);
  },
);

it("leaves unknown community brands to their own declared artwork", () => {
  expect(bundledPluginLogo("unknown-community-service")).toBeUndefined();
});
