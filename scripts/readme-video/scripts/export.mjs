import { execFileSync } from "node:child_process";
import { copyFileSync, mkdirSync } from "node:fs";
import { fileURLToPath } from "node:url";
import path from "node:path";

const root = fileURLToPath(new URL("..", import.meta.url));
const destination = path.resolve(root, "../../assets/demo/readme-2026-09");
const colorTags = "h264_metadata=colour_primaries=1:transfer_characteristics=1:matrix_coefficients=1:video_full_range_flag=0";
mkdirSync(destination, { recursive: true });
const exports = [
  ["JarvisOrchestrator", "jarvis-orchestrator", 270],
  ["JarvisAgents", "jarvis-agents", 180],
  ["UltraSwarm", "ultra-swarm", 360],
];
function ffmpeg(args) {
  execFileSync("ffmpeg", ["-hide_banner", "-loglevel", "error", "-y", ...args], { windowsHide: true, encoding: "utf8", stdio: "inherit" });
}
for (const [id, name, poster] of exports) {
  const master = path.join(root, "out", `${id}.mp4`);
  ffmpeg(["-i", master, "-c", "copy", "-an", "-bsf:v", colorTags, "-movflags", "+faststart", path.join(root, "out", `${id}-4k.mp4`)]);
  const palette = path.join(root, "out", `${id}-palette.png`);
  const filter = "fps=12,scale=1000:-1:flags=lanczos";
  ffmpeg(["-i", master, "-vf", `${filter},palettegen=max_colors=128:stats_mode=diff`, "-frames:v", "1", palette]);
  ffmpeg(["-i", master, "-i", palette, "-lavfi", `${filter}[scaled];[scaled][1:v]paletteuse=dither=bayer:bayer_scale=3:diff_mode=rectangle`, "-loop", "0", path.join(destination, `${name}.gif`)]);
  ffmpeg(["-i", master, "-vf", "scale=1600:900:flags=lanczos", "-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-color_primaries", "bt709", "-color_trc", "bt709", "-colorspace", "bt709", "-color_range", "tv", "-bsf:v", colorTags, "-movflags", "+faststart", "-an", path.join(destination, `${name}.mp4`)]);
  copyFileSync(path.join(root, "out", `${id}-${poster}.png`), path.join(destination, `${name}.png`));
  console.log(`Exported ${name}`);
}
