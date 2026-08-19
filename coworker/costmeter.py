"""API 费用统计（cost meter）—— 会话/当日费用、预算、官方余额、历史看板、峰谷计价。

How it works
------------
The engine never knows about money: every provider call funnels through a single
`ProviderClient`, and the manager wraps that client with `MeteredProvider`. Each
`AssistantTurn` carries normalized `TokenUsage` ({input, output, cache_read,
cache_write}); the wrapper records it here with the session id + model id.

Costs are computed from a local price table (per provider:model, per 1M tokens).
DeepSeek V4 switched to tiered "peak/off-peak" (峰谷) pricing on 2026-08-17: the
table stores PEAK prices and an off-peak multiplier (off-peak = half of peak).
A call timestamp inside a configured peak window is billed at peak; otherwise the
multiplier applies. Everything is persisted as JSONL under the state dir so the
history dashboard survives restarts.

`official balance` (官方余额) and `one-click price sync` (官方价格一键同步) need
outbound network at runtime and are best-effort: a missing key, blocked network,
or an unparseable pricing page never breaks the meter itself.
"""

from __future__ import annotations

import json
import os
import re
import threading
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, time as dtime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional
from zoneinfo import ZoneInfo

# ---------------------------------------------------------------------------
# Price table — per provider:model, PEAK prices in ¥/$ per 1M tokens.
# `source` marks where a price came from: "default" (bundled), "synced" (fetched
# from an official source), or "custom" (edited in the GUI).
# ---------------------------------------------------------------------------

DEFAULT_PRICES: dict[str, dict[str, Any]] = {
    # DeepSeek V4 (峰谷定价，2026-08-17 生效；表内为高峰价，空闲 = 高峰 × 0.5):
    "deepseek:deepseek-v4-flash": {
        "input": 3.0,
        "cache_read": 0.10,
        "cache_write": 3.0,
        "output": 9.0,
        "currency": "CNY",
    },
    "deepseek:deepseek-v4-pro": {
        "input": 9.0,
        "cache_read": 0.30,
        "cache_write": 9.0,
        "output": 27.0,
        "currency": "CNY",
    },
    # The rest are USD estimates bundled so the meter never silently reports 0.
    # Run the one-click sync (OpenRouter catalog) to replace them with the
    # providers' own current listings.
    "openai:gpt-5.6-sol": {"input": 1.25, "cache_read": 0.125, "cache_write": 1.25, "output": 10.0, "currency": "USD"},
    "anthropic:claude-fable-5": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.0, "output": 15.0, "currency": "USD"},
    "anthropic:claude-opus-4-8": {"input": 5.0, "cache_read": 0.50, "cache_write": 5.0, "output": 25.0, "currency": "USD"},
    "anthropic:claude-sonnet-4-6": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.0, "output": 15.0, "currency": "USD"},
    "anthropic:claude-haiku-4-5": {"input": 0.80, "cache_read": 0.08, "cache_write": 0.80, "output": 4.0, "currency": "USD"},
    "gemini:gemini-3.1-pro-preview": {"input": 2.50, "cache_read": 0.25, "cache_write": 2.50, "output": 15.0, "currency": "USD"},
    "gemini:gemini-3.6-flash": {"input": 0.30, "cache_read": 0.03, "cache_write": 0.30, "output": 2.50, "currency": "USD"},
}

# Provider-level fallbacks when an exact provider:model entry is missing.
PROVIDER_DEFAULT_PRICES: dict[str, dict[str, Any]] = {
    "openai": {"input": 1.25, "cache_read": 0.125, "cache_write": 1.25, "output": 10.0, "currency": "USD"},
    "anthropic": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.0, "output": 15.0, "currency": "USD"},
    "gemini": {"input": 1.25, "cache_read": 0.125, "cache_write": 1.25, "output": 5.0, "currency": "USD"},
    "deepseek": {"input": 3.0, "cache_read": 0.10, "cache_write": 3.0, "output": 9.0, "currency": "CNY"},
    "zai": {"input": 2.0, "cache_read": 0.20, "cache_write": 2.0, "output": 8.0, "currency": "CNY"},
    "kimi": {"input": 4.0, "cache_read": 0.40, "cache_write": 4.0, "output": 16.0, "currency": "CNY"},
    "qwen": {"input": 1.0, "cache_read": 0.10, "cache_write": 1.0, "output": 4.0, "currency": "CNY"},
    "minimax": {"input": 1.0, "cache_read": 0.10, "cache_write": 1.0, "output": 5.0, "currency": "CNY"},
    "xai": {"input": 3.0, "cache_read": 0.30, "cache_write": 3.0, "output": 15.0, "currency": "USD"},
    "mistral": {"input": 0.15, "cache_read": 0.10, "cache_write": 0.15, "output": 0.60, "currency": "USD"},
}

# OpenRouter slugs → our provider:model keys, so the OpenRouter catalog sync can
# match models sold through resellers (together:/fireworks:/openrouter:).
_OPENROUTER_MAP: dict[str, str] = {
    "deepseek/deepseek-v4-flash": "deepseek:deepseek-v4-flash",
    "deepseek/deepseek-v4-pro": "deepseek:deepseek-v4-pro",
}

DEEPSEEK_BALANCE_URL = "https://api.deepseek.com/user/balance"
DEEPSEEK_PRICING_URLS = (
    "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/",
    "https://api-docs.deepseek.com/quick_start/pricing/",
)
OPENROUTER_MODELS_URL = "https://openrouter.ai/api/v1/models"


# ---------------------------------------------------------------------------
# Pure price math (unit-testable, no I/O)
# ---------------------------------------------------------------------------

@dataclass
class PriceEntry:
    input: float
    cache_read: float
    cache_write: float
    output: float
    currency: str = "USD"
    source: str = "default"
    updated_at: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["updated_at"] = self.updated_at
        return d


def _fmt_time(hhmm: str) -> dtime:
    """"09:30" → time(9, 30). Tolerates "9:30" and "0930"."""
    hhmm = str(hhmm).strip().replace(":", "")
    if len(hhmm) == 3:
        hhmm = "0" + hhmm
    if len(hhmm) != 4 or not hhmm.isdigit():
        raise ValueError(f"bad time: {hhmm!r}")
    return dtime(int(hhmm[:2]), int(hhmm[2:]))


def in_peak_window(
    ts: float,
    windows: list[list[str]],
    tz: str,
) -> bool:
    """True when `ts` (epoch seconds) falls inside any [start, end) peak window
    in the given IANA timezone. Windows may wrap midnight ("22:00" → "02:00")."""
    if not windows:
        return False
    try:
        zone = ZoneInfo(tz)
    except Exception:
        zone = ZoneInfo("UTC")
    local = datetime.fromtimestamp(ts, zone).time()
    for pair in windows:
        try:
            start = _fmt_time(pair[0])
            end = _fmt_time(pair[1])
        except (ValueError, IndexError):
            continue
        if start <= end:
            if start <= local < end:
                return True
        else:  # wraps midnight
            if local >= start or local < end:
                return True
    return False


def compute_cost(
    model: str,
    usage: dict[str, int],
    ts: float,
    prices: dict[str, PriceEntry],
    *,
    off_peak: dict[str, Any],
) -> tuple[float, str, bool, bool]:
    """Return (cost, currency, was_peak, priced).

    `prices` maps "provider:model" → PriceEntry (already merged with provider
    defaults). `off_peak` = {"enabled", "windows", "tz", "multiplier"}; the
    multiplier is applied when the timestamp is NOT inside a peak window and the
    entry is billed in CNY (peak-tier pricing is a DeepSeek/CNY concept).
    """
    entry = prices.get(model)
    if entry is None:
        # Provider-level fallback ("*openai" etc.) so unknown models are still
        # priced at the provider's default instead of silently counting as 0.
        provider = model.split(":", 1)[0] if ":" in model else ""
        entry = prices.get(f"*{provider}")
        if entry is None:
            return 0.0, "USD", False, False
    input_t = int(usage.get("input", 0) or 0)
    output_t = int(usage.get("output", 0) or 0)
    cache_read = int(usage.get("cache_read", 0) or 0)
    cache_write = int(usage.get("cache_write", 0) or 0)

    peak = True
    if (
        entry.currency == "CNY"
        and off_peak.get("enabled", False)
        and off_peak.get("windows")
    ):
        peak = in_peak_window(
            ts,
            off_peak.get("windows", []),
            str(off_peak.get("tz", "Asia/Shanghai")),
        )
        if not peak:
            mult = float(off_peak.get("multiplier", 0.5) or 1.0)
            factor = mult
        else:
            factor = 1.0
    else:
        factor = 1.0

    cost = (
        (input_t * entry.input)
        + (cache_read * entry.cache_read)
        + (cache_write * entry.cache_write)
        + (output_t * entry.output)
    ) * factor / 1_000_000.0
    return round(cost, 8), entry.currency, peak, True


# ---------------------------------------------------------------------------
# Persisted record + the meter itself
# ---------------------------------------------------------------------------

@dataclass
class UsageRecord:
    ts: float
    session_id: str
    model: str
    input: int
    output: int
    cache_read: int
    cache_write: int
    cost: float
    currency: str
    peak: bool
    priced: bool

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


_DEFAULT_OFF_PEAK: dict[str, Any] = {
    "enabled": True,
    "tz": "Asia/Shanghai",
    # DeepSeek V4 peak windows: daily 09:00 and 14:00 (Beijing time). Durations
    # are editable in Settings ▸ 费用 — sync may update them from the official page.
    "windows": [["09:00", "11:00"], ["14:00", "16:00"]],
    "multiplier": 0.5,
}

_DEFAULT_SETTINGS: dict[str, Any] = {
    # Budget in the cost currency. period: "day" | "month" | "total" (all-time).
    "budget": {"amount": 0.0, "currency": "CNY", "period": "month"},
    "off_peak": _DEFAULT_OFF_PEAK,
}


class CostMeter:
    """Collects per-call usage, prices it, and serves the dashboard aggregates."""

    def __init__(
        self,
        data_dir: str | Path,
        secrets: Any = None,
        *,
        fetch: Optional[Callable[..., Any]] = None,
    ) -> None:
        self._dir = Path(data_dir).expanduser()
        self._dir.mkdir(parents=True, exist_ok=True)
        self._records_path = self._dir / "costmeter.jsonl"
        self._settings_path = self._dir / "costmeter-settings.json"
        self._secrets = secrets
        self._fetch = fetch or urllib.request.urlopen
        self._lock = threading.Lock()

        self._settings: dict[str, Any] = json.loads(
            json.dumps(_DEFAULT_SETTINGS)
        )
        self._prices: dict[str, PriceEntry] = {}
        self._records: list[UsageRecord] = []
        self._load()

    # -- persistence ----------------------------------------------------------
    def _load(self) -> None:
        if self._settings_path.is_file():
            try:
                loaded = json.loads(self._settings_path.read_text(encoding="utf-8"))
                if isinstance(loaded, dict):
                    self._settings = self._merge_settings(loaded)
            except (OSError, json.JSONDecodeError):
                pass
        merged_prices: dict[str, PriceEntry] = {}
        for key, spec in DEFAULT_PRICES.items():
            merged_prices[key] = self._entry_from_spec(key, spec, "default")
        # User-saved price overrides (source custom/synced) win over defaults.
        saved = self._settings.pop("prices", None)
        if isinstance(saved, dict):
            for key, spec in saved.items():
                if isinstance(spec, dict):
                    merged_prices[key] = self._entry_from_spec(key, spec, "custom")
        self._prices = merged_prices

        if self._records_path.is_file():
            try:
                for line in self._records_path.read_text(encoding="utf-8").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        self._records.append(UsageRecord(**json.loads(line)))
                    except (TypeError, json.JSONDecodeError):
                        continue
            except OSError:
                pass

    def _merge_settings(self, loaded: dict[str, Any]) -> dict[str, Any]:
        out = json.loads(json.dumps(_DEFAULT_SETTINGS))
        for key in ("budget", "off_peak"):
            if isinstance(loaded.get(key), dict):
                out[key] = {**out[key], **loaded[key]}
        if isinstance(loaded.get("prices"), dict):
            out["prices"] = loaded["prices"]  # popped back out in _load
        for key in out:
            if key not in ("budget", "off_peak", "prices") and key in loaded:
                out[key] = loaded[key]
        return out

    def _entry_from_spec(
        self, key: str, spec: dict[str, Any], source: str
    ) -> PriceEntry:
        get = lambda k, d: float(spec.get(k, d) or 0.0)
        return PriceEntry(
            input=get("input", 0.0),
            cache_read=get("cache_read", 0.0),
            cache_write=get("cache_write", get("input", 0.0)),
            output=get("output", 0.0),
            currency=str(spec.get("currency", "USD")),
            source=str(spec.get("source", source)),
            updated_at=spec.get("updated_at"),
        )

    def _save_settings(self) -> None:
        payload = json.loads(json.dumps(self._settings))
        payload["prices"] = {
            k: v.as_dict() for k, v in self._prices.items() if v.source != "default"
        }
        self._settings_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8"
        )

    def _append_record(self, rec: UsageRecord) -> None:
        with self._lock:
            self._records.append(rec)
            try:
                with open(self._records_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec.as_dict(), ensure_ascii=False) + "\n")
            except OSError:
                pass

    # -- recording ------------------------------------------------------------
    def record(
        self,
        session_id: str,
        model: str,
        usage: dict[str, int],
        ts: Optional[float] = None,
    ) -> None:
        if not isinstance(usage, dict):
            return
        ts = ts if ts is not None else datetime.now().timestamp()
        model = str(model or "")
        prices = self._price_map()
        cost, currency, peak, priced = compute_cost(
            model, usage, ts, prices, off_peak=self._settings["off_peak"]
        )
        rec = UsageRecord(
            ts=ts,
            session_id=str(session_id or ""),
            model=model,
            input=int(usage.get("input", 0) or 0),
            output=int(usage.get("output", 0) or 0),
            cache_read=int(usage.get("cache_read", 0) or 0),
            cache_write=int(usage.get("cache_write", 0) or 0),
            cost=cost,
            currency=currency,
            peak=peak,
            priced=priced,
        )
        self._append_record(rec)

    def _resolve_price(self, model: str) -> Optional[PriceEntry]:
        return self._prices.get(model)

    def _price_map(self) -> dict[str, PriceEntry]:
        """Exact entries merged with provider-level fallbacks (never mutates)."""
        out: dict[str, PriceEntry] = {}
        for key, entry in self._prices.items():
            out[key] = entry
        seen: set[str] = set()
        for key in self._prices:
            provider = key.split(":", 1)[0]
            if provider and provider not in seen and provider in PROVIDER_DEFAULT_PRICES:
                seen.add(provider)
                out[f"*{provider}"] = self._entry_from_spec(
                    provider, PROVIDER_DEFAULT_PRICES[provider], "default"
                )
        return out

    # -- aggregation ----------------------------------------------------------
    def _day_key(self, ts: float) -> str:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")

    def stats(self, session_id: Optional[str] = None) -> dict[str, Any]:
        today_key = self._day_key(datetime.now().timestamp())
        by_day: dict[str, dict[str, float]] = {}
        today_cost = 0.0
        today_calls = 0
        total_cost = 0.0
        total_calls = 0
        session_cost = 0.0
        session_calls = 0
        by_model: dict[str, dict[str, Any]] = {}

        for rec in self._records:
            key = self._day_key(rec.ts)
            day = by_day.setdefault(key, {"cost": 0.0, "calls": 0})
            day["cost"] += rec.cost
            day["calls"] += 1
            total_cost += rec.cost
            total_calls += 1
            if key == today_key:
                today_cost += rec.cost
                today_calls += 1
            if session_id and rec.session_id == session_id:
                session_cost += rec.cost
                session_calls += 1
            m = by_model.setdefault(
                rec.model,
                {
                    "input": 0,
                    "output": 0,
                    "cache_read": 0,
                    "cache_write": 0,
                    "cost": 0.0,
                    "calls": 0,
                    "currency": rec.currency,
                    "priced": rec.priced,
                },
            )
            m["input"] += rec.input
            m["output"] += rec.output
            m["cache_read"] += rec.cache_read
            m["cache_write"] += rec.cache_write
            m["cost"] += rec.cost
            m["calls"] += 1

        days = sorted(by_day.items())
        budget = self._settings["budget"]
        period_cost = total_cost
        if budget.get("period") == "day":
            period_cost = today_cost
        elif budget.get("period") == "month":
            month_key = datetime.now().strftime("%Y-%m")
            period_cost = sum(
                r.cost for r in self._records if self._day_key(r.ts).startswith(month_key)
            )
        amount = float(budget.get("amount", 0.0) or 0.0)
        used_pct = round(period_cost / amount * 100, 1) if amount > 0 else None

        off_peak = self._settings["off_peak"]
        return {
            "ok": True,
            "today": {"cost": round(today_cost, 6), "calls": today_calls},
            "total": {"cost": round(total_cost, 6), "calls": total_calls},
            "session": (
                {"cost": round(session_cost, 6), "calls": session_calls}
                if session_id
                else None
            ),
            "by_day": [
                {"day": k, "cost": round(v["cost"], 6), "calls": v["calls"]}
                for k, v in days[-31:]
            ],
            "by_model": [
                {
                    "model": k,
                    **{kk: vv for kk, vv in v.items() if kk != "currency"},
                    "currency": v["currency"],
                }
                for k, v in sorted(
                    by_model.items(), key=lambda kv: kv[1]["cost"], reverse=True
                )
            ],
            "budget": {
                "amount": amount,
                "currency": budget.get("currency", "CNY"),
                "period": budget.get("period", "month"),
                "period_cost": round(period_cost, 6),
                "used_pct": used_pct,
            },
            "peak": {
                "now": in_peak_window(
                    datetime.now().timestamp(),
                    off_peak.get("windows", []),
                    str(off_peak.get("tz", "Asia/Shanghai")),
                ),
                "windows": off_peak.get("windows", []),
                "tz": off_peak.get("tz", "Asia/Shanghai"),
                "multiplier": off_peak.get("multiplier", 0.5),
                "enabled": off_peak.get("enabled", False),
            },
        }

    # -- settings -------------------------------------------------------------
    def get_settings(self) -> dict[str, Any]:
        return {
            "ok": True,
            "settings": {
                "budget": self._settings["budget"],
                "off_peak": self._settings["off_peak"],
                "prices": {
                    k: v.as_dict() for k, v in sorted(self._prices.items())
                },
            },
        }

    def update_settings(self, patch: dict[str, Any]) -> dict[str, Any]:
        patch = patch or {}
        if isinstance(patch.get("budget"), dict):
            self._settings["budget"] = {
                **self._settings["budget"],
                **patch["budget"],
            }
        if isinstance(patch.get("off_peak"), dict):
            self._settings["off_peak"] = {
                **self._settings["off_peak"],
                **patch["off_peak"],
            }
        if isinstance(patch.get("prices"), dict):
            for key, spec in patch["prices"].items():
                if not isinstance(spec, dict):
                    continue
                if spec.get("reset"):
                    self._prices.pop(key, None)
                    continue
                self._prices[key] = self._entry_from_spec(key, spec, "custom")
        self._save_settings()
        return self.get_settings()

    # -- official balance -----------------------------------------------------
    def balance(self) -> dict[str, Any]:
        """DeepSeek 官方余额（GET /user/balance）。其他 provider 无公开余额端点。"""
        api_key = self._deepseek_key()
        if not api_key:
            return {
                "ok": False,
                "provider": "deepseek",
                "error": (
                    "未配置 DeepSeek API 密钥 — 在 设置 ▸ 模型 添加 DeepSeek 密钥后，"
                    "即可查询官方余额。"
                ),
            }
        try:
            req = urllib.request.Request(
                DEEPSEEK_BALANCE_URL, headers={"Authorization": f"Bearer {api_key}"}
            )
            with self._fetch(req, timeout=15) as resp:  # type: ignore[arg-type]
                body = json.loads(resp.read().decode("utf-8"))
            infos = body.get("balance_infos") or []
            balances = []
            for info in infos:
                def _num(v: Any) -> float:
                    try:
                        return float(v)
                    except (TypeError, ValueError):
                        return 0.0
                balances.append(
                    {
                        "currency": info.get("currency", "CNY"),
                        "total": _num(info.get("total_balance")),
                        "granted": _num(info.get("granted_balance")),
                        "topped_up": _num(info.get("topped_up_balance")),
                    }
                )
            return {
                "ok": True,
                "provider": "deepseek",
                "is_available": bool(body.get("is_available", True)),
                "balances": balances,
            }
        except Exception as exc:  # network / auth / parse — never crash the meter
            return {
                "ok": False,
                "provider": "deepseek",
                "error": f"查询官方余额失败：{exc}",
            }

    def _deepseek_key(self) -> Optional[str]:
        key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
        if key:
            return key
        if self._secrets is not None:
            try:
                profile = self._secrets.get("provider:deepseek") or {}
                key = str(profile.get("api_key") or "").strip()
                if key:
                    return key
            except Exception:
                pass
        return None

    # -- official price sync --------------------------------------------------
    def sync_prices(self) -> dict[str, Any]:
        """一键同步官方价格。

        1) DeepSeek 官方定价页 → 更新 deepseek:* 条目（含峰谷时段，若能解析）。
        2) OpenRouter 模型目录（官方聚合的厂商标价）→ 按映射补齐所有条目。
        任一步失败不阻断整体；返回每步的明细。
        """
        updated: list[str] = []
        failed: list[str] = []
        notes: list[str] = []

        # Step 1: OpenRouter catalog (aggregated vendor listings, USD).
        or_ = self._sync_openrouter(updated, failed)
        if or_:
            notes.append(or_)

        # Step 2: DeepSeek official pricing page — runs LAST so the vendor's own
        # CNY peak/off-peak prices win for deepseek:* over the reseller listing.
        ds = self._sync_deepseek_page(updated, failed)
        if ds:
            notes.append(ds)

        if not updated and not failed:
            return {
                "ok": False,
                "updated": [],
                "failed": [],
                "message": "同步失败：无法访问官方定价来源（网络不可用？）。当前价格表未变化。",
            }
        self._save_settings()
        return {
            "ok": True,
            "updated": updated,
            "failed": failed,
            "notes": notes,
            "message": (
                f"已更新 {len(updated)} 个模型的价格"
                + (f"，{len(failed)} 个解析失败" if failed else "")
                + "。"
            ),
        }

    def _sync_deepseek_page(
        self, updated: list[str], failed: list[str]
    ) -> Optional[str]:
        html = None
        for url in DEEPSEEK_PRICING_URLS:
            try:
                with self._fetch(url, timeout=20) as resp:  # type: ignore[arg-type]
                    html = resp.read().decode("utf-8", "replace")
                break
            except Exception:
                continue
        if not html:
            failed.append("deepseek 官方定价页（网络不可达）")
            return None
        parsed = parse_deepseek_pricing(html)
        if not parsed.get("models"):
            failed.append("deepseek 官方定价页（无法解析价格表）")
            return None
        count = 0
        for key, spec in parsed["models"].items():
            if key in self._prices:
                self._prices[key] = self._entry_from_spec(key, spec, "synced")
                updated.append(key)
                count += 1
        note = f"DeepSeek 官方页：更新 {count} 个模型"
        if parsed.get("windows"):
            self._settings["off_peak"]["windows"] = parsed["windows"]
            note += "，同步峰谷时段"
        return note

    def _sync_openrouter(
        self, updated: list[str], failed: list[str]
    ) -> Optional[str]:
        try:
            with self._fetch(OPENROUTER_MODELS_URL, timeout=20) as resp:  # type: ignore[arg-type]
                body = json.loads(resp.read().decode("utf-8"))
        except Exception:
            failed.append("OpenRouter 目录（网络不可达）")
            return None
        models = body.get("data") if isinstance(body, dict) else None
        if not isinstance(models, list):
            failed.append("OpenRouter 目录（响应格式异常）")
            return None
        count = 0
        for m in models:
            if not isinstance(m, dict):
                continue
            slug = str(m.get("id", ""))
            key = _OPENROUTER_MAP.get(slug)
            if key is None and slug.startswith("deepseek/"):
                key = "deepseek:" + slug.split("/", 1)[1]
            pricing = m.get("pricing") if isinstance(m.get("pricing"), dict) else {}
            try:
                prompt = float(pricing.get("prompt") or 0)
                completion = float(pricing.get("completion") or 0)
            except (TypeError, ValueError):
                continue
            if prompt <= 0 and completion <= 0:
                continue
            if key is None:
                key = slug  # keep the raw slug for models we don't map
            prev = self._prices.get(key)
            if prev is not None:
                # OpenRouter lists prices per token (USD), while our table stores
                # USD per 1M tokens — scale up or every sync would under-bill ~1e6x.
                input_usd = round(prompt * 1e6, 6)
                output_usd = round(completion * 1e6, 6)
                self._prices[key] = PriceEntry(
                    input=input_usd,
                    cache_read=round(input_usd * 0.1, 6),  # OpenRouter has no cache split
                    cache_write=input_usd,
                    output=output_usd,
                    currency="USD",
                    source="synced",
                    updated_at=datetime.now().timestamp(),
                )
                updated.append(key)
                count += 1
        return f"OpenRouter 目录：更新 {count} 个模型" if count else None


# ---------------------------------------------------------------------------
# DeepSeek pricing-page parser (pure; fed by _sync_deepseek_page)
# ---------------------------------------------------------------------------

def _strip_html(html: str) -> str:
    text = re.sub(r"<script[\s\S]*?</script>", " ", html, flags=re.I)
    text = re.sub(r"<style[\s\S]*?</style>", " ", text, flags=re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&yen;", "¥").replace("&dollar;", "$")
    text = re.sub(r"\s+", " ", text)
    return text


def _parse_price_token(token: str) -> Optional[float]:
    """'¥3.00' → 3.0; '3.00元' → 3.0; '1.5' → 1.5; '$0.30' → 0.3. None on junk."""
    token = token.strip()
    token = token.replace("¥", "").replace("$", "").replace("元", "").replace(",", "")
    m = re.match(r"^(\d+(?:\.\d+)?)$", token)
    return float(m.group(1)) if m else None


def parse_deepseek_pricing(html: str) -> dict[str, Any]:
    """Best-effort parse of the DeepSeek official pricing page.

    The page (Docusaurus) renders a markdown table per model:
      | 模型 | 输入缓存命中 | 输入缓存未命中 | 输出 | …
      | deepseek-v4-flash | 高峰 0.10 | 高峰 3.00 | 高峰 9.00 | 空闲 0.05 | 空闲 1.50 | 空闲 4.50 |
    Returns {"models": {key: spec}, "windows": [...]|None}.
    """
    text = _strip_html(html)
    result: dict[str, Any] = {"models": {}, "windows": None}

    # Peak windows: look for "高峰时段" followed by times like "09:00" "14:00".
    win = re.search(r"高峰时段[^。；;]{0,60}?(\d{1,2}[:：]\d{2})[^。；;]{0,30}?(\d{1,2}[:：]\d{2})", text)
    if win:
        result["windows"] = [
            [win.group(1).replace("：", ":"), win.group(2).replace("：", ":")]
        ]

    for model_id in ("deepseek-v4-flash", "deepseek-v4-pro"):
        key = f"deepseek:{model_id}"
        # Find the table segment for this model, bounded before the next model's
        # name so the off-peak row's unlabeled cells still belong to this model.
        m = re.search(
            re.escape(model_id) + r"(?:(?!deepseek-v4-).){0,900}", text
        )
        if not m:
            continue
        seg = m.group(0)
        # Labeled ("高峰"/"空闲") or bare prices. Bare integers (years, context
        # sizes, model ids like "v4") are dropped: prices carry a decimal point
        # or an explicit ¥/$/元 marker.
        nums: list[float] = []
        for pm in re.finditer(
            r"(?:高峰|空闲)?\s*([¥$]?\s*\d+(?:\.\d+)?)\s*(?:元)?", seg
        ):
            raw = pm.group(1).strip()
            if "." not in raw and "¥" not in raw and "$" not in raw:
                continue
            n = _parse_price_token(raw)
            if n is not None:
                nums.append(n)
        # Expect ≥6 numbers: peak(hit, miss, out) + off-peak(hit, miss, out).
        if len(nums) < 6:
            # fall back to a bare 3-number peak row
            if len(nums) < 3:
                continue
            peak = nums[:3]
            spec = {
                "cache_read": peak[0],
                "input": peak[1],
                "cache_write": peak[1],
                "output": peak[2],
                "currency": "CNY",
            }
        else:
            peak = nums[:3]
            off = nums[3:6]
            spec = {
                "cache_read": peak[0],
                "input": peak[1],
                "cache_write": peak[1],
                "output": peak[2],
                "currency": "CNY",
                "off_peak_prices": {
                    "cache_read": off[0],
                    "input": off[1],
                    "cache_write": off[1],
                    "output": off[2],
                },
            }
        result["models"][key] = spec

    # Off-peak multiplier from the page when present ("空闲时段价格为高峰的一半").
    if re.search(r"空闲[^。]{0,30}?高峰[^。]{0,10}?一半", text):
        result["multiplier"] = 0.5
    return result


# ---------------------------------------------------------------------------
# Provider wrapper — records usage from every model call
# ---------------------------------------------------------------------------

class MeteredProvider:
    """Transparent wrapper around a ProviderClient.

    Every `complete()`/`stream()` result is checked for `usage` and forwarded to
    the meter with the call's model id. The wrapped client is shared (the router
    caches it); the wrapper is per-engine so the session id is known at record
    time.
    """

    def __init__(
        self,
        inner: Any,
        on_usage: Callable[[str, str, dict[str, int]], None],
        session_id: str,
    ) -> None:
        self._inner = inner
        self._on_usage = on_usage
        self._session_id = session_id

    def complete(self, **kwargs: Any) -> Any:
        turn = self._inner.complete(**kwargs)
        self._maybe_record(kwargs.get("model"), turn)
        return turn

    def stream(self, **kwargs: Any):
        model = kwargs.get("model")
        for chunk in self._inner.stream(**kwargs):
            if chunk is not None and getattr(chunk, "turn", None) is not None:
                self._maybe_record(model, chunk.turn)
            yield chunk

    def capabilities(self, model: str) -> Any:
        return self._inner.capabilities(model)

    def __getattr__(self, name: str) -> Any:
        # Any other provider API (e.g. invalidate) passes through untouched.
        return getattr(self._inner, name)

    def _maybe_record(self, model: Any, turn: Any) -> None:
        usage = getattr(turn, "usage", None)
        if usage is None:
            return
        try:
            self._on_usage(self._session_id, str(model or ""), usage.as_dict())
        except Exception:
            pass  # metering must never break a turn
