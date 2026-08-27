import { afterEach, expect, it, vi } from "vitest";
import { connectEvents, getHealth, Session } from "./api";

afterEach(() => {
  vi.unstubAllGlobals();
});

it("authenticates REST and session WebSocket calls with the launch token", async () => {
  vi.stubGlobal("__COWORKER_API_TOKEN__", "launch-token");
  const request = vi.fn(async (_url: string, init?: RequestInit) => {
    expect(new Headers(init?.headers).get("X-OpenWorker-Token")).toBe("launch-token");
    return { json: async () => ({ status: "ok" }) } as Response;
  });
  vi.stubGlobal("fetch", request);

  const sockets: FakeWebSocket[] = [];
  class FakeWebSocket {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    readyState = FakeWebSocket.CONNECTING;
    onmessage: ((event: MessageEvent) => void) | null = null;
    onopen: (() => void) | null = null;
    onclose: (() => void) | null = null;
    send = vi.fn();
    close = vi.fn();

    constructor(
      public readonly url: string,
      public readonly protocols?: string | string[],
    ) {
      sockets.push(this);
    }
  }
  vi.stubGlobal("WebSocket", FakeWebSocket);

  await getHealth();
  expect(request).toHaveBeenCalledOnce();

  const session = new Session("s1", "/workspace", "code", { onEvent: vi.fn() });
  const socket = (session as unknown as { ws: FakeWebSocket }).ws;
  expect(socket.protocols).toEqual(["openworker", "launch-token"]);

  socket.readyState = FakeWebSocket.OPEN;
  session.userMessage("explain this", [], "gpt-5.6-sol", undefined, "high");
  expect(socket.send).toHaveBeenCalledWith(
    JSON.stringify({
      type: "user_message",
      text: "explain this",
      model: "gpt-5.6-sol",
      reasoning_effort: "high",
    }),
  );

  const stopEvents = connectEvents(vi.fn());
  expect(sockets[1].url).toBe("ws://127.0.0.1:8765/ws/events?surface=browser");
  expect(sockets[1].protocols).toEqual(["openworker", "launch-token"]);
  stopEvents();
  expect(sockets[1].close).toHaveBeenCalledOnce();

  vi.stubGlobal("__TAURI__", {});
  const stopTauriEvents = connectEvents(vi.fn());
  expect(sockets[2].url).toBe("ws://127.0.0.1:8765/ws/events");
  stopTauriEvents();
});
