"""
lakehouse_utils.py — Helper library for Trino lakehouse data analysis.

Pre-installed in every sandbox workspace at /home/user/lib/lakehouse_utils.py.
Provides consistent, styled output for tables and charts with minimal code.

Usage:
    from lib.lakehouse_utils import query_to_df, display_table, display_chart, save_chart, save_table

    df = query_to_df(rows, columns)
    display_table(df, title="My Table")
    fig = display_chart(df, x="month", y="revenue", kind="bar")
    save_chart(fig)
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Optional, Union

import pandas as pd
import numpy as np

# ---------------------------------------------------------------------------
# Color palette & defaults
# ---------------------------------------------------------------------------

# Corporate-neutral palette (blues/grays — works on any background)
PALETTE = [
    "#2E75B6",  # primary blue
    "#4BACC6",  # teal
    "#F4B183",  # warm orange
    "#A5A5A5",  # gray
    "#70AD47",  # green
    "#ED7D31",  # dark orange
    "#5B9BD5",  # light blue
    "#FFC000",  # gold
    "#44546A",  # dark gray-blue
    "#C55A11",  # brown-orange
]

_FONT_FAMILY = "Arial, Helvetica, sans-serif"
_OUTPUT_DIR = "/home/user/output"


def _ensure_output_dir(path: str) -> str:
    """Create parent directories for an output path."""
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    return path


def _auto_rename(path: str) -> str:
    """
    If ``path`` already exists, return a collision-free variant like
    ``chart(1).html``, ``chart(2).html``, etc.  Otherwise return as-is.
    """
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    n = 1
    while True:
        candidate = f"{base}({n}){ext}"
        if not os.path.exists(candidate):
            return candidate
        n += 1


# ---------------------------------------------------------------------------
# query_to_df — Convert JSON rows from run_query into a typed DataFrame
# ---------------------------------------------------------------------------

_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(T\d{2}:\d{2})?")


def query_to_df(
    rows: list[dict[str, Any]],
    columns: list[str] | None = None,
) -> pd.DataFrame:
    """
    Convert JSON rows (from MCP run_query result) to a typed DataFrame.

    Args:
        rows:    List of dicts, e.g. [{"col1": val, "col2": val}, ...]
        columns: Optional column order. If None, inferred from first row.

    Returns:
        pd.DataFrame with auto-detected date columns and clean numeric types.
    """
    if not rows:
        return pd.DataFrame(columns=columns or [])

    df = pd.DataFrame(rows)

    if columns:
        # Reorder / filter to requested columns
        present = [c for c in columns if c in df.columns]
        df = df[present]

    # Auto-detect date columns (ISO 8601 string patterns)
    for col in df.columns:
        if df[col].dtype == object:
            sample = df[col].dropna().head(20)
            if len(sample) > 0 and sample.apply(
                lambda v: isinstance(v, str) and bool(_ISO_DATE_RE.match(v))
            ).mean() > 0.8:
                try:
                    df[col] = pd.to_datetime(df[col], errors="coerce")
                except Exception:
                    pass

    # Coerce numeric-looking object columns
    for col in df.select_dtypes(include=["object"]).columns:
        try:
            converted = pd.to_numeric(df[col], errors="coerce")
            if converted.notna().sum() > 0.5 * len(df):
                df[col] = converted
        except Exception:
            pass

    return df


# ---------------------------------------------------------------------------
# display_table — Render a styled HTML table
# ---------------------------------------------------------------------------

_TABLE_CSS = """
<style>
.lh-table-container { font-family: %(font)s; margin: 16px 0; }
.lh-table-title { font-size: 16px; font-weight: bold; margin-bottom: 8px; color: #333; }
.lh-table {
    border-collapse: collapse; width: 100%%; font-size: 13px;
}
.lh-table th {
    background: #2E75B6; color: white; padding: 8px 12px;
    text-align: left; font-weight: 600; border: 1px solid #2060A0;
}
.lh-table td {
    padding: 6px 12px; border: 1px solid #ddd; color: #333;
}
.lh-table tr:nth-child(even) { background: #F5F7FA; }
.lh-table tr:hover { background: #E8F0FE; }
.lh-table .null-val { color: #999; font-style: italic; background: #FFF5F5; }
.lh-table .num-val { text-align: right; font-variant-numeric: tabular-nums; }
.lh-footer { font-size: 12px; color: #888; margin-top: 6px; }
</style>
"""


def _fmt_value(val: Any) -> tuple[str, str]:
    """Format a cell value. Returns (html_string, css_class)."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "<em>NULL</em>", "null-val"
    if isinstance(val, (int, np.integer)):
        return f"{val:,}", "num-val"
    if isinstance(val, (float, np.floating)):
        return f"{val:,.2f}", "num-val"
    return str(val), ""


def display_table(
    df: pd.DataFrame,
    title: str | None = None,
    max_rows: int = 50,
    output_path: str | None = None,
) -> str:
    """
    Render a styled HTML table and write to disk.

    Args:
        df:          DataFrame to render.
        title:       Optional title shown above the table.
        max_rows:    Truncate display after this many rows.
        output_path: Where to write the HTML file.
                     Defaults to /home/user/output/table.html

    Returns:
        The output file path.
    """
    if output_path is None:
        output_path = f"{_OUTPUT_DIR}/table.html"
    _ensure_output_dir(output_path)
    output_path = _auto_rename(output_path)

    total_rows = len(df)
    show_df = df.head(max_rows)

    # Build HTML
    parts = [_TABLE_CSS % {"font": _FONT_FAMILY}, '<div class="lh-table-container">']
    if title:
        parts.append(f'<div class="lh-table-title">{title}</div>')

    parts.append('<table class="lh-table"><thead><tr>')
    for col in show_df.columns:
        parts.append(f"<th>{col}</th>")
    parts.append("</tr></thead><tbody>")

    for _, row in show_df.iterrows():
        parts.append("<tr>")
        for col in show_df.columns:
            html, cls = _fmt_value(row[col])
            cls_attr = f' class="{cls}"' if cls else ""
            parts.append(f"<td{cls_attr}>{html}</td>")
        parts.append("</tr>")

    parts.append("</tbody></table>")

    if total_rows > max_rows:
        parts.append(
            f'<div class="lh-footer">Showing {max_rows} of {total_rows:,} rows</div>'
        )
    parts.append(
        f'<div class="lh-footer">{total_rows:,} rows × {len(df.columns)} columns</div>'
    )
    parts.append("</div>")

    html = "\n".join(parts)
    Path(output_path).write_text(html, encoding="utf-8")
    print(f"[OUTPUT_HTML:{output_path}]")
    print(f"Table: {title or '(untitled)'} ({total_rows}×{len(df.columns)}) → {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# display_chart / save_chart — Plotly-based charting with consistent style
# ---------------------------------------------------------------------------

def display_chart(
    df: pd.DataFrame,
    x: str,
    y: Union[str, list[str]],
    kind: str = "bar",
    title: str | None = None,
    color: str | None = None,
    output_path: str | None = None,
    **plotly_kwargs,
):
    """
    Create a plotly Figure with consistent corporate styling.
    Automatically saves to HTML for inline rendering in the chatbot UI.

    Args:
        df:     DataFrame with the data.
        x:      Column name for the x-axis.
        y:      Column name (or list) for the y-axis.
        kind:   Chart type: bar, barh, line, scatter, pie, histogram, heatmap
        title:  Chart title.
        color:  Optional column for color grouping.
        output_path: Where to save the HTML. Defaults to /home/user/output/chart.html
        **plotly_kwargs: Extra args passed to the plotly express function.

    Returns:
        plotly.graph_objects.Figure — you can modify it further before saving.
    """
    import plotly.express as px
    import plotly.graph_objects as go

    y_list = [y] if isinstance(y, str) else y

    # Map kind to plotly express function
    common = dict(
        data_frame=df,
        title=title,
        color_discrete_sequence=PALETTE,
        **plotly_kwargs,
    )

    if kind == "bar":
        fig = px.bar(x=x, y=y_list[0], color=color, barmode="group", **common)
    elif kind == "barh":
        fig = px.bar(x=y_list[0], y=x, color=color, orientation="h", barmode="group", **common)
    elif kind == "line":
        if len(y_list) == 1:
            fig = px.line(x=x, y=y_list[0], color=color, markers=True, **common)
        else:
            # Multi-line: melt to long form
            melted = df.melt(id_vars=[x], value_vars=y_list, var_name="series", value_name="value")
            fig = px.line(data_frame=melted, x=x, y="value", color="series",
                          markers=True, title=title, color_discrete_sequence=PALETTE, **plotly_kwargs)
    elif kind == "scatter":
        fig = px.scatter(x=x, y=y_list[0], color=color, **common)
    elif kind == "pie":
        fig = px.pie(names=x, values=y_list[0], **common)
    elif kind == "histogram":
        fig = px.histogram(x=x, color=color, **common)
    elif kind == "heatmap":
        if len(y_list) >= 2:
            fig = px.density_heatmap(x=x, y=y_list[0], z=y_list[1] if len(y_list) > 1 else None, **common)
        else:
            fig = px.density_heatmap(x=x, y=y_list[0], **common)
    else:
        raise ValueError(f"Unknown chart kind: '{kind}'. Use: bar, barh, line, scatter, pie, histogram, heatmap")

    # Apply consistent layout
    fig.update_layout(
        font_family=_FONT_FAMILY,
        font_size=12,
        title_font_size=16,
        title_font_color="#333",
        plot_bgcolor="white",
        paper_bgcolor="white",
        xaxis=dict(showgrid=True, gridcolor="#EEEEEE", gridwidth=1),
        yaxis=dict(showgrid=True, gridcolor="#EEEEEE", gridwidth=1),
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=-0.25,
            xanchor="center",
            x=0.5,
        ),
        margin=dict(l=60, r=30, t=60, b=60),
    )

    # Auto-save to HTML for inline rendering in the chatbot UI
    save_path = output_path or f"{_OUTPUT_DIR}/chart.html"
    save_chart(fig, output_path=save_path)

    return fig


def save_chart(
    fig,
    output_path: str | None = None,
) -> dict:
    """
    Save a plotly Figure to an interactive HTML file.

    Args:
        fig:         plotly Figure object.
        output_path: Path for the HTML file.
                     Defaults to /home/user/output/chart.html

    Returns:
        {"html": path}
    """
    if output_path is None:
        output_path = f"{_OUTPUT_DIR}/chart.html"
    _ensure_output_dir(output_path)
    output_path = _auto_rename(output_path)

    # Write interactive HTML (CDN keeps file small)
    fig.write_html(output_path, include_plotlyjs="cdn")
    print(f"[OUTPUT_HTML:{output_path}]")
    print(f"Chart saved → {output_path}")

    return {"html": output_path}


# ---------------------------------------------------------------------------
# save_table — Export DataFrame to csv / xlsx / json
# ---------------------------------------------------------------------------

def save_table(
    df: pd.DataFrame,
    output_path: str,
    fmt: str = "csv",
) -> str:
    """
    Export a DataFrame to a file.

    Args:
        df:          DataFrame to export.
        output_path: Destination file path.
        fmt:         Format: 'csv', 'xlsx', 'json'

    Returns:
        The output file path.
    """
    _ensure_output_dir(output_path)

    if fmt == "csv":
        df.to_csv(output_path, index=False)
    elif fmt == "xlsx":
        df.to_excel(output_path, index=False, engine="openpyxl")
    elif fmt == "json":
        df.to_json(output_path, orient="records", indent=2, default_handler=str)
    else:
        raise ValueError(f"Unknown format: '{fmt}'. Use: csv, xlsx, json")

    size = os.path.getsize(output_path)
    print(f"Exported ({fmt}) → {output_path} ({size:,} bytes)")
    return output_path
