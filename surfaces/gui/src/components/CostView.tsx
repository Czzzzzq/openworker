// API 费用统计看板（Settings ▸ 费用）：预算图框、今日/会话/累计费用、官方余额、
// 历史看板、峰谷计价与官方价格一键同步。数据来自 /v1/cost/*（后端 costmeter）。

import { useCallback, useEffect, useState } from "react";
import {
  getCostBalance,
  getCostSettings,
  getCostStats,
  saveCostSettings,
  syncCostPrices,
  type CostBalance,
  type CostSettings,
  type CostStats,
} from "../api";

const CARD = "rounded-xl2 border border-line bg-panel";
const SEC_H = "text-[11px] uppercase tracking-[0.05em] text-faint font-semibold";
const FIELD_HELP = "text-[12px] text-muted mt-1.5 leading-relaxed";
const BTN_ACCENT = "text-[12.5px] px-3 py-1.5 rounded-lg bg-accent text-white shrink-0 disabled:opacity-50";
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

const shortModel = (m: string) => (m.includes(":") ? m.split(":").slice(1).join(":") : m);

// -- 预算图框（环形进度）-----------------------------------------------------
function BudgetGauge({ pct }: { pct: number | null }) {
  const R = 52;
  const C = 2 * Math.PI * R;
  const clamped = pct == null ? 0 : Math.max(0, Math.min(100, pct));
  const color =
    pct == null ? "var(--line)" : pct >= 90 ? "var(--danger)" : pct >= 70 ? "var(--warn-ink)" : "var(--ok)";
  const text = pct == null ? "—" : `${Math.round(clamped)}%`;
  return (
    <div className="relative w-[120px] h-[120px] shrink-0" aria-label={`预算已用 ${text}`}>
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
    </div>
  );
}

// -- 历史看板：近 14 天纯 CSS 柱状图 ------------------------------------------
function HistoryChart({ byDay }: { byDay: CostStats["by_day"] }) {
  const days = byDay.slice(-14);
  if (days.length === 0) {
    return <div className="text-[12.5px] text-muted">暂无费用记录 — 完成一次对话后这里会出现每日柱状图。</div>;
  }
  const max = Math.max(...days.map((d) => d.cost), 1e-9);
  return (
    <div>
      <div className="flex items-end gap-[6px] h-28">
        {days.map((d) => (
          <div key={d.day} className="flex-1 flex flex-col items-center gap-1 min-w-0 group">
            <div className="w-full flex justify-center">
              <span className="text-[10px] text-muted tabular-nums opacity-0 group-hover:opacity-100 transition-opacity">
                {fmtMoney(d.cost)}
              </span>
            </div>
            <div
              className="w-full rounded-t bg-accent/80 group-hover:bg-accent transition-colors"
              style={{ height: `${Math.max((d.cost / max) * 76, 3)}px` }}
              title={`${d.day} · ${fmtMoney(d.cost)} · ${d.calls} 次调用`}
            />
            <div className="text-[10px] text-faint tabular-nums truncate">{d.day.slice(5)}</div>
          </div>
        ))}
      </div>
      <div className={FIELD_HELP}>近 {days.length} 天费用（按服务器本地日期分组）。</div>
    </div>
  );
}

// -- 主看板 -------------------------------------------------------------------
export function CostView({ sessionId }: { sessionId?: string }) {
  const [stats, setStats] = useState<CostStats | null>(null);
  const [balance, setBalance] = useState<CostBalance | null>(null);
  const [settings, setSettings] = useState<CostSettings["settings"] | null>(null);
  const [syncing, setSyncing] = useState(false);
  const [syncMsg, setSyncMsg] = useState<string | null>(null);
  const [balanceMsg, setBalanceMsg] = useState<string | null>(null);
  const [savedMsg, setSavedMsg] = useState<string | null>(null);

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

  const doSync = async () => {
    setSyncing(true);
    setSyncMsg(null);
    try {
      const r = await syncCostPrices();
      setSyncMsg(r.ok ? r.message || "同步完成。" : r.message || "同步失败。");
      refresh();
    } catch {
      setSyncMsg("同步失败：无法连接服务器。");
    } finally {
      setSyncing(false);
    }
  };

  const saveBudget = async (patch: Partial<CostSettings["settings"]["budget"]>) => {
    if (!settings) return;
    setSavedMsg(null);
    await saveCostSettings({ budget: { ...settings.budget, ...patch } });
    setSavedMsg("预算已保存。");
    refresh();
  };

  const saveOffPeak = async (patch: Partial<CostSettings["settings"]["off_peak"]>) => {
    if (!settings) return;
    setSavedMsg(null);
    await saveCostSettings({ off_peak: { ...settings.off_peak, ...patch } });
    setSavedMsg("峰谷计价设置已保存。");
    refresh();
  };

  const b = stats?.budget;
  const currency = settings?.budget.currency || b?.currency || "CNY";
  const off = settings?.off_peak;

  return (
    <div className="space-y-4">
      {/* 预算 + 今日/会话/累计 */}
      <div className={CARD + " p-4 flex items-center gap-5"}>
        <BudgetGauge pct={b?.used_pct ?? null} />
        <div className="flex-1 min-w-0">
          <div className={SEC_H + " mb-2"}>预算（{b?.period === "day" ? "日" : b?.period === "month" ? "月" : "累计"}）</div>
          <div className="flex items-baseline gap-2.5">
            <input
              className={INPUT + " w-24 text-right"}
              type="number"
              min={0}
              step={1}
              value={b?.amount ?? 0}
              data-testid="cost-budget-amount"
              onChange={(e) => saveBudget({ amount: Number(e.target.value) || 0 })}
            />
            <select
              className={INPUT}
              value={currency}
              data-testid="cost-budget-currency"
              onChange={(e) => saveBudget({ currency: e.target.value })}
            >
              <option value="CNY">¥ CNY</option>
              <option value="USD">$ USD</option>
            </select>
            <select
              className={INPUT}
              value={b?.period ?? "month"}
              data-testid="cost-budget-period"
              onChange={(e) => saveBudget({ period: e.target.value as any })}
            >
              <option value="day">按日</option>
              <option value="month">按月</option>
              <option value="total">累计</option>
            </select>
          </div>
          <div className="mt-1.5 text-[12.5px] text-muted tabular-nums">
            已用 <span className="text-ink font-medium">{fmtMoney(b?.period_cost, currency)}</span>
            {b?.used_pct != null && <span> · {b.used_pct}%</span>}
          </div>
          <div className={FIELD_HELP}>
            达到预算后看板会标红提醒；费用统计不影响对话运行，仅为本地记录。
          </div>
        </div>
        <div className="grid grid-cols-2 gap-x-6 gap-y-2 shrink-0 text-right">
          <Stat label="今日费用" value={fmtMoney(stats?.today.cost, currency)} sub={`${stats?.today.calls ?? 0} 次调用`} />
          {sessionId && (
            <Stat label="本会话" value={fmtMoney(stats?.session?.cost, currency)} sub={`${stats?.session?.calls ?? 0} 次调用`} />
          )}
          <Stat label="累计费用" value={fmtMoney(stats?.total.cost, currency)} sub={`${stats?.total.calls ?? 0} 次调用`} />
          <Stat
            label="当前计价"
            value={off?.enabled ? (stats?.peak.now ? "高峰" : "空闲") : "统一"}
            sub={off?.enabled ? `×${off.multiplier}（空闲）` : "峰谷未启用"}
            accent={off?.enabled && stats?.peak.now}
          />
        </div>
      </div>

      {/* 官方余额 */}
      <div className={CARD + " p-4"}>
        <div className="flex items-center justify-between">
          <div className={SEC_H}>官方余额（DeepSeek）</div>
          <button className={BTN_BORDERED} onClick={refreshBalance} disabled={!!balance?.ok && false}>
            刷新
          </button>
        </div>
        {balanceMsg && <div className="text-[12.5px] text-muted mt-2">{balanceMsg}</div>}
        {balance?.ok ? (
          <div className="mt-2.5 grid grid-cols-1 sm:grid-cols-3 gap-3">
            {(balance.balances || []).map((x) => (
              <div key={x.currency} className="rounded-lg border border-line bg-paper p-3">
                <div className="text-[11px] uppercase tracking-wide text-faint font-semibold">{x.currency} 总余额</div>
                <div className="text-[18px] font-semibold tabular-nums mt-1">{fmtMoney(x.total, x.currency)}</div>
                <div className="text-[11.5px] text-muted mt-1 tabular-nums">
                  赠金 {fmtMoney(x.granted, x.currency)} · 充值 {fmtMoney(x.topped_up, x.currency)}
                </div>
              </div>
            ))}
          </div>
        ) : (
          !balanceMsg && <div className="text-[12.5px] text-muted mt-2">查询中…</div>
        )}
        <div className={FIELD_HELP}>
          读取的是 DeepSeek 官方 /user/balance 接口（使用 设置 ▸ 模型 中配置的 DeepSeek 密钥）。
          其他提供商没有公开余额端点，故不显示。
        </div>
      </div>

      {/* 历史看板 */}
      <div className={CARD + " p-4"}>
        <div className={SEC_H + " mb-3"}>历史看板</div>
        <HistoryChart byDay={stats?.by_day || []} />
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

      {/* 价格表 + 一键同步 */}
      <div className={CARD + " p-4"}>
        <div className="flex items-center justify-between">
          <div className={SEC_H}>模型价格（每百万 tokens）</div>
          <button className={BTN_ACCENT} onClick={doSync} disabled={syncing} data-testid="cost-sync-prices">
            {syncing ? "同步中…" : "一键同步官方价格"}
          </button>
        </div>
        {syncMsg && <div className="text-[12.5px] text-muted mt-2">{syncMsg}</div>}
        {settings && (
          <div className="mt-3 overflow-x-auto">
            <table className="w-full text-[12px]">
              <thead>
                <tr className="text-left text-faint text-[10.5px] uppercase tracking-wide">
                  <th className="pb-1.5 pr-3 font-semibold">模型</th>
                  <th className="pb-1.5 pr-3 font-semibold text-right">输入</th>
                  <th className="pb-1.5 pr-3 font-semibold text-right">缓存读</th>
                  <th className="pb-1.5 pr-3 font-semibold text-right">缓存写</th>
                  <th className="pb-1.5 pr-3 font-semibold text-right">输出</th>
                  <th className="pb-1.5 font-semibold text-right">来源</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {Object.entries(settings.prices).map(([id, p]) => (
                  <tr key={id} className="text-muted">
                    <td className="py-1.5 pr-3 text-ink font-medium" title={id}>
                      {shortModel(id)}
                    </td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtMoney(p.input, p.currency)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtMoney(p.cache_read, p.currency)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtMoney(p.cache_write, p.currency)}</td>
                    <td className="py-1.5 pr-3 text-right tabular-nums">{fmtMoney(p.output, p.currency)}</td>
                    <td className="py-1.5 text-right">
                      <span
                        className={
                          "text-[10.5px] px-1.5 py-0.5 rounded-full " +
                          (p.source === "default"
                            ? "bg-paper border border-line text-faint"
                            : p.source === "synced"
                              ? "bg-tealSoft text-tealInk"
                              : "bg-warnSoft text-warnInk")
                        }
                      >
                        {p.source === "default" ? "默认" : p.source === "synced" ? "官方" : "自定义"}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        <div className={FIELD_HELP}>
          同步优先抓取 DeepSeek 官方定价页（更新 deepseek:* 价格与峰谷时段），随后用
          OpenRouter 目录补齐其余厂商的官方标价。未知模型按其 provider 的默认价估算，
          标记为“默认”的价格可在同步后替换。
        </div>
      </div>

      {savedMsg && <div className="text-[12.5px] text-ok">{savedMsg}</div>}
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
