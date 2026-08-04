import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import path from "path";

export default defineConfig({
  plugins: [react()],
  esbuild: {
    // Strip console/debugger from production bundles - 28 console.log calls
    // (including env dumps) were shipping to users.
    drop: process.env.NODE_ENV === "production" ? ["console", "debugger"] : [],
  },
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "./src"),
    },
  },
  server: {
    host: true,
    port: process.env.PORT ? parseInt(process.env.PORT) : 5173,
  },
  preview: {
    host: true,
    port: process.env.PORT ? parseInt(process.env.PORT) : 4173,
  },
  build: {
    outDir: "dist",
    sourcemap: false,
    rollupOptions: {
      output: {
        manualChunks: {
          "react-vendor": ["react", "react-dom"],
          "router-vendor": ["react-router-dom"],
          "chart-vendor": ["recharts"],
          "http-vendor": ["axios"],
        },
      },
    },
  },
  test: {
    // `test`/`test:ui` in package.json ran vitest with no environment
    // config and no jsdom - @testing-library/react was installed but
    // couldn't actually render anything (no DOM), so no test using it
    // could ever have passed.
    environment: "jsdom",
    setupFiles: ["./src/setupTests.js"],
    globals: true,
  },
});
