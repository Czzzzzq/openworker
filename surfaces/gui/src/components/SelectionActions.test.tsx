import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SelectionActions } from "./SelectionActions";

// A "plugin" behavior test: selecting text with the mouse surfaces a floating 询问 OpenWorker
// button, and clicking it hands the quoted text to the app.

afterEach(() => {
  cleanup();
  window.getSelection()?.removeAllRanges();
});

function selectText(text: string): string {
  const host = document.createElement("div");
  host.textContent = text;
  document.body.appendChild(host);
  const sel = window.getSelection()!;
  const range = document.createRange();
  range.selectNodeContents(host);
  sel.removeAllRanges();
  sel.addRange(range);
  return sel.toString();
}

describe("SelectionActions", () => {
  it("shows the button after a mouse selection and asks OpenWorker with the quoted text", async () => {
    const onAction = vi.fn();
    render(<SelectionActions onAction={onAction} />);
    selectText("DeepSeek is a Chinese AI company.");
    fireEvent.mouseUp(document.body);

    await waitFor(() => expect(screen.getByTestId("selection-actions")).toBeTruthy());
    fireEvent.click(screen.getByTestId("sel-ask"));

    expect(onAction).toHaveBeenCalledTimes(1);
    expect(onAction).toHaveBeenCalledWith("DeepSeek is a Chinese AI company.");
    // The button dismisses itself after dispatching.
    expect(screen.queryByTestId("selection-actions")).toBeNull();
  });

  it("disappears when the selection collapses", async () => {
    const onAction = vi.fn();
    render(<SelectionActions onAction={onAction} />);
    selectText("some words to select");
    fireEvent.mouseUp(document.body);
    await waitFor(() => expect(screen.getByTestId("selection-actions")).toBeTruthy());

    window.getSelection()?.removeAllRanges();
    fireEvent.mouseUp(document.body);
    await waitFor(() => expect(screen.queryByTestId("selection-actions")).toBeNull());
  });

  it("disappears on Escape", async () => {
    const onAction = vi.fn();
    render(<SelectionActions onAction={onAction} />);
    selectText("escape me");
    fireEvent.mouseUp(document.body);
    await waitFor(() => expect(screen.getByTestId("selection-actions")).toBeTruthy());

    fireEvent.keyDown(document, { key: "Escape" });
    await waitFor(() => expect(screen.queryByTestId("selection-actions")).toBeNull());
  });

  it("ignores whitespace-only selections", async () => {
    const onAction = vi.fn();
    render(<SelectionActions onAction={onAction} />);
    selectText("   \n  ");
    fireEvent.mouseUp(document.body);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("selection-actions")).toBeNull();
  });

  it("never appears while disabled", async () => {
    const onAction = vi.fn();
    render(<SelectionActions onAction={onAction} disabled />);
    selectText("disabled context");
    fireEvent.mouseUp(document.body);
    await new Promise((r) => setTimeout(r, 30));
    expect(screen.queryByTestId("selection-actions")).toBeNull();
    expect(onAction).not.toHaveBeenCalled();
  });
});
