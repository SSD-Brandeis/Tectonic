import os
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker
import numpy as np
import plot_style

def parse_elapsed_wall_clock(time_str):
    """Parse 'h:mm:ss' or 'm:ss.ss' to seconds."""
    parts = time_str.strip().split(':')
    if len(parts) == 2:
        return float(parts[0]) * 60 + float(parts[1])
    elif len(parts) == 3:
        return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
    return 0.0

def parse_time_v_log(filepath):
    """Parse /usr/bin/time -v output. Uses wall clock time (not CPU time),
    so YCSB (multi-threaded JVM at ~760% CPU) is measured correctly."""
    elapsed = 0.0
    max_rss = 0.0

    with open(filepath, 'r') as f:
        for line in f:
            if "Elapsed (wall clock) time" in line:
                # Format: "\tElapsed (wall clock) time (h:mm:ss or m:ss): 0:05.11"
                # Split on "): " to isolate the time value
                time_str = line.split("): ")[-1].strip()
                elapsed += parse_elapsed_wall_clock(time_str)
            elif "Maximum resident set size (kbytes):" in line:
                # Format: "\tMaximum resident set size (kbytes): 1234567"
                rss = float(line.split(":")[-1].strip()) / 1024.0  # convert to MB
                if rss > max_rss:
                    max_rss = rss

    if elapsed == 0:
        elapsed = 0.001
    return elapsed, max_rss

def save_fig(fig, path_without_ext):
    """
    Saves the figure in PDF format only.
    """
    plt.tight_layout()
    fig.savefig(f"{path_without_ext}.pdf", bbox_inches="tight")
    plt.close(fig)

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
    plt.close(fig_leg)

def plot_fig3():
    workloads = ["A", "B", "C", "D", "E", "F"]
    generators = ["Tectonic", "YCSB", "KVBench"]
    
    times = {g: [] for g in generators}
    throughputs = {g: [] for g in generators}
    memory = {g: [] for g in generators}
    
    total_ops = 2000000 # 1M load + 1M run for YCSB A-F (approx)

    for w in workloads:
        for g in generators:
            if g == "KVBench" and w == "F":
                times[g].append(0)
                throughputs[g].append(0)
                memory[g].append(0)
                continue

            log_file = f"/home/cc/Tectonic/data/overall_benchmarks/logs_fig3/{g}_{w}.log"
            if os.path.exists(log_file):
                t, rss = parse_time_v_log(log_file)
                times[g].append(t)
                throughputs[g].append(total_ops / t)
                memory[g].append(rss)
            else:
                times[g].append(0)
                throughputs[g].append(0)
                memory[g].append(0)

    x = np.arange(len(workloads))
    width = 0.25

    fig, axs = plt.subplots(1, 3, figsize=(15, 4))

    # (A) Latency: log scale, y starts at 10^0 = 1, pure 10^x tick labels
    axs[0].bar(x - width, times["Tectonic"], width, label='Tectonic+', **plot_style.BAR_STYLES["Tectonic"])
    axs[0].bar(x, times["YCSB"], width, label='YCSB', **plot_style.BAR_STYLES["YCSB"])
    axs[0].bar(x + width, times["KVBench"], width, label='KVBench', **plot_style.BAR_STYLES["KVBench"])
    axs[0].set_xticks(x)
    axs[0].set_xticklabels(workloads)
    axs[0].set_yscale('log')
    axs[0].yaxis.set_major_locator(mticker.LogLocator(base=10))
    axs[0].yaxis.set_major_formatter(mticker.LogFormatterMathtext(base=10))
    axs[0].yaxis.set_minor_locator(mticker.NullLocator())
    plot_style.apply_plot_style(axs[0], title='(a) end-to-end latency', ylabel='end-to-end latency (s)')

    # (B) Throughput: linear scale, y starts at 0
    # Ticks in units of 10^5, annotation "10^5" at top-left corner inside plot
    axs[1].bar(x - width, [v/1e5 for v in throughputs["Tectonic"]], width, label='Tectonic+', **plot_style.BAR_STYLES["Tectonic"])
    axs[1].bar(x, [v/1e5 for v in throughputs["YCSB"]], width, label='YCSB', **plot_style.BAR_STYLES["YCSB"])
    axs[1].bar(x + width, [v/1e5 for v in throughputs["KVBench"]], width, label='KVBench', **plot_style.BAR_STYLES["KVBench"])
    axs[1].set_xticks(x)
    axs[1].set_xticklabels(workloads)
    axs[1].text(0.01, 0.99, r'$\times10^5$', transform=axs[1].transAxes,
                va='top', ha='left', fontsize=10)
    plot_style.apply_plot_style(axs[1], title='(b) operational throughput', ylabel='throughput (ops)')

    # (C) Memory: linear scale, y starts at 0
    axs[2].bar(x - width, memory["Tectonic"], width, label='Tectonic+', **plot_style.BAR_STYLES["Tectonic"])
    axs[2].bar(x, memory["YCSB"], width, label='YCSB', **plot_style.BAR_STYLES["YCSB"])
    axs[2].bar(x + width, memory["KVBench"], width, label='KVBench', **plot_style.BAR_STYLES["KVBench"])
    axs[2].set_xticks(x)
    axs[2].set_xticklabels(workloads)
    plot_style.apply_plot_style(axs[2], title='(c) peak memory footprint', ylabel='memory footprint (MB)')

    save_legend(fig, "/home/cc/Tectonic/experiment_plots/ycsb_workloads_comparison")
    save_fig(fig, "/home/cc/Tectonic/experiment_plots/ycsb_workloads_comparison")

def plot_fig4():
    workloads = ["I", "II", "III", "IV", "V"]
    generators = ["Tectonic", "KVBench"]
    
    times = {g: [] for g in generators}
    throughputs = {g: [] for g in generators}
    memory = {g: [] for g in generators}
    
    ops_map = {"I": 2000000, "II": 1000000, "III": 2000000, "IV": 2000000, "V": 1000000}

    for w in workloads:
        for g in generators:
            log_file = f"/home/cc/Tectonic/data/overall_benchmarks/logs_fig4/{g}_{w}.log"
            if os.path.exists(log_file):
                t, rss = parse_time_v_log(log_file)
                times[g].append(t)
                throughputs[g].append(ops_map[w] / t)
                memory[g].append(rss)
            else:
                times[g].append(0)
                throughputs[g].append(0)
                memory[g].append(0)

    x = np.arange(len(workloads))
    width = 0.35

    # (A) Latency: log scale, y starts at 10^0 = 1, pure 10^x tick labels
    fig_lat, ax_lat = plt.subplots(figsize=(6, 4.5))
    ax_lat.bar(x - width/2, times["Tectonic"], width, label='Tectonic+', **plot_style.BAR_STYLES["Tectonic"])
    ax_lat.bar(x + width/2, times["KVBench"], width, label='KVBench', **plot_style.BAR_STYLES["KVBench"])
    ax_lat.set_xticks(x)
    ax_lat.set_xticklabels(workloads)
    ax_lat.set_yscale('log')
    ax_lat.yaxis.set_major_locator(mticker.LogLocator(base=10))
    ax_lat.yaxis.set_major_formatter(mticker.LogFormatterMathtext(base=10))
    ax_lat.yaxis.set_minor_locator(mticker.NullLocator())
    plot_style.apply_plot_style(ax_lat, title='(a) end-to-end latency', ylabel='end-to-end latency (s)')

    save_legend(fig_lat, "/home/cc/Tectonic/experiment_plots/kvbench_workloads_comparison_latency")
    save_fig(fig_lat, "/home/cc/Tectonic/experiment_plots/kvbench_workloads_comparison_latency")

    # (C) Memory: linear scale, y starts at 0
    fig_mem, ax_mem = plt.subplots(figsize=(6, 4.5))
    ax_mem.bar(x - width/2, memory["Tectonic"], width, label='Tectonic+', **plot_style.BAR_STYLES["Tectonic"])
    ax_mem.bar(x + width/2, memory["KVBench"], width, label='KVBench', **plot_style.BAR_STYLES["KVBench"])
    ax_mem.set_xticks(x)
    ax_mem.set_xticklabels(workloads)
    plot_style.apply_plot_style(ax_mem, title='(c) peak memory footprint', ylabel='memory footprint (MB)')

    save_legend(fig_mem, "/home/cc/Tectonic/experiment_plots/kvbench_workloads_comparison_memory")
    save_fig(fig_mem, "/home/cc/Tectonic/experiment_plots/kvbench_workloads_comparison_memory")

if __name__ == "__main__":
    import shutil
    os.makedirs("/home/cc/Tectonic/experiment_plots", exist_ok=True)
    os.makedirs("/home/cc/Tectonic/data/overall_benchmarks/logs_fig3", exist_ok=True)
    os.makedirs("/home/cc/Tectonic/data/overall_benchmarks/logs_fig4", exist_ok=True)
    
    if os.path.exists("/tmp/logs_fig3"):
        for f in os.listdir("/tmp/logs_fig3"):
            shutil.copy(os.path.join("/tmp/logs_fig3", f), "/home/cc/Tectonic/data/overall_benchmarks/logs_fig3/")
    if os.path.exists("/tmp/logs_fig4"):
        for f in os.listdir("/tmp/logs_fig4"):
            shutil.copy(os.path.join("/tmp/logs_fig4", f), "/home/cc/Tectonic/data/overall_benchmarks/logs_fig4/")

    plot_fig3()
    plot_fig4()
