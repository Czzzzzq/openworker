// FloatingIconToggle — the composer's 悬浮窗 capsule toggle. Hides entirely while the
// sidecar reports the plugin unavailable; shows on/off state and flips it via POST.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { FloatingIconToggle } from "./FloatingIconToggle";

/** Stub global fetch against /v1/floating-icon. POST flips the reported state (the
 *  component's own toggle contract); GET returns the current state. */
function stubFloating(initial: { available?: boolean; running?: boolean }) {
  let current = { ok: true, available: true, running: false, ...initial };
  const calls: { method: string; enabled?: boolean }[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      if (!String(url).includes("/v1/floating-icon")) {
        return { ok: true, json: async () => ({}) } as Response;
      }
      const method = (init?.method || "GET").toUpperCase();
      if (method === "POST") {
        const body = JSON.parse(String(init?.body || "{}"));
        current = { ok: true, available: true, running: !!body.enabled };
        calls.push({ method, enabled: !!body.enabled });
      } else {
        calls.push({ method });
      }
      return { ok: true, json: async () => current } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("FloatingIconToggle", () => {
  it("renders nothing while the plugin is unavailable", async () => {
    stubFloating({ available: false });
    render(<FloatingIconToggle />);
    await waitFor(() =>
      expect(screen.queryByTestId("floating-icon-toggle")).toBeNull(),
    );
  });

  it("shows an on-state capsule when the icon is running", async () => {
    stubFloating({ available: true, running: true });
    render(<FloatingIconToggle />);
    const btn = await screen.findByTestId("floating-icon-toggle");
    expect(btn.getAttribute("aria-pressed")).toBe("true");
    expect(btn.getAttribute("title")).toBe("关闭悬浮窗");
    expect(btn.textContent).toContain("悬浮窗");
  });

  it("clicking closes a running icon (POST enabled=false) and flips to off", async () => {
    const calls = stubFloating({ available: true, running: true });
    render(<FloatingIconToggle />);
    const btn = await screen.findByTestId("floating-icon-toggle");
    fireEvent.click(btn);
    await waitFor(() => expect(calls.some((c) => c.method === "POST")).toBe(true));
    expect(calls[calls.length - 1]).toEqual({ method: "POST", enabled: false });
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("false"));
    expect(btn.getAttribute("title")).toBe("打开悬浮窗");
  });

  it("clicking opens a stopped icon (POST enabled=true) and flips to on", async () => {
    const calls = stubFloating({ available: true, running: false });
    render(<FloatingIconToggle />);
    const btn = await screen.findByTestId("floating-icon-toggle");
    expect(btn.getAttribute("aria-pressed")).toBe("false");
    fireEvent.click(btn);
    await waitFor(() => expect(calls[calls.length - 1]).toEqual({ method: "POST", enabled: true }));
    await waitFor(() => expect(btn.getAttribute("aria-pressed")).toBe("true"));
  });
});
