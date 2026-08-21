import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";

// `base: "./"` makes built asset URLs relative, so the bundle loads from the `tauri://`
// origin in the desktop shell (absolute `/assets` 404s there); a server-hosted build is
// unaffected. Dev runs on a fixed port (1420) with strictPort so the Tauri webview always
// loads the vite instance Tauri itself spawns (a drifting port would make the window load a
// stale/other server). `tauri.conf.json` devUrl must match this.

// The sidecar's launch token, so the browser GUI can authenticate against
// 127.0.0.1:8765. Candidates, newest mtime wins:
//   COWORKER_STATE_DIR override / the conventional state dir,
//   <repo>/.dev-state  (run.py pins MSIX Store Python dev runs here — same physical
//                       store start-dev.bat uses, so a raw server command works too),
//   MSIX Store-Python's virtualized %APPDATA% (LocalCache) copy (see below).
function readDevToken(): string {
  const candidates: string[] = [];
  const state =
    process.env.COWORKER_STATE_DIR ||
    (process.platform === "win32"
      ? path.join(process.env.APPDATA || os.homedir(), "coworker")
      : path.join(os.homedir(), ".config", "coworker"));
  candidates.push(path.join(state, "sidecar-8765.token"));
  if (process.platform === "win32") {
    // The dev GUI always runs from <repo>/surfaces/gui, so the repo root is two levels up.
    const guiDir = typeof __dirname !== "undefined" ? __dirname : process.cwd();
    candidates.push(path.resolve(guiDir, "..", "..", ".dev-state", "sidecar-8765.token"));
    // Microsoft Store Python is MSIX-packaged: writes under %APPDATA% are redirected into
    // the package's LocalCache, invisible to Node. A plain `openworker-server` run (no
    // COWORKER_STATE_DIR) therefore used to leave its per-launch token only here.
    const pkgs = path.join(process.env.LOCALAPPDATA || os.homedir(), "Packages");
    try {
      for (const dir of fs.readdirSync(pkgs)) {
        if (!dir.startsWith("PythonSoftwareFoundation.Python.")) continue;
        candidates.push(
          path.join(pkgs, dir, "LocalCache", "Roaming", "coworker", "sidecar-8765.token")
        );
      }
    } catch {
      // Packages dir unreadable — nothing to add
    }
  }
  let best: { mtimeMs: number; token: string } | null = null;
  for (const file of candidates) {
    try {
      const st = fs.statSync(file);
      const token = fs.readFileSync(file, "utf8").trim();
      if (token && (!best || st.mtimeMs > best.mtimeMs)) {
        best = { mtimeMs: st.mtimeMs, token };
      }
    } catch {
      // no token at this location — keep looking
    }
  }
  return best?.token ?? "";
}

export default defineConfig(({ command }) => {
  // Vite's `config.define` is only applied by the vite:define plugin on BUILD —
  // in dev (non-SSR) it early-returns, so the launch token must reach the browser
  // through import.meta.env instead: loadEnv picks up VITE_-prefixed process.env
  // vars in BOTH dev and build. Leave an explicit VITE_COWORKER_DEV_TOKEN set by
  // the user alone; otherwise publish the per-launch token we just read.
  if (command === "serve") {
    const devToken = readDevToken();
    if (devToken && !process.env.VITE_COWORKER_DEV_TOKEN) {
      process.env.VITE_COWORKER_DEV_TOKEN = devToken;
    }
  }
  return {
    base: "./",
    plugins: [react()],
    server: { port: 1420, strictPort: true },
    // Build-only: vite:define runs for production bundles; the desktop shell injects
    // its in-memory token at runtime, so the value here is irrelevant there.
    define: { __COWORKER_DEV_TOKEN__: JSON.stringify(process.env.VITE_COWORKER_DEV_TOKEN || "") },
    // Tauri CLI looks for these; harmless for the browser build.
    clearScreen: false,
    envPrefix: ["VITE_", "TAURI_"],
  };
});
