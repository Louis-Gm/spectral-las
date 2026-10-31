import path from "node:path";
import { fileURLToPath } from "node:url";
import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, loadEnv } from "vite";
const directory = path.dirname(fileURLToPath(import.meta.url));

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, directory, "VITE_");
  return {
    root: directory,
    envDir: directory,
    base: env.VITE_SITE_BASE_PATH || "/",
    plugins: [react(), tailwindcss()],
    resolve: { alias: { "@": path.resolve(directory, "src") } },
    build: { outDir: "dist", emptyOutDir: true },
  };
});
