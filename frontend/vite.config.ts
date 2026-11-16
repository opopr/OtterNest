import { defineConfig, loadEnv } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), "");
  const apiTarget = env.OTTERNEST_DEV_API_TARGET || "http://127.0.0.1:8000";
  const port = Number(env.PORT || 5173);

  return {
    base: "/ui-assets/",
    plugins: [react()],
    server: {
      host: "0.0.0.0",
      port,
      strictPort: false,
      proxy: {
        "/api": { target: apiTarget, changeOrigin: true, ws: true },
        "/assets": { target: apiTarget, changeOrigin: true }
      }
    },
    preview: { host: "0.0.0.0", port: Number(env.PORT || 4173) },
    build: { outDir: "dist", emptyOutDir: true }
  };
});
