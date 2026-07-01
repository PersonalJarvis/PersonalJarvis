import { defineConfig } from "vite";
import { resolve } from "path";

// Multi-entry: edge-glow.html and (later) mascot.html. Vite places them
// flat in dist/ — window_glow.py loads dist/edge-glow.html via
// QUrl.fromLocalFile.
export default defineConfig({
  base: "./",
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
    rollupOptions: {
      input: {
        "edge-glow": resolve(__dirname, "edge-glow.html"),
        "mascot": resolve(__dirname, "mascot.html"),
      },
      output: {
        // Flat output structure, so QtWebEngine can resolve relative
        // paths easily (assets/ as a sibling folder to the HTML).
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
