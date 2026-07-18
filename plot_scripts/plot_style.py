#!/usr/bin/env python3
import os
import warnings
warnings.filterwarnings("ignore", category=UserWarning, module="matplotlib")
import matplotlib.pyplot as plt
import matplotlib.font_manager as font_manager

# Font configuration
FONT_PATH = "/home/cc/Tectonic/LinLibertine_Mah.ttf"
if not os.path.exists(FONT_PATH):
    raise FileNotFoundError(f"Strict font file not found: {FONT_PATH}")

# Strictly load the custom font and set up fallback for missing small cap glyphs
font_manager.fontManager.addfont(FONT_PATH)
prop = font_manager.FontProperties(fname=FONT_PATH)
plt.rcParams["font.family"] = prop.get_name()
plt.rcParams["text.usetex"] = True
plt.rcParams["font.weight"] = "bold"

import re
import matplotlib.ticker as mticker

def to_small_caps(text):
    s = str(text).lower()
    s = re.sub(r'\bycsb\b', 'YCSB', s)
    s = re.sub(r'\b(\d+)\s*m\b', r'\1 M', s)
    s = re.sub(r'\bmillion\b', 'M', s)
    s = re.sub(r'\bseconds\b', 's', s)
    s = re.sub(r'\bmb\b', 'MB', s)
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

# Unified styles for YCSB, Tectonic, and KVBench
BAR_STYLES = {
    "Tectonic": {"facecolor": "tab:red", "edgecolor": "tab:red", "hatch": ""},
    "Tectonic Unique": {"facecolor": "tab:orange", "edgecolor": "tab:orange", "hatch": ""},
    "YCSB": {"facecolor": "white", "edgecolor": "grey", "hatch": "///"},
    "KVBench": {"facecolor": "white", "edgecolor": "tab:blue", "hatch": "\\\\"},
    "KVbench": {"facecolor": "white", "edgecolor": "tab:blue", "hatch": "\\\\"}
}

LINE_STYLES = {
    "Tectonic": {"color": "tab:red", "linestyle": "-.", "marker": "s", "markersize": 8, "markerfacecolor": "none"},
    "Tectonic Unique": {"color": "tab:orange", "linestyle": ":", "marker": "o", "markersize": 8, "markerfacecolor": "none"},
    "YCSB": {"color": "grey", "linestyle": "-", "marker": "^", "markersize": 8, "markerfacecolor": "none"},
    "KVBench": {"color": "tab:blue", "linestyle": "--", "marker": "v", "markersize": 8, "markerfacecolor": "none"},
    "KVbench": {"color": "tab:blue", "linestyle": "--", "marker": "v", "markersize": 8, "markerfacecolor": "none"}
}

def get_seq_par_style(generator, setting):
    """
    Returns appropriate style (dict) for a generator ('YCSB', 'Tectonic', 'KVBench')
    and setting ('sequential', 'parallel').
    """
    if generator.lower() == 'tectonic':
        color = 'tab:red'
    elif generator.lower() in ('tectonic_unique', 'tectonic unique'):
        color = 'tab:orange'
    elif generator.lower() in ('kvbench', 'kvbench'):
        color = 'tab:blue'
    else:
        color = 'grey'
        
    if setting == 'sequential':
        hatch = '///' if generator.lower() not in ('kvbench', 'tectonic_unique', 'tectonic unique') else '\\\\'
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
    base = LINE_STYLES.get(generator, LINE_STYLES['Tectonic']).copy()
    if setting == 'sequential':
        base['markerfacecolor'] = 'none'
    else:
        base['markerfacecolor'] = base['color']
    return base

# Premium aesthetic defaults
plt.rcParams["font.size"] = 20
plt.rcParams["axes.titlesize"] = 20
plt.rcParams["axes.labelsize"] = 20
plt.rcParams["xtick.labelsize"] = 20
plt.rcParams["ytick.labelsize"] = 20
plt.rcParams["legend.fontsize"] = 20
plt.rcParams["legend.frameon"] = False
plt.rcParams["figure.titlesize"] = 20
plt.rcParams["savefig.dpi"] = 300
plt.rcParams["axes.grid"] = False

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
        ax.set_title(format_label(title), pad=12, fontweight="bold")
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
    legend = fig_leg.legend(handles, labels, loc='center', frameon=False, ncol=ncol)
    
    fig_leg.canvas.draw()
    bbox = legend.get_window_extent()
    bbox = bbox.transformed(fig_leg.dpi_scale_trans.inverted())
    fig_leg.set_size_inches(bbox.width + 0.4, bbox.height + 0.4)
    
    fig_leg.savefig(f"{path_without_ext}_legend.pdf", bbox_inches='tight')
    fig_leg.savefig(f"{path_without_ext}_legend.png", bbox_inches='tight', dpi=300)
    plt.close(fig_leg)

def save_fig(fig, path_without_ext):
    """
    Saves the figure in both PDF and PNG formats.
    """
    plt.tight_layout()
    fig.savefig(f"{path_without_ext}.pdf", bbox_inches="tight")
    fig.savefig(f"{path_without_ext}.png", bbox_inches="tight", dpi=300)
    plt.close(fig)

