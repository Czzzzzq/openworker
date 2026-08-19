// API 费用看板（Settings ▸ 费用）：环形图框显示「已用占官方余额 %」、今日/会话/累计
// 统计、官方余额、峰谷计价。数据来自 /v1/cost/*（后端 costmeter）。

import { useCallback, useEffect, useState } from "react";
import {
  getCostBalance,
  getCostSettings,
  getCostStats,
  saveCostSettings,
  type CostBalance,
  type CostSettings,
  type CostStats,
} from "../api";

const CARD = "rounded-xl2 border border-line bg-panel";
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const BTN_BORDERED =
  "text-[12.5px] px-3 py-1.5 rounded-lg border border-line bg-paper hover:border-lineStrong shrink-0";
const INPUT =
  "px-2 py-1.5 rounded-lg border border-line bg-paper text-[12.5px] text-ink outline-none focus:border-accent";

/** 1_234.5 → "1,234.5"; tiny amounts keep 4 decimals so sub-cent spend is visible. */
function fmtMoney(n: number | undefined | null, currency?: string): string {
  const v = Number(n || 0);
  const sym = currency === "CNY" ? "¥" : currency === "USD" ? "$" : "";
  const s = v < 0.01 && v > 0 ? v.toFixed(4) : v.toFixed(2);
  const [i, d] = s.split(".");
  const grouped = i.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sym}${grouped}${d ? "." + d : ""}`;
}

// -- 环形图框：已用占官方余额 % ----------------------------------------------
function UsageGauge({ pct, spent, total }: { pct: number | null; spent: string; total: string }) {
  const R = 52;
  const C = 2 * Math.PI * R;
  const clamped = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const color =
    pct == null ? "var(--line)" : pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warn-ink)" : "var(--ok)";
  const text = pct == null ? "—" : `${Math.round(clamped)}%`;
  return (
    <div className="relative w-[120px] h-[120px] shrink-0" aria-label={`已用占充值金额 ${text}`}>
      <svg width="120" height="120" viewBox="0 0 120 120">
        <circle cx="60" cy="60" r={R} fill="none" stroke="var(--line)" strokeWidth="10" />
        <circle
          cx="60"
          cy="60"
          r={R}
          fill="none"
          stroke={color}
          strokeWidth="10"
          strokeLinecap="round"
          strokeDasharray={`${(clamped / 100) * C} ${C}`}
          transform="rotate(-90 60 60)"
          style={{ transition: "stroke-dasharray .4s ease" }}
        />
      </svg>
      <div className="absolute inset-0 grid place-items-center">
        <div className="text-center">
          <div className="text-[20px] font-semibold tabular-nums leading-none" style={{ color }}>
            {text}
          </div>
          <div className="text-[10px] text-faint mt-1">已用</div>
        </div>
      </div>
      <div className="absolute -bottom-1 left-1/2 -translate-x-1/2 text-[10.5px] text-faint tabular-nums whitespace-nowrap">
        {spent} / {total}
      </div>
    </div>
  );
}

// -- 主看板 -------------------------------------------------------------------
export function CostView({ sessionId }: { sessionId?: string }) {
  const [stats, setStats] = useState<CostStats | null>(null);
  const [balance, setBalance] = useState<CostBalance | null>(null);
  const [settings, setSettings] = useState<CostSettings["settings"] | null>(null);
  const [balanceMsg, setBalanceMsg] = useState<string | null>(null);

  const refresh = useCallback(() => {
    getCostStats(sessionId).then(setStats).catch(() => setStats(null));
    getCostSettings().then((s) => setSettings(s.settings)).catch(() => undefined);
  }, [sessionId]);

  useEffect(() => {
    refresh();
    const t = window.setInterval(refresh, 30_000);
    return () => window.clearInterval(t);
  }, [refresh]);

  const refreshBalance = useCallback(() => {
    setBalanceMsg(null);
    getCostBalance().then((b) => {
      setBalance(b);
      if (!b.ok && b.error) setBalanceMsg(b.error);
    });
  }, []);

  useEffect(() => {
    refreshBalance();
  }, [refreshBalance]);

  const saveOffPeak = async (patch: Partial<CostSettings["settings"]["off_peak"]>) => {
    if (!settings) return;
    await saveCostSettings({ off_peak: { ...settings.off_peak, ...patch } });
    refresh();
  };

  const spent = stats?.total?.cost ?? 0;
  const officialTotal = balance?.ok && balance.balances?.length ? (balance.balances[0].total ?? 0) : 0;
  const balanceCurrency = balance?.ok && balance.balances?.length ? balance.balances[0].currency : "CNY";
  // 总金额 = 累计充值总额（用户填写）；未设置时回退到官方总余额。
  const rechargeTotal = stats?.recharge_total && stats.recharge_total > 0 ? stats.recharge_total : officialTotal;
  // 已用金额 = 累计充值总额 − 官方总余额（官方剩余），图框 = 已用占充值总额 %。
  const usedAmount = Math.max(0, rechargeTotal - officialTotal);
  const usedPct = rechargeTotal > 0 ? (usedAmount / rechargeTotal) * 100 : null;
  const off = settings?.off_peak;

  const saveRechargeTotal = async (v: number) => {
    await saveCostSettings({ recharge_total: v });
    refresh();
  };

  return (
    <div className="space-y-4">
      {/* 环形图框（已用占充值总额）+ 统计 */}
      <div className={CARD + " p-4 flex items-center gap-5"}>
        <UsageGauge
          pct={usedPct}
          spent={fmtMoney(usedAmount, balanceCurrency)}
          total={fmtMoney(rechargeTotal, balanceCurrency)}
        />
        <div className="flex-1 min-w-0">
          <div className={SEC_H + " mb-1.5"}>已用占充值总额</div>
          <div className="text-[13px] text-ink tabular-nums">
            已用 {fmtMoney(usedAmount, balanceCurrency)} / 充值 {fmtMoney(rechargeTotal, balanceCurrency)}
          </div>
          <div className={FIELD_HELP}>
            已用金额 = 累计充值总额 − 官方总余额（官方剩余）；累计充值总额可在下方「用量信息」里填写。
          </div>
          {!balance?.ok && !balanceMsg && <div className="text-[12.5px] text-muted mt-1">余额查询中…</div>}
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 shrink-0 text-right">
          <Stat label="今日费用" value={fmtMoney(stats?.today.cost, balanceCurrency)} sub={`${stats?.today.calls ?? 0} 次调用`} />
          {sessionId && (
            <Stat label="本会话" value={fmtMoney(stats?.session?.cost, balanceCurrency)} sub={`${stats?.session?.calls ?? 0} 次调用`} />
          )}
          <Stat label="累计费用" value={fmtMoney(stats?.total.cost, balanceCurrency)} sub={`${stats?.total.calls ?? 0} 次调用`} />
          <Stat
            label="当前计价"
            value={off?.enabled ? (stats?.peak.now ? "高峰" : "空闲") : "统一"}
            sub={off?.enabled ? `×${off.multiplier}（空闲）` : "峰谷未启用"}
            accent={off?.enabled && stats?.peak.now}
          />
        </div>
      </div>

      {/* 用量信息（充值余额 + 累计消费金额） */}
      <div className={CARD + " p-4"}>
        <div className="flex items-center justify-between">
          <div className={SEC_H}>用量信息</div>
          <button className={BTN_BORDERED} onClick={refreshBalance}>
            刷新
          </button>
        </div>
        {balanceMsg && <div className="text-[12.5px] text-muted mt-2">{balanceMsg}</div>}
        {balance?.ok ? (
          <div className="mt-2.5 grid grid-cols-2 sm:grid-cols-3 gap-3">
            <InfoBox label="累计消费金额" value={fmtMoney(spent, balanceCurrency)} />
            {balance.balances?.[0] && (
              <InfoBox
                label="官方总余额"
                value={fmtMoney(balance.balances[0].total, balanceCurrency)}
                sub={`赠金 ${fmtMoney(balance.balances[0].granted, balanceCurrency)}`}
              />
            )}
            <InfoBox label="已用（充值−余额）" value={fmtMoney(usedAmount, balanceCurrency)} />
          </div>
        ) : (
          !balanceMsg && <div className="text-[12.5px] text-muted mt-2">查询中…</div>
        )}
        {/* 累计充值总额（图框分母）：默认取官方总余额，可手动改成实际累计充值 */}
        <div className="mt-3 flex items-center gap-2.5">
          <span className="text-[12.5px] text-muted">累计充值总额</span>
          <input
            className={INPUT + " w-28 text-right"}
            type="number"
            min={0}
            step={1}
            data-testid="cost-recharge-total"
            value={rechargeTotal > 0 ? Math.round(rechargeTotal * 100) / 100 : ""}
            placeholder={officialTotal > 0 ? String(Math.round(officialTotal * 100) / 100) : "0"}
            onChange={(e) => saveRechargeTotal(Number(e.target.value) || 0)}
          />
          <span className="text-[12px] text-faint">{balanceCurrency}</span>
        </div>
        <div className={FIELD_HELP}>
          官方总余额与赠金来自 DeepSeek 官方 /user/balance 接口（使用 设置 ▸ 模型 中配置的 DeepSeek
          密钥）；累计消费金额由本机费用记录统计。「累计充值总额」用于图框分母——官方接口不提供累计充值，
          请手动填写（例如 ¥40）。
        </div>
      </div>

      {/* 峰谷计价 */}
      <div className={CARD + " p-4"}>
        <div className={SEC_H + " mb-2"}>峰谷计价</div>
        {off && (
          <div className="space-y-3">
            <label className="flex items-center gap-2.5">
              <input
                type="checkbox"
                className="mt-0.5"
                checked={off.enabled}
                data-testid="cost-offpeak-toggle"
                onChange={(e) => saveOffPeak({ enabled: e.target.checked })}
              />
              <span className="text-[13px] text-ink">启用峰谷计价（DeepSeek 官方 2026-08-17 起实行）</span>
            </label>
            <div className="flex items-center gap-3 flex-wrap">
              <span className="text-[12.5px] text-muted">高峰时段</span>
              {off.windows.map((w, i) => (
                <span key={i} className="inline-flex items-center gap-1.5">
                  <input
                    className={INPUT + " w-20 text-center"}
                    value={w[0]}
                    data-testid={`cost-peak-start-${i}`}
                    onChange={(e) => {
                      const next = off.windows.map((x, j) => (j === i ? [e.target.value, x[1]] : x));
                      saveOffPeak({ windows: next });
                    }}
                  />
                  <span className="text-faint">–</span>
                  <input
                    className={INPUT + " w-20 text-center"}
                    value={w[1]}
                    data-testid={`cost-peak-end-${i}`}
                    onChange={(e) => {
                      const next = off.windows.map((x, j) => (j === i ? [x[0], e.target.value] : x));
                      saveOffPeak({ windows: next });
                    }}
                  />
                </span>
              ))}
              <label className="flex items-center gap-2">
                <span className="text-[12.5px] text-muted">时区</span>
                <input
                  className={INPUT + " w-36"}
                  value={off.tz}
                  data-testid="cost-offpeak-tz"
                  onChange={(e) => saveOffPeak({ tz: e.target.value })}
                />
              </label>
              <label className="flex items-center gap-2">
                <span className="text-[12.5px] text-muted">空闲折扣</span>
                <input
                  className={INPUT + " w-20 text-center"}
                  type="number"
                  min={0.1}
                  max={1}
                  step={0.05}
                  value={off.multiplier}
                  data-testid="cost-offpeak-multiplier"
                  onChange={(e) => saveOffPeak({ multiplier: Number(e.target.value) || 0.5 })}
                />
                <span className="text-[12px] text-faint">×（高峰价的倍数）</span>
              </label>
            </div>
            <div className={FIELD_HELP}>
              高峰时段内按表内价格计费；其余时间按 高峰 × 折扣 计费。DeepSeek 官方默认高峰为每日
              9:00 与 14:00（北京时间）。时段格式 HH:MM，跨零点时段（如 22:00–02:00）也支持。
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function Stat({ label, value, sub, accent }: { label: string; value: string; sub?: string; accent?: boolean }) {
  return (
    <div>
      <div className="text-[10.5px] uppercase tracking-wide text-faint font-semibold">{label}</div>
      <div className={"text-[16px] font-semibold tabular-nums mt-0.5 " + (accent ? "text-warnInk" : "text-ink")}>
        {value}
      </div>
      {sub && <div className="text-[11px] text-faint tabular-nums">{sub}</div>}
    </div>
  );
}

function InfoBox({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-lg border border-line bg-paper p-3">
      <div className="text-[11px] uppercase tracking-wide text-faint font-semibold">{label}</div>
      <div className="text-[18px] font-semibold tabular-nums mt-1">{value}</div>
      {sub && <div className="text-[11.5px] text-muted mt-1 tabular-nums">{sub}</div>}
    </div>
  );
}
