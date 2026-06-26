/// <reference types="vitest" />
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
export default defineConfig({
    plugins: [react()],
    resolve: {
        alias: {
            "@": path.resolve(__dirname, "./src"),
        },
    },
    server: {
        port: 5173,
        proxy: {
            "/api": {
                target: "http://127.0.0.1:47821",
                changeOrigin: true,
            },
            "/ws": {
                target: "ws://127.0.0.1:47821",
                ws: true,
                changeOrigin: true,
            },
        },
    },
    build: {
        outDir: "../dist",
        emptyOutDir: true,
        // No source maps in production builds. They bloat the bundle, expose the
        // full original source, and bake the absolute build path (e.g. a personal
        // C:\Users\... home dir) into the .map files — none of which belongs in the
        // prebuilt dist shipped to the public repo. The Vite dev server keeps its
        // own inline maps, so local debugging is unaffected.
        sourcemap: false,
    },
    test: {
        environment: "jsdom",
        globals: true,
    },
});
