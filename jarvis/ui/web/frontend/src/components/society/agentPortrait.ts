/** Compact portraits are independent of the agent's world figure. */
import botCreator from "@/assets/society/portraits/bot-creator.webp";
import communitySupport from "@/assets/society/portraits/community-support.webp";
import morningBriefing from "@/assets/society/portraits/morning-briefing.webp";

const BUILT_IN: Record<string, string> = {
  "bot-creator": botCreator,
  "community-support": communitySupport,
  "morning-briefing": morningBriefing,
};

const WEBP_DATA_URL = /^data:image\/webp;base64,[A-Za-z0-9+/]+={0,2}$/;
export const MAX_PORTRAIT_DATA_URL_LENGTH = 160_000;

export function agentPortraitUrl(value: string | undefined): string | null {
  if (!value) return null;
  if (Object.prototype.hasOwnProperty.call(BUILT_IN, value)) return BUILT_IN[value];
  if (value.length <= MAX_PORTRAIT_DATA_URL_LENGTH && WEBP_DATA_URL.test(value)) return value;
  return null;
}

/** Keep uploaded portraits small because the roster API carries the recipe. */
export async function encodeAgentPortrait(file: File): Promise<string> {
  if (!(["image/png", "image/jpeg", "image/webp"] as string[]).includes(file.type) || file.size > 8 * 1024 * 1024) {
    throw new Error("portrait_invalid_file");
  }
  const objectUrl = URL.createObjectURL(file);
  try {
    const image = new Image();
    await new Promise<void>((resolve, reject) => {
      image.onload = () => resolve();
      image.onerror = () => reject(new Error("portrait_invalid_file"));
      image.src = objectUrl;
    });
    if (!image.naturalWidth || !image.naturalHeight || image.naturalWidth > 4096 || image.naturalHeight > 4096) {
      throw new Error("portrait_invalid_file");
    }
    const canvas = document.createElement("canvas");
    canvas.width = 256;
    canvas.height = 256;
    const ctx = canvas.getContext("2d");
    if (!ctx) throw new Error("portrait_save_failed");
    const side = Math.min(image.naturalWidth, image.naturalHeight);
    ctx.drawImage(image, (image.naturalWidth - side) / 2, (image.naturalHeight - side) / 2, side, side, 0, 0, 256, 256);
    const dataUrl = canvas.toDataURL("image/webp", 0.82);
    if (!agentPortraitUrl(dataUrl)) throw new Error("portrait_too_large");
    return dataUrl;
  } finally {
    URL.revokeObjectURL(objectUrl);
  }
}
