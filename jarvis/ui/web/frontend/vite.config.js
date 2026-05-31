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
        sourcemap: true,
    },
    test: {
        environment: "jsdom",
        globals: true,
    },
});
