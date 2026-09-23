/** Capture the shipping SVG silhouettes as editable Blender authoring input. */
import { createRequire } from "node:module";
import { fileURLToPath } from "node:url";
import path from "node:path";
import fs from "node:fs";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "../..");
const frontend = path.join(root, "jarvis/ui/web/frontend");
const require = createRequire(path.join(frontend, "package.json"));
const { build } = require("esbuild");
const result = await build({
  stdin: { contents: `import { createElement } from 'react';
    import { renderToStaticMarkup } from 'react-dom/server';
    import { AgentSymbol } from './src/components/society/AgentSymbol';
    import { COMPANION_SHAPES } from './src/components/society/companion/appearance';
    export default Object.fromEntries(COMPANION_SHAPES.map(shape => [shape, renderToStaticMarkup(createElement(AgentSymbol, { shape, color:'#ffffff', size:128 }))]));`,
    resolveDir: frontend, loader: "tsx" },
  bundle: true, platform: "node", format: "cjs", jsx: "automatic", write: false,
  loader: { ".css": "empty" },
});
const module = { exports: {} };
new Function("require", "module", "exports", result.outputFiles[0].text)(require, module, module.exports);
const output = path.join(root, "art/studies/agent-symbol-companions/source/shapes.json");
fs.writeFileSync(output, JSON.stringify(module.exports.default, null, 2) + "\n");
console.log("Captured seven source silhouettes.");
