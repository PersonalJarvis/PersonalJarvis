/**
 * The office outfits and their curated colourways, as plain data. Kept free of
 * runtime imports so both the figure model (default looks) and the wardrobe
 * (dressing) can read it.
 */
import type { OutfitId } from "./toyFigureModel";

export interface Colourway {
  /** Stable id, also the i18n key suffix (`society.office.colourway_<id>`). */
  id: string;
  primary: string;
  secondary: string;
  accent: string;
  inner: string;
  shoes: string;
}

export interface Outfit {
  id: OutfitId;
  colourways: readonly Colourway[];
}

/** The office outfits, casual to formal; the first colourway is the signature one. */
export const OUTFITS: readonly Outfit[] = [
  {
    id: "leather",
    colourways: [
      { id: "black", primary: "#131316", secondary: "#1d2029", accent: "#9aa0a8", inner: "#1b1b1e", shoes: "#101012" },
      { id: "cognac", primary: "#5c3622", secondary: "#2a3346", accent: "#c9a227", inner: "#efe8dc", shoes: "#3b2418" },
      { id: "oxblood", primary: "#4a1a1f", secondary: "#1d2029", accent: "#9aa0a8", inner: "#1b1b1e", shoes: "#101012" },
    ],
  },
  {
    id: "suit",
    colourways: [
      { id: "charcoal", primary: "#3a3d44", secondary: "#3a3d44", accent: "#8b1e2d", inner: "#f4f4f0", shoes: "#1a1414" },
      { id: "navy", primary: "#1f2a44", secondary: "#1f2a44", accent: "#c9a227", inner: "#eaf1fb", shoes: "#3b2418" },
      { id: "tuxedo", primary: "#141418", secondary: "#141418", accent: "#141418", inner: "#ffffff", shoes: "#0d0d0d" },
      { id: "linen", primary: "#cbb795", secondary: "#cbb795", accent: "#2f4a7a", inner: "#ffffff", shoes: "#6b4226" },
    ],
  },
  {
    id: "blazer",
    colourways: [
      { id: "old_money", primary: "#23304f", secondary: "#dccfb3", accent: "#b8943e", inner: "#f2efe6", shoes: "#6b4226" },
      { id: "camel", primary: "#b08a5a", secondary: "#3b3f4a", accent: "#f2efe6", inner: "#ffffff", shoes: "#2a1d15" },
      { id: "forest", primary: "#2f4a3a", secondary: "#e3dccb", accent: "#c9a227", inner: "#f2efe6", shoes: "#4a2e22" },
    ],
  },
  {
    id: "quarterzip",
    colourways: [
      { id: "oat", primary: "#d8cbb4", secondary: "#3b3f4a", accent: "#c7c9cc", inner: "#ffffff", shoes: "#f4f4f2" },
      { id: "navy", primary: "#1f2a44", secondary: "#c8b28e", accent: "#c7c9cc", inner: "#cfe0f5", shoes: "#3b2418" },
      { id: "forest", primary: "#2f4a3a", secondary: "#e3dccb", accent: "#c7c9cc", inner: "#ffffff", shoes: "#f4f4f2" },
    ],
  },
  {
    id: "vest",
    colourways: [
      { id: "navy", primary: "#1f2a44", secondary: "#b9a27f", accent: "#e5674f", inner: "#cfe0f5", shoes: "#3b2418" },
      { id: "charcoal", primary: "#3b3f4a", secondary: "#2b2f38", accent: "#6fbf5a", inner: "#ffffff", shoes: "#f4f4f2" },
      { id: "black", primary: "#18181c", secondary: "#c8b28e", accent: "#f2c14e", inner: "#eaf1fb", shoes: "#2a1d15" },
    ],
  },
  {
    id: "turtleneck",
    colourways: [
      { id: "black", primary: "#141416", secondary: "#3d5a80", accent: "#141416", inner: "#141416", shoes: "#9a9aa0" },
      { id: "camel", primary: "#b08a5a", secondary: "#2b2b33", accent: "#b08a5a", inner: "#b08a5a", shoes: "#3b2418" },
      { id: "cream", primary: "#eee6d6", secondary: "#3b3f4a", accent: "#eee6d6", inner: "#eee6d6", shoes: "#2a1d15" },
    ],
  },
  {
    id: "hoodie",
    colourways: [
      { id: "grey", primary: "#8a8d93", secondary: "#2b2f38", accent: "#f2f2ee", inner: "#f2f2ee", shoes: "#f4f4f2" },
      { id: "black", primary: "#1c1c20", secondary: "#1c1c20", accent: "#d9d9d9", inner: "#1c1c20", shoes: "#f4f4f2" },
      { id: "navy", primary: "#23304f", secondary: "#8a8d93", accent: "#f2f2ee", inner: "#23304f", shoes: "#f4f4f2" },
      { id: "sage", primary: "#8fa58a", secondary: "#e3dccb", accent: "#f2f2ee", inner: "#8fa58a", shoes: "#f4f4f2" },
    ],
  },
  {
    id: "tee",
    colourways: [
      { id: "white", primary: "#f2f2ee", secondary: "#3d5a80", accent: "#2b2b33", inner: "#f2f2ee", shoes: "#f4f4f2" },
      { id: "black", primary: "#1c1c20", secondary: "#2b2f38", accent: "#f2f2ee", inner: "#1c1c20", shoes: "#f4f4f2" },
      { id: "green", primary: "#3f9d5a", secondary: "#2b4a8b", accent: "#f2c14e", inner: "#3f9d5a", shoes: "#f2f2ee" },
      { id: "coral", primary: "#e5674f", secondary: "#2b2f38", accent: "#f2f2ee", inner: "#e5674f", shoes: "#f4f4f2" },
    ],
  },
];
