import { execFileSync } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
const root = fileURLToPath(new URL("..", import.meta.url));
const frontend = path.resolve(root, "../../jarvis/ui/web/frontend");
const source = readFileSync(path.join(frontend, "src/index.css"), "utf8");
// Let webpack resolve packaged fonts. Inlining them with Tailwind would leave
// their relative font URLs pointing into the generated stylesheet directory.
const imports = source.match(/^@import .+;$/gm) ?? [];
mkdirSync(path.join(root, "out"), { recursive: true });
const input = path.join(root, "out/theme-input.css");
const output = path.join(root, "src/app.css");
const content = [path.join(root, "src/**/*.tsx"), path.join(frontend, "src/components/ui/**/*.tsx")].join(",");
writeFileSync(input, source.replace(/^@import .+;$/gm, ""));
execFileSync(process.execPath, [path.join(root, "node_modules/tailwindcss/lib/cli.js"), "-i", input, "-o", output, "-c", path.join(frontend, "tailwind.config.ts"), "--content", content], { cwd: root, windowsHide: true, encoding: "utf8", stdio: "inherit", env: { ...process.env, NODE_PATH: path.join(root, "node_modules") } });
writeFileSync(output, imports.join("\n") + "\n" + readFileSync(output, "utf8"));
