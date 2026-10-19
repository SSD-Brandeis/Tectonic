//! End-to-end checks that key selection distributions are laid over the sorted key domain, and that
//! each phase of a workload can move its most frequent key with `offset`.

use std::collections::HashMap;
use std::path::PathBuf;

const NUM_KEYS: usize = 20_000;
const QUERIES: usize = 200_000;

/// Generates the workload and splits its operations into one list per group.
fn generate(name: &str, spec: &str, group_sizes: &[usize]) -> Vec<Vec<Vec<String>>> {
    generate_for_thread(name, spec, group_sizes, None)
}

/// Generates the workload as thread `thread_id` and splits its operations into one list per group.
fn generate_for_thread(
    name: &str,
    spec: &str,
    group_sizes: &[usize],
    thread_id: Option<usize>,
) -> Vec<Vec<Vec<String>>> {
    let path = PathBuf::from(std::env::temp_dir()).join(format!(
        "tectonic-{name}-{thread_id:?}-{}.txt",
        std::process::id()
    ));
    tectonic::generate_workload(spec.to_string(), &path, thread_id)
        .expect("to generate the workload");
    let output = std::fs::read_to_string(&path).expect("to read the generated workload");
    std::fs::remove_file(&path).ok();

    let mut lines = output
        .lines()
        .map(|l| l.split(' ').map(str::to_string).collect::<Vec<_>>());
    let groups: Vec<Vec<Vec<String>>> = group_sizes
        .iter()
        .map(|&n| lines.by_ref().take(n).collect())
        .collect();
    assert!(lines.next().is_none(), "unexpected trailing operations");
    groups
}

/// Rank of every inserted key in sorted key order.
fn sorted_ranks(inserts: &[Vec<String>]) -> HashMap<String, usize> {
    let mut keys: Vec<&String> = inserts.iter().map(|op| &op[1]).collect();
    keys.sort();
    keys.dedup();
    keys.into_iter()
        .enumerate()
        .map(|(i, k)| (k.clone(), i))
        .collect()
}

/// How often each sorted rank was selected by the operations of a group, ordered from most to least
/// frequent.
fn ranks_by_frequency(ops: &[Vec<String>], ranks: &HashMap<String, usize>) -> Vec<(usize, usize)> {
    let mut counts: HashMap<usize, usize> = HashMap::new();
    for op in ops {
        *counts.entry(ranks[&op[1]]).or_default() += 1;
    }
    let mut counts: Vec<(usize, usize)> = counts.into_iter().collect();
    counts.sort_by(|a, b| b.1.cmp(&a.1).then(a.0.cmp(&b.0)));
    counts
}

fn zipf_point_queries(offset: usize) -> String {
    format!(
        r#"{{ "point_queries": {{ "op_count": {QUERIES},
              "selection": {{ "zipf": {{ "n": {NUM_KEYS}, "s": 1.2 }}, "offset": {offset} }} }} }}"#
    )
}

#[test]
fn every_phase_can_move_its_most_frequent_key() {
    check_phase_offsets(None);
}

#[test]
fn every_phase_can_move_its_most_frequent_key_in_a_thread() {
    // Threads rewrite the last characters of every key to keep keys unique across threads.
    check_phase_offsets(Some(7));
}

fn check_phase_offsets(thread_id: Option<usize>) {
    let offsets = [0, 1, 1000, 15_000];
    let phases: Vec<String> = offsets.iter().map(|&o| zipf_point_queries(o)).collect();
    let spec = format!(
        r#"{{ "sections": [{{ "groups": [
              {{ "inserts": {{ "op_count": {NUM_KEYS}, "key": {{ "uniform": {{ "len": 16 }} }},
                               "val": {{ "uniform": {{ "len": 8 }} }} }} }},
              {} ] }}] }}"#,
        phases.join(",")
    );
    let groups = generate_for_thread(
        "zipf-phases",
        &spec,
        &[NUM_KEYS, QUERIES, QUERIES, QUERIES, QUERIES],
        thread_id,
    );
    let ranks = sorted_ranks(&groups[0]);
    assert_eq!(ranks.len(), NUM_KEYS, "keys are expected to be unique");

    for (phase, &offset) in groups[1..].iter().zip(&offsets) {
        assert!(phase.iter().all(|op| op[0] == "P"));
        let by_frequency = ranks_by_frequency(phase, &ranks);
        // The ten most frequent keys are the ten consecutive keys starting at the offset, in order.
        let top: Vec<usize> = by_frequency
            .iter()
            .take(10)
            .map(|&(rank, _)| rank)
            .collect();
        let expected: Vec<usize> = (offset..offset + 10).map(|r| r % NUM_KEYS).collect();
        assert_eq!(top, expected, "offset {offset}");
    }
}

#[test]
fn all_key_selecting_operations_use_the_sorted_key_domain() {
    let spec = format!(
        r#"{{ "sections": [{{ "groups": [
              {{ "inserts": {{ "op_count": {NUM_KEYS}, "key": {{ "uniform": {{ "len": 16 }} }},
                               "val": {{ "uniform": {{ "len": 8 }} }} }} }},
              {{ "updates": {{ "op_count": {QUERIES}, "val": {{ "uniform": {{ "len": 8 }} }},
                   "selection": {{ "normal": {{ "mean": 0.25, "std_dev": 0.01 }} }} }} }},
              {{ "merges": {{ "op_count": {QUERIES}, "val": {{ "uniform": {{ "len": 8 }} }},
                   "selection": {{ "latest": {{ "n": {NUM_KEYS}, "s": 1.2 }} }} }} }},
              {{ "range_queries": {{ "op_count": {QUERIES}, "scan_length": 10,
                   "selection": {{ "zipf": {{ "n": {NUM_KEYS}, "s": 1.2 }}, "offset": 3000 }} }} }},
              {{ "range_queries": {{ "op_count": {QUERIES}, "scan_length": 10, "range_format": "StartEnd",
                   "selection": {{ "zipf": {{ "n": {NUM_KEYS}, "s": 1.2 }}, "offset": 7000 }} }} }},
              {{ "point_deletes": {{ "op_count": 1000,
                   "selection": {{ "zipf": {{ "n": {NUM_KEYS}, "s": 1.2 }}, "offset": 12000 }} }} }}
            ] }}] }}"#
    );
    let groups = generate(
        "all-ops",
        &spec,
        &[NUM_KEYS, QUERIES, QUERIES, QUERIES, QUERIES, 1000],
    );
    let ranks = sorted_ranks(&groups[0]);

    // Updates: normal around 25% of the key domain.
    let updates = ranks_by_frequency(&groups[1], &ranks);
    let mean = updates.iter().map(|&(r, c)| (r * c) as f64).sum::<f64>() / QUERIES as f64;
    assert!(
        (mean / NUM_KEYS as f64 - 0.25).abs() < 0.001,
        "mean = {mean}"
    );
    assert!(
        updates
            .iter()
            .all(|&(r, _)| (r as f64 / NUM_KEYS as f64 - 0.25).abs() < 0.07)
    );

    // Merges: latest is anchored at the largest key.
    let merges = ranks_by_frequency(&groups[2], &ranks);
    let top: Vec<usize> = merges.iter().take(5).map(|&(r, _)| r).collect();
    assert_eq!(top, (NUM_KEYS - 5..NUM_KEYS).rev().collect::<Vec<_>>());

    // Range queries select their start key; StartEnd also returns the key 10 ranks further.
    for (group, offset) in [(&groups[3], 3000), (&groups[4], 7000)] {
        let starts = ranks_by_frequency(group, &ranks);
        let top: Vec<usize> = starts.iter().take(5).map(|&(r, _)| r).collect();
        assert_eq!(top, (offset..offset + 5).collect::<Vec<_>>());
    }
    assert!(groups[3].iter().all(|op| op[0] == "SC" && op[2] == "10"));
    assert!(
        groups[4]
            .iter()
            .all(|op| op[0] == "S" && ranks[&op[2]] == ranks[&op[1]] + 10)
    );

    // Point deletes: each delete removes a key, so the hot spot is consumed key by key starting at
    // the offset; deleted keys are distinct and concentrated just above the offset.
    let deleted: Vec<usize> = groups[5].iter().map(|op| ranks[&op[1]]).collect();
    let mut unique = deleted.clone();
    unique.sort();
    unique.dedup();
    assert_eq!(unique.len(), deleted.len(), "a key was deleted twice");
    let near = deleted
        .iter()
        .filter(|&&r| (12_000..16_000).contains(&r))
        .count();
    assert!(
        near as f64 > 0.85 * deleted.len() as f64,
        "{near} of {}",
        deleted.len()
    );
}

#[test]
fn interleaved_inserts_keep_selections_on_the_sorted_key_domain() {
    // Queries interleaved with inserts: the hottest key must always be the smallest existing key.
    let spec = r#"{ "sections": [{ "groups": [
          { "inserts": { "op_count": 2000, "key": { "uniform": { "len": 16 } },
                         "val": { "uniform": { "len": 8 } } } },
          { "inserts": { "op_count": 20000, "key": { "uniform": { "len": 16 } },
                         "val": { "uniform": { "len": 8 } } },
            "point_queries": { "op_count": 100000,
                               "selection": { "zipf": { "n": 1000000, "s": 2.0 } } } }
        ] }] }"#;
    let groups = generate("interleaved", spec, &[2000, 120_000]);
    let mut existing: std::collections::BTreeSet<String> =
        groups[0].iter().map(|op| op[1].clone()).collect();
    let (mut smallest, mut total) = (0usize, 0usize);
    for op in &groups[1] {
        match op[0].as_str() {
            "I" => {
                existing.insert(op[1].clone());
            }
            "P" => {
                assert!(existing.contains(&op[1]));
                total += 1;
                if existing.first() == Some(&op[1]) {
                    smallest += 1;
                }
            }
            other => panic!("unexpected operation {other}"),
        }
    }
    // With s = 2, P(rank 1) = 1 / zeta(2) = 0.608.
    let share = smallest as f64 / total as f64;
    assert!(
        (share - 0.608).abs() < 0.01,
        "share of the smallest key = {share}"
    );
}
