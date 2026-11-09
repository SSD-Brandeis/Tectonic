from pathlib import Path
from glob import glob
import re
from string import Template


def pascal_to_snake(s):
    return re.sub(r"(?<!^)(?=[A-Z])", "_", s).lower()


keysets = [
    "VecKeySet",
#     "VecOptionKeySet",
    "VecHashSetKeySet",
    "VecBloomFilterKeySet",
    "VecHashMapIndexKeySet",
    "BTreeSetKeySet",
]

operations = [
    "inserts",
    "updates",
    "point_deletes",
    "empty_point_deletes",
    "range_deletes",
    "point_queries",
    "empty_point_queries",
    "range_queries",
]

MAP_INSERT_TEMPLATE = Template("""\
    specs.insert("${key}", serde_json::from_str(include_str!("${file}")).unwrap());
""")

spec_insert = ""
specs = []
for file in sorted(glob("./bench-specs/*.json")):
    path = Path(file)
    spec = path.stem
    specs.append(spec)
    spec_insert += MAP_INSERT_TEMPLATE.substitute({"key": spec, "file": file})


BENCH_TEMPLATE = Template("""\
fn ${bench_fn_name}(c: &mut Criterion) {
    let keyset_constructor = |cap| workload_gen::keyset::${keyset}::new(cap);

${benches}
}
""")
BENCH_FN_TEMPLATE = Template("""\
    c.bench_function("${spec}", |b| {
        let spec = &SPECS["${spec}"];
        b.iter(|| generate_operations(&spec, &keyset_constructor).expect("Failed to generate operations"));
    });
""")

bench_fns = ""
bench_targets = ""

for keyset in keysets:
    keyset_snake = pascal_to_snake(keyset)
    benches = ""
    for spec in specs:
        benches += BENCH_FN_TEMPLATE.substitute({"spec": spec})
    bench_fns += BENCH_TEMPLATE.substitute(
        {"bench_fn_name": keyset_snake, "benches": benches, "keyset": keyset}
    )
    bench_targets += f"    {keyset_snake},\n"

code = Template("""\
#![allow(non_snake_case)]
#![allow(clippy::needless_return)]

use std::collections::HashMap;
use anyhow::Result;
use criterion::{criterion_group, criterion_main, Criterion};
use std::io::sink;
use workload_gen::{spec::WorkloadSpec, write_operations_with_keyset, keyset::KeySet};
use std::sync::LazyLock;

fn generate_operations<TKeySet: KeySet>(spec: &WorkloadSpec, keyset_constructor: impl Fn(usize) -> TKeySet) -> Result<()> {
    return write_operations_with_keyset(&mut sink(), spec, keyset_constructor);
}

static SPECS: LazyLock<HashMap<&'static str, WorkloadSpec>> = LazyLock::new(|| {
    let mut specs = HashMap::new();
${spec_insert}
    specs
});

${bench_fns}

fn criterion_custom() -> Criterion {
    Criterion::default()
        .warm_up_time(std::time::Duration::from_secs(1))
        .measurement_time(std::time::Duration::from_secs(1))
}

criterion_group!(
    name = benches;
    config = criterion_custom();
    targets =
${bench_targets}
);
criterion_main!(benches);
""")

with open("benchmark.rs", "w") as f:
    f.write(
        code.substitute(
            {
                "bench_targets": bench_targets,
                "bench_fns": bench_fns,
                "spec_insert": spec_insert,
            }
        )
    )
