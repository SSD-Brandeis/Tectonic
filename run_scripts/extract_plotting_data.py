import json
import os

RESULTS_PATH = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes/results_io_bytes.json"
TABLE_OUT = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes/table_5_plotting_data"
LOG_OUT = "/home/cc/Tectonic/data/ycsb_tectonic_similarity_io_bytes/plotting_data_summary.json"

def extract_data():
    if not os.path.exists(RESULTS_PATH):
        print(f"Error: {RESULTS_PATH} does not exist.")
        return

    with open(RESULTS_PATH, "r") as f:
        data = json.load(f)

    results = data.get("results", {})
    
    # Structure of our summary JSON
    summary = {}
    
    # Markdown Table rows
    table_rows = []
    table_rows.append("| Database | Scale | Workload | Wall Time (s) | Disk Read (MB) | Disk Write (MB) |")
    table_rows.append("| :--- | :---: | :---: | :---: | :---: | :---: |")

    # Get databases and sorted scales
    dbs = sorted(list(results.keys()))
    
    for db in dbs:
        db_data = results[db]
        summary[db] = {}
        
        # Sort scales numerically
        scales = sorted([int(s) for s in db_data.keys()])
        for scale in scales:
            skey = str(scale)
            scale_lbl = f"2^{scale.bit_length()-1}"
            scale_data = db_data[skey]
            
            summary[db][scale_lbl] = {}
            
            for wl in ["ycsb", "tectonic"]:
                wl_data = scale_data.get(wl, {})
                
                wall_time = wl_data.get("wall_time_s", 0.0)
                io_bytes = wl_data.get("io_bytes_total", {})
                read_mb = io_bytes.get("total_read_mb", 0.0)
                write_mb = io_bytes.get("total_write_mb", 0.0)
                
                summary[db][scale_lbl][wl] = {
                    "wall_time_s": wall_time,
                    "total_read_mb": read_mb,
                    "total_write_mb": write_mb
                }
                
                wl_lbl = "YCSB" if wl == "ycsb" else "X-Bench"
                table_rows.append(
                    f"| **{db}** | {scale_lbl} | {wl_lbl} | {wall_time:.2f} | {read_mb:.2f} | {write_mb:.2f} |"
                )

    # Write Table format to file
    table_content = "\n".join(table_rows) + "\n"
    with open(TABLE_OUT, "w") as f:
        f.write(table_content)
    print(f"Saved human-readable table to: {TABLE_OUT}")

    # Write clean JSON summary to file
    with open(LOG_OUT, "w") as f:
        json.dump(summary, f, indent=2)
    print(f"Saved clean JSON summary to: {LOG_OUT}")

    # Print table to stdout
    print("\n" + table_content)

if __name__ == "__main__":
    extract_data()
