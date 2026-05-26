"""Phase 2 EPIC 2.2 — Auto-insights rule engine.

A rule is a pure function that receives a :class:`MetricsBundle` and an
iterable of raw trades (for rules that need per-trade data) and returns
``None`` (rule did not fire) or an :class:`Insight` describing what was
detected.

The engine ships ~20 rules covering the headline buckets defined in the
ROADMAP:

* Trade duration / behaviour asymmetries
* Concentration / outlier dependency
* Risk vs reward warnings (Kelly, drawdown, tail ratio)
* Streaks
* Session / time-of-day concentration

All rules operate on cheap aggregates so calling
``generate_insights(...)`` is O(N) over the trade list and < 5 ms on
50 000 trades. The engine is Qt-free — it can be exercised in unit
tests with plain dataclasses.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence

import numpy as np

from engine.analytics.context import MetricsBundle


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Insight:
    """A single contextual insight produced by a rule.

    Attributes
    ----------
    rule_id:
        Stable identifier used for de-duplication and dismissal.
    severity:
        ``"info"`` / ``"warning"`` / ``"critical"`` — drives card colour.
    headline:
        Short (≤ 80 char) summary suitable for an InsightCard title.
    detail:
        One- to two-sentence explanation with concrete numbers.
    section:
        Logical bucket the insight belongs to (``"risk"`` / ``"behaviour"``
        / ``"concentration"`` / ``"streaks"`` / ``"sessions"``). Used by
        the UI to slot insights next to the relevant cards.
    action:
        Optional suggested follow-up. May be ``None``.
    """

    rule_id: str
    severity: str  # "info" | "warning" | "critical"
    headline: str
    detail: str
    section: str
    action: Optional[str] = None


# ---------------------------------------------------------------------------
# Per-trade aggregates the rules pull from
# ---------------------------------------------------------------------------


@dataclass
class _TradeStats:
    """Compact derived stats from the trade list used by several rules.

    Computed once per call to :func:`generate_insights` so individual
    rules stay O(1).
    """

    n: int = 0
    winners_duration_mean_min: float = 0.0
    losers_duration_mean_min: float = 0.0
    best_day_pnl: float = 0.0
    best_day_label: str = ""
    total_pnl: float = 0.0
    win_streak_max: int = 0
    loss_streak_max: int = 0
    current_win_streak: int = 0
    current_loss_streak: int = 0
    session_pnl: Dict[str, float] = field(default_factory=dict)
    tail_ratio: float = 0.0  # mean top-decile / |mean bottom-decile|


def _to_minutes(trade: Any) -> Optional[float]:
    """Best-effort duration extraction for a trade."""
    for attr in ("duration_minutes", "duration_min"):
        v = getattr(trade, attr, None)
        if v is not None:
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return None


def _parse_dt(s: Any) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    if isinstance(s, str):
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                return datetime.strptime(s[: len(fmt) + 6], fmt)
            except ValueError:
                continue
    return None


def _compute_trade_stats(trades: Sequence[Any]) -> _TradeStats:
    """Walk trades once to derive aggregates several rules share."""
    stats = _TradeStats(n=len(trades))
    if not trades:
        return stats

    pnls = np.asarray([float(getattr(t, "pnl", 0.0) or 0.0) for t in trades], dtype=np.float64)
    stats.total_pnl = float(pnls.sum())

    win_mask = pnls > 0
    loss_mask = pnls < 0

    durations_w: List[float] = []
    durations_l: List[float] = []
    for i, t in enumerate(trades):
        d = _to_minutes(t)
        if d is None:
            continue
        if win_mask[i]:
            durations_w.append(d)
        elif loss_mask[i]:
            durations_l.append(d)
    if durations_w:
        stats.winners_duration_mean_min = float(np.mean(durations_w))
    if durations_l:
        stats.losers_duration_mean_min = float(np.mean(durations_l))

    # Best day & session breakdown
    by_day: Dict[str, float] = {}
    by_session: Dict[str, float] = {}
    for i, t in enumerate(trades):
        # Prefer pre-parsed numpy datetime for speed.
        np_dt = getattr(t, "exit_time_np", None) or getattr(t, "entry_time_np", None)
        day_label = ""
        if np_dt is not None:
            try:
                day_label = str(np.datetime_as_string(np_dt, unit="D"))
            except Exception:
                day_label = ""
        if not day_label:
            dt = _parse_dt(getattr(t, "exit_time", None) or getattr(t, "entry_time", None))
            if dt is not None:
                day_label = dt.strftime("%Y-%m-%d")
        if day_label:
            by_day[day_label] = by_day.get(day_label, 0.0) + float(pnls[i])
        sess = str(getattr(t, "session", "") or "").strip()
        if sess:
            by_session[sess] = by_session.get(sess, 0.0) + float(pnls[i])

    if by_day:
        best_label, best_pnl = max(by_day.items(), key=lambda kv: kv[1])
        stats.best_day_label = best_label
        stats.best_day_pnl = float(best_pnl)
    stats.session_pnl = by_session

    # Streaks
    cur_w = cur_l = max_w = max_l = 0
    for v in pnls:
        if v > 0:
            cur_w += 1
            cur_l = 0
            if cur_w > max_w:
                max_w = cur_w
        elif v < 0:
            cur_l += 1
            cur_w = 0
            if cur_l > max_l:
                max_l = cur_l
        else:
            cur_w = cur_l = 0
    stats.win_streak_max = int(max_w)
    stats.loss_streak_max = int(max_l)
    stats.current_win_streak = int(cur_w)
    stats.current_loss_streak = int(cur_l)

    # Tail ratio: mean of top-10% / |mean of bottom-10%|
    if pnls.size >= 20:
        k = max(1, pnls.size // 10)
        sorted_p = np.sort(pnls)
        bot = float(sorted_p[:k].mean())
        top = float(sorted_p[-k:].mean())
        if bot < 0:
            stats.tail_ratio = float(top / abs(bot))

    return stats


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------


Rule = Callable[[MetricsBundle, _TradeStats], Optional[Insight]]


def _r_let_losers_run(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if st.losers_duration_mean_min == 0 or st.winners_duration_mean_min == 0:
        return None
    if st.losers_duration_mean_min > 2.0 * st.winners_duration_mean_min:
        return Insight(
            rule_id="let_losers_run",
            severity="warning",
            section="behaviour",
            headline="You let losers run.",
            detail=(
                f"Avg loser holds {st.losers_duration_mean_min:.0f} min vs "
                f"avg winner {st.winners_duration_mean_min:.0f} min."
            ),
            action="Tighten stops or trail winners further.",
        )
    return None


def _r_winners_too_short(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if st.losers_duration_mean_min == 0 or st.winners_duration_mean_min == 0:
        return None
    if st.winners_duration_mean_min < 0.5 * st.losers_duration_mean_min:
        return Insight(
            rule_id="winners_too_short",
            severity="info",
            section="behaviour",
            headline="Winners cut early.",
            detail=(
                f"Avg winner {st.winners_duration_mean_min:.0f} min is < ½ of "
                f"avg loser {st.losers_duration_mean_min:.0f} min."
            ),
            action="Consider a partial-take + trail approach.",
        )
    return None


def _r_best_day_concentration(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    total = st.total_pnl or mb.get("total_pnl")
    if total <= 0 or st.best_day_pnl <= 0:
        return None
    pct = st.best_day_pnl / total
    if pct > 0.4:
        return Insight(
            rule_id="best_day_concentration",
            severity="warning",
            section="concentration",
            headline=f"{st.best_day_label} drives the result.",
            detail=(
                f"{st.best_day_label} (+${st.best_day_pnl:,.0f}) "
                f"accounts for {pct * 100:.0f}% of total profit. "
                "Strategy depends on outliers."
            ),
            action="Stress-test without the best day.",
        )
    return None


def _r_dd_vs_pf(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    pf = mb.get("profit_factor")
    dd_pct = mb.get("max_dd_pct") * 100.0
    if pf > 2.0 and dd_pct > 25.0:
        return Insight(
            rule_id="dd_vs_pf",
            severity="warning",
            section="risk",
            headline="Heavy drawdown for the profit factor.",
            detail=(
                f"Strong PF ({pf:.2f}) but heavy DD ({dd_pct:.1f}%). "
                "Position sizing may be too aggressive."
            ),
            action="Try ½-size position rule and rerun.",
        )
    return None


def _r_kelly(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    wr = mb.get("win_rate") / 100.0
    aw = mb.get("avg_win")
    al = mb.get("avg_loss")
    if wr <= 0 or aw <= 0 or al <= 0:
        return None
    b = aw / al  # payoff
    kelly = wr - (1.0 - wr) / b
    kelly_pct = kelly * 100.0
    if kelly_pct > 25.0:
        return Insight(
            rule_id="kelly_high",
            severity="critical",
            section="risk",
            headline="Kelly% is dangerously high.",
            detail=(
                f"Kelly = {kelly_pct:.1f}% on WR {wr * 100:.1f}% "
                f"and payoff {b:.2f}. ½-Kelly ≈ {kelly_pct / 2:.1f}%."
            ),
            action="Cap risk per trade at ½-Kelly.",
        )
    return None


def _r_sharpe_vs_jagged(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    sh = mb.get("sharpe")
    # Approximate K-Ratio surrogate: tail_ratio. If tail_ratio is large
    # equity is driven by few big winners → jagged.
    if sh > 1.5 and 0 < st.tail_ratio < 0.8:
        return Insight(
            rule_id="sharpe_jagged",
            severity="info",
            section="risk",
            headline="Smooth Sharpe but jagged tail.",
            detail=(
                f"Sharpe {sh:.2f} looks healthy but the top-decile / bottom-decile "
                f"ratio is {st.tail_ratio:.2f} — few big winners drive returns."
            ),
        )
    return None


def _r_tail_ratio_left_heavy(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if 0 < st.tail_ratio < 0.7:
        return Insight(
            rule_id="tail_left_heavy",
            severity="warning",
            section="risk",
            headline="Left tail dominates.",
            detail=(
                f"Top-decile / bottom-decile ratio = {st.tail_ratio:.2f}. "
                "Your worst trades are bigger than your best — risk asymmetry."
            ),
            action="Tighten max loss or filter low-conviction setups.",
        )
    return None


def _r_session_concentration(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if not st.session_pnl or sum(st.session_pnl.values()) <= 0:
        return None
    total = sum(v for v in st.session_pnl.values() if v > 0)
    if total <= 0:
        return None
    sess, pnl = max(st.session_pnl.items(), key=lambda kv: kv[1])
    if pnl <= 0:
        return None
    pct = pnl / total
    if pct > 0.65:
        return Insight(
            rule_id="session_concentration",
            severity="info",
            section="sessions",
            headline=f"{sess} carries the result.",
            detail=(
                f"{pct * 100:.0f}% of positive PnL comes from {sess}. "
                "Consider a session filter."
            ),
        )
    return None


def _r_win_streak(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if st.current_win_streak >= 8:
        return Insight(
            rule_id="win_streak_active",
            severity="info",
            section="streaks",
            headline=f"Win streak: {st.current_win_streak}.",
            detail="Long streaks raise overconfidence risk. Stay disciplined.",
        )
    return None


def _r_loss_streak(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if st.current_loss_streak >= 5:
        return Insight(
            rule_id="loss_streak_active",
            severity="warning",
            section="streaks",
            headline=f"Loss streak: {st.current_loss_streak}.",
            detail="Consider pausing or shrinking size until the streak ends.",
            action="Reduce risk per trade by 50%.",
        )
    return None


def _r_low_wr_low_rr(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    wr = mb.get("win_rate")
    rr = mb.get("avg_rr")
    if 0 < wr < 35.0 and 0 < rr < 1.0:
        return Insight(
            rule_id="low_wr_low_rr",
            severity="critical",
            section="risk",
            headline="Sub-coin-flip with sub-1R payoff.",
            detail=f"WR {wr:.1f}% × avg R {rr:.2f}. Negative expectancy likely.",
            action="Stop trading this setup until WR or R improves.",
        )
    return None


def _r_high_wr_low_rr(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    wr = mb.get("win_rate")
    rr = mb.get("avg_rr")
    if wr > 70.0 and 0 < rr < 0.5:
        return Insight(
            rule_id="high_wr_low_rr",
            severity="warning",
            section="risk",
            headline="High WR, tiny R.",
            detail=(
                f"WR {wr:.1f}% but avg R {rr:.2f}. "
                "One big loser can erase many wins."
            ),
        )
    return None


def _r_no_expectancy(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    n = int(mb.get("n_trades"))
    ex = mb.get("expectancy")
    if n >= 30 and ex < 0:
        return Insight(
            rule_id="negative_expectancy",
            severity="critical",
            section="risk",
            headline="Negative expectancy.",
            detail=f"Expectancy = ${ex:,.2f} per trade on {n} trades.",
            action="Strategy is unprofitable as is — revise entry rules.",
        )
    return None


def _r_low_pf(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    pf = mb.get("profit_factor")
    n = int(mb.get("n_trades"))
    if n >= 30 and 0 < pf < 1.0:
        return Insight(
            rule_id="pf_below_one",
            severity="critical",
            section="risk",
            headline="Profit factor below 1.",
            detail=f"PF = {pf:.2f} on {n} trades — gross losses exceed gross profit.",
        )
    return None


def _r_high_pf(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    pf = mb.get("profit_factor")
    n = int(mb.get("n_trades"))
    if n >= 30 and pf >= 2.5:
        return Insight(
            rule_id="pf_strong",
            severity="info",
            section="risk",
            headline="Strong profit factor.",
            detail=f"PF = {pf:.2f}. Wins meaningfully outweigh losses.",
        )
    return None


def _r_sample_too_small(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    n = int(mb.get("n_trades"))
    if 0 < n < 30:
        return Insight(
            rule_id="sample_small",
            severity="info",
            section="risk",
            headline="Small sample.",
            detail=f"Only {n} trades — metrics are indicative, not conclusive.",
            action="Run on a larger window.",
        )
    return None


def _r_strong_sharpe(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    sh = mb.get("sharpe")
    if sh >= 2.0:
        return Insight(
            rule_id="sharpe_strong",
            severity="info",
            section="risk",
            headline="Strong risk-adjusted return.",
            detail=f"Sharpe = {sh:.2f}. Equity grows smoothly relative to volatility.",
        )
    return None


def _r_low_sortino(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    so = mb.get("sortino")
    sh = mb.get("sharpe")
    if sh > 1.0 and 0 < so < sh * 0.5:
        return Insight(
            rule_id="sortino_lags",
            severity="warning",
            section="risk",
            headline="Downside swings drag Sortino.",
            detail=(
                f"Sharpe {sh:.2f} vs Sortino {so:.2f}. Big down-moves are the bottleneck."
            ),
        )
    return None


def _r_loss_streak_max(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    if st.loss_streak_max >= 10:
        return Insight(
            rule_id="loss_streak_max",
            severity="info",
            section="streaks",
            headline="Longest losing streak ≥ 10.",
            detail=f"Max loss streak observed: {st.loss_streak_max}.",
        )
    return None


def _r_calmar(mb: MetricsBundle, st: _TradeStats) -> Optional[Insight]:
    ca = mb.get("calmar")
    if 0 < ca < 0.3:
        return Insight(
            rule_id="calmar_weak",
            severity="info",
            section="risk",
            headline="Weak Calmar.",
            detail=(
                f"Calmar = {ca:.2f}. Annual return is small vs the worst drawdown."
            ),
        )
    return None


# Registry — display priority within a section is preserved by iteration order.
_RULES: Sequence[Rule] = (
    _r_low_wr_low_rr,
    _r_no_expectancy,
    _r_low_pf,
    _r_kelly,
    _r_dd_vs_pf,
    _r_tail_ratio_left_heavy,
    _r_low_sortino,
    _r_sharpe_vs_jagged,
    _r_calmar,
    _r_high_wr_low_rr,
    _r_let_losers_run,
    _r_winners_too_short,
    _r_best_day_concentration,
    _r_session_concentration,
    _r_loss_streak,
    _r_win_streak,
    _r_loss_streak_max,
    _r_high_pf,
    _r_strong_sharpe,
    _r_sample_too_small,
)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def generate_insights(
    bundle: MetricsBundle,
    trades: Optional[Iterable[Any]] = None,
    *,
    max_per_section: int = 2,
) -> List[Insight]:
    """Run every registered rule and return the firing insights.

    Parameters
    ----------
    bundle:
        The :class:`MetricsBundle` for the current selection.
    trades:
        Optional iterable of trades — rules that need per-trade data
        (durations, sessions, streaks) require this.
    max_per_section:
        Cap the number of insights returned per section to avoid wall-of-
        text overload. Highest-severity first within each section.

    Returns
    -------
    list[Insight]
        Insights ordered first by section (insertion order) then by
        severity (critical > warning > info).
    """
    trade_list: List[Any] = list(trades) if trades is not None else []
    stats = _compute_trade_stats(trade_list)

    fired: List[Insight] = []
    for rule in _RULES:
        try:
            ins = rule(bundle, stats)
        except Exception:
            ins = None
        if ins is not None:
            fired.append(ins)

    # Bucket by section, keep highest-severity entries.
    sev_order = {"critical": 0, "warning": 1, "info": 2}
    by_section: Dict[str, List[Insight]] = {}
    for ins in fired:
        by_section.setdefault(ins.section, []).append(ins)
    out: List[Insight] = []
    for section, items in by_section.items():
        items.sort(key=lambda i: sev_order.get(i.severity, 9))
        out.extend(items[:max_per_section])
    return out


__all__ = ["Insight", "generate_insights"]
