/**
 * Flat map or space: which dimension the memory maps are drawn in.
 *
 * Both graph surfaces — the vault Memory Map in the normal Wiki and the topic
 * map in UltraWiki Explore — read this one preference, so the choice follows
 * the user around instead of having to be made twice. It is a view preference,
 * not knowledge: it lives in localStorage next to the other per-machine view
 * settings (`jarvis.agenticIde.workspaceView`, `jarvis.ui.sound`) rather than
 * in `jarvis.toml`, because a monitor that can render a 3D scene is a property
 * of the machine in front of the user, not of the install.
 *
 * The WebGL probe is the honest half of the feature. 3D needs a hardware
 * (or software-emulated) graphics context, and there are real machines that
 * cannot give one: a locked-down WebView, a VM with no GPU passthrough, a
 * remote desktop session. Offering a button that silently paints nothing there
 * would be worse than not offering it, so the probe decides whether the switch
 * is live and the UI says why when it is not.
 *
 * Pure + framework-free apart from the tiny store at the bottom, so the
 * parsing and fallback rules are unit-testable without a renderer.
 */
import { create } from "zustand";

/** `2d` is the flat canvas map, `3d` the WebGL scene. */
export type GraphDimension = "2d" | "3d";

/** Where the preference is stored. Shared by both graph surfaces. */
export const GRAPH_DIMENSION_KEY = "jarvis.wiki.graphDimension";

/** Flat stays the default: it works on every machine and costs no download. */
export const DEFAULT_GRAPH_DIMENSION: GraphDimension = "2d";

/** Narrow an arbitrary stored string; anything else is treated as unset. */
export function parseGraphDimension(raw: string | null): GraphDimension | null {
  return raw === "2d" || raw === "3d" ? raw : null;
}

/**
 * What to actually render, given the stored wish and what the machine can do.
 *
 * A stored `3d` on a machine without WebGL degrades to the flat map instead of
 * a black rectangle. The preference itself is left alone — plug the same
 * profile into a machine that can render it and 3D comes back.
 */
export function resolveGraphDimension(
  stored: GraphDimension,
  webglSupported: boolean,
): GraphDimension {
  return stored === "3d" && !webglSupported ? "2d" : stored;
}

/** The stored preference, or the default when storage is unavailable/empty. */
export function readGraphDimension(): GraphDimension {
  try {
    return (
      parseGraphDimension(window.localStorage.getItem(GRAPH_DIMENSION_KEY)) ??
      DEFAULT_GRAPH_DIMENSION
    );
  } catch {
    // Private mode / storage disabled — a view preference is never worth
    // taking the Wiki tab down for.
    return DEFAULT_GRAPH_DIMENSION;
  }
}

/** Persist the preference; a storage failure only costs the memory of it. */
export function writeGraphDimension(value: GraphDimension): void {
  try {
    window.localStorage.setItem(GRAPH_DIMENSION_KEY, value);
  } catch {
    // Same reasoning as readGraphDimension: never fatal.
  }
}

// Creating a WebGL context is not free and browsers cap how many can exist at
// once, so the answer is probed once per page and remembered.
let webglProbe: boolean | null = null;

/**
 * Can this machine render a WebGL scene at all?
 *
 * Deliberately a CAPABILITY probe rather than a platform or browser check: the
 * same build runs inside the desktop WebView on Windows, macOS and Linux, in a
 * plain browser tab against `--dev`, and in jsdom during tests. Only actually
 * asking for a context tells the truth in all four.
 */
export function detectWebgl(): boolean {
  if (webglProbe !== null) return webglProbe;
  webglProbe = probeWebgl();
  return webglProbe;
}

/** Drop the cached probe result. Exists for tests that switch the answer. */
export function resetWebglProbe(): void {
  webglProbe = null;
}

function probeWebgl(): boolean {
  try {
    if (typeof document === "undefined") return false;
    // Ask the cheap question first. jsdom (and any environment without WebGL
    // at all) does not define these constructors, and calling `getContext` on
    // its canvas raises a "not implemented" error into the console — noise
    // during every test run for an answer we already have.
    if (
      typeof WebGLRenderingContext === "undefined" &&
      typeof WebGL2RenderingContext === "undefined"
    ) {
      return false;
    }
    const canvas = document.createElement("canvas");
    const context =
      (canvas.getContext("webgl2") as WebGLRenderingContext | null) ??
      (canvas.getContext("webgl") as WebGLRenderingContext | null);
    if (!context) return false;
    // Hand the context straight back. Without this the probe permanently
    // occupies one of the handful of contexts a browser allows per page, and
    // the graph itself would be the one that fails to get one.
    const lose = context.getExtension("WEBGL_lose_context") as {
      loseContext?: () => void;
    } | null;
    lose?.loseContext?.();
    return true;
  } catch {
    // Some environments throw rather than return null (jsdom, hardened
    // WebViews). Either way the answer is "no 3D here".
    return false;
  }
}

interface GraphDimensionStore {
  /** What the user asked for, regardless of what the machine can do. */
  preference: GraphDimension;
  setPreference: (value: GraphDimension) => void;
}

const useGraphDimensionStore = create<GraphDimensionStore>((set) => ({
  preference: readGraphDimension(),
  setPreference: (value) => {
    writeGraphDimension(value);
    set({ preference: value });
  },
}));

/**
 * Set the preference from outside React — the same path the switch takes, so
 * every mounted graph reacts. Used by non-component callers and by tests that
 * need a surface to start in 3D.
 */
export function setGraphDimension(value: GraphDimension): void {
  useGraphDimensionStore.getState().setPreference(value);
}

export interface GraphDimensionHandle {
  /** The dimension to render — already degraded when WebGL is missing. */
  dimension: GraphDimension;
  /** What the user last chose, even if it cannot be honoured right now. */
  preference: GraphDimension;
  setDimension: (value: GraphDimension) => void;
  webglSupported: boolean;
}

/**
 * The switch state, shared by every graph on screen: flipping it in UltraWiki
 * Explore also flips the vault Memory Map, which is what a single preference
 * called "3D" has to mean.
 */
export function useGraphDimension(): GraphDimensionHandle {
  const preference = useGraphDimensionStore((s) => s.preference);
  const setDimension = useGraphDimensionStore((s) => s.setPreference);
  const webglSupported = detectWebgl();
  return {
    dimension: resolveGraphDimension(preference, webglSupported),
    preference,
    setDimension,
    webglSupported,
  };
}
