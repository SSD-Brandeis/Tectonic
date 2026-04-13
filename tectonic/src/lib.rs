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
use std::fs::File;
use std::io::{BufWriter, Write};
use std::mem::variant_count;
use std::path::PathBuf;
use std::time::{Duration, Instant};
use tracing::{debug, info, trace};

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
use crate::spec::{CharacterSet, RangeFormat, StringExpr, WorkloadSpec, WorkloadSpecSection};

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
}

struct DBHandler<'a, 'b>(&'a mut Benchmarker<'b>);

impl<'a, 'b> OperationHandler for DBHandler<'a, 'b> {
    fn handle_insert(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let value = val.generate(rng, character_set);
        self.0.handle_insert(key, value.as_ref())
    }

    fn handle_update(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let value = val.generate(rng, character_set);
        self.0.handle_update(key, value.as_ref())
    }

    fn handle_merge(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        let value = val.generate(rng, character_set);
        self.0.handle_merge(key, value.as_ref())
    }

    fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        self.0.handle_point_delete(key)
    }

    fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        self.0.handle_point_query(key)
    }

    fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        self.0.handle_range_query(key1, key2)
    }

    fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        self.0.handle_range_query_count(key1, count)
    }

    fn handle_range_delete(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        self.0.handle_range_delete(key1, key2)
    }

    fn handle_range_delete_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        self.0.handle_range_delete_count(key1, count)
    }

    fn start_stat_flush(&mut self, benchmarker: BenchmarkerType) -> Result<()> {
        self.0.start_stat_flush(benchmarker);

        Ok(())
    }

    fn end_stat_flush(&mut self, name: &str, benchmarker: BenchmarkerType) -> Result<()> {
        self.0.end_stat_flush(name, benchmarker)?;

        Ok(())
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
pub fn generate_operations<OP: OperationHandler>(
    mut operation_handler: OP,
    workload: &WorkloadSpec,
) -> Result<()> {
    // write_operations_with_keyset(writer, workload, VecBloomFilterKeySet::new)

    // WARN: This doesn't make sense to me
    // Shouldn't we be using bloom filters or a hash_map if we have empty queries
    // Also why do we need a vector if we don't have range queries, can't we just use a hashmap, or
    // just a set
    let mut operation_timings = OperationTimings::default();

    for (i, section) in workload.sections.iter().enumerate() {
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
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                EmptyKeySet::new,
            )?
        } else if requires_sorting && requires_deletion && requires_contains_check {
            // TODO: Should use skip list or b+ tree
            info!("Using VecHashSetOptionKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecOptionHashSetKeySet::new,
            )?
        } else if requires_sorting && requires_deletion {
            info!("Using VecOptionKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecOptionKeySet::new,
            )?
        } else if requires_sorting && requires_contains_check {
            info!("Using VecHashSetKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecHashSetKeySet::new,
            )?
        } else if requires_deletion && requires_contains_check {
            info!("Using VecKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecKeySet::new,
            )?
        } else if requires_contains_check && requires_random_element {
            info!("Using VecBloomFilterKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecBloomFilterKeySet::new,
            )?
        } else if requires_contains_check {
            info!("Using BloomFilterKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                BloomFilterKeySet::new,
            )?
        } else {
            info!("Using VecKeySet");
            write_operations_with_keyset(
                &mut operation_handler,
                &mut operation_timings,
                workload,
                section,
                i,
                VecKeySet::new,
            )?
        }
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
        // unreachable!("Failed to generate next operation");
    }
}

pub fn write_operations_with_keyset<KeySetT: KeySet, OP: OperationHandler>(
    operation_handler: &mut OP,
    operation_timings: &mut OperationTimings,
    workload: &WorkloadSpec,
    section: &WorkloadSpecSection,
    section_num: usize,
    keyset_constructor: impl Fn(usize) -> KeySetT,
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

        let more_delete_point_than_keys = delete_point_count > keys_valid.len();
        if more_delete_point_than_keys {
            bail!("Cannot have more point deletes than existing valid keys.");
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
                    let key = is
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for unique inserts")
                        .generate(rng_ref, is.character_set.or(character_set));
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
                        .generate(rng_ref, ups.character_set.or(character_set));
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
                            .generate(rng_ref, is.character_set.or(character_set))
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
                        .generate(rng_ref, ups.character_set.or(character_set))
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
                                    .generate(rng_ref, is.character_set.or(character_set))
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
                                .generate(rng_ref, is.character_set.or(character_set))
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
                        bail!("Cannot have updates when there are no valid keys.");
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
                        bail!("Cannot have merges when there are no valid keys.");
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
                        bail!("Cannot have point queries when there are no valid keys.");
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
                            .generate(rng_ref, epd.character_set.or(character_set));
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
                            .generate(rng_ref, char_set);
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
                        bail!("Cannot have range queries when there are no valid keys.");
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
                        bail!("Cannot have range deletes when there are no valid keys.");
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
                    let bpq = group.blind_point_queries.as_ref().ok_or_else(||
                        anyhow!("BlindPointQuery marker can only appear when blind_point_queries is not None"))?;

                    if keys_valid.is_empty() {
                        bail!("Cannot have range deletes when there are no valid keys.");
                    }

                    let key = bpq
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for blind point queries")
                        .generate(rng_ref, bpq.character_set.or(character_set));

                    operation_handler.handle_point_query(&key)?;

                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_blind_point_query += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::BlindPointDelete => {
                    let start = Instant::now();
                    let bpd = group.blind_point_deletes.as_ref().ok_or_else(
                        || anyhow!("BlindPointDelete marker can only appear when blind_point_queries is not None"))?;

                    if keys_valid.is_empty() {
                        bail!("Cannot have range deletes when there are no valid keys.");
                    }

                    let key = bpd
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for blind point deletes")
                        .generate(rng_ref, bpd.character_set.or(character_set));

                    operation_handler.handle_point_query(&key)?;

                    let duration = Instant::now().duration_since(start);
                    operation_timings.time_blind_point_delete += duration;
                    if duration > Duration::from_millis(1) {
                        trace!(?marker, ?duration);
                    }
                }
                Op::BlindRangeQuery => {
                    let start = Instant::now();
                    let brq = group.blind_range_queries.as_ref().ok_or_else(
                        || anyhow!("BlindPointDelete marker can only appear when blind_point_queries is not None"))?;

                    if keys_valid.is_empty() {
                        bail!("Cannot have range deletes when there are no valid keys.");
                    }

                    let key = brq
                        .key
                        .as_ref()
                        .or(key)
                        .expect("No key or default key set for blind range queries")
                        .generate(rng_ref, brq.character_set.or(character_set));

                    let count = brq.get_range_length(rng_ref, total_entries);
                    operation_handler.handle_range_query_count(&key, count)?;

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

/// Takes in a JSON representation of a workload specification and writes the workload to a file.
pub fn generate_workload(workload_spec_string: String, output_file: &PathBuf) -> Result<()> {
    let workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string.as_str()).context("Parsing spec file")?;
    drop(workload_spec_string);
    let mut buf_writer = BufWriter::with_capacity(1024 * 1024, File::create(output_file)?);
    let write_handler = WriteHandler(&mut buf_writer);
    generate_operations(write_handler, &workload_spec)?;
    buf_writer.flush()?;

    Ok(())
}

pub fn scale_and_generate_workload(
    workload_spec_string: String,
    output_file: &PathBuf,
    scale: f64,
) -> Result<()> {
    let mut workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string.as_str()).context("Parsing spec file")?;
    drop(workload_spec_string);
    println!("Scaling spec");
    scale_spec(&mut workload_spec, scale);
    let mut buf_writer = BufWriter::with_capacity(1024 * 1024, File::create(output_file)?);
    let write_handler = WriteHandler(&mut buf_writer);
    generate_operations(write_handler, &workload_spec)?;
    buf_writer.flush()?;

    Ok(())
}

pub fn scale_and_benchmark_workload(
    workload_spec_string: String,
    database_name: &str,
    db_path: Option<&str>,
    config: Option<&str>,
    scale: f64,
) -> Result<()> {
    let mut workload_spec: WorkloadSpec =
        serde_json::from_str(&workload_spec_string).context("Parsing spec file")?;
    drop(workload_spec_string);
    scale_spec(&mut workload_spec, scale);
    let mut benchmarker = Benchmarker::new(Db::new(database_name, db_path, config)?);
    benchmarker.start();
    generate_operations(DBHandler(&mut benchmarker), &workload_spec)?;
    benchmarker.end();
    benchmarker.print_summary();
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

fn scale_spec(workload_spec: &mut WorkloadSpec, scale: f64) {
    for section in workload_spec.sections.iter_mut() {
        for group in section.groups.iter_mut() {
            scale_fields!(
                group,
                scale,
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
) -> Result<()> {
    let workload_spec: WorkloadSpec =
        serde_json::from_str(&workload_spec_string).context("Parsing spec file")?;
    drop(workload_spec_string);
    let mut benchmarker = Benchmarker::new(Db::new(database_name, db_path, config)?);
    benchmarker.start();
    generate_operations(DBHandler(&mut benchmarker), &workload_spec)?;
    benchmarker.end();
    benchmarker.print_summary();
    Ok(())
}
