import { loadFont as loadSpaceGrotesk } from "@remotion/google-fonts/SpaceGrotesk";
import { loadFont as loadJetBrains } from "@remotion/google-fonts/JetBrainsMono";

// The website's display + utility faces, mirrored into the film so it reads as
// the same brand (Jarvis Web UI/src/styles/global.css: Space Grotesk display,
// JetBrains Mono for terminal / labels / data). Body stays Inter (theme.FONT).
const display = loadSpaceGrotesk("normal", {
  weights: ["400", "500", "600", "700"],
  subsets: ["latin"],
  ignoreTooManyRequestsWarning: true,
});
const mono = loadJetBrains("normal", {
  weights: ["400", "500", "700"],
  subsets: ["latin"],
  ignoreTooManyRequestsWarning: true,
});

export const FONT_DISPLAY = display.fontFamily;
export const FONT_MONO = mono.fontFamily;
