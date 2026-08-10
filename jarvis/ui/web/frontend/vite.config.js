/// <reference types="vitest" />
import fs from "node:fs";
import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "node:path";
var MATERIAL_ICON_ROUTE = "/assets/material-file-icons";
var materialIconSource = path.resolve(__dirname, "node_modules/material-icon-theme/icons");
/**
 * Keep the complete icon theme out of the Agentic IDE JavaScript chunk.
 * Development serves the pinned package directly; production copies the same
 * MIT-licensed SVGs beside the static assets for offline desktop use.
 */
function materialIconAssets() {
    var outputDirectory = "";
    return {
        name: "material-file-icon-assets",
        configResolved: function (config) {
            outputDirectory = path.resolve(config.root, config.build.outDir);
        },
        configureServer: function (server) {
            server.middlewares.use(MATERIAL_ICON_ROUTE, function (request, response, next) {
                var _a;
                var requestedFile = "";
                try {
                    requestedFile = decodeURIComponent((_a = request.url) !== null && _a !== void 0 ? _a : "")
                        .split("?", 1)[0]
                        .replace(/^\/+/, "");
                }
                catch (_b) {
                    next();
                    return;
                }
                if (!/^[a-zA-Z0-9_.-]+\.svg$/.test(requestedFile)) {
                    next();
                    return;
                }
                var sourceFile = path.resolve(materialIconSource, requestedFile);
                if (path.dirname(sourceFile) !== materialIconSource ||
                    !fs.existsSync(sourceFile)) {
                    next();
                    return;
                }
                response.setHeader("Content-Type", "image/svg+xml");
                response.setHeader("Cache-Control", "public, max-age=3600");
                fs.createReadStream(sourceFile).pipe(response);
            });
        },
        writeBundle: function () {
            fs.cpSync(materialIconSource, path.join(outputDirectory, "assets", "material-file-icons"), { recursive: true });
        },
    };
}
export default defineConfig({
    plugins: [react(), materialIconAssets()],
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
