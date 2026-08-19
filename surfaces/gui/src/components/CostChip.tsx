// Composer 中的预算条（预算图框的常驻形态）：迷你预算进度条 + 今日费用，点击展开
// 今日/会话/预算/峰谷明细，并可跳转到 Settings ▸ 费用 看板。数据来自 /v1/cost/stats。

import { useEffect, useState } from "react";
import { getCostStats, type CostStats } from "../api";

const fmt = (n: number | undefined | null, currency?: string): string => {
  const v = Number(n || 0);
  const sym = currency === "CNY" ? "¥" : currency === "USD" ? "$" : "";
  const s = v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2);
  return `${sym}${s}`;
};

export function CostChip({
  sessionId,
  onOpenCost,
}: {
  sessionId?: string;
  onOpenCost?: () => void;
}) {
  const [stats, setStats] = useState<CostStats | null>(null);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    if (!sessionId) return;
    let active = true;
    const load = () =>
      getCostStats(sessionId)
        .then((s) => {
          if (active) setStats(s);
        })
        .catch(() => undefined);
    load();
    const t = window.setInterval(load, 30_000);
    return () => {
      active = false;
      window.clearInterval(t);
    };
  }, [sessionId]);

  if (!sessionId || !stats?.total || stats.total.calls === 0) return null;

  const currency = stats.budget?.currency || "CNY";
  const pct = stats.budget?.used_pct;
  const showBar = pct != null && stats.budget.amount > 0;
  const barColor =
    pct == null ? "var(--line)" : pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warn-ink)" : "var(--ok)";

  return (
    <div className="relative shrink-0">
      <button
        className="inline-flex items-center gap-1.5 px-2 py-1 rounded-lg text-[11.5px] text-muted hover:text-ink hover:bg-paper"
        onClick={() => setOpen((v) => !v)}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-label="API 费用"
        title={
          showBar
            ? `今日 ${fmt(stats.today?.cost, currency)} · 预算已用 ${Math.round(pct as number)}%`
            : `今日 ${fmt(stats.today?.cost, currency)}`
        }
        data-testid="cost-chip"
      >
        <span className="w-10 h-1.5 rounded-full bg-line overflow-hidden" aria-hidden="true">
          <span
            className="block h-full transition-all"
            style={{
              width: showBar ? `${Math.max(pct as number, 4)}%` : "0%",
              background: barColor,
            }}
          />
        </span>
        <span className="tabular-nums">{fmt(stats.today?.cost, currency)}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-30" onClick={() => setOpen(false)} />
          <div
            className="absolute z-40 bottom-full mb-1 right-0 w-[260px] rounded-xl border border-line bg-panel shadow-2xl p-3"
            role="menu"
            data-testid="cost-popover"
          >
            <div className="text-[10.5px] uppercase tracking-[0.06em] text-faint font-semibold mb-1.5">
              API 费用（本地统计）
            </div>
            <div className="flex flex-col gap-1.5 text-[12px]">
              <PopRow label="今日" value={fmt(stats.today?.cost, currency)} sub={`${stats.today?.calls ?? 0} 次调用`} />
              <PopRow label="本会话" value={fmt(stats.session?.cost, currency)} sub={`${stats.session?.calls ?? 0} 次调用`} />
              <PopRow label="累计" value={fmt(stats.total?.cost, currency)} sub={`${stats.total?.calls ?? 0} 次调用`} />
              {showBar && (
                <div className="pt-1.5 mt-0.5 border-t border-line">
                  <div className="flex items-baseline justify-between text-[11.5px]">
                    <span className="text-faint">
                      预算（{stats.budget.period === "day" ? "日" : stats.budget.period === "month" ? "月" : "累计"}）
                    </span>
                    <span className="text-ink tabular-nums">
                      {fmt(stats.budget.period_cost, currency)} / {fmt(stats.budget.amount, currency)} ·{" "}
                      {Math.round(pct as number)}%
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-line overflow-hidden">
                    <div
                      className="h-full transition-all"
                      style={{ width: `${Math.min(pct as number, 100)}%`, background: barColor }}
                    />
                  </div>
                </div>
              )}
              <div className="text-[11px] text-faint">
                当前：{stats.peak?.enabled ? (stats.peak.now ? "高峰计价" : `空闲计价（×${stats.peak.multiplier}）`) : "统一计价"}
              </div>
            </div>
            {onOpenCost && (
              <button
                className="mt-2.5 w-full text-[12px] px-2.5 py-1.5 rounded-lg bg-paper border border-line text-accent hover:border-lineStrong"
                onClick={() => {
                  setOpen(false);
                  onOpenCost();
                }}
              >
                打开费用看板 →
              </button>
            )}
          </div>
        </>
      )}
    </div>
  );
}

function PopRow({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="flex items-baseline justify-between leading-snug">
      <span className="text-faint">{label}</span>
      <span className="text-ink tabular-nums">
        {value}
        {sub && <span className="text-faint text-[10.5px] ml-1.5">{sub}</span>}
      </span>
    </div>
  );
}
