"""Terminal charts.

``profile_chart`` draws a dive's depth profile with temperature overlaid; it uses
the optional ``plotext`` package for a coloured, dual-axis chart (depth left in
blue, temperature right in red) and falls back to a dependency-free ASCII grid
when plotext is absent. ``line_chart`` is a plain-ASCII single-series helper kept
for a future ``graph`` command.
"""

from __future__ import annotations

import shutil


def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


class _Grid:
    """A character grid. Row 0 is the TOP line of the plot."""

    def __init__(self, width: int, height: int):
        self.w = max(1, width)
        self.h = max(1, height)
        self.cells = [[" "] * self.w for _ in range(self.h)]

    def set(self, col: int, row: int, ch: str) -> None:
        if 0 <= col < self.w and 0 <= row < self.h:
            self.cells[row][col] = ch

    def line(self, col: int, r0: int, r1: int, ch: str) -> None:
        """Draw a vertical run between two rows (connects a sloped series)."""
        lo, hi = (r0, r1) if r0 <= r1 else (r1, r0)
        for r in range(lo, hi + 1):
            self.set(col, r, ch)

    def rows(self) -> list[str]:
        return ["".join(r) for r in self.cells]


def _resample(xs: list[float], ys: list[float | None], width: int
              ) -> list[float | None]:
    """Pick, for each of ``width`` columns, the y of the nearest sample in x."""
    if not xs:
        return [None] * width
    x0, x1 = xs[0], xs[-1]
    span = (x1 - x0) or 1.0
    out: list[float | None] = []
    j = 0
    for c in range(width):
        xt = x0 + (c / max(1, width - 1)) * span
        while j + 1 < len(xs) and abs(xs[j + 1] - xt) <= abs(xs[j] - xt):
            j += 1
        out.append(ys[j])
    return out


def _plot_series(grid: _Grid, cols: list[float | None], lo: float, hi: float,
                 glyph: str, *, top_is_min: bool) -> None:
    """Map values in [lo, hi] onto grid rows and draw a connected line.

    ``top_is_min``: put the smallest value at the top row (used for depth, so the
    surface is at the top and the dive goes downward)."""
    span = (hi - lo) or 1.0

    def row_of(v: float) -> int:
        frac = (v - lo) / span
        return round(frac * (grid.h - 1)) if top_is_min \
            else round((1 - frac) * (grid.h - 1))

    prev: int | None = None
    for c, v in enumerate(cols):
        if v is None:
            prev = None
            continue
        r = row_of(v)
        if prev is not None:
            grid.line(c, prev, r, glyph)
        else:
            grid.set(c, r, glyph)
        prev = r


def _int_ticks(lo: int, hi: int, n: int) -> list[int]:
    """Up to ``n`` evenly spaced whole-number ticks spanning [lo, hi]."""
    if hi <= lo:
        return [lo]
    step = max(1, round((hi - lo) / (n - 1)))
    ticks = list(range(lo, hi + 1, step))
    if ticks[-1] != hi:
        ticks.append(hi)
    return ticks


def _nice_labels(lo: float, hi: float, n: int) -> list[tuple[float, float]]:
    """Return (fraction-from-top, value) pairs for n evenly spaced ticks."""
    out = []
    for i in range(n):
        frac = i / (n - 1)
        out.append((frac, hi - frac * (hi - lo)))
    return out


def line_chart(xs: list[float], ys: list[float | None], *, width: int = 72,
               height: int = 16, unit: str = "", title: str | None = None,
               invert: bool = False) -> list[str]:
    """One numeric series over xs, with a labelled left y-axis.

    ``invert`` puts the minimum at the top (handy for depth-like fields)."""
    vals = [v for v in ys if v is not None]
    if not vals:
        return ["(no data to plot)"]
    lo, hi = min(vals), max(vals)
    if lo == hi:
        lo, hi = lo - 1, hi + 1
    grid = _Grid(width, height)
    cols = _resample(xs, ys, width)
    _plot_series(grid, cols, lo, hi, "•", top_is_min=invert)

    labels = _nice_labels(lo, hi, height) if not invert else \
        [(f, lo + f * (hi - lo)) for f in (i / (height - 1) for i in range(height))]
    lab = [f"{v:6.1f}" for _, v in labels]
    width_lab = max(len(s) for s in lab)
    out = []
    if title:
        out.append(title)
    body = grid.rows()
    for i, line in enumerate(body):
        out.append(f"{lab[i]:>{width_lab}} │{line}")
    axis = " " * width_lab + " └" + "─" * width
    out.append(axis)
    if xs:
        span_lbl = f"{xs[0]:g} … {xs[-1]:g}"
        out.append(" " * (width_lab + 2) + span_lbl +
                   (f"   ({unit})" if unit else ""))
    return out


def series_chart(xs: list[float], ys: list[float | None], *,
                 title: str | None = None, ylabel: str = "",
                 xticks: list[tuple[float, str]] | None = None,
                 color: str = "cyan+", width: int | None = None,
                 height: int = 18, int_y: bool = False) -> list[str]:
    """Plot one numeric series. Uses plotext when available (coloured line with
    custom x tick labels), else the plain ASCII ``line_chart``.

    ``xticks`` is a list of (x position, label) for the x axis (e.g. dates)."""
    vals = [v for v in ys if v is not None]
    if not vals:
        return ["(no data to plot)"]
    try:
        import plotext as plt
    except ImportError:
        return line_chart(xs, ys, width=width or 72, height=height,
                          unit=ylabel, title=title)
    if width is None:
        width = max(40, min(100, term_width() - 2))
    # plotext can't skip None; plot only the present points
    px = [x for x, v in zip(xs, ys) if v is not None]
    py = [v for v in ys if v is not None]
    plt.clf()
    plt.theme("clear")
    plt.plot(px, py, color=color, label=ylabel or None)
    if xticks:
        pos = [p for p, _ in xticks]
        lab = [l for _, l in xticks]
        plt.xticks(pos, lab)
    if int_y:
        import math
        yt = _int_ticks(math.floor(min(py)), math.ceil(max(py)), 8)
        plt.yticks(yt, [str(t) for t in yt])
    if title:
        plt.title(title)
    plt.plotsize(width, height)
    return plt.build().split("\n")


def profile_chart(times: list[float], depths: list[float],
                  temps: list[float | None] | None = None, *,
                  show_temp: bool = False, width: int | None = None,
                  height: int = 18, title: str | None = None) -> list[str]:
    """Depth profile (surface at top), optionally overlaying temperature.

    Temperature is only drawn when ``show_temp`` is true and the dive logged it.
    Uses ``plotext`` for a coloured, dual-axis chart (depth in blue on the left,
    temperature in red on the right) when it is installed; otherwise falls back
    to a plain ASCII rendering. ``times`` in minutes, ``depths`` in metres,
    ``temps`` in °C (same length, None where missing)."""
    if not depths:
        return ["(no depth profile recorded)"]
    try:
        import plotext as plt
    except ImportError:
        return _ascii_profile(times, depths, temps, show_temp=show_temp,
                              width=width, height=height)

    if width is None:
        width = max(40, min(90, term_width() - 2))
    plt.clf()
    plt.theme("clear")
    # negate depth so the surface (0) sits at the top; relabel ticks positive
    plt.plot(times, [-d for d in depths], yside="left", color="blue+",
             label="depth (m)")

    # all axes use whole-number ticks (the dive computer has no decimals)
    dmax = int(max(depths)) or 1
    dticks = _int_ticks(0, dmax, 7)
    plt.yticks([-t for t in dticks], [str(t) for t in dticks], yside="left")

    tmax = int(round(times[-1])) or 1
    xticks = _int_ticks(0, tmax, 6)
    plt.xticks(xticks, [str(t) for t in xticks])

    have_temp = show_temp and bool(temps) and any(t is not None for t in temps)
    if have_temp:
        xt = [x for x, t in zip(times, temps) if t is not None]  # type: ignore[arg-type]
        yt = [t for t in temps if t is not None]                 # type: ignore[union-attr]
        plt.plot(xt, yt, yside="right", color="red+", label="temp (°C)")
        import math
        tticks = _int_ticks(math.floor(min(yt)), math.ceil(max(yt)), 7)
        plt.yticks(tticks, [str(t) for t in tticks], yside="right")

    plt.xlabel("time (min)")
    if title:
        plt.title(title)
    plt.plotsize(width, height)
    return plt.build().split("\n")


def _ascii_profile(times: list[float], depths: list[float],
                   temps: list[float | None] | None = None, *,
                   show_temp: bool = False, width: int | None = None,
                   height: int = 16) -> list[str]:
    """Dependency-free fallback: depth silhouette, temperature when requested."""
    if width is None:
        width = max(30, min(72, term_width() - 12))
    dmax = max(depths) or 1.0
    grid = _Grid(width, height)
    _plot_series(grid, _resample(times, depths, width), 0.0, dmax, "•",
                 top_is_min=True)
    have_temp = show_temp and bool(temps) and any(t is not None for t in temps)
    if have_temp:
        tv = [t for t in temps if t is not None]  # type: ignore[union-attr]
        tlo, thi = min(tv), max(tv)
        if tlo == thi:
            tlo, thi = tlo - 1, thi + 1
        _plot_series(grid, _resample(times, temps, width), tlo, thi, "·",
                     top_is_min=False)
    lab = [f"{(i / (height - 1)) * dmax:5.1f}" for i in range(height)]
    wlab = max(len(s) for s in lab)
    out = ["", "Depth profile  (• depth m" +
           ("   · temp °C)" if have_temp else ")")]
    for i, line in enumerate(grid.rows()):
        out.append(f"{lab[i]:>{wlab}} │{line}")
    out.append(" " * wlab + " └" + "─" * width)
    foot = f"{' ' * (wlab + 2)}0 … {times[-1]:.0f} min"
    if have_temp:
        foot += f"      temp {min(tv):.1f}–{max(tv):.1f} °C"
    out.append(foot)
    return out
