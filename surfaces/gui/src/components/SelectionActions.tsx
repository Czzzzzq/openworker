import { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { Icon } from "./Icon";

// "插件" behavior: when the user selects text with the mouse anywhere in the app, a small
// floating button appears next to the selection — 询问 OpenWorker — which quotes the
// selected text into a message so the agent answers about it in the current session.

interface SelectionActionsProps {
  /** Called when the user clicks 询问 OpenWorker; receives the quoted selected text. */
  onAction: (text: string) => void;
  /** When true the popup never appears (no active session / no model connected). */
  disabled?: boolean;
}

/** Long selections are clamped before being quoted into a message (keeps sends sane). */
const MAX_SELECTION_CHARS = 8000;
const EDGE_PAD = 8;

interface PopupState {
  text: string;
  /** Selection-rect center X (viewport coords); the popup centers on it via translateX(-50%). */
  x: number;
  /** Candidate top edges — chosen in a layout pass once the popup's own height is known. */
  yBelow: number;
  yAbove: number;
}

export function SelectionActions({ onAction, disabled }: SelectionActionsProps) {
  const [popup, setPopup] = useState<PopupState | null>(null);
  const [pos, setPos] = useState<{ x: number; y: number } | null>(null);
  const popupRef = useRef<HTMLDivElement | null>(null);

  const hide = useCallback(() => {
    setPopup(null);
    setPos(null);
  }, []);

  // Read the live selection and stage a popup next to it. Runs on mouseup (and debounced
  // selectionchange), so the text is captured when the popup SHOWS — the click handler uses
  // this captured text, not the live selection, because mousedown on the toolbar must not
  // clear the user's highlight (that's what preventDefault on the toolbar buys us).
  const readSelection = useCallback(() => {
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || sel.rangeCount === 0) return hide();
    const text = sel.toString().trim();
    if (!text) return hide();
    // Never pop up on a selection made inside the toolbar itself.
    const anchor = sel.anchorNode;
    if (anchor && popupRef.current && popupRef.current.contains(anchor)) return;
    const range = sel.getRangeAt(0);
    // jsdom lacks Range.getBoundingClientRect (real Chromium/WebKit webviews have it); fall
    // back to a zero rect so the toolbar still surfaces in tests and degenerate contexts.
    const rect =
      typeof range.getBoundingClientRect === "function"
        ? range.getBoundingClientRect()
        : { left: 0, top: 0, right: 0, bottom: 0, width: 0, height: 0 };
    setPopup({
      text: text.slice(0, MAX_SELECTION_CHARS),
      x: rect.left + rect.width / 2,
      yBelow: (rect.bottom || rect.top) + EDGE_PAD,
      yAbove: (rect.top || rect.bottom) - EDGE_PAD,
    });
    setPos(null); // re-measured by the layout pass
  }, [hide]);

  // Final placement: clamp horizontally to the viewport and flip above the selection when
  // there isn't room below (measured once the popup is actually in the DOM).
  useLayoutEffect(() => {
    if (!popup || !popupRef.current) return;
    const el = popupRef.current;
    const w = el.offsetWidth;
    const h = el.offsetHeight;
    const x = Math.min(
      Math.max(popup.x, w / 2 + EDGE_PAD),
      Math.max(window.innerWidth - w / 2 - EDGE_PAD, w / 2 + EDGE_PAD),
    );
    const fitsBelow = popup.yBelow + h <= window.innerHeight - EDGE_PAD;
    const fitsAbove = popup.yAbove - h >= EDGE_PAD;
    setPos({ x, y: fitsBelow || !fitsAbove ? popup.yBelow : popup.yAbove - h });
  }, [popup]);

  useEffect(() => {
    if (disabled) {
      hide();
      return;
    }
    // Clicks on the toolbar itself must neither hide it (mousedown) nor re-read the still-live
    // selection (mouseup) — the button click handler owns the dispatch from there.
    const insideToolbar = (t: EventTarget | null) =>
      !!t && t instanceof Node && !!popupRef.current && popupRef.current.contains(t);

    const onMouseDown = (e: MouseEvent) => {
      if (insideToolbar(e.target)) return;
      hide(); // beginning any other interaction dismisses the toolbar
    };
    const onMouseUp = (e: MouseEvent) => {
      if (insideToolbar(e.target)) return;
      // Defer one tick: the browser finalizes the selection after mouseup.
      window.setTimeout(readSelection, 0);
    };
    const onKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") hide();
    };
    // selectionchange fires constantly mid-drag / mid-caret-move; debounce so the popup
    // only responds to the settled selection (and disappears when it collapses).
    let selectionTimer: ReturnType<typeof setTimeout> | undefined;
    const onSelectionChange = () => {
      window.clearTimeout(selectionTimer);
      selectionTimer = window.setTimeout(readSelection, 120);
    };
    const onScroll = () => hide();
    const onBlur = () => hide();
    const onVisibility = () => {
      if (document.hidden) hide();
    };

    document.addEventListener("mousedown", onMouseDown, true);
    document.addEventListener("mouseup", onMouseUp, true);
    document.addEventListener("selectionchange", onSelectionChange);
    document.addEventListener("keydown", onKeyDown);
    document.addEventListener("scroll", onScroll, true);
    window.addEventListener("blur", onBlur);
    document.addEventListener("visibilitychange", onVisibility);
    window.addEventListener("resize", hide);

    return () => {
      window.clearTimeout(selectionTimer);
      document.removeEventListener("mousedown", onMouseDown, true);
      document.removeEventListener("mouseup", onMouseUp, true);
      document.removeEventListener("selectionchange", onSelectionChange);
      document.removeEventListener("keydown", onKeyDown);
      document.removeEventListener("scroll", onScroll, true);
      window.removeEventListener("blur", onBlur);
      document.removeEventListener("visibilitychange", onVisibility);
      window.removeEventListener("resize", hide);
    };
  }, [disabled, hide, readSelection]);

  const fire = () => {
    if (!popup) return;
    const text = popup.text;
    hide();
    onAction(text);
  };

  if (!popup) return null;
  const p = pos ?? { x: popup.x, y: popup.yBelow };
  const btn =
    "flex items-center gap-1.5 rounded-md px-2.5 py-1.5 text-[12.5px] font-medium whitespace-nowrap cursor-pointer transition-colors";
  return (
    <div
      ref={popupRef}
      data-testid="selection-actions"
      role="toolbar"
      aria-label="Selected-text actions"
      className="fixed z-[70] flex items-center rounded-lg border border-line bg-panel shadow-lg p-1 select-none"
      style={{ left: p.x, top: p.y, transform: "translateX(-50%)" }}
      // Keep the user's highlight (and any textarea caret) alive while they aim at the button —
      // clicking the toolbar must not collapse the selection underneath it.
      onMouseDown={(e) => e.preventDefault()}
    >
      <button
        type="button"
        data-testid="sel-ask"
        className={btn + " text-accent hover:bg-accentSoft"}
        title="把选中的文字发给 OpenWorker，让它围绕这段文字回答"
        onClick={fire}
      >
        <Icon name="sparkle" size={13} />
        询问 OpenWorker
      </button>
    </div>
  );
}
