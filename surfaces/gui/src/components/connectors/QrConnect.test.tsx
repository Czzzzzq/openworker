import { act, cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { Connector, QrLoginSession } from "../../api";
import { QrConnect } from "./QrConnect";

const api = vi.hoisted(() => ({
  startQrLogin: vi.fn(),
  getQrLogin: vi.fn(),
  verifyQrLogin: vi.fn(),
  cancelQrLogin: vi.fn(),
}));

vi.mock("../../api", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../api")>()),
  ...api,
}));

const CONNECTOR = {
  name: "weixin",
  title: "微信",
  icon: "微",
  blurb: "在微信中使用 OpenWorker。",
  auth: "qr",
  two_way: true,
  channels: false,
  available: true,
  fields: [],
  instructions: [],
  connected: false,
  account: null,
  enabled: false,
  brand_color: "#07c160",
  logo: "weixin",
  allowed_users: [],
  tools: [],
  managed: false,
  managed_profile: false,
} satisfies Connector;

function login(
  state: QrLoginSession["state"],
  extra: Partial<QrLoginSession> = {},
): QrLoginSession {
  return {
    session_id: "login-1",
    state,
    qr_content: "https://example.invalid/secret-qr-payload",
    expires_at: Math.floor(Date.now() / 1000) + 120,
    ...extra,
  };
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  api.startQrLogin.mockReset();
  api.getQrLogin.mockReset();
  api.verifyQrLogin.mockReset();
  api.cancelQrLogin.mockReset().mockResolvedValue({ ok: true });
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("QrConnect", () => {
  it("polls scan → confirm → connected without exposing the QR payload as text", async () => {
    api.startQrLogin.mockResolvedValue(login("waiting_scan"));
    api.getQrLogin
      .mockResolvedValueOnce(login("waiting_confirm", { qr_content: undefined }))
      .mockResolvedValueOnce(login("connected", { qr_content: undefined, account: "Maya" }));
    const onConnected = vi.fn();
    const { container } = render(<QrConnect connector={CONNECTOR} onConnected={onConnected} />);

    await screen.findByTestId("qr-image");
    expect(screen.getByText(/请用微信扫一扫/)).toBeTruthy();
    expect(container.textContent).not.toContain("secret-qr-payload");

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(await screen.findByText(/已扫码/)).toBeTruthy();
    // Status responses may omit qr_content; the in-memory image remains available.
    expect(screen.getByTestId("qr-image")).toBeTruthy();

    await act(async () => vi.advanceTimersByTimeAsync(1000));
    await waitFor(() => expect(onConnected).toHaveBeenCalledTimes(1));
    expect(api.cancelQrLogin).not.toHaveBeenCalled();
  });

  it("submits a verification code and resumes the same login session", async () => {
    api.startQrLogin.mockResolvedValue(
      login("need_verify_code", { needs_verify_code: true }),
    );
    api.verifyQrLogin.mockResolvedValue(
      login("waiting_confirm", { needs_verify_code: false, qr_content: undefined }),
    );
    api.getQrLogin.mockResolvedValue(login("connected", { qr_content: undefined }));

    render(<QrConnect connector={CONNECTOR} onConnected={vi.fn()} />);
    await screen.findByTestId("qr-verify");
    fireEvent.change(screen.getByLabelText("微信验证码"), { target: { value: " 123456 " } });
    fireEvent.click(screen.getByRole("button", { name: "提交验证码" }));

    await waitFor(() =>
      expect(api.verifyQrLogin).toHaveBeenCalledWith("weixin", "login-1", "123456"),
    );
    expect(api.getQrLogin).not.toHaveBeenCalled();
    await act(async () => vi.advanceTimersByTimeAsync(1000));
    expect(api.getQrLogin).toHaveBeenCalledWith("weixin", "login-1");
  });

  it("refreshes an expired login and cancels the replacement when unmounted", async () => {
    api.startQrLogin
      .mockResolvedValueOnce(login("expired"))
      .mockResolvedValueOnce(login("waiting_scan", { session_id: "login-2" }));

    const view = render(<QrConnect connector={CONNECTOR} onConnected={vi.fn()} />);
    await screen.findByTestId("qr-expired");
    fireEvent.click(screen.getByRole("button", { name: "刷新二维码" }));

    await screen.findByTestId("qr-image");
    expect(api.cancelQrLogin).toHaveBeenCalledWith("weixin", "login-1");
    view.unmount();
    expect(api.cancelQrLogin).toHaveBeenCalledWith("weixin", "login-2");
  });

  it.each([
    ["error", "微信连接失败"],
    ["cancelled", "登录已取消"],
  ] as const)("renders the %s terminal state", async (state, copy) => {
    api.startQrLogin.mockResolvedValue(login(state));
    render(<QrConnect connector={CONNECTOR} onConnected={vi.fn()} />);
    expect(await screen.findByText(new RegExp(copy))).toBeTruthy();
    expect(screen.getByRole("button", { name: "刷新二维码" })).toBeTruthy();
  });
});
