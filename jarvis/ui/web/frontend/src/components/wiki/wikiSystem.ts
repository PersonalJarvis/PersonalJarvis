/**
 * The sun, its corona, and the light they throw.
 *
 * Colours come from a per-appearance table — signal-yellow on dark, gold on
 * light — never one hex on both grounds (CLOUD.md frontend theming).
 */
import {
  AdditiveBlending,
  CanvasTexture,
  Group,
  Mesh,
  MeshBasicMaterial,
  NormalBlending,
  PointLight,
  SphereGeometry,
  Sprite,
  SpriteMaterial,
  type Scene,
  type Texture,
} from "three";
import SpriteText from "three-spritetext";

import type { Theme } from "@/hooks/useTheme";

export interface SunPalette {
  core: number;
  glowRgb: string;
  light: number;
  lightIntensity: number;
  coronaScale: number;
}

const PALETTE: Record<Theme, SunPalette> = {
  dark: {
    core: 0xffd60a,
    glowRgb: "255, 214, 10",
    light: 0xffe680,
    lightIntensity: 2.1,
    coronaScale: 6.4,
  },
  light: {
    core: 0xa86b00,
    glowRgb: "168, 107, 0",
    light: 0xc49214,
    lightIntensity: 1.15,
    coronaScale: 5.2,
  },
};

export function sunPalette(theme: Theme): SunPalette {
  return PALETTE[theme];
}

const SUN_LIGHT_NAME = "wiki-sun-light";
/** Leftover name from the drawn rings — still stripped so a live reload cleans them. */
const SUN_RING_NAME = "wiki-orbit-ring";

let glowCache: { theme: Theme; texture: Texture } | null = null;

function coronaTexture(theme: Theme, glowRgb: string): Texture | null {
  if (glowCache?.theme === theme) return glowCache.texture;
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = 128;
  canvas.height = 128;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  const gradient = ctx.createRadialGradient(64, 64, 6, 64, 64, 64);
  gradient.addColorStop(0, `rgba(255, 248, 220, 0.95)`);
  gradient.addColorStop(0.22, `rgba(${glowRgb}, 0.55)`);
  gradient.addColorStop(0.55, `rgba(${glowRgb}, 0.16)`);
  gradient.addColorStop(1, `rgba(${glowRgb}, 0)`);
  ctx.fillStyle = gradient;
  ctx.fillRect(0, 0, 128, 128);
  const texture = new CanvasTexture(canvas);
  texture.needsUpdate = true;
  glowCache = { theme, texture };
  return texture;
}

/** The sun that replaces the hub's default sphere — core, corona, label. */
export function buildSunObject(
  title: string,
  theme: Theme,
  radius: number,
): Group {
  const palette = sunPalette(theme);
  const group = new Group();
  group.name = "wiki-sun";

  const core = new Mesh(
    new SphereGeometry(radius, 28, 28),
    new MeshBasicMaterial({ color: palette.core }),
  );
  group.add(core);

  const glowMap = coronaTexture(theme, palette.glowRgb);
  if (glowMap) {
    const corona = new Sprite(
      new SpriteMaterial({
        map: glowMap,
        color: palette.core,
        transparent: true,
        depthWrite: false,
        blending: theme === "dark" ? AdditiveBlending : NormalBlending,
        opacity: theme === "dark" ? 0.95 : 0.7,
      }),
    );
    const span = radius * palette.coronaScale;
    corona.scale.set(span, span, 1);
    group.add(corona);
  }

  if (title) {
    const label = new SpriteText(title);
    label.color = theme === "dark" ? "#fff3c4" : "#5a3a00";
    label.textHeight = 5.8;
    label.position.set(0, -(radius + 5), 0);
    group.add(label);
  }

  return group;
}

/**
 * Keep the scene's sun-light in step with the theme. Old dust rings (from a
 * previous build) are stripped and never put back — the orbits stay invisible.
 */
export function syncSystemDecor(scene: Scene, theme: Theme): void {
  const palette = sunPalette(theme);

  const stale: PointLight[] = [];
  scene.traverse((obj) => {
    if (obj.name === SUN_LIGHT_NAME || obj.name === SUN_RING_NAME) {
      stale.push(obj as PointLight);
    }
  });
  for (const obj of stale) {
    obj.removeFromParent();
  }

  const light = new PointLight(palette.light, palette.lightIntensity, 520, 2);
  light.name = SUN_LIGHT_NAME;
  light.position.set(0, 0, 0);
  scene.add(light);
}
