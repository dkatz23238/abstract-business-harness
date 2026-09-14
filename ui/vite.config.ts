import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig, type ProxyOptions } from "vite";

/** Engine origin. Browser stays on the Vite port; these paths are proxied. */
const backend = process.env.VITE_API_URL || "http://localhost:8811";

const apiProxy: ProxyOptions = {
  target: backend,
  changeOrigin: true,
  // Agent runs and tool-event SSE can last hours. http-proxy's default
  // proxyTimeout is 2 minutes (and `0` is treated as unset).
  timeout: 24 * 60 * 60 * 1000,
  proxyTimeout: 24 * 60 * 60 * 1000,
  configure(proxy) {
    proxy.on("proxyReq", (proxyReq) => {
      proxyReq.setTimeout(0);
    });
  },
};

const proxy: Record<string, ProxyOptions> = {
  "/agui": apiProxy,
  "/profile": apiProxy,
  "/admin": apiProxy,
  "/threads": apiProxy,
  "/workspace": apiProxy,
  "/runs": apiProxy,
  "/events": apiProxy,
};

export default defineConfig({
  plugins: [react(), tailwindcss()],
  server: { proxy },
  preview: { proxy },
});
