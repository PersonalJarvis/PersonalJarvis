import { defineConfig } from "vite";
import { resolve } from "path";

// Multi-Entry: edge-glow.html und (spaeter) mascot.html. Vite legt sie
// flach in dist/ ab — window_glow.py laedt dist/edge-glow.html via
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
        // Flache Output-Struktur, damit QtWebEngine relative Pfade
        // einfach aufloesen kann (assets/ als Geschwister-Ordner zur HTML).
        entryFileNames: "assets/[name]-[hash].js",
        chunkFileNames: "assets/[name]-[hash].js",
        assetFileNames: "assets/[name]-[hash][extname]",
      },
    },
  },
});
