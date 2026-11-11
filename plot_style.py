#!/usr/bin/env python3
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
import matplotlib.pyplot as plt

# Font: with usetex, LaTeX typesets all text and ignores font.family and
# font.weight, so the face is chosen in the LaTeX preamble: Linux Libertine with
# matching math, as in the ACM (acmart) paper body. Regular weight throughout.
plt.rcParams["text.usetex"] = True
plt.rcParams["text.latex.preamble"] = r"\usepackage{libertine}\usepackage[libertine]{newtxmath}"
plt.rcParams["font.family"] = "serif"

import re
import matplotlib.ticker as mticker

def to_small_caps(text):
    s = str(text).lower()
    s = re.sub(r'\bycsb\b', 'YCSB', s)
    s = re.sub(r'\b(\d+)\s*m\b', r'\1 M', s)
    s = re.sub(r'\bmillion\b', 'M', s)
    s = re.sub(r'\bseconds\b', 's', s)
    s = re.sub(r'\bmb\b', 'MB', s)
    s = re.sub(r'\bgb\b', 'GB', s)
    return s

def format_label(text):
    return to_small_caps(text)

def format_number_clean(y):
    # Check if y is close to an integer
    if abs(y - round(y)) < 1e-9:
        return f"{int(round(y))}"
    s = f"{y:.2f}"
    if s.endswith(".00"):
        return s[:-3]
    if s.endswith("0") and "." in s:
        return s[:-1]
    return s

# Unified styles for YCSB, XBench, and KVBench
BAR_STYLES = {
    "XBench": {"facecolor": "tab:blue", "edgecolor": "tab:blue", "hatch": ""},
    "XBench Unique": {"facecolor": "tab:orange", "edgecolor": "tab:orange", "hatch": ""},
    "YCSB": {"facecolor": "white", "edgecolor": "grey", "hatch": "///////"},
    "KVBench": {"facecolor": "white", "edgecolor": "tab:blue", "hatch": "\\\\\\"},
    "KVbench": {"facecolor": "white", "edgecolor": "tab:blue", "hatch": "\\\\\\"},
    "db_bench": {"facecolor": "white", "edgecolor": "black", "hatch": ".."}
}

LINE_STYLES = {
    "XBench": {"color": "tab:blue", "linestyle": "-.", "marker": "s", "markersize": 15, "markerfacecolor": "none"},
    "XBench Unique": {"color": "tab:orange", "linestyle": ":", "marker": "o", "markersize": 15, "markerfacecolor": "none"},
    "YCSB": {"color": "grey", "linestyle": "-", "marker": "^", "markersize": 15, "markerfacecolor": "none"},
    "KVBench": {"color": "tab:blue", "linestyle": "--", "marker": "v", "markersize": 15, "markerfacecolor": "none"},
    "KVbench": {"color": "tab:blue", "linestyle": "--", "marker": "v", "markersize": 15, "markerfacecolor": "none"}
}

def get_seq_par_style(generator, setting):
    """
    Returns appropriate style (dict) for a generator ('YCSB', 'XBench', 'KVBench')
    and setting ('sequential', 'parallel').
    """
    if generator.lower() == 'xbench':
        color = 'tab:red'
    elif generator.lower() in ('xbench_unique', 'xbench unique'):
        color = 'tab:orange'
    elif generator.lower() in ('kvbench', 'kvbench'):
        color = 'tab:blue'
    else:
        color = 'grey'
        
    if setting == 'sequential':
        hatch = '///' if generator.lower() not in ('kvbench', 'xbench_unique', 'xbench unique') else '\\\\'
        return {
            "facecolor": "white",
            "edgecolor": color,
            "hatch": hatch
        }
    else:
        return {
            "facecolor": color,
            "edgecolor": color,
            "hatch": ""
        }

def get_seq_par_line_style(generator, setting):
    base = LINE_STYLES.get(generator, LINE_STYLES['XBench']).copy()
    if setting == 'sequential':
        base['markerfacecolor'] = 'none'
    else:
        base['markerfacecolor'] = base['color']
    return base

# ---------------------------------------------------------------------------
# Paper figure style, shared by every figure script so all figures match:
# same text sizes, line widths, canvas and plot-frame size.
# ---------------------------------------------------------------------------
FONT_SIZE = 18             # axis labels, tick labels, legend text
ANNOTATION_FONTSIZE = 13   # small values written on or above the data
AXES_LINEWIDTH = 0.8       # plot frame
BAR_EDGE_WIDTH = 0.6       # outline and hatch lines of unfilled bars
# Canvas, in inches. The plot frame sits at fixed margins (inches, not
# fractions) so its size is identical in every figure; save_figure() then
# crops the unused margin.
FIGSIZE = (5, 3.4)
AXES_MARGINS_IN = {"left": 1.3, "right": 0.15, "bottom": 0.97, "top": 0.22}
TOP_PAD = 0.2              # canvas space above the plot frame, in inches
YLABEL_TOP_DROP_IN = 0.2   # long y-labels start this far below the canvas top
XLABEL_PAD = 0             # points between x tick labels and the x-axis label
PANEL_TAG_SHIFT_PT = 4     # "(A)" tags sit this far right of the y-label's left edge
# Canvas depth below the plot frame for x tick labels plus an x-label (18 pt),
# as measured in the figures that have both. Panels without an x-label are
# padded to it (save_figure(..., match_xlabel_depth=True)) so all canvases have
# the same height and their frames line up side by side.
CANVAS_BELOW_FRAME_IN = 40.907 / 72
# Figures with nothing drawn above the frame need only enough canvas space above
# it for the tallest top tick label (10^4); figures that write values above the
# frame (Fig. 7) use TOP_PAD.
TIGHT_TOP_PAD = 9 / 72

# Line styles per tool, shared by all line plots (open markers in the line color).
LINE_WIDTH = 2.0
MARKER_SIZE = 7
LINE_TOOL_STYLES = {
    "ycsb": ("YCSB", {"color": "grey", "linestyle": "-", "marker": "^"}),
    "xbench_untracked": ("X-Bench (untracked)", {"color": "tab:red", "linestyle": "-.", "marker": "s"}),
    "xbench": ("X-Bench", {"color": "tab:blue", "linestyle": ":", "marker": "o"}),
}


def bar_kwargs(tool):
    """ax.bar / Patch keyword arguments for a BAR_STYLES tool: its fill and hatch,
    a thin black outline on unfilled bars, none on solid-filled ones (edge color
    stays black so any hatch still draws in black)."""
    style = BAR_STYLES[tool]
    solid = style["facecolor"] != "white"
    return {"facecolor": style["facecolor"], "hatch": style["hatch"], "edgecolor": "black",
            "linewidth": 0 if solid else BAR_EDGE_WIDTH}


def line_kwargs(tool, **extra):
    """ax.plot / Line2D keyword arguments for a tool's line."""
    label, style = LINE_TOOL_STYLES[tool]
    return {"label": label, "linewidth": LINE_WIDTH, "markersize": MARKER_SIZE,
            "markerfacecolor": "none", "markeredgecolor": style["color"], **style, **extra}

plt.rcParams.update({
    "font.size": FONT_SIZE,
    "axes.titlesize": FONT_SIZE,
    "axes.labelsize": FONT_SIZE,
    "xtick.labelsize": FONT_SIZE,
    "ytick.labelsize": FONT_SIZE,
    "legend.fontsize": FONT_SIZE,
    "figure.titlesize": FONT_SIZE,
    "legend.frameon": False,
    "axes.grid": False,
    "axes.edgecolor": "black",
    "axes.linewidth": AXES_LINEWIDTH,
    "xtick.direction": "out",
    "ytick.direction": "out",
    "hatch.linewidth": BAR_EDGE_WIDTH,
    "hatch.color": "black",
    "savefig.dpi": 300,
    # Crop saved figures and legends to their content. The pad is half the
    # frame line width: the minimum that keeps the outer frame from clipping.
    "savefig.pad_inches": AXES_LINEWIDTH / 2 / 72,
})


def apply_frame_margins(fig, left_in=None):
    """Place the plot frame at AXES_MARGINS_IN (left_in overrides the left margin)."""
    w, h = fig.get_size_inches()
    m = AXES_MARGINS_IN
    left = m["left"] if left_in is None else left_in
    fig.subplots_adjust(left=left / w, right=1 - m["right"] / w,
                        bottom=m["bottom"] / h, top=1 - m["top"] / h)


def anchor_ylabel_to_canvas_top(ax, text, drop_in=YLABEL_TOP_DROP_IN):
    """A y-label longer than the frame: its top starts YLABEL_TOP_DROP_IN below
    the canvas top and it runs down past the frame bottom, rather than being
    trimmed. Assumes FIGSIZE and AXES_MARGINS_IN."""
    ax.set_ylabel(text, loc="top")
    frame_h = FIGSIZE[1] - AXES_MARGINS_IN["top"] - AXES_MARGINS_IN["bottom"]
    raise_in = TOP_PAD - plt.rcParams["savefig.pad_inches"] - drop_in
    ax.yaxis.label.set_y(1 + raise_in / frame_h)


def add_panel_tag(fig, ax, tag, right_of_ylabel=False):
    """Sub-figure tag such as "(A)", always bold, in the bottom-left corner of the
    canvas: left edge PANEL_TAG_SHIFT_PT right of the y-label, bottom level with
    the x-label. Call after the frame margins and labels are set.

    right_of_ylabel: start the tag right of the y-label instead, for a y-label
    that runs the full canvas height and would otherwise be covered."""
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    to_fig = fig.transFigure.inverted()
    ylabel_box = ax.yaxis.label.get_window_extent(renderer)
    left = to_fig.transform(ylabel_box.p1 if right_of_ylabel else ylabel_box.p0)[0]
    if ax.xaxis.label.get_text():
        bottom = to_fig.transform(ax.xaxis.label.get_window_extent(renderer).p0)[1]
    else:
        # No x-label: the bottom row of a canvas padded to CANVAS_BELOW_FRAME_IN.
        frame_y0 = ax.get_window_extent(renderer).transformed(fig.transFigure.inverted()).y0
        bottom = frame_y0 - (CANVAS_BELOW_FRAME_IN - plt.rcParams["savefig.pad_inches"]) / fig.get_size_inches()[1]
    left += PANEL_TAG_SHIFT_PT / 72 / fig.get_size_inches()[0]
    # usetex ignores fontweight, so bold is set in LaTeX.
    fig.text(left, bottom, rf"\textbf{{{tag}}}", ha="left", va="bottom")


def save_figure(fig, ax, out_path, top_pad=TOP_PAD, extra_artists=(), match_xlabel_depth=False):
    """Crop to the content and save; returns the crop box (inches).

    The tight crop ignores how far an axis label extends along its axis, so the
    y-label is included explicitly. The top edge is top_pad (default TOP_PAD)
    above the plot frame; top_pad=None ends the canvas at the topmost element
    instead (no padding). A fixed top_pad keeps the same top edge in every
    figure, so frames line up side by side and the top tick label is not cut off.
    """
    from matplotlib.transforms import Bbox
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    box = fig.get_tightbbox(renderer, bbox_extra_artists=[ax.yaxis.label, *extra_artists])
    frame = ax.get_window_extent(renderer).transformed(fig.dpi_scale_trans.inverted())
    pad = plt.rcParams["savefig.pad_inches"]
    y0 = box.y0 - pad
    if match_xlabel_depth:
        y0 = min(y0, frame.y0 - CANVAS_BELOW_FRAME_IN)
    top = box.y1 + pad if top_pad is None else frame.y1 + top_pad
    box = Bbox.from_extents(box.x0 - pad, y0, box.x1 + pad, top)
    fig.savefig(out_path, bbox_inches=box)
    plt.close(fig)
    print(f"wrote {out_path}")
    return box


def save_legend_figure(handles, out_path, **legend_kwargs):
    """A standalone legend, cropped with no whitespace around it."""
    fig = plt.figure()
    legend_kwargs.setdefault("ncol", len(handles))
    fig.legend(handles=handles, loc="center", borderpad=0, **legend_kwargs)
    fig.savefig(out_path, bbox_inches="tight")
    plt.close(fig)
    print(f"wrote {out_path}")

# Cohesive Palette for the four databases
DB_COLORS = {
    "RocksDB": "#1f77b4",    # Slate Blue
    "Redis": "#d62728",      # Elegant Red
    "Cassandra": "#9467bd",  # Royal Purple
    "ScyllaDB": "#17becf",   # Clean Teal
}

# Hatching patterns to ensure readability in black-and-white
DB_HATCHES = {
    "RocksDB": "",           # Solid
    "Redis": "//",          # Striped
    "Cassandra": "..",       # Dotted
    "ScyllaDB": "xx",        # Cross-hatched
}

def apply_plot_style(ax, title=None, xlabel=None, ylabel=None):
    """
    Applies consistent grids, titles, borders, and margins to the given Axes,
    and strictly enforces start limits for linear (0.0) and log (10^0) scales.
    """
    if title:
        ax.set_title(format_label(title), pad=12)
    if xlabel:
        ax.set_xlabel(format_label(xlabel), labelpad=8)
    if ylabel:
        ax.set_ylabel(format_label(ylabel), labelpad=8)
    
    # Enforce axis limits on y-axis
    if ax.get_yscale() == 'log':
        ax.set_ylim(bottom=10**0)
    else:
        ax.set_ylim(bottom=0.0)
        # Ensure 0 is explicitly ticked/labeled on y-axis
        yticks = list(ax.get_yticks())
        if 0.0 not in yticks:
            yticks = [y for y in yticks if y >= 0]
            yticks.insert(0, 0.0)
            ax.set_yticks(yticks)
        # Apply clean number formatter to y-axis
        ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda y, _: format_number_clean(y)))
            
    # Enforce axis limits on x-axis
    if ax.get_xscale() == 'log':
        ax.set_xlim(left=10**0)
    elif ax.get_xscale() == 'linear':
        is_categorical = False
        try:
            import matplotlib.ticker as ticker
            locator = ax.xaxis.get_major_locator()
            if isinstance(locator, ticker.FixedLocator) or "Category" in type(locator).__name__:
                is_categorical = True
        except:
            pass
        if not is_categorical:
            ax.set_xlim(left=0.0)
            # Ensure 0 is explicitly ticked/labeled on x-axis
            xticks = list(ax.get_xticks())
            if 0.0 not in xticks:
                xticks = [x for x in xticks if x >= 0]
                xticks.insert(0, 0.0)
                ax.set_xticks(xticks)
            # Apply clean number formatter to x-axis
            ax.xaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: format_number_clean(x)))

    # Clean up grid and borders (spines)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(True)
        spine.set_color("black")
        spine.set_linewidth(1.0)
    ax.tick_params(colors="black", which="both", direction="out")

def save_legend(fig_or_ax, path_without_ext):
    """
    Extracts the legend from the figure or axis, saves it to a separate PDF file,
    and removes it from the original plot to prevent overlapping.
    """
    handles = []
    labels = []
    
    # Helper to extract from legend object safely
    def extract_from_legend(legend):
        h_list = []
        l_list = []
        if legend:
            # Matplotlib version compatibility for legend handles
            leg_handles = getattr(legend, "legend_handles", None) or getattr(legend, "legendHandles", [])
            for handle, text_obj in zip(leg_handles, legend.get_texts()):
                h_list.append(handle)
                l_list.append(text_obj.get_text())
        return h_list, l_list

    # Handle figure
    if hasattr(fig_or_ax, 'axes') and isinstance(fig_or_ax.axes, list):
        for ax in fig_or_ax.axes:
            legend = ax.get_legend()
            if legend:
                h, l = extract_from_legend(legend)
                for handle, label in zip(h, l):
                    if label not in labels:
                        handles.append(handle)
                        labels.append(label)
                legend.remove()
            else:
                h, l = ax.get_legend_handles_labels()
                for handle, label in zip(h, l):
                    if label not in labels:
                        handles.append(handle)
                        labels.append(label)
                        
        for legend in list(fig_or_ax.legends):
            h, l = extract_from_legend(legend)
            for handle, label in zip(h, l):
                if label not in labels:
                    handles.append(handle)
                    labels.append(label)
            fig_or_ax.legends.remove(legend)
    # Handle single axis
    else:
        legend = fig_or_ax.get_legend()
        if legend:
            h, l = extract_from_legend(legend)
            handles.extend(h)
            labels.extend(l)
            legend.remove()
        elif hasattr(fig_or_ax, 'get_legend_handles_labels'):
            handles, labels = fig_or_ax.get_legend_handles_labels()
            
    if not handles:
        return
        
    ncol = 1
    if len(handles) > 4:
        ncol = min(4, len(handles))
        
    fig_leg = plt.figure(figsize=(ncol * 2.5, 1.0))
    fig_leg.legend(handles, labels, loc='center', frameon=False, ncol=ncol, borderpad=0)

    fig_leg.savefig(f"{path_without_ext}_legend.pdf", bbox_inches='tight')
    plt.close(fig_leg)

def save_fig(fig, path_without_ext):
    """
    Saves the figure in PDF format only.
    """
    plt.tight_layout()
    fig.savefig(f"{path_without_ext}.pdf", bbox_inches="tight")
    plt.close(fig)

