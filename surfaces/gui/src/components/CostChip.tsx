// Composer 中的预算条：淡蓝迷你进度条 = 已用占官方余额 %，旁边显示今日费用。
// 点击展开今日/会话/累计、已用占余额明细，并可跳转到 Settings ▸ 费用 看板。

import { useEffect, useState } from "react";
import { getCostBalance, getCostStats, type CostBalance, type CostStats } from "../api";

const fmt = (n: number | undefined | null, currency?: string): string => {
  const v = Number(n || 0);
  const sym = currency === "CNY" ? "¥" : currency === "USD" ? "$" : "";
  const s = v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2);
  return `${sym}${s}`;
};

// 淡蓝（用户指定）：进度条与其余 UI 的 accent 区分开。
const BAR_COLOR = "#8ab8e6";

export function CostChip({
  sessionId,
  onOpenCost,
}: {
  sessionId?: string;
  onOpenCost?: () => void;
}) {
  const [stats, setStats] = useState<CostStats | null>(null);
  const [balance, setBalance] = useState<CostBalance | null>(null);
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
    const loadBalance = () =>
      getCostBalance()
        .then((b) => {
          if (active) setBalance(b);
        })
        .catch(() => undefined);
    load();
    loadBalance();
    const t = window.setInterval(() => {
      load();
      loadBalance();
    }, 30_000);
    return () => {
      active = false;
      window.clearInterval(t);
    };
  }, [sessionId]);

  if (!sessionId || !stats?.total || stats.total.calls === 0) return null;

  const currency = stats.budget?.currency || "CNY";
  const officialTotal =
    balance?.ok && balance.balances?.length ? (balance.balances[0].total ?? 0) : 0;
  // 总金额 = 累计充值总额（用户填写）；未填写时回退到官方总余额。
  // 已用金额 = 充值总额 − 官方总余额；进度条 = 已用占充值总额 %。
  const totalAmount = stats.recharge_total && stats.recharge_total > 0 ? stats.recharge_total : officialTotal;
  const usedAmount = Math.max(0, totalAmount - officialTotal);
  const pct = totalAmount > 0 ? (usedAmount / totalAmount) * 100 : null;
  const showBar = pct != null;

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
            ? `今日 ${fmt(stats.today?.cost, currency)} · 已用占充值总额 ${Math.round(pct as number)}%`
            : `今日 ${fmt(stats.today?.cost, currency)}`
        }
        data-testid="cost-chip"
      >
        <span className="w-10 h-1.5 rounded-full bg-line overflow-hidden" aria-hidden="true">
          <span
            className="block h-full transition-all"
            style={{
              width: showBar ? `${Math.max(pct as number, 4)}%` : "0%",
              background: BAR_COLOR,
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
                    <span className="text-faint">已用占充值总额</span>
                    <span className="text-ink tabular-nums">
                      {fmt(usedAmount, currency)} / {fmt(totalAmount, currency)} · {Math.round(pct as number)}%
                    </span>
                  </div>
                  <div className="mt-1 h-1.5 rounded-full bg-line overflow-hidden">
                    <div
                      className="h-full transition-all"
                      style={{ width: `${Math.min(pct as number, 100)}%`, background: BAR_COLOR }}
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
