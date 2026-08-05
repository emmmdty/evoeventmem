"""Minimal dependency-free SVG plotting for report figures."""

from __future__ import annotations

import html
from collections.abc import Iterable, Sequence


def escape(value: str) -> str:
    return html.escape(value, quote=True)


def _text(
    x: float,
    y: float,
    content: str,
    *,
    anchor: str = "start",
    size: int = 11,
) -> str:
    return (
        f'<text x="{x}" y="{y}" text-anchor="{anchor}" '
        f'font-size="{size}" font-family="sans-serif">{escape(content)}</text>'
    )


def bar_chart(
    *,
    title: str,
    categories: Sequence[str],
    values: Sequence[float],
    errors: Sequence[tuple[float, float]] | None = None,
    value_labels: Sequence[str] | None = None,
    height: int = 320,
    width: int = 720,
) -> str:
    """Horizontal bar chart with optional whiskers (low, high)."""
    margin_left = 16
    margin_right = 24
    margin_top = 28
    margin_bottom = 30
    label_width = 170
    plot_left = margin_left + label_width
    plot_width = width - plot_left - margin_right
    plot_height = height - margin_top - margin_bottom
    bar_height = 18
    gap = 12
    maximum = max(values) if values else 1.0
    row_height = bar_height + gap
    content_height = max(plot_height, len(categories) * row_height)
    total_height = content_height + margin_top + margin_bottom

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{total_height}">',
        _text(width / 2, 18, title, anchor="middle", size=13),
    ]
    for index, (category, value) in enumerate(zip(categories, values, strict=True)):
        y = margin_top + index * row_height
        bar_width = (value / maximum) * plot_width if maximum > 0 else 0.0
        parts.append(
            _text(plot_left - 6, y + 13, category, anchor="end")
        )
        parts.append(
            f'<rect x="{plot_left}" y="{y}" width="{bar_width:.1f}" '
            f'height="{bar_height}" fill="#4c78a8" rx="2"/>'
        )
        if errors is not None and errors[index] is not None:
            low, high = errors[index]
            low_x = plot_left + (low / maximum) * plot_width if maximum > 0 else plot_left
            high_x = plot_left + (high / maximum) * plot_width if maximum > 0 else plot_left
            parts.append(
                f'<line x1="{low_x:.1f}" y1="{y + bar_height / 2}" '
                f'x2="{high_x:.1f}" y2="{y + bar_height / 2}" '
                'stroke="#111" stroke-width="1.5"/>'
            )
            for edge_x in (low_x, high_x):
                parts.append(
                    f'<line x1="{edge_x:.1f}" y1="{y + 3}" '
                    f'x2="{edge_x:.1f}" y2="{y + bar_height - 3}" '
                    'stroke="#111" stroke-width="1.5"/>'
                )
        label = value_labels[index] if value_labels is not None else f"{value:.3f}"
        parts.append(_text(plot_left + bar_width + 6, y + 13, label))
    parts.append("</svg>")
    return "\n".join(parts)


def heatmap(
    *,
    title: str,
    row_labels: Sequence[str],
    column_labels: Sequence[str],
    values: Sequence[Sequence[float]],
    width: int = 720,
) -> str:
    """Cell values heatmap; rows are methods, columns are categories."""
    cell_width = 88
    cell_height = 26
    margin_left = 170
    margin_top = 46
    table_width = len(column_labels) * cell_width
    total_width = margin_left + table_width + 20
    total_height = margin_top + len(row_labels) * cell_height + 24

    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{total_width}" '
        f'height="{total_height}">',
        _text(total_width / 2, 18, title, anchor="middle", size=13),
    ]
    for column_index, label in enumerate(column_labels):
        x = margin_left + column_index * cell_width + cell_width / 2
        parts.append(_text(x, margin_top - 16, label, anchor="middle", size=10))
    for row_index, (row_label, row_values) in enumerate(
        zip(row_labels, values, strict=True)
    ):
        y = margin_top + row_index * cell_height
        parts.append(
            _text(margin_left - 6, y + cell_height - 7, row_label, anchor="end")
        )
        for column_index, value in enumerate(row_values):
            x = margin_left + column_index * cell_width
            intensity = min(1.0, value / 0.25) if value > 0 else 0.0
            red = int(255 * (1 - intensity) + 70 * intensity)
            green = int(215 * (1 - intensity) + 170 * intensity)
            blue = int(230 * (1 - intensity) + 150 * intensity)
            parts.append(
                f'<rect x="{x}" y="{y}" width="{cell_width - 2}" '
                f'height="{cell_height - 2}" fill="rgb({red},{green},{blue})" rx="2"/>'
            )
            parts.append(
                _text(
                    x + (cell_width - 2) / 2,
                    y + cell_height - 9,
                    f"{value:.3f}",
                    anchor="middle",
                    size=10,
                )
            )
    parts.append("</svg>")
    return "\n".join(parts)


def write_figure(path: object, svg: str) -> None:
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(svg + "\n", encoding="utf-8")
    temporary.replace(target)


def write_csv(path: object, rows: Iterable[Sequence[object]]) -> None:
    import csv
    from pathlib import Path

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(f".{target.name}.tmp")
    with temporary.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        for row in rows:
            writer.writerow(["" if value is None else value for value in row])
    temporary.replace(target)
