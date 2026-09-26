import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync, readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const destination = path.resolve(root, "../../assets/demo/readme-2026-09");
const settings = JSON.parse(readFileSync(path.join(root, "settings.json"), "utf8"));
const colorTags = "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0";
mkdirSync(destination, { recursive: true });
function ffmpeg(args) {
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { windowsHide: true, encoding: "utf8", stdio: "inherit" });
}
const requested = process.argv.find(arg => arg.startsWith("--id="))?.slice(5);
if (requested && !settings.compositions.some(item => item.id === requested)) throw new Error(`Unknown composition: ${requested}`);
for (const { id, file: name, poster, gifLoop = true } of settings.compositions.filter(item => !requested || item.id === requested)) {
  const master = path.join(root, "out", `${id}.mp4`);
  ffmpeg(["-i", master, "-c", "copy", "-an", "-bsf:v", colorTags, "-movflags", "+faststart", path.join(root, "out", `${id}-4k.mp4`)]);
  const palette = path.join(root, "out", `${id}-palette.png`);
  // GIF timing has 10 ms units: 25 fps gives an exact 40 ms cadence.
  const filter = `fps=${settings.gifFps},scale=${settings.gifWidth}:-1:flags=lanczos`;
  ffmpeg(["-i", master, "-vf", `${filter},palettegen=max_colors=256:stats_mode=full`, "-frames:v", "1", palette]);
  ffmpeg(["-i", master, "-i", palette, "-lavfi", `${filter}[scaled];[scaled][1:v]paletteuse=dither=none:diff_mode=rectangle`, "-loop", gifLoop ? "0" : "-1", path.join(destination, `${name}.gif`)]);
  ffmpeg(["-i", master, "-vf", `scale=${settings.width}:${settings.height}:flags=lanczos`, "-c:v", "libx264", "-crf", "16", "-preset", "slow", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", "-bsf:v", colorTags, "-movflags", "+faststart", "-an", path.join(destination, `${name}.mp4`)]);
  copyFileSync(path.join(root, "out", `${id}-${poster}.png`), path.join(destination, `${name}.png`));
  console.log(`Exported ${name}`);
}
