#!/usr/bin/env python3
import json
import os

def main():
    s = 0.99
    # Compute Zipfian weights for the 30 table prefixes
    weights = [1.0 / (i ** s) for i in range(1, 31)]
    
    # Construct the weighted list of table prefixes
    prefix_items = []
    for i, w in enumerate(weights):
        prefix_items.append({
            "weight": round(w, 5),
            "value": f"t{i:02d}"
        })
        
    # Key expression: 90% are 28 bytes (3 prefix + 25 suffix), 10% are 32 bytes (3 prefix + 29 suffix)
    key_expr = {
      "weighted": [
        {
          "weight": 0.9,
          "value": {
            "segmented": {
              "separator": "",
              "segments": [
                {
                  "weighted": prefix_items
                },
                {
                  "uniform": {
                    "len": 25,
                    "character_set": "alphanumeric"
                  }
                }
              ]
            }
          }
        },
        {
          "weight": 0.1,
          "value": {
            "segmented": {
              "separator": "",
              "segments": [
                {
                  "weighted": prefix_items
                },
                {
                  "uniform": {
                    "len": 29,
                    "character_set": "alphanumeric"
                  }
                }
              ]
            }
          }
        }
      ]
    }
    
    # Value size: Pareto with shape=3.824 and scale=37.0 to yield mean ~50
    val_expr = {
      "uniform": {
        "len": {
          "pareto": {
            "scale": 37.0,
            "shape": 3.824
          }
        }
      }
    }
    
    # Iterator scan length distribution: GPD with shape=2.517 and scale=1.5
    scan_len_expr = {
      "pareto": {
        "scale": 1.5,
        "shape": 2.517
      }
    }
    
    # Preload spec (30,000,000 unsorted inserts matching the paper)
    preload_spec = {
      "$schema": "../workload_schema.json",
      "sections": [
        {
          "defaults": {
            "key": key_expr,
            "val": val_expr
          },
          "groups": [
            {
              "name": "Preload",
              "enable_granular_stats": True,
              "inserts": {
                "op_count": 30000000
              }
            }
          ]
        }
      ]
    }
    
    # Runtime spec (10,000,000 operations following diurnal arrival curve)
    runtime_groups = [
      {
        "name": "Morning Ramp",
        "enable_granular_stats": True,
        "inserts": {"op_count": 205160},
        "point_queries": {"op_count": 1039997},
        "range_queries": {"op_count": 45161, "scan_length": scan_len_expr}
      },
      {
        "name": "Midday Peak",
        "enable_granular_stats": True,
        "inserts": {"op_count": 410321},
        "point_queries": {"op_count": 2079995},
        "range_queries": {"op_count": 90322, "scan_length": scan_len_expr}
      },
      {
        "name": "Afternoon Peak",
        "enable_granular_stats": True,
        "inserts": {"op_count": 512902},
        "point_queries": {"op_count": 2599994},
        "range_queries": {"op_count": 112903, "scan_length": scan_len_expr}
      },
      {
        "name": "Evening Decline",
        "enable_granular_stats": True,
        "inserts": {"op_count": 307741},
        "point_queries": {"op_count": 1559996},
        "range_queries": {"op_count": 67741, "scan_length": scan_len_expr}
      },
      {
        "name": "Night Trough",
        "enable_granular_stats": True,
        "inserts": {"op_count": 102580},
        "point_queries": {"op_count": 519998},
        "range_queries": {"op_count": 22580, "scan_length": scan_len_expr}
      },
      {
        "name": "Late Night Trough",
        "enable_granular_stats": True,
        "inserts": {"op_count": 51290},
        "point_queries": {"op_count": 259999},
        "range_queries": {"op_count": 11290, "scan_length": scan_len_expr}
      }
    ]
    
    runtime_spec = {
      "$schema": "../workload_schema.json",
      "sections": [
        {
          "defaults": {
            "key": key_expr,
            "val": val_expr
          },
          "groups": runtime_groups
        }
      ]
    }
    
    spec_dir = "/home/cc/Tectonic/tectonic-specs"
    os.makedirs(spec_dir, exist_ok=True)
    
    preload_path = os.path.join(spec_dir, "udb_assoc_preload.spec.json")
    with open(preload_path, "w") as f:
        json.dump(preload_spec, f, indent=2)
        
    runtime_path = os.path.join(spec_dir, "udb_assoc_runtime.spec.json")
    with open(runtime_path, "w") as f:
        json.dump(runtime_spec, f, indent=2)
        
    print(f"Preload spec generated: {preload_path}")
    print(f"Runtime spec generated: {runtime_path}")

if __name__ == "__main__":
    main()
