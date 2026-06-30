import { defineConfig } from "vite";
import { resolve } from "path";

// Content script must be a single self-contained IIFE bundle.
// Builds to a temp directory; the package.json build script copies
// content.js into dist/ after the main build.
export default defineConfig({
  resolve: {
    alias: {
      "@": resolve(__dirname, "src"),
    },
  },
  build: {
    outDir: "dist-content",
    emptyDirOnBuild: true,
    rollupOptions: {
      input: resolve(__dirname, "src/content/index.ts"),
      output: {
        entryFileNames: "content.js",
        format: "iife",
      },
    },
    target: "es2020",
  },
});
