from itertools import combinations
import json

config = {
    "inserts": {"op_count": 1000, "key_len": 8, "val_len": 8},
    "updates": {"op_count": 1000, "val_len": 8},
    "point_deletes": {"op_count": 1000},
    "empty_point_deletes": {"op_count": 1000, "key_len": 8},
    "range_deletes": {"op_count": 100, "selectivity": 0.01},
    "point_queries": {"op_count": 1000},
    "empty_point_queries": {"op_count": 1000, "key_len": 8},
    "range_queries": {"op_count": 1000, "selectivity": 0.1},
}


operations = list(config.keys())
for i in range(1, len(operations) + 1):
    for comb in combinations(operations, i):
        comb = sorted(comb)
        name = "__".join(comb) + ".json"
        name = "./bench-specs/" + name

        group = {}

        for item in comb:
            group[item] = config[item]

        workload_spec = {
            "$schema": "../../../workload_schema.json",
            "sections": [
                {
                    "groups": [
                        {
                            "inserts": {"op_count": 10000, "key_len": 8, "val_len": 8},
                        },
                        group,
                    ]
                }
            ],
        }

        with open(name, "w") as f:
            f.write(json.dumps(workload_spec))
