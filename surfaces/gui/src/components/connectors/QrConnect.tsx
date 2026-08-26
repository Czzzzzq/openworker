import { useCallback, useEffect, useRef, useState } from "react";
import QRCode from "react-qr-code";
import {
  cancelQrLogin,
  getQrLogin,
  startQrLogin,
  verifyQrLogin,
  type Connector,
  type QrLoginSession,
} from "../../api";
import { PILL_ACCENT, PILL_LINE } from "./ui";

const POLL_MS = 1000;

function shouldPoll(session: QrLoginSession): boolean {
  return (
    !session.needs_verify_code &&
    (session.state === "waiting_scan" || session.state === "waiting_confirm")
  );
}

function mergeSession(previous: QrLoginSession | null, next: QrLoginSession): QrLoginSession {
  if (!previous || previous.session_id !== next.session_id) return next;
  return {
    ...previous,
    ...next,
    // The server may omit the payload after the initial response. Keep it only in
    // component memory so the visible QR does not disappear while status is polled.
    qr_content: next.qr_content || previous.qr_content,
  };
}

export function QrConnect({
  connector,
  onConnected,
}: {
  connector: Connector;
  onConnected: () => void;
}) {
  const [session, setSession] = useState<QrLoginSession | null>(null);
  const [starting, setStarting] = useState(true);
  const [requestError, setRequestError] = useState<string | null>(null);
  const [verifyCode, setVerifyCode] = useState("");
  const [verifying, setVerifying] = useState(false);
  const activeSession = useRef<string | null>(null);
  const completed = useRef(false);
  const started = useRef(false);
  const onConnectedRef = useRef(onConnected);
  onConnectedRef.current = onConnected;

  const begin = useCallback(async () => {
    setStarting(true);
    setRequestError(null);
    setVerifyCode("");
    try {
      const next = await startQrLogin(connector.name);
      activeSession.current = next.session_id;
      setSession(next);
    } catch (error) {
      setSession(null);
      setRequestError(error instanceof Error ? error.message : "无法生成二维码");
    } finally {
      setStarting(false);
    }
  }, [connector.name]);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    void begin();
  }, [begin]);

  // Exactly one status request is scheduled at a time. A fresh response changes
  // `session`, which replaces this timer; transient failures retry without creating
  // a second login session.
  useEffect(() => {
    if (!session || !shouldPoll(session)) return;
    const sessionId = session.session_id;
    const timer = setTimeout(async () => {
      try {
        const next = await getQrLogin(connector.name, sessionId);
        if (activeSession.current !== sessionId) return;
        setRequestError(null);
        setSession((previous) => mergeSession(previous, next));
      } catch (error) {
        if (activeSession.current !== sessionId) return;
        setRequestError(error instanceof Error ? error.message : "无法检查扫码状态");
        // Force a new one-shot timer while preserving the active session.
        setSession((previous) => (previous ? { ...previous } : previous));
      }
    }, POLL_MS);
    return () => clearTimeout(timer);
  }, [connector.name, session]);

  useEffect(() => {
    if (session?.state !== "connected" || completed.current) return;
    completed.current = true;
    activeSession.current = null;
    onConnectedRef.current();
  }, [session?.state]);

  // Closing the modal or navigating away revokes only an unfinished, ephemeral
  // login session. A completed connector profile is never touched here.
  useEffect(
    () => () => {
      const sessionId = activeSession.current;
      if (!completed.current && sessionId) {
        activeSession.current = null;
        void cancelQrLogin(connector.name, sessionId).catch(() => undefined);
      }
    },
    [connector.name],
  );

  const restart = async () => {
    const oldSession = activeSession.current;
    activeSession.current = null;
    if (oldSession) {
      await cancelQrLogin(connector.name, oldSession).catch(() => undefined);
    }
    setSession(null);
    await begin();
  };

  const submitVerification = async () => {
    const code = verifyCode.trim();
    if (!session || !code) return;
    setVerifying(true);
    setRequestError(null);
    try {
      const next = await verifyQrLogin(connector.name, session.session_id, code);
      if (activeSession.current !== session.session_id) return;
      setSession((previous) => mergeSession(previous, next));
      setVerifyCode("");
    } catch (error) {
      setRequestError(error instanceof Error ? error.message : "验证码无效");
    } finally {
      setVerifying(false);
    }
  };

  const needsCode = session?.state === "need_verify_code" || !!session?.needs_verify_code;
  const terminal =
    session?.state === "expired" ||
    session?.state === "error" ||
    session?.state === "cancelled";

  return (
    <div className="px-5 py-4 space-y-3" data-testid="qr-connect">
      {starting && !session ? (
        <div className="py-12 text-center text-[13px] text-muted">正在生成二维码…</div>
      ) : session ? (
        <>
          {!terminal && !needsCode && session.qr_content && (
            <div className="flex justify-center">
              <div
                className="bg-white p-3 rounded-xl border border-line"
                role="img"
                aria-label={`${connector.title} 登录二维码`}
                data-testid="qr-image"
              >
                <QRCode value={session.qr_content} size={216} />
              </div>
            </div>
          )}

          {session.state === "waiting_scan" && !needsCode && (
            <p className="text-[13px] text-ink text-center">请用微信扫一扫，并在手机上确认登录。</p>
          )}
          {session.state === "waiting_confirm" && !needsCode && (
            <p className="text-[13px] text-ink text-center">已扫码，请在微信中确认。</p>
          )}

          {needsCode && (
            <div className="space-y-3" data-testid="qr-verify">
              <p className="text-[13px] text-ink text-center">微信需要额外验证，请输入手机上显示的验证码。</p>
              <input
                className="w-full px-3 py-2 rounded-lg border border-line bg-paper text-[13px] text-ink outline-none focus:border-accent"
                aria-label="微信验证码"
                inputMode="numeric"
                autoComplete="one-time-code"
                value={verifyCode}
                onChange={(event) => setVerifyCode(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") void submitVerification();
                }}
              />
              <button
                className={PILL_ACCENT + " w-full !py-2"}
                onClick={() => void submitVerification()}
                disabled={verifying || !verifyCode.trim()}
              >
                {verifying ? "正在验证…" : "提交验证码"}
              </button>
              <p className="text-[12px] text-faint text-center">验证码仅用于本次登录，不会显示给 Agent。</p>
            </div>
          )}

          {terminal && (
            <div className="py-5 text-center space-y-3" data-testid={`qr-${session.state}`}>
              <p className="text-[13px] text-danger">
                {session.error ||
                  (session.state === "expired"
                    ? "二维码已过期。"
                    : session.state === "cancelled"
                      ? "登录已取消。"
                      : "微信连接失败。")}
              </p>
              <button className={PILL_LINE} onClick={() => void restart()} disabled={starting}>
                {starting ? "正在刷新…" : "刷新二维码"}
              </button>
            </div>
          )}
        </>
      ) : (
        <div className="py-5 text-center space-y-3">
          <p className="text-[13px] text-danger">{requestError || "无法生成二维码"}</p>
          <button className={PILL_LINE} onClick={() => void restart()} disabled={starting}>
            重试
          </button>
        </div>
      )}

      {requestError && session && !terminal && (
        <div className="text-[12px] text-danger text-center" role="alert">
          {requestError}
        </div>
      )}
      {session?.expires_at && !terminal && (
        <p className="text-[12px] text-faint text-center">
          二维码有效至 {new Date(session.expires_at * 1000).toLocaleTimeString()}
        </p>
      )}
      <p className="text-[12px] text-faint text-center">
        仅连接你与 ClawBot 的直聊；不会读取普通好友、群聊或历史消息。登录凭据只保存在这台电脑上。
      </p>
    </div>
  );
}
