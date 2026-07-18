#![feature(extend_one)]
#![feature(btree_cursors)]
#![feature(trusted_random_access)]
#![feature(trait_alias)]
#![feature(variant_count)]
#![allow(clippy::needless_return)]
#![allow(dead_code)]

use crate::spec::Scalable;
use anyhow::{Context, Result, anyhow, bail};
use db_layer::{Benchmarker, BenchmarkerType, Db};
use indicatif::{ProgressBar, ProgressDrawTarget, ProgressStyle};
use rand::prelude::SliceRandom;
use rand::seq::IndexedMutRandom;
use rand::{Rng, SeedableRng};
use rand_xoshiro::Xoshiro256Plus;
use std::{
    borrow::Cow,
    collections::HashMap,
    fs::File,
    io::{BufRead, BufReader, BufWriter, Write},
    path::Path,
    sync::atomic::{AtomicUsize, Ordering},
    sync::Arc,
};
use std::mem::variant_count;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tracing::{debug, info, trace, warn};

mod keyset;
pub mod spec;

// Operation order to be kept for each enum/match statement
// - unique insert
// - upsert
// - update
// - merge
// - delete point
// - delete point empty
// - delete range
// - query point
// - query point empty
// - query range

use crate::keyset::{
    BloomFilterKeySet, EmptyKeySet, Key, KeySet, VecBloomFilterKeySet, VecHashSetKeySet, VecKeySet,
    VecOptionHashSetKeySet, VecOptionKeySet,
};
use crate::spec::{CharacterSet, RangeFormat, StringExpr, WorkloadSpec, WorkloadSpecSection, BlindRangeQueries};

pub trait OperationHandler {
    fn handle_insert(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()>;
    fn handle_update(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()>;
    fn handle_merge(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()>;
    fn handle_point_delete(&mut self, key: &Key) -> Result<()>;
    fn handle_point_query(&mut self, key: &Key) -> Result<()>;
    fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()>;
    fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()>;
    fn handle_range_delete(&mut self, key1: &Key, key2: &Key) -> Result<()>;
    fn handle_range_delete_count(&mut self, key1: &Key, count: usize) -> Result<()>;
    fn handle_blind_point_query(&mut self, key: &Key) -> Result<()>;
    fn handle_blind_point_delete(&mut self, key: &Key) -> Result<()>;
    fn handle_blind_range_query(&mut self, key: &Key, count: usize) -> Result<()>;
    fn start_stat_flush(&mut self, benchmarker: BenchmarkerType) -> Result<()>;
    fn end_stat_flush(&mut self, name: &str, benchmarker: BenchmarkerType) -> Result<()>;
}

struct WriteHandler<'a, W: Write>(&'a mut W);

impl<'a, W: Write> OperationHandler for WriteHandler<'a, W> {
    fn handle_insert(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let w = &mut self.0;
        w.write_all("I ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_update(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let w = &mut self.0;
        w.write_all("U ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_merge(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let w = &mut self.0;
        w.write_all("M ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("D ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("P ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("S ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(key2)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        let w = &mut self.0;
        w.write_all("SC ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(count.to_string().as_bytes())?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_range_delete(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("R ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(key2)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn handle_range_delete_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        let w = &mut self.0;
        w.write_all("RC ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(count.to_string().as_bytes())?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }

    fn start_stat_flush(&mut self, benchmarker: BenchmarkerType) -> Result<()> {
        let writer = &mut self.0;
        writer.write_all("FS ".as_bytes())?;
        writer.write_all({
            match benchmarker {
                BenchmarkerType::Overall => "O".as_bytes(),
                BenchmarkerType::Section => "S".as_bytes(),
                BenchmarkerType::Group => "G".as_bytes(),
            }
        })?;
        writer.write_all("\n".as_bytes())?;

        Ok(())
    }

    fn end_stat_flush(&mut self, name: &str, benchmarker: BenchmarkerType) -> Result<()> {
        let writer = &mut self.0;
        writer.write_all("FE ".as_bytes())?;
        writer.write_all({
            match benchmarker {
                BenchmarkerType::Overall => "O".as_bytes(),
                BenchmarkerType::Section => "S".as_bytes(),
                BenchmarkerType::Group => "G".as_bytes(),
            }
        })?;
        writer.write_all(" ".as_bytes())?;
        writer.write_all(name.as_bytes())?;
        writer.write_all("\n".as_bytes())?;

        Ok(())
    }

    fn handle_blind_point_query(&mut self, key: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("BP ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;
        return Ok(());
    }

    fn handle_blind_point_delete(&mut self, key: &Key) -> Result<()> {
        let w = &mut self.0;
        w.write_all("BD ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;
        return Ok(());
    }

    fn handle_blind_range_query(&mut self, key: &Key, count: usize) -> Result<()> {
        let w = &mut self.0;
        w.write_all("BR ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(count.to_string().as_bytes())?;
        w.write_all("\n".as_bytes())?;
        return Ok(());
    }
}

pub struct DBHandler<'a, 'b> {
    benchmarker: &'a mut Benchmarker<'b>,
    prefix: Option<Vec<u8>>,
    target_rate_per_thread: Option<u64>,
    start_time: std::time::Instant,
    operations_done: u64,
    global_ops: Option<Arc<AtomicUsize>>,
    global_read_latency_sum: Option<Arc<std::sync::atomic::AtomicU64>>,
    global_read_count: Option<Arc<AtomicUsize>>,
}

impl<'a, 'b> DBHandler<'a, 'b> {
    pub fn new(
        benchmarker: &'a mut Benchmarker<'b>,
        prefix: Option<Vec<u8>>,
        target_rate_per_thread: Option<u64>,
        global_ops: Option<Arc<AtomicUsize>>,
        global_read_latency_sum: Option<Arc<std::sync::atomic::AtomicU64>>,
        global_read_count: Option<Arc<AtomicUsize>>,
    ) -> Self {
        Self {
            benchmarker,
            prefix,
            target_rate_per_thread,
            start_time: std::time::Instant::now(),
            operations_done: 0,
            global_ops,
            global_read_latency_sum,
            global_read_count,
        }
    }

    fn apply_prefix<'c>(prefix: &Option<Vec<u8>>, key: &'c [u8]) -> std::borrow::Cow<'c, [u8]> {
        if let Some(p) = prefix {
            let mut new_key = p.clone();
            new_key.extend_from_slice(key);
            std::borrow::Cow::Owned(new_key)
        } else {
            std::borrow::Cow::Borrowed(key)
        }
    }

    fn throttle(&mut self) {
        self.operations_done += 1;
        if let Some(rate) = self.target_rate_per_thread {
            let expected_time = std::time::Duration::from_secs_f64(self.operations_done as f64 / rate as f64);
            let elapsed = self.start_time.elapsed();
            if elapsed < expected_time {
                std::thread::sleep(expected_time - elapsed);
            }
        }
        if let Some(global_ops) = &self.global_ops {
            global_ops.fetch_add(1, Ordering::Relaxed);
        }
    }
}

impl<'a, 'b> OperationHandler for DBHandler<'a, 'b> {
    fn handle_insert(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        self.throttle();
        let value = val.generate(rng, character_set, None);
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_insert(&prefixed_key, value.as_ref())
    }

    fn handle_update(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        self.throttle();
        let value = val.generate(rng, character_set, None);
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_update(&prefixed_key, value.as_ref())
    }

    fn handle_merge(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        self.throttle();
        let value = val.generate(rng, character_set, None);
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_merge(&prefixed_key, value.as_ref())
    }

    fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_point_delete(&prefixed_key)
    }

    fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        let start = std::time::Instant::now();
        let res = self.benchmarker.handle_point_query(&prefixed_key);
        let latency_nanos = start.elapsed().as_nanos() as u64;
        if let Some(sum) = &self.global_read_latency_sum {
            sum.fetch_add(latency_nanos, Ordering::Relaxed);
        }
        if let Some(count) = &self.global_read_count {
            count.fetch_add(1, Ordering::Relaxed);
        }
        res
    }

    fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key1 = Self::apply_prefix(&self.prefix, key1);
        let prefixed_key2 = Self::apply_prefix(&self.prefix, key2);
        self.benchmarker.handle_range_query(&prefixed_key1, &prefixed_key2)
    }

    fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        self.throttle();
        let prefixed_key1 = Self::apply_prefix(&self.prefix, key1);
        self.benchmarker.handle_range_query_count(&prefixed_key1, count)
    }

    fn handle_range_delete(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key1 = Self::apply_prefix(&self.prefix, key1);
        let prefixed_key2 = Self::apply_prefix(&self.prefix, key2);
        self.benchmarker.handle_range_delete(&prefixed_key1, &prefixed_key2)
    }

    fn handle_range_delete_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        self.throttle();
        let prefixed_key1 = Self::apply_prefix(&self.prefix, key1);
        self.benchmarker.handle_range_delete_count(&prefixed_key1, count)
    }

    fn start_stat_flush(&mut self, benchmarker: BenchmarkerType) -> Result<()> {
        self.benchmarker.start_stat_flush(benchmarker);

        Ok(())
    }

    fn end_stat_flush(&mut self, name: &str, benchmarker: BenchmarkerType) -> Result<()> {
        self.benchmarker.end_stat_flush(name, benchmarker)?;

        Ok(())
    }

    fn handle_blind_point_query(&mut self, key: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        let start = std::time::Instant::now();
        let res = self.benchmarker.handle_point_query(&prefixed_key);
        let latency_nanos = start.elapsed().as_nanos() as u64;
        if let Some(sum) = &self.global_read_latency_sum {
            sum.fetch_add(latency_nanos, Ordering::Relaxed);
        }
        if let Some(count) = &self.global_read_count {
            count.fetch_add(1, Ordering::Relaxed);
        }
        res
    }

    fn handle_blind_point_delete(&mut self, key: &Key) -> Result<()> {
        self.throttle();
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_point_delete(&prefixed_key)
    }

    fn handle_blind_range_query(&mut self, key: &Key, count: usize) -> Result<()> {
        self.throttle();
        let prefixed_key = Self::apply_prefix(&self.prefix, key);
        self.benchmarker.handle_range_query_count(&prefixed_key, count)
    }
}

pub struct OperationTimings {
    time_insert: Duration,
    time_upsert: Duration,
    time_update: Duration,
    time_merge: Duration,
    time_delete_point: Duration,
    time_delete_point_empty: Duration,
    time_delete_range: Duration,
    time_query_point: Duration,
    time_query_point_empty: Duration,
    time_query_range: Duration,
    time_blind_point_query: Duration,
    time_blind_point_delete: Duration,
    time_blind_range_query: Duration,
}

impl Default for OperationTimings {
    fn default() -> Self {
        Self {
            time_insert: Duration::from_secs(0),
            time_upsert: Duration::from_secs(0),
            time_update: Duration::from_secs(0),
            time_merge: Duration::from_secs(0),
            time_delete_point: Duration::from_secs(0),
            time_delete_point_empty: Duration::from_secs(0),
            time_delete_range: Duration::from_secs(0),
            time_query_point: Duration::from_secs(0),
            time_query_point_empty: Duration::from_secs(0),
            time_query_range: Duration::from_secs(0),
            time_blind_point_query: Duration::from_secs(0),
            time_blind_point_delete: Duration::from_secs(0),
            time_blind_range_query: Duration::from_secs(0),
        }
    }
}

#[derive(Debug, Copy, Clone, Eq, Ord, PartialOrd, PartialEq)]
enum Op {
    UniqueInsert,
    Upsert,
    Update,
    Merge,
    PointDelete,
    PointDeleteEmpty,
    RangeDelete,
    PointQuery,
    EmptyPointQuery,
    RangeQuery,
    BlindPointQuery,
    BlindPointDelete,
    BlindRangeQuery,
}

// TODO: Allow for different sections to use different keysets

/// Generates a workload given the spec and writes it to the given writer.
pub fn generate_section<OP: OperationHandler>(
    operation_handler: &mut OP,
    operation_timings: &mut OperationTimings,
    workload: &WorkloadSpec,
    section: &WorkloadSpecSection,
    section_idx: usize,
    thread_id: Option<usize>,
) -> Result<()> {
    let no_keyset = !(section.has_unique_insert()
        || section.has_update()
        || section.has_merge()
        || section.has_query_point()
        || section.has_query_point_empty()
        || section.has_delete_point()
        || section.has_delete_point_empty()
        || section.has_query_range()
        || section.has_query_range_count()
        || section.has_delete_range());

    let requires_deletion = section.has_delete_point() || section.has_delete_range();
    let requires_sorting = section.has_query_range() || section.has_delete_range();

    let requires_contains_check = !section.skip_contains_check()
        && (section.has_unique_insert()
            || section.has_query_point_empty()
            || section.has_delete_point_empty());
    let requires_random_element = section.has_update()
        || section.has_merge()
        || section.has_delete_point()
        || section.has_delete_range()
        || section.has_query_point()
        || section.has_query_range()
        || section.has_query_range_count();

    if no_keyset {
        info!("Using EmptyKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            EmptyKeySet::new,
            thread_id,
        )?
    } else if requires_sorting && requires_deletion && requires_contains_check {
        info!("Using VecHashSetOptionKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecOptionHashSetKeySet::new,
            thread_id,
        )?
    } else if requires_sorting && requires_deletion && !requires_contains_check {
        info!("Using VecOptionKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecOptionKeySet::new,
            thread_id,
        )?
    } else if (requires_sorting || requires_deletion) && requires_contains_check {
        info!("Using VecHashSetKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecHashSetKeySet::new,
            thread_id,
        )?
    } else if requires_sorting || requires_deletion {
        info!("Using VecKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecKeySet::new,
            thread_id,
        )?
    } else if requires_contains_check && requires_random_element {
        info!("Using VecBloomFilterKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecBloomFilterKeySet::new,
            thread_id,
        )?
    } else if requires_contains_check {
        info!("Using BloomFilterKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            BloomFilterKeySet::new,
            thread_id,
        )?
    } else {
        info!("Using VecKeySet");
        write_operations_with_keyset(
            operation_handler,
            operation_timings,
            workload,
            section,
            section_idx,
            VecKeySet::new,
            thread_id,
        )?
    }

    Ok(())
}

/// Generates a workload given the spec and writes it to the given writer.
pub fn generate_operations<OP: OperationHandler>(
    mut operation_handler: OP,
    workload: &WorkloadSpec,
) -> Result<()> {
    let mut operation_timings = OperationTimings::default();

    for (i, section) in workload.sections.iter().enumerate() {
        generate_section(&mut operation_handler, &mut operation_timings, workload, section, i, None)?;
    }

    debug!(
        unique_insert = %operation_timings.time_insert.as_secs_f64(),
        upsert = %operation_timings.time_upsert.as_secs_f64(),
        update = %operation_timings.time_update.as_secs_f64(),
        merge = %operation_timings.time_merge.as_secs_f64(),
        delete_point = %operation_timings.time_delete_point.as_secs_f64(),
        delete_point_empty = %operation_timings.time_delete_point_empty.as_secs_f64(),
        delete_range = %operation_timings.time_delete_range.as_secs_f64(),
        query_point = %operation_timings.time_query_point.as_secs_f64(),
        query_point_empty = %operation_timings.time_query_point_empty.as_secs_f64(),
        query_range = %operation_timings.time_query_range.as_secs_f64(),
        "operation generation timings (in seconds)"
    );

    return Ok(());
}

// TODO: Eliminate Marker Array
// How to do this?
// Make a new iter that generates the next operation based on how many operations are remaining
// How to do this? Simple
// Each operation has a count, and a threshold
// We also can sum up all remaining operations
// Then we generate a random value between 0 (or maybe 1) and the current number of operations remaining
// Whichever threshold the number lands on is the operation we choose
// We then decrement the count of the operation we chose
// If all operations have a count of 0, we are done

struct OpCount {
    op_type: Op,
    remaining_count: usize,
}

struct MarkerIter {
    rng: Xoshiro256Plus,
    // op_counts: OpCounts,
    op_count: Vec<OpCount>,
    total: usize,
}

impl MarkerIter {
    fn new(rng: Xoshiro256Plus) -> Self {
        Self {
            rng,
            op_count: Vec::with_capacity(variant_count::<Op>()),
            total: 0,
        }
    }

    fn add_op_count(&mut self, op_type: Op, count: usize) {
        self.op_count.push(OpCount {
            op_type,
            remaining_count: count,
        });
    }

    fn calculate_total(&mut self) {
        self.total = self.op_count.iter().map(|data| data.remaining_count).sum();
    }

    fn prepare_iter(&mut self) {
        self.calculate_total();
        self.op_count.shuffle(&mut self.rng);
    }
}

impl Iterator for MarkerIter {
    type Item = Op;

    fn next(&mut self) -> Option<Self::Item> {
        if self.total == 0 {
            return None;
        }
        let op = self
            .op_count
            .choose_weighted_mut(&mut self.rng, |op_count| op_count.remaining_count)
            .ok()?;
        op.remaining_count -= 1;
        self.total -= 1;
        return Some(op.op_type);
        // Generate a random number between 0 and total - 1
        // let num = {
        //     if self.total > 1 {
        //         self.rng.random_range(0..(self.total - 1))
        //     } else {
        //         0
        //     }
        // };
        // // Check which threshold this number corresponds to
        // let mut threshold = 0;
        // for data in &mut self.op_count {
        //     if data.remaining_count == 0 {
        //         continue;
        //     }
        //
        //     threshold += data.remaining_count;
        //     if num < threshold {
        //         data.remaining_count -= 1;
        //         self.total -= 1;
        //         return Some(data.op_type);
        //     }
        // }
        //
    }
}

fn pregenerate_keys_parallel(
    expr: &StringExpr,
    char_set: Option<CharacterSet>,
    count: usize,
    thread_id: Option<usize>,
) -> Vec<Key> {
    if count == 0 {
        return Vec::new();
    }
    let mut rng = Xoshiro256Plus::from_os_rng();
    let mut keys = Vec::with_capacity(count);
    for _ in 0..count {
        let key = expr.generate(&mut rng, char_set, thread_id);
        keys.push(key);
    }
    keys
}

fn pregenerate_range_queries_parallel(
    brq: &BlindRangeQueries,
    char_set: Option<CharacterSet>,
    count: usize,
    default_key: Option<&StringExpr>,
    total_entries: usize,
    thread_id: Option<usize>,
) -> Vec<(Key, usize)> {
    if count == 0 {
        return Vec::new();
    }
    let mut rng = Xoshiro256Plus::from_os_rng();
    let mut queries = Vec::with_capacity(count);
    for _ in 0..count {
        let key_expr = brq.key.as_ref().or(default_key).expect("No key or default key set for blind range queries");
        let key = key_expr.generate(&mut rng, brq.character_set.or(char_set), thread_id);
        let range_count = brq.get_range_length(&mut rng, total_entries);
        queries.push((key, range_count));
    }
    queries
}

pub fn write_operations_with_keyset<KeySetT: KeySet, OP: OperationHandler>(
    operation_handler: &mut OP,
    operation_timings: &mut OperationTimings,
    workload: &WorkloadSpec,
    section: &WorkloadSpecSection,
    section_num: usize,
    keyset_constructor: impl Fn(usize) -> KeySetT,
    thread_id: Option<usize>,
) -> Result<()> {
    if section.enable_granular_stats {
        operation_handler.start_stat_flush(BenchmarkerType::Section)?;
    }

    let mut rng = Xoshiro256Plus::from_os_rng();
    // let mut keys_prev_sections = BloomFilter::with_rate(0.01, todo!());

    let unique_insert_counts: Vec<usize> = section
        .groups
        .iter()
        .map(|g| {
            g.unique_inserts
                .as_ref()
                .map_or(0, |is| is.op_count.evaluate(&mut rng) as usize)
        })
        .collect();

    let upsert_counts: Vec<usize> = section
        .groups
        .iter()
        .map(|g| {
            g.inserts
                .as_ref()
                .map_or(0, |is| is.op_count.evaluate(&mut rng) as usize)
        })
        .collect();
    let total_entries: usize =
        upsert_counts.iter().sum::<usize>() + unique_insert_counts.iter().sum::<usize>();

    let mut keys_valid = keyset_constructor(
        unique_insert_counts.iter().sum::<usize>() + upsert_counts.iter().sum::<usize>(), /*section.insert_count()*/
    );

    for (group_num, (group, (unique_insert_count, upsert_count))) in std::iter::zip(
        &section.groups,
        std::iter::zip(unique_insert_counts, upsert_counts),
    )
    .enumerate()
    {
        if group.enable_granular_stats {
            operation_handler.start_stat_flush(BenchmarkerType::Group)?;
        }

        let rng_ref = &mut rng;
        let mut markers = MarkerIter::new(rng_ref.clone());
        let character_set = group
            .defaults
            .as_ref()
            .and_then(|d| d.character_set)
            .or(section
                .defaults
                .as_ref()
                .and_then(|d| d.character_set)
                .or(workload.defaults.as_ref().and_then(|d| d.character_set)));
        let key = group
            .defaults
            .as_ref()
            .and_then(|d| d.key.as_ref())
            .or(section
                .defaults
                .as_ref()
                .and_then(|d| d.key.as_ref())
                .or(workload.defaults.as_ref().and_then(|d| d.key.as_ref())));
        let val = group
            .defaults
            .as_ref()
            .and_then(|d| d.val.as_ref())
            .or(section
                .defaults
                .as_ref()
                .and_then(|d| d.val.as_ref())
                .or(workload.defaults.as_ref().and_then(|d| d.val.as_ref())));

        let update_count = group
            .updates
            .as_ref()
            .map_or(0, |us| us.op_count.evaluate(rng_ref) as usize);
        let merge_count = group
            .merges
            .as_ref()
            .map_or(0, |us| us.op_count.evaluate(rng_ref) as usize);
        let delete_point_count = group
            .point_deletes
            .as_ref()
            .map_or(0, |dps| dps.op_count.evaluate(rng_ref) as usize);
        let delete_point_empty_count = group
            .empty_point_deletes
            .as_ref()
            .map_or(0, |dpes| dpes.op_count.evaluate(rng_ref) as usize);
        let delete_range_count = group
            .range_deletes
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);
        let query_point_count = group
            .point_queries
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);
        let query_point_empty_count = group
            .empty_point_queries
            .as_ref()
            .map_or(0, |qpes| qpes.op_count.evaluate(rng_ref) as usize);
        let query_range_count = group
            .range_queries
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);
        let blind_point_query_count = group
            .blind_point_queries
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);
        let blind_point_delete_count = group
            .blind_point_deletes
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);
        let blind_range_query_count = group
            .blind_range_queries
            .as_ref()
            .map_or(0, |drs| drs.op_count.evaluate(rng_ref) as usize);

        debug!(
            ?unique_insert_count,
            ?upsert_count,
            ?update_count,
            ?merge_count,
            ?delete_point_count,
            ?delete_point_empty_count,
            ?delete_range_count,
            ?query_point_count,
            ?query_point_empty_count,
            ?query_range_count
        );

        let more_delete_point_than_keys = delete_point_count > keys_valid.len() + unique_insert_count + upsert_count;
        if more_delete_point_than_keys {
            bail!("Cannot have more point deletes than existing valid keys plus new inserts in the group.");
        }

        let mut key_pool = if let Some(sorted) = &group.sorted {
            let is = group.unique_inserts.as_ref();
            let ups = group.inserts.as_ref();

            if ups.is_none() && is.is_none() {
                bail!("Insert spec must exist if sorted config exists");
            };

            let mut pool = Vec::with_capacity(unique_insert_count);

            if let Some(is) = is {
                for _ in 0..unique_insert_count {
                    // TODO: Make unique inserts function with nearly sorted data, as currently
                    // duplicate keys can be generated
                    let key = is
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for unique inserts")
                        .generate(rng_ref, is.character_set.or(character_set), thread_id);
                    pool.push(key);
                }
            }

            if let Some(ups) = ups {
                for _ in 0..upsert_count {
                    let key = ups
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for inserts")
                        .generate(rng_ref, ups.character_set.or(character_set), thread_id);
                    pool.push(key);
                }
            }

            // reverse sort so that we can pop from the end
            pool.sort_by(|a, b| b.cmp(a));

            let k = sorted.k.evaluate(rng_ref) as usize;
            for _ in 0..(k / 2) {
                // clamp bounds are [idx-l = 0, idx+l = pool.len() - 1]
                let idx = rng_ref.random_range(0..pool.len()) as isize;
                let l = (sorted.l.evaluate(rng_ref) as isize)
                    .clamp(-idx, pool.len() as isize - 1 - idx);
                pool.swap(idx as usize, (idx + l) as usize);
            }
            Some(pool)
        } else {
            None
        };

        // A group must have at least 1 valid key before any other operation can occur.
        if keys_valid.is_empty() {
            if unique_insert_count + upsert_count == 0 {
                bail!(
                    "Invalid workload spec. Group must have existing valid keys or have insert operations."
                );
            }
            if let Some(is) = group.unique_inserts.as_ref() {
                // .expect("inserts to exist if insert count > 0");
                markers.add_op_count(Op::UniqueInsert, unique_insert_count - 1);
                let key = key_pool
                    .as_mut()
                    .and_then(|pool| pool.pop())
                    .unwrap_or_else(|| {
                        is.key
                            .as_ref()
                            .or(key)
                            .expect("No key or default key set for unique inserts")
                            .generate(rng_ref, is.character_set.or(character_set), thread_id)
                    });
                // let key = is.key.as_ref().or(key).expect("No key or default key set for unique inserts").generate(rng_ref, is.character_set);
                operation_handler.handle_insert(
                    rng_ref,
                    &key,
                    is.val
                        .as_ref()
                        .or(val)
                        .expect("No value or default value set for unique inserts"),
                    is.character_set.or(character_set),
                )?;
                keys_valid.push(key);
            };
        } else {
            markers.add_op_count(Op::UniqueInsert, unique_insert_count);
        }
        if keys_valid.is_empty() {
            let ups = group
                .inserts
                .as_ref()
                .expect("upserts to exist if no unique inserts and insert + upsert count > 0");
            markers.add_op_count(Op::Upsert, upsert_count - 1);

            let key = key_pool
                .as_mut()
                .and_then(|pool| pool.pop())
                .unwrap_or_else(|| {
                    ups.key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for inserts")
                        .generate(rng_ref, ups.character_set.or(character_set), thread_id)
                });
            // let key = is.key.as_ref().or(key).expect("No key or default key set for unique inserts").generate(rng_ref, is.character_set);
            operation_handler.handle_insert(
                rng_ref,
                &key,
                ups.val
                    .as_ref()
                    .or(val)
                    .expect("No value or default value set for inserts"),
                ups.character_set.or(character_set),
            )?;
            keys_valid.push(key);
        } else {
            markers.add_op_count(Op::Upsert, upsert_count);
        }

        markers.add_op_count(Op::Update, update_count);
        markers.add_op_count(Op::Merge, merge_count);
        markers.add_op_count(Op::PointDelete, delete_point_count);
        markers.add_op_count(Op::PointDeleteEmpty, delete_point_empty_count);
        markers.add_op_count(Op::RangeDelete, delete_range_count);
        markers.add_op_count(Op::PointQuery, query_point_count);
        markers.add_op_count(Op::EmptyPointQuery, query_point_empty_count);
        markers.add_op_count(Op::RangeQuery, query_range_count);
        markers.add_op_count(Op::BlindPointQuery, blind_point_query_count);
        markers.add_op_count(Op::BlindPointDelete, blind_point_delete_count);
        markers.add_op_count(Op::BlindRangeQuery, blind_range_query_count);
        markers.prepare_iter();
        let total_markers = markers.total;

        let default_fallback = StringExpr::Inner(crate::spec::StringExprInner::Segmented {
            separator: "".to_string(),
            segments: vec![
                StringExpr::Constant("usertable:user".to_string()),
                StringExpr::Inner(crate::spec::StringExprInner::Uniform {
                    len: crate::spec::NumberExpr::Constant(19.0),
                    character_set: Some(CharacterSet::Numeric),
                }),
            ],
        });

        let mut pregen_blind_point_queries = if let Some(bpq) = &group.blind_point_queries {
            pregenerate_keys_parallel(
                bpq.key.as_ref().or(key).unwrap_or(&default_fallback),
                bpq.character_set.or(character_set),
                blind_point_query_count,
                thread_id,
            )
        } else {
            Vec::new()
        };
        pregen_blind_point_queries.reverse();

        let mut pregen_blind_point_deletes = if let Some(bpd) = &group.blind_point_deletes {
            pregenerate_keys_parallel(
                bpd.key.as_ref().or(key).unwrap_or(&default_fallback),
                bpd.character_set.or(character_set),
                blind_point_delete_count,
                thread_id,
            )
        } else {
            Vec::new()
        };
        pregen_blind_point_deletes.reverse();

        let mut pregen_blind_range_queries = if let Some(brq) = &group.blind_range_queries {
            pregenerate_range_queries_parallel(
                brq,
                character_set,
                blind_range_query_count,
                key.or(Some(&default_fallback)),
                total_entries,
                thread_id,
            )
        } else {
            Vec::new()
        };
        pregen_blind_range_queries.reverse();

        eprintln!("[Generating] Section {} | Group {}", section_num, group_num);
        let progress_bar = ProgressBar::with_draw_target(
            Some(total_markers as u64),
            ProgressDrawTarget::stderr_with_hz(5),
        );
        progress_bar
            .set_style(ProgressStyle::default_bar().template("{bar:40} {percent}% ({eta})")?);

        let marker_iter = progress_bar.wrap_iter(markers.enumerate());
        // let marker_iter = markers.enumerate();

        for (i, marker) in marker_iter {
            // FIX: Add this back (need to get total number of operations and store it somewhere)

            if i.is_multiple_of(total_markers / 10) {
                debug!(
                    "Generating operation {i} ({}%)",
                    (i as f64 * 100.0 / total_markers as f64).round()
                );
            }

            match marker {
                Op::UniqueInsert => {
                    let start = Instant::now();
                    let is = group.unique_inserts.as_ref().ok_or_else(|| {
                        anyhow!("Insert marker can only appear when inserts is not None")
                    })?;
                    let key = loop {
                        let key = key_pool
                            .as_mut()
                            .and_then(|pool| pool.pop())
                            .unwrap_or_else(|| {
                                is.key
                                    .as_ref()
                                    .or(key)
                                    .expect("No key or default key set for unique inserts")
                                    .generate(rng_ref, is.character_set.or(character_set), thread_id)
                            });
                        if !keys_valid.contains(&key) {
                            break key;
                        }
                    };
                    // let key = is.key.as_ref().or(key).expect("No key or default key set for unique inserts").generate(rng_ref, is.character_set);
                    operation_handler.handle_insert(
                        rng_ref,
                        &key,
                        is.val
                            .as_ref()
                            .or(val)
                            .expect("No value or default value set for unique inserts"),
                        is.character_set.or(character_set),
                    )?;
                    keys_valid.push(key);
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_insert += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::Upsert => {
                    let start = Instant::now();
                    let is = group.inserts.as_ref().ok_or_else(|| {
                        anyhow!("Upsert marker can only appear when upserts is not None")
                    })?;
                    let key = key_pool
                        .as_mut()
                        .and_then(|pool| pool.pop())
                        .unwrap_or_else(|| {
                            is.key
                                .as_ref()
                                .or(key)
                                .expect("No key or default key set for unique inserts")
                                .generate(rng_ref, is.character_set.or(character_set), thread_id)
                        });
                    operation_handler.handle_insert(
                        rng_ref,
                        &key,
                        is.val
                            .as_ref()
                            .or(val)
                            .expect("No value or default value set for unique inserts"),
                        is.character_set.or(character_set),
                    )?;

                    keys_valid.push(key);
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_upsert += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::Update => {
                    let start = Instant::now();
                    let us = group.updates.as_ref().ok_or_else(|| {
                        anyhow!("Update marker can only appear when updates is not None")
                    })?;
                    if keys_valid.is_empty() {
                        continue;
                    }
                    // keys_valid.sort();
                    let key = keys_valid.get_random(
                        rng_ref,
                        us.selection.as_ref().unwrap_or(
                            section
                                .default_distributions
                                .updates_selection
                                .as_ref()
                                .unwrap_or(&workload.default_distributions.updates_selection),
                        ),
                    );
                    operation_handler.handle_update(
                        rng_ref,
                        key,
                        us.val
                            .as_ref()
                            .or(val)
                            .expect("No value or default value set for updates"),
                        us.character_set.or(character_set),
                    )?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_update += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::Merge => {
                    let start = Instant::now();
                    let ms = group.merges.as_ref().ok_or_else(|| {
                        anyhow!("Merge marker can only appear when updates is not None")
                    })?;
                    if keys_valid.is_empty() {
                        continue;
                    }
                    // keys_valid.sort();
                    let key = keys_valid.get_random(
                        rng_ref,
                        ms.selection.as_ref().unwrap_or(
                            section
                                .default_distributions
                                .merges_selection
                                .as_ref()
                                .unwrap_or(&workload.default_distributions.merges_selection),
                        ),
                    );
                    operation_handler.handle_merge(
                        rng_ref,
                        key,
                        ms.val
                            .as_ref()
                            .or(val)
                            .expect("No value or default value set for merges"),
                        ms.character_set.or(character_set),
                    )?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_merge += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::PointDelete => {
                    let start = Instant::now();
                    if keys_valid.is_empty() {
                        continue;
                    }
                    let pds = group.point_deletes.as_ref().ok_or_else(|| {
                        anyhow!(
                            "Point delete marker can only appear when point deletes is not None"
                        )
                    })?;
                    // keys_valid.sort();
                    let key = keys_valid.remove_random(
                        rng_ref,
                        pds.selection.as_ref().unwrap_or(
                            section
                                .default_distributions
                                .point_deletes_selection
                                .as_ref()
                                .unwrap_or(&workload.default_distributions.point_deletes_selection),
                        ),
                    );

                    operation_handler.handle_point_delete(&key)?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_delete_point += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::PointQuery => {
                    let start = Instant::now();
                    if keys_valid.is_empty() {
                        continue;
                    }
                    let pqs = group.point_queries.as_ref().ok_or_else(|| {
                        anyhow!("Point query marker can only appear when updates is not None")
                    })?;
                    // keys_valid.sort();
                    let key = keys_valid.get_random(
                        rng_ref,
                        pqs.selection.as_ref().unwrap_or(
                            section
                                .default_distributions
                                .point_queries_selection
                                .as_ref()
                                .unwrap_or(&workload.default_distributions.point_queries_selection),
                        ),
                    );
                    operation_handler.handle_point_query(key)?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_query_point += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::PointDeleteEmpty => {
                    let start = Instant::now();
                    let epd = group.empty_point_deletes.as_ref().ok_or_else(|| {
                            anyhow!("Empty point delete marker can only appear when empty_point_deletes is not None")
                        })?;
                    let key = loop {
                        let k = epd
                            .key
                            .as_ref()
                            .or(key)
                            .expect("No key or default key set for empty point deletes")
                            .generate(rng_ref, epd.character_set.or(character_set), thread_id);
                        if !keys_valid.contains(&k) {
                            break k;
                        }
                    };

                    operation_handler.handle_point_delete(&key)?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_delete_point_empty += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::EmptyPointQuery => {
                    let start = Instant::now();
                    let epq = group.empty_point_queries.as_ref().ok_or_else(|| {
                            anyhow!("Empty point query marker can only appear when empty_point_queries is not None")
                        })?;
                    let char_set = epq.character_set.or(character_set);
                    let key = loop {
                        let k = epq
                            .key
                            .as_ref()
                            .or(key)
                            .expect("No key or default key set for empty point queries")
                            .generate(rng_ref, char_set, thread_id);
                        if !keys_valid.contains(&k) {
                            break k;
                        }
                    };

                    operation_handler.handle_point_query(&key)?;
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_query_point_empty += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::RangeQuery => {
                    let start = Instant::now();
                    let rqs = group.range_queries.as_ref().ok_or_else(|| {
                        anyhow!("Range query marker can only appear when range_queries is not None")
                    })?;
                    if keys_valid.is_empty() {
                        continue;
                    }

                    let range_length = rqs.get_range_length(rng_ref, keys_valid.len());
                    match rqs.range_format {
                        RangeFormat::StartCount => {
                            let (_, key) = keys_valid.get_random_range_start(
                                range_length,
                                rng_ref,
                                rqs.selection.as_ref().unwrap_or(
                                    section
                                        .default_distributions
                                        .range_queries_selection
                                        .as_ref()
                                        .unwrap_or(
                                            &workload.default_distributions.range_queries_selection,
                                        ),
                                ),
                            );

                            operation_handler.handle_range_query_count(key, range_length)?
                        }
                        RangeFormat::StartEnd => {
                            keys_valid.sort();
                            let (key1, key2) = keys_valid.get_range_random(
                                range_length,
                                rng_ref,
                                rqs.selection.as_ref().unwrap_or(
                                    section
                                        .default_distributions
                                        .range_queries_selection
                                        .as_ref()
                                        .unwrap_or(
                                            &workload.default_distributions.range_queries_selection,
                                        ),
                                ),
                            );

                            operation_handler.handle_range_query(key1, key2)?
                        }
                    }
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_query_range += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::RangeDelete => {
                    let start = Instant::now();
                    let rds =
                        group.range_deletes.as_ref().ok_or_else(|| {
                            anyhow!(
                                "RangeDelete marker can only appear when range_deletes is not None",
                            )
                        })?;
                    if keys_valid.is_empty() {
                        continue;
                    }

                    let range_length = rds.get_range_length(rng_ref, keys_valid.len());
                    keys_valid.sort();
                    match rds.range_format {
                        RangeFormat::StartCount => {
                            let (start_index, key) = keys_valid.get_random_range_start(
                                range_length,
                                rng_ref,
                                rds.selection.as_ref().unwrap_or(
                                    section
                                        .default_distributions
                                        .range_deletes_selection
                                        .as_ref()
                                        .unwrap_or(
                                            &workload.default_distributions.range_deletes_selection,
                                        ),
                                ),
                            );
                            let key = key.clone();
                            let end_index = start_index + range_length;
                            keys_valid.remove_range(start_index..end_index);
                            operation_handler.handle_range_delete_count(&key, range_length)?
                        }
                        RangeFormat::StartEnd => {
                            let (key1, key2) = keys_valid.remove_range_random(
                                range_length,
                                rng_ref,
                                rds.selection.as_ref().unwrap_or(
                                    section
                                        .default_distributions
                                        .range_deletes_selection
                                        .as_ref()
                                        .unwrap_or(
                                            &workload.default_distributions.range_deletes_selection,
                                        ),
                                ),
                            );

                            operation_handler.handle_range_delete(&key1, &key2)?
                        }
                    }
                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_delete_range += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::BlindPointQuery => {
                    let start = Instant::now();
                    let key = pregen_blind_point_queries.pop().expect("pregenerated blind query key");

                    operation_handler.handle_blind_point_query(&key)?;

                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_blind_point_query += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::BlindPointDelete => {
                    let start = Instant::now();
                    let key = pregen_blind_point_deletes.pop().expect("pregenerated blind delete key");

                    operation_handler.handle_blind_point_delete(&key)?;

                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_blind_point_delete += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::BlindRangeQuery => {
                    let start = Instant::now();
                    let (key, count) = pregen_blind_range_queries.pop().expect("pregenerated blind range query");

                    operation_handler.handle_blind_range_query(&key, count)?;

                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_blind_range_query += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
            }
        }

        if group.enable_granular_stats {
            if let Some(name) = &group.name {
                operation_handler.end_stat_flush(name, BenchmarkerType::Group)?;
            } else {
                let name = format!("Section {} Group {}", section_num, group_num);
                operation_handler.end_stat_flush(name.as_str(), BenchmarkerType::Group)?;
            }
        }

        progress_bar.finish_and_clear();
    }

    if section.enable_granular_stats {
        if let Some(name) = &section.name {
            operation_handler.end_stat_flush(name, BenchmarkerType::Section)?;
        } else {
            let name = format!("Section {}", section_num);
            operation_handler.end_stat_flush(name.as_str(), BenchmarkerType::Section)?;
        }
    }

    return Ok(());
}

fn generate_workload_spec(workload_spec: WorkloadSpec, output_file: &PathBuf, thread_id: Option<usize>) -> Result<()> {
    let global_start = std::time::Instant::now();
    if std::env::var("TECTONIC_PARALLEL_GEN").is_ok() && workload_spec.sections.len() > 1 {
        println!("[Tectonic Parallel Gen] Spawning {} threads (one per section) to generate workload sections in parallel", workload_spec.sections.len());
        let workload_spec = Arc::new(workload_spec);
        let mut handles = Vec::new();

        for i in 0..workload_spec.sections.len() {
            let spec = Arc::clone(&workload_spec);
            let handle = std::thread::spawn(move || -> Result<Vec<u8>> {
                let section_name = spec.sections[i].name.clone().unwrap_or_else(|| format!("Section {}", i));
                let start_elapsed = global_start.elapsed().as_secs_f64();
                let mut buffer = Vec::new();
                {
                    let mut write_handler = WriteHandler(&mut buffer);
                    let mut timings = OperationTimings::default();
                    generate_section(&mut write_handler, &mut timings, &spec, &spec.sections[i], i, thread_id)?;
                }
                let end_elapsed = global_start.elapsed().as_secs_f64();
                println!("[Tectonic Parallel Gen] Section {} ({}) started at {:.4}s, finished at {:.4}s (duration: {:.4}s)", i, section_name, start_elapsed, end_elapsed, end_elapsed - start_elapsed);
                Ok(buffer)
            });
            handles.push(handle);
        }

        let mut final_file = BufWriter::with_capacity(1024 * 1024, File::create(output_file)?);
        for handle in handles {
            let buffer = handle.join().map_err(|e| anyhow!("Thread panicked: {:?}", e))??;
            final_file.write_all(&buffer)?;
        }
        final_file.flush()?;
    } else {
        println!("[Tectonic Sequential Gen] Generating {} sections sequentially", workload_spec.sections.len());
        let mut final_file = BufWriter::with_capacity(1024 * 1024, File::create(output_file)?);
        {
            let mut write_handler = WriteHandler(&mut final_file);
            let mut timings = OperationTimings::default();
            for i in 0..workload_spec.sections.len() {
                let section_name = workload_spec.sections[i].name.clone().unwrap_or_else(|| format!("Section {}", i));
                let start_elapsed = global_start.elapsed().as_secs_f64();
                generate_section(&mut write_handler, &mut timings, &workload_spec, &workload_spec.sections[i], i, thread_id)?;
                let end_elapsed = global_start.elapsed().as_secs_f64();
                println!("[Tectonic Sequential Gen] Section {} ({}) started at {:.4}s, finished at {:.4}s (duration: {:.4}s)", i, section_name, start_elapsed, end_elapsed, end_elapsed - start_elapsed);
            }
            println!("Tectonic_Op_Timings: {{\"Insert\":{},\"Update\":{},\"Point Query\":{},\"Point Delete\":{},\"Range Query\":{},\"Range Delete\":{}}}",
                timings.time_insert.as_secs_f64() + timings.time_upsert.as_secs_f64(),
                timings.time_update.as_secs_f64() + timings.time_merge.as_secs_f64(),
                timings.time_query_point.as_secs_f64() + timings.time_query_point_empty.as_secs_f64() + timings.time_blind_point_query.as_secs_f64(),
                timings.time_delete_point.as_secs_f64() + timings.time_delete_point_empty.as_secs_f64() + timings.time_blind_point_delete.as_secs_f64(),
                timings.time_query_range.as_secs_f64(),
                timings.time_delete_range.as_secs_f64() + timings.time_blind_range_query.as_secs_f64()
            );
        }
        final_file.flush()?;
    }
    Ok(())
}

/// Takes in a JSON representation of a workload specification and writes the workload to a file.
pub fn generate_workload(workload_spec_string: String, output_file: &PathBuf, thread_id: Option<usize>) -> Result<()> {
    let workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string.as_str()).context("Parsing spec file")?;
    drop(workload_spec_string);
    generate_workload_spec(workload_spec, output_file, thread_id)
}

pub fn scale_and_generate_workload(
    workload_spec_string: String,
    output_file: &PathBuf,
    scale: f64,
    thread_id: Option<usize>,
) -> Result<()> {
    let mut workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string.as_str()).context("Parsing spec file")?;
    drop(workload_spec_string);
    println!("Scaling spec");
    scale_spec(&mut workload_spec, scale);
    generate_workload_spec(workload_spec, output_file, thread_id)
}

pub fn scale_and_benchmark_workload(
    workload_spec_string: String,
    database_name: &str,
    db_path: Option<&str>,
    config: Option<&str>,
    scale: f64,
    threads: usize,
    target_rate: Option<u64>,
    status_interval: Option<u64>,
    csv_log: Option<&str>,
) -> Result<()> {
    let database_name = database_name.to_string();
    let db_path = db_path.map(String::from);
    let config = config.map(String::from);

    let mut handles = Vec::new();
    let global_ops = Arc::new(AtomicUsize::new(0));
    let global_read_latency_sum = Arc::new(std::sync::atomic::AtomicU64::new(0));
    let global_read_count = Arc::new(AtomicUsize::new(0));
    let stop_signal = Arc::new(std::sync::atomic::AtomicBool::new(false));

    if let Some(interval) = status_interval {
        let global_ops_clone = global_ops.clone();
        let global_read_latency_sum_clone = global_read_latency_sum.clone();
        let global_read_count_clone = global_read_count.clone();
        let stop_clone = stop_signal.clone();
        let csv_path = csv_log.map(|s| s.to_string());
        
        std::thread::spawn(move || {
            let mut last_ops = 0;
            let start = std::time::Instant::now();
            if let Some(ref path) = csv_path {
                if let Ok(mut f) = std::fs::File::create(path) {
                    use std::io::Write;
                    let _ = writeln!(f, "TimeElapsed(s),AvgReadLatency(ms)");
                }
            }
            while !stop_clone.load(Ordering::Relaxed) {
                std::thread::sleep(std::time::Duration::from_secs(interval));
                let current_ops = global_ops_clone.load(Ordering::Relaxed);
                let ops_in_interval = current_ops.saturating_sub(last_ops);
                last_ops = current_ops;
                
                let sum = global_read_latency_sum_clone.swap(0, Ordering::Relaxed);
                let count = global_read_count_clone.swap(0, Ordering::Relaxed);
                let avg_latency_ms = if count > 0 {
                    (sum as f64 / count as f64) / 1_000_000.0
                } else {
                    0.0
                };
                let elapsed = start.elapsed().as_secs();
                if avg_latency_ms > 0.0 {
                    println!("[Periodic] {}s elapsed: {} ops/sec, Avg Read Latency: {:.4} ms", elapsed, ops_in_interval as f64 / (interval as f64), avg_latency_ms);
                } else {
                    println!("[Periodic] {}s elapsed: {} ops/sec", elapsed, ops_in_interval as f64 / (interval as f64));
                }
                
                if let Some(ref path) = csv_path {
                    if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(path) {
                        use std::io::Write;
                        if avg_latency_ms > 0.0 {
                            let _ = writeln!(f, "{},{:.4}", elapsed, avg_latency_ms);
                        }
                    }
                }
            }
        });
    }

    for t in 0..threads {
        let db_name_clone = database_name.clone();
        let db_path_clone = db_path.clone();
        let config_clone = config.clone();
        let spec_string_clone = workload_spec_string.clone();
        let global_ops_clone = global_ops.clone();
        
        let global_read_latency_sum_clone = global_read_latency_sum.clone();
        let global_read_count_clone = global_read_count.clone();
        
        let prefix = if threads > 1 {
            Some(format!("t{}:", t).into_bytes())
        } else {
            None
        };

        let handle = std::thread::spawn(move || -> Result<()> {
            let mut spec_clone: WorkloadSpec =
                serde_json::from_str(&spec_string_clone).context("Parsing spec file")?;
            scale_spec(&mut spec_clone, scale / (threads as f64));
            let mut benchmarker = Benchmarker::new(Db::new(&db_name_clone, db_path_clone.as_deref(), config_clone.as_deref())?);
            benchmarker.start();
            let mut handler = DBHandler::new(&mut benchmarker, prefix, target_rate.map(|r| r / (threads as u64).max(1)), Some(global_ops_clone), Some(global_read_latency_sum_clone), Some(global_read_count_clone));
            generate_operations(handler, &spec_clone)?;
            benchmarker.end();
            if threads > 1 {
                println!("--- Thread {} Summary ---", t);
            }
            benchmarker.print_summary();
            Ok(())
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().unwrap()?;
    }
    
    stop_signal.store(true, Ordering::Relaxed);

    Ok(())
}

macro_rules! scale_fields {
    ($group:expr, $scale:expr, [$($field:ident),*]) => {
        $(
            if let Some(ref mut op) = $group.$field {
                op.scale($scale);
            }
        )*
    };
}

fn scale_spec(workload_spec: &mut WorkloadSpec, factor: f64) {
    workload_spec
        .default_distributions
        .updates_selection
        .scale(factor);
    workload_spec
        .default_distributions
        .merges_selection
        .scale(factor);
    workload_spec
        .default_distributions
        .point_deletes_selection
        .scale(factor);
    workload_spec
        .default_distributions
        .range_queries_selection
        .scale(factor);
    workload_spec
        .default_distributions
        .point_queries_selection
        .scale(factor);
    workload_spec
        .default_distributions
        .range_deletes_selection
        .scale(factor);
    for section in workload_spec.sections.iter_mut() {
        if let Some(distr) = &mut section.default_distributions.updates_selection {
            distr.scale(factor);
        }
        if let Some(distr) = &mut section.default_distributions.merges_selection {
            distr.scale(factor);
        }
        if let Some(distr) = &mut section.default_distributions.point_deletes_selection {
            distr.scale(factor);
        }
        if let Some(distr) = &mut section.default_distributions.range_queries_selection {
            distr.scale(factor);
        }
        if let Some(distr) = &mut section.default_distributions.point_queries_selection {
            distr.scale(factor);
        }
        if let Some(distr) = &mut section.default_distributions.range_deletes_selection {
            distr.scale(factor);
        }
        for group in section.groups.iter_mut() {
            scale_fields!(
                group,
                factor,
                [
                    unique_inserts,
                    inserts,
                    updates,
                    merges,
                    point_deletes,
                    empty_point_deletes,
                    range_deletes,
                    point_queries,
                    empty_point_queries,
                    range_queries,
                    blind_point_queries,
                    blind_point_deletes,
                    blind_range_queries
                ]
            );
        }
    }
}

pub fn generate_workload_spec_schema() -> serde_json::Result<String> {
    let schema = schemars::schema_for!(WorkloadSpec);
    return serde_json::to_string_pretty(&schema);
}

pub fn benchmark_workload(
    workload_spec_string: String,
    database_name: &str,
    db_path: Option<&str>,
    config: Option<&str>,
    threads: usize,
    target_rate: Option<u64>,
    status_interval: Option<u64>,
    csv_log: Option<&str>,
) -> Result<()> {
    let database_name = database_name.to_string();
    let db_path = db_path.map(String::from);
    let config = config.map(String::from);

    let mut handles = Vec::new();
    let global_ops = Arc::new(AtomicUsize::new(0));
    let global_read_latency_sum = Arc::new(std::sync::atomic::AtomicU64::new(0));
    let global_read_count = Arc::new(AtomicUsize::new(0));
    let stop_signal = Arc::new(std::sync::atomic::AtomicBool::new(false));

    if let Some(interval) = status_interval {
        let global_ops_clone = global_ops.clone();
        let global_read_latency_sum_clone = global_read_latency_sum.clone();
        let global_read_count_clone = global_read_count.clone();
        let stop_clone = stop_signal.clone();
        let csv_path = csv_log.map(|s| s.to_string());
        
        std::thread::spawn(move || {
            let mut last_ops = 0;
            let start = std::time::Instant::now();
            if let Some(ref path) = csv_path {
                if let Ok(mut f) = std::fs::File::create(path) {
                    use std::io::Write;
                    let _ = writeln!(f, "TimeElapsed(s),AvgReadLatency(ms)");
                }
            }
            while !stop_clone.load(Ordering::Relaxed) {
                std::thread::sleep(std::time::Duration::from_secs(interval));
                let current_ops = global_ops_clone.load(Ordering::Relaxed);
                let ops_in_interval = current_ops.saturating_sub(last_ops);
                last_ops = current_ops;
                
                let sum = global_read_latency_sum_clone.swap(0, Ordering::Relaxed);
                let count = global_read_count_clone.swap(0, Ordering::Relaxed);
                let avg_latency_ms = if count > 0 {
                    (sum as f64 / count as f64) / 1_000_000.0
                } else {
                    0.0
                };
                let elapsed = start.elapsed().as_secs();
                if avg_latency_ms > 0.0 {
                    println!("[Periodic] {}s elapsed: {} ops/sec, Avg Read Latency: {:.4} ms", elapsed, ops_in_interval as f64 / (interval as f64), avg_latency_ms);
                } else {
                    println!("[Periodic] {}s elapsed: {} ops/sec", elapsed, ops_in_interval as f64 / (interval as f64));
                }
                
                if let Some(ref path) = csv_path {
                    if let Ok(mut f) = std::fs::OpenOptions::new().append(true).open(path) {
                        use std::io::Write;
                        if avg_latency_ms > 0.0 {
                            let _ = writeln!(f, "{},{:.4}", elapsed, avg_latency_ms);
                        }
                    }
                }
            }
        });
    }

    for t in 0..threads {
        let db_name_clone = database_name.clone();
        let db_path_clone = db_path.clone();
        let config_clone = config.clone();
        let spec_string_clone = workload_spec_string.clone();
        let global_ops_clone = global_ops.clone();
        
        let global_read_latency_sum_clone = global_read_latency_sum.clone();
        let global_read_count_clone = global_read_count.clone();
        
        let prefix = if threads > 1 {
            Some(format!("t{}:", t).into_bytes())
        } else {
            None
        };

        let handle = std::thread::spawn(move || -> Result<()> {
            let mut spec_clone: WorkloadSpec =
                serde_json::from_str(&spec_string_clone).context("Parsing spec file")?;
            scale_spec(&mut spec_clone, 1.0 / (threads as f64));
            let mut benchmarker = Benchmarker::new(Db::new(&db_name_clone, db_path_clone.as_deref(), config_clone.as_deref())?);
            benchmarker.start();
            let mut handler = DBHandler::new(&mut benchmarker, prefix, target_rate.map(|r| r / (threads as u64).max(1)), Some(global_ops_clone), Some(global_read_latency_sum_clone), Some(global_read_count_clone));
            generate_operations(handler, &spec_clone)?;
            benchmarker.end();
            if threads > 1 {
                println!("--- Thread {} Summary ---", t);
            }
            benchmarker.print_summary();
            Ok(())
        });
        handles.push(handle);
    }

    for handle in handles {
        handle.join().unwrap()?;
    }
    
    stop_signal.store(true, Ordering::Relaxed);

    Ok(())
}
