import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { Composer } from "./Composer";

const props = () => ({
  mode: "interactive",
  model: "gpt-5.6-sol",
  reasoningEffort: "medium" as const,
  running: false,
  connected: true,
  onSend: vi.fn(),
  onInterrupt: vi.fn(),
  onModeChange: vi.fn(),
  onModelChange: vi.fn(),
  onReasoningEffortChange: vi.fn(),
});

afterEach(cleanup);

describe("Composer reasoning effort", () => {
  it("shows the current level and lets the user choose a faster level", () => {
    const p = props();
    render(<Composer {...p} />);
    fireEvent.click(screen.getByTitle("推理: 标准"));
    fireEvent.click(screen.getByText("快速"));
    expect(p.onReasoningEffortChange).toHaveBeenCalledWith("low");
  });
});
