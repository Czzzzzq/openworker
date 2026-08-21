// Composer 的悬浮窗胶囊开关：一键打开/关闭 OpenWorker 悬浮窗（floating-icon 插件）。
// 进程由后端 sidecar 管理（打开=spawn pythonw，关闭=终止进程），本组件只做
// 状态轮询 + 点击切换，并跟随窗口焦点刷新（用户也可能从悬浮窗右键菜单退出）。

import { useCallback, useEffect, useRef, useState } from "react";
import { getFloatingIconStatus, setFloatingIcon } from "../api";

const POLL_MS = 10_000;

export function FloatingIconToggle() {
  const [running, setRunning] = useState<boolean | null>(null); // null = 状态未知/插件不可用
  const [busy, setBusy] = useState(false);
  const mounted = useRef(true);

  const refresh = useCallback(() => {
    getFloatingIconStatus()
      .then((s) => {
        if (mounted.current && s?.available) setRunning(!!s.running);
      })
      .catch(() => undefined); // 后端未启动/旧版本 → 保持隐藏，不打扰用户
  }, []);

  useEffect(() => {
    mounted.current = true;
    refresh();
    const t = window.setInterval(refresh, POLL_MS);
    const onFocus = () => refresh();
    window.addEventListener("focus", onFocus);
    return () => {
      mounted.current = false;
      window.clearInterval(t);
      window.removeEventListener("focus", onFocus);
    };
  }, [refresh]);

  const toggle = () => {
    if (busy || running === null) return;
    setBusy(true);
    setFloatingIcon(!running)
      .then((s) => {
        if (mounted.current && s?.available) setRunning(!!s.running);
      })
      .catch(() => undefined)
      .finally(() => {
        if (mounted.current) setBusy(false);
      });
  };

  if (running === null) return null; // 插件不可用时整个开关不渲染

  return (
    <button
      className={
        "inline-flex items-center gap-1.5 px-2.5 py-1 rounded-full border text-[11.5px] shrink-0 transition-colors " +
        (busy ? " opacity-60 cursor-default " : " cursor-pointer ") +
        (running
          ? " border-okLine bg-okSoft text-ok hover:brightness-105"
          : " border-line bg-paper text-muted hover:text-ink hover:border-lineStrong")
      }
      onClick={toggle}
      disabled={busy}
      aria-pressed={running}
      aria-label="悬浮窗"
      data-testid="floating-icon-toggle"
      title={running ? "关闭悬浮窗" : "打开悬浮窗"}
    >
      <span
        className={"w-1.5 h-1.5 rounded-full " + (running ? "bg-ok" : "bg-faint")}
        aria-hidden="true"
      />
      <span className="whitespace-nowrap">悬浮窗</span>
    </button>
  );
}
