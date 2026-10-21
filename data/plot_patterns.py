"""Check charts for the planted patterns. Writes PNGs to data/out/.

Run: python -m data.plot_patterns
"""

from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import pandas as pd  # noqa: E402

from data.generate import (  # noqa: E402
    HISTORY_END, HISTORY_START, REGIONS, WEST_DIP_END, WEST_DIP_START, generate, with_region,
)

OUT = Path(__file__).parent / "out"

# Reference palette (dataviz skill, light mode): one highlight hue, everything else recessive.
SURFACE = "#fcfcfb"
TEXT_PRIMARY = "#0b0b0b"
TEXT_SECONDARY = "#52514e"
HIGHLIGHT = "#2a78d6"   # series-1 blue
MUTED = "#b5b4ad"       # context lines
GRID = "#e6e5e0"
BAND = "#f0efec"


def style(ax, title, ylabel):
    ax.set_facecolor(SURFACE)
    ax.set_title(title, loc="left", color=TEXT_PRIMARY, fontsize=13, fontweight="bold", pad=12)
    ax.set_ylabel(ylabel, color=TEXT_SECONDARY)
    ax.grid(axis="y", color=GRID, linewidth=0.8)
    ax.set_axisbelow(True)
    for side in ("top", "right", "left"):
        ax.spines[side].set_visible(False)
    ax.spines["bottom"].set_color(GRID)
    ax.tick_params(colors=TEXT_SECONDARY, length=0)


def spread_labels(ends: dict[str, float], min_gap: float) -> dict[str, float]:
    """Nudge end-of-line label positions apart so none overlap (keeps their order)."""
    ordered = sorted(ends.items(), key=lambda kv: kv[1])
    placed: dict[str, float] = {}
    prev = None
    for name, y in ordered:
        y = y if prev is None else max(y, prev + min_gap)
        placed[name] = prev = y
    return placed


def plot_west_dip(deals, reps):
    d = with_region(deals, reps)
    d = d[d.closed_date.between(HISTORY_START, HISTORY_END)]
    monthly = (d.groupby([d.closed_date.dt.to_period("M"), d.region]).is_won.mean()
               .astype(float).unstack())
    x = monthly.index.to_timestamp()

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    ax.axvspan(WEST_DIP_START, WEST_DIP_END, color=BAND, zorder=0)
    ax.text(WEST_DIP_START, 0.47, "  Planted dip: Jul–Sep 2025", color=TEXT_SECONDARY,
            fontsize=9, va="top")
    label_y = spread_labels({r: monthly[r].iloc[-1] for r in REGIONS}, min_gap=0.022)
    for region in REGIONS:
        is_west = region == "West"
        ax.plot(x, monthly[region], color=HIGHLIGHT if is_west else MUTED,
                linewidth=2.5 if is_west else 1.5, zorder=3 if is_west else 2,
                marker="o" if is_west else None, markersize=4)
        # direct label at the line end; text stays in text ink
        ax.annotate(region, (x[-1], label_y[region]), xytext=(6, 0),
                    textcoords="offset points", va="center", fontsize=9,
                    color=TEXT_PRIMARY if is_west else TEXT_SECONDARY,
                    fontweight="bold" if is_west else "normal")
    style(ax, "Monthly win rate by region — West dips in Q3 2025, then recovers",
          "Win rate (won ÷ closed)")
    ax.set_ylim(0, 0.5)
    ax.yaxis.set_major_formatter(matplotlib.ticker.PercentFormatter(1.0, decimals=0))
    fig.tight_layout()
    fig.savefig(OUT / "west_dip.png", dpi=150)
    plt.close(fig)


def plot_seasonality(deals):
    won = deals[(deals.is_won == True) & deals.closed_date.between(HISTORY_START, HISTORY_END)]
    monthly = won.groupby(won.closed_date.dt.to_period("M")).amount.sum() / 1e6
    labels = [p.strftime("%b\n%y") if p.month in (1, 4, 7, 10) else p.strftime("%b")
              for p in monthly.index]
    q4 = [p.quarter == 4 for p in monthly.index]

    fig, ax = plt.subplots(figsize=(10, 5), facecolor=SURFACE)
    ax.bar(range(len(monthly)), monthly.values, width=0.7,
           color=[HIGHLIGHT if q else MUTED for q in q4], edgecolor=SURFACE, linewidth=2)
    peak = monthly.values.argmax()
    ax.annotate(f"Dec peak: {monthly.iloc[peak]:.1f}M", (peak, monthly.iloc[peak]),
                xytext=(0, 6), textcoords="offset points", ha="center", fontsize=9,
                color=TEXT_PRIMARY, fontweight="bold")
    ax.set_xticks(range(len(monthly)), labels)
    style(ax, "Closed-won value by month — Q4 2025 (blue) is the seasonal high",
          "Closed-won value (millions)")
    fig.tight_layout()
    fig.savefig(OUT / "seasonality.png", dpi=150)
    plt.close(fig)


def main():
    OUT.mkdir(exist_ok=True)
    t = generate()
    plot_west_dip(t["fact_deals"], t["dim_rep"])
    plot_seasonality(t["fact_deals"])
    print(f"wrote {OUT / 'west_dip.png'} and {OUT / 'seasonality.png'}")


if __name__ == "__main__":
    main()
