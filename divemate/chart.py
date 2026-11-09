"""Terminal charts (coloured, via ``plotext``).

``profile_chart`` draws a dive's depth profile (surface at the top, blue) with
temperature overlaid (red, right axis). ``series_chart`` plots a single numeric
series over time. Both use ``plotext``, which is a hard dependency.
"""

from __future__ import annotations

import math
import re
import shutil

import plotext as plt


def term_width(default: int = 80) -> int:
    try:
        return shutil.get_terminal_size((default, 24)).columns
    except Exception:
        return default


def _int_ticks(lo: int, hi: int, n: int) -> list[int]:
    """Up to ``n`` evenly spaced whole-number ticks spanning [lo, hi]."""
    if hi <= lo:
        return [lo]
    step = max(1, round((hi - lo) / (n - 1)))
    ticks = list(range(lo, hi + 1, step))
    if ticks[-1] != hi:
        ticks.append(hi)
    return ticks


def series_chart(xs: list[float], ys: list[float | None], *,
                 title: str | None = None, ylabel: str = "",
                 xticks: list[tuple[float, str]] | None = None,
                 color: str = "cyan+", width: int | None = None,
                 height: int = 18, int_y: bool = False) -> list[str]:
    """Plot one numeric series as a coloured line with custom x tick labels.

    ``xticks`` is a list of (x position, label) for the x axis (e.g. dates)."""
    vals = [v for v in ys if v is not None]
    if not vals:
        return ["(no data to plot)"]
    if width is None:
        width = max(40, min(100, term_width() - 2))
    # plotext can't skip None; plot only the present points
    px = [x for x, v in zip(xs, ys) if v is not None]
    py = [v for v in ys if v is not None]
    plt.clf()
    plt.theme("clear")
    plt.plot(px, py, color=color, label=ylabel or None)
    if xticks:
        plt.xticks([p for p, _ in xticks], [l for _, l in xticks])
    if int_y:
        yt = _int_ticks(math.floor(min(py)), math.ceil(max(py)), 8)
        plt.yticks(yt, [str(t) for t in yt])
    if title:
        plt.title(title)
    plt.plotsize(width, height)
    return plt.build().split("\n")


def profile_chart(times: list[float], depths: list[float],
                  temps: list[float | None] | None = None, *,
                  width: int | None = None, height: int = 18,
                  title: str | None = None) -> list[str]:
    """Depth profile (surface at top, blue on the left axis), with temperature
    overlaid (red on the right axis) when the dive logged it. ``times`` in
    minutes, ``depths`` in metres, ``temps`` in °C (same length, None where
    missing)."""
    if not depths:
        return ["(no depth profile recorded)"]
    if width is None:
        width = max(40, min(90, term_width() - 2))
    plt.clf()
    plt.theme("clear")

    # draw temperature FIRST so depth is plotted on top of it (plotext lets the
    # last series win a shared cell) — depth must stay readable where they cross.
    # plotext ties legend order to draw order, so disable its legend (no labels)
    # and render our own below, in the natural depth-left / temp-right order.
    have_temp = bool(temps) and any(t is not None for t in temps)
    if have_temp:
        xt = [x for x, t in zip(times, temps) if t is not None]  # type: ignore[arg-type]
        yt = [t for t in temps if t is not None]                 # type: ignore[union-attr]
        plt.plot(xt, yt, yside="right", color="red+")
        tticks = _int_ticks(math.floor(min(yt)), math.ceil(max(yt)), 7)
        plt.yticks(tticks, [str(t) for t in tticks], yside="right")

    # negate depth so the surface (0) sits at the top; relabel ticks positive
    plt.plot(times, [-d for d in depths], yside="left", color="blue+")
    dmax = int(max(depths)) or 1
    dticks = _int_ticks(0, dmax, 7)
    plt.yticks([-t for t in dticks], [str(t) for t in dticks], yside="left")

    tmax = int(round(times[-1])) or 1
    xticks = _int_ticks(0, tmax, 6)
    plt.xticks(xticks, [str(t) for t in xticks])

    # x-axis label is drawn by us in the footer (merged with the legend), so no
    # plt.xlabel() here
    if title:
        plt.title(title)
    plt.plotsize(width, height)
    lines = plt.build().split("\n")
    while lines and not lines[-1].strip():
        lines.pop()
    lines.extend(_profile_footer(lines, have_temp))
    return lines


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*m")


def _profile_footer(lines: list[str], have_temp: bool) -> list[str]:
    """Footer combining the x-axis label with the legend: depth label at the far
    left (blue), `time (min)` centred, temperature label at the far right (red).
    Falls back to two lines (centred label, then the labels) when it won't fit."""
    blue, red, reset = "\033[94m", "\033[91m", "\033[0m"
    width = max((len(_ANSI_RE.sub("", ln).rstrip()) for ln in lines), default=40)
    left, mid = "▬ depth (m)", "time (min)"
    right = "▬ temp (°C)" if have_temp else ""
    lL, lC, lR = len(left), len(mid), len(right)

    def colour(plain: str) -> str:
        """Colour the first lL chars blue and the last lR chars red."""
        tail = red + plain[width - lR:] + reset if lR else plain[width - lR:]
        return blue + plain[:lL] + reset + plain[lL:width - lR] + tail

    cstart = (width - lC) // 2
    if cstart >= lL + 1 and cstart + lC <= width - lR - (1 if lR else 0):
        buf = list(" " * width)
        for i, ch in enumerate(mid):
            buf[cstart + i] = ch
        for i, ch in enumerate(left):
            buf[i] = ch
        for i, ch in enumerate(right):
            buf[width - lR + i] = ch
        return [colour("".join(buf))]
    # fallback: centred label on its own line, then the labels left/right
    buf = list(" " * width)
    for i, ch in enumerate(left):
        buf[i] = ch
    for i, ch in enumerate(right):
        buf[width - lR + i] = ch
    return [" " * max(0, (width - lC) // 2) + mid, colour("".join(buf))]
