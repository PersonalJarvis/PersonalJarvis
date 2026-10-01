import { Color, SRGBColorSpace } from "three";

/** CSS theme tokens describe sRGB HSL, while Three materials store linear RGB. */
export function cssHslColor(token: string): Color {
  const [h, s, l] = token.trim().split(/\s+/).map(parseFloat);
  return new Color().setHSL((h || 0) / 360, (s || 0) / 100, (l || 0) / 100, SRGBColorSpace);
}
