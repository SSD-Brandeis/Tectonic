#![feature(extend_one)]
#![feature(btree_cursors)]
#![feature(trusted_random_access)]
#![feature(trait_alias)]
#![allow(clippy::needless_return)]
#![allow(dead_code)]

use anyhow::{Context, Result, anyhow, bail};
use db_layer::{Benchmarker, DBTranslationLayer, Db};
use rand::prelude::SliceRandom;
use rand::{Rng, SeedableRng};
use rand_xoshiro::Xoshiro256Plus;
use std::fs::File;
use std::io::{BufWriter, Write};
use std::iter::repeat_n;
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
    Key, KeySet, VecBloomFilterKeySet, VecHashMapIndexKeySet, VecKeySet, VecOptionKeySet,
};
use crate::spec::{CharacterSet, RangeFormat, StringExpr, WorkloadSpec, WorkloadSpecGroup};

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
        AsciiOperationFormatter::write_insert(self.0, rng, key, val, character_set)
    }

    fn handle_update(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        AsciiOperationFormatter::write_update(self.0, rng, key, val, character_set)
    }

    fn handle_merge(
        &mut self,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        AsciiOperationFormatter::write_merge(self.0, rng, key, val, character_set)
    }

    fn handle_point_delete(&mut self, key: &Key) -> Result<()> {
        AsciiOperationFormatter::write_point_delete(self.0, key)
    }

    fn handle_point_query(&mut self, key: &Key) -> Result<()> {
        AsciiOperationFormatter::write_point_query(self.0, key)
    }

    fn handle_range_query(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        AsciiOperationFormatter::write_range_query(self.0, key1, key2)
    }

    fn handle_range_query_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        AsciiOperationFormatter::write_range_query_count(self.0, key1, count)
    }

    fn handle_range_delete(&mut self, key1: &Key, key2: &Key) -> Result<()> {
        AsciiOperationFormatter::write_range_delete(self.0, key1, key2)
    }

    fn handle_range_delete_count(&mut self, key1: &Key, count: usize) -> Result<()> {
        AsciiOperationFormatter::write_range_delete_count(self.0, key1, count)
    }
}

struct AsciiOperationFormatter;
impl AsciiOperationFormatter {
    fn write_insert(
        w: &mut impl Write,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        w.write_all("I ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_update(
        w: &mut impl Write,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        w.write_all("U ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_merge(
        w: &mut impl Write,
        rng: &mut impl Rng,
        key: &Key,
        val: &StringExpr,
        character_set: Option<CharacterSet>,
    ) -> Result<()> {
        w.write_all("M ".as_bytes())?;
        w.write_all(key)?;
        w.write_all(" ".as_bytes())?;
        val.write_all(w, rng, character_set)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_point_delete(w: &mut impl Write, key: &Key) -> Result<()> {
        w.write_all("D ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_point_query(w: &mut impl Write, key: &Key) -> Result<()> {
        w.write_all("P ".as_bytes())?;
        w.write_all(key)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_range_query(w: &mut impl Write, key1: &Key, key2: &Key) -> Result<()> {
        w.write_all("S ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(key2)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_range_query_count(w: &mut impl Write, key1: &Key, count: usize) -> Result<()> {
        w.write_all("S ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(count.to_string().as_bytes())?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_range_delete(w: &mut impl Write, key1: &Key, key2: &Key) -> Result<()> {
        w.write_all("R ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(key2)?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
    }
    fn write_range_delete_count(w: &mut impl Write, key1: &Key, count: usize) -> Result<()> {
        w.write_all("R ".as_bytes())?;
        w.write_all(key1)?;
        w.write_all(" ".as_bytes())?;
        w.write_all(count.to_string().as_bytes())?;
        w.write_all("\n".as_bytes())?;

        return Ok(());
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
}

// TODO: Allow for different sections to use different keysets

/// Generates a workload given the spec and writes it to the given writer.
pub fn generate_operations<OP: OperationHandler>(
    operation_handler: OP,
    workload: &WorkloadSpec,
) -> Result<()> {
    // write_operations_with_keyset(writer, workload, VecBloomFilterKeySet::new)
    // let insert_only = workload.has_unique_insert();
    let has_nonempty_deletes = workload.has_delete_point() || workload.has_delete_range();
    let has_sort_heavy = workload.has_update()
        || workload.has_merge()
        || workload.has_query_point()
        || workload.has_query_range();

    // TODO: Update this so it works properly with upserts
    let has_contains_check = !workload.skip_contains_check_all()
        && (workload.has_unique_insert()
            || workload.has_query_point_empty()
            || workload.has_delete_point_empty());

    // TODO: If is only upsert only workload, use empty keyset
    // WARN: This doesn't make sense to me
    // Shouldn't we be using bloom filters or a hash_map if we have empty queries
    // Also why do we need a vector if we don't have range queries, can't we just use a hashmap, or
    // just a set
    let mut operation_timings = OperationTimings::default();

    return if (has_nonempty_deletes) && (has_sort_heavy) {
        info!("Using VecOptionKeySet");
        // WARN: Is this a skiplist
        write_operations_with_keyset(
            operation_handler,
            workload,
            VecOptionKeySet::new,
            &mut operation_timings,
        )
    } else if has_nonempty_deletes {
        info!("Using VecHashMapIndexKeySet");
        write_operations_with_keyset(
            operation_handler,
            workload,
            VecHashMapIndexKeySet::new,
            &mut operation_timings,
        )
    } else if has_contains_check {
        info!("Using VecBloomFilterKeySet");
        write_operations_with_keyset(
            operation_handler,
            workload,
            VecBloomFilterKeySet::new,
            &mut operation_timings,
        )
    } else {
        info!("Using VecKeySet");
        write_operations_with_keyset(
            operation_handler,
            workload,
            VecKeySet::new,
            &mut operation_timings,
        )
    };
}

pub fn write_operations_with_keyset<KeySetT: KeySet, OP: OperationHandler>(
    mut operation_handler: OP,
    workload: &WorkloadSpec,
    keyset_constructor: impl Fn(usize) -> KeySetT,
    operation_timings: &mut OperationTimings,
) -> Result<()> {
    let mut rng = Xoshiro256Plus::from_os_rng();
    // let mut keys_prev_sections = BloomFilter::with_rate(0.01, todo!());

    for section in &workload.sections {
        let insert_counts: Vec<usize> = section
            .groups
            .iter()
            .map(|g| {
                g.unique_inserts
                    .as_ref()
                    .map_or(0, |is| is.op_count.evaluate(&mut rng) as usize)
            })
            .collect();

        let mut keys_valid =
            keyset_constructor(insert_counts.iter().sum() /*section.insert_count()*/);

        for (group, insert_count) in std::iter::zip(&section.groups, insert_counts) {
            let rng_ref = &mut rng;
            let mut markers: Vec<Op> = Vec::with_capacity(0 /*group.operation_count()*/);
            let character_set = group
                .character_set
                .or(section.character_set)
                .or(workload.character_set);

            let upsert_count = group
                .inserts
                .as_ref()
                .map_or(0, |is| is.op_count.evaluate(rng_ref) as usize);
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

            debug!(
                ?insert_count,
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
                let is = group
                    .unique_inserts
                    .as_ref()
                    .ok_or_else(|| anyhow!("Insert spec must exist if sorted config exists"))?;
                let mut pool = Vec::with_capacity(insert_count);
                for _ in 0..insert_count {
                    let key = is.key.generate(rng_ref, is.character_set.or(character_set));
                    pool.push(key);
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

            // if let WorkloadSpecGroup {
            //     unique_inserts: None,
            //     updates: None,
            //     merges: None,
            //     point_deletes: None,
            //     empty_point_deletes: None,
            //     range_deletes: None,
            //     point_queries: None,
            //     empty_point_queries: None,
            //     range_queries: None,
            //     ..
            // } = group
            // {
            //     if upsert_count == 0 {
            //         bail!("Invalid workload spec. Group cannot be empty")
            //     }
            // }
            // A group must have at least 1 valid key before any other operation can occur.
            if keys_valid.is_empty() {
                if insert_count + upsert_count == 0 {
                    bail!(
                        "Invalid workload spec. Group must have existing valid keys or have insert operations."
                    );
                }
                if let Some(is) = group.unique_inserts.as_ref() {
                    // .expect("inserts to exist if insert count > 0");
                    markers.extend(repeat_n(Op::UniqueInsert, insert_count - 1));
                    let key = key_pool
                        .as_mut()
                        .and_then(|pool| pool.pop())
                        .unwrap_or_else(|| {
                            is.key.generate(rng_ref, is.character_set.or(character_set))
                        });
                    // let key = is.key.generate(rng_ref, is.character_set);
                    operation_handler.handle_insert(
                        rng_ref,
                        &key,
                        &is.val,
                        is.character_set.or(character_set),
                    )?;
                    keys_valid.push(key);
                };
            } else {
                markers.extend(repeat_n(Op::UniqueInsert, insert_count));
            }
            if keys_valid.is_empty() {
                let ups = group
                    .inserts
                    .as_ref()
                    .expect("upserts to exist if no unique inserts and insert + upsert count > 0");
                markers.extend(repeat_n(Op::Upsert, upsert_count - 1));

                let key = key_pool
                    .as_mut()
                    .and_then(|pool| pool.pop())
                    .unwrap_or_else(|| {
                        ups.key
                            .generate(rng_ref, ups.character_set.or(character_set))
                    });
                // let key = is.key.generate(rng_ref, is.character_set);
                operation_handler.handle_insert(
                    rng_ref,
                    &key,
                    &ups.val,
                    ups.character_set.or(character_set),
                )?;
                keys_valid.push(key);
            } else {
                markers.extend(repeat_n(Op::Upsert, upsert_count));
            }

            markers.extend(repeat_n(Op::Update, update_count));
            markers.extend(repeat_n(Op::Merge, merge_count));
            markers.extend(repeat_n(Op::PointDelete, delete_point_count));
            markers.extend(repeat_n(Op::PointDeleteEmpty, delete_point_empty_count));
            markers.extend(repeat_n(Op::RangeDelete, delete_range_count));
            markers.extend(repeat_n(Op::PointQuery, query_point_count));
            markers.extend(repeat_n(Op::EmptyPointQuery, query_point_empty_count));
            markers.extend(repeat_n(Op::RangeQuery, query_range_count));
            markers.shuffle(rng_ref);

            for (i, marker) in markers.iter().enumerate() {
                if i.is_multiple_of(markers.len() / 10) {
                    debug!(
                        "Generating operation {i} ({}%)",
                        (i as f64 * 100.0 / markers.len() as f64).round()
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
                                    is.key.generate(rng_ref, is.character_set.or(character_set))
                                });
                            if !keys_valid.contains(&key) {
                                break key;
                            }
                        };
                        // let key = is.key.generate(rng_ref, is.character_set);
                        operation_handler.handle_insert(
                            rng_ref,
                            &key,
                            &is.val,
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
                                is.key.generate(rng_ref, is.character_set.or(character_set))
                            });
                        operation_handler.handle_insert(
                            rng_ref,
                            &key,
                            &is.val,
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
                        let key = keys_valid.get_random(rng_ref, &us.selection);
                        operation_handler.handle_update(
                            rng_ref,
                            key,
                            &us.val,
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
                        let key = keys_valid.get_random(rng_ref, &ms.selection);
                        operation_handler.handle_merge(
                            rng_ref,
                            key,
                            &ms.val,
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
                        let key = keys_valid.remove_random(rng_ref, &pds.selection);

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
                        let key = keys_valid.get_random(rng_ref, &pqs.selection);
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
                            let k = epq.key.generate(rng_ref, char_set);
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
                            anyhow!(
                                "Range query marker can only appear when range_queries is not None"
                            )
                        })?;
                        if keys_valid.is_empty() {
                            bail!("Cannot have range queries when there are no valid keys.");
                        }

                        let sel = rqs.selectivity.evaluate(rng_ref);
                        match rqs.range_format {
                            RangeFormat::StartCount => {
                                let key = keys_valid.get_random(rng_ref, &rqs.selection);

                                let count = (sel * keys_valid.len() as f64) as usize;
                                operation_handler.handle_range_query_count(key, count)?
                            }
                            RangeFormat::StartEnd => {
                                keys_valid.sort();
                                let (key1, key2) =
                                    keys_valid.get_range_random(sel, rng_ref, &rqs.selection);

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
                        let rds = group.range_deletes.as_ref().ok_or_else(|| {
                            anyhow!(
                                "RangeDelete marker can only appear when range_deletes is not None",
                            )
                        })?;
                        if keys_valid.is_empty() {
                            bail!("Cannot have range deletes when there are no valid keys.");
                        }

                        let sel = rds.selectivity.evaluate(rng_ref);
                        match rds.range_format {
                            RangeFormat::StartCount => {
                                let key = keys_valid.get_random(rng_ref, &rds.selection);

                                let count = (sel * keys_valid.len() as f64) as usize;
                                operation_handler.handle_range_delete_count(key, count)?
                            }
                            RangeFormat::StartEnd => {
                                keys_valid.sort();
                                let (key1, key2) =
                                    keys_valid.get_range_random(sel, rng_ref, &rds.selection);

                                operation_handler.handle_range_delete(key1, key2)?
                            }
                        }
                        let duration = Instant::now().duration_since(start);
                        operation_timings.time_delete_range += duration;
                        if duration > Duration::from_millis(1) {
                            trace!(?marker, ?duration);
                        }
                    }
                }
            }
        }
    }
    debug!(
        insert = %operation_timings.time_insert.as_secs_f64(),
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

/// Takes in a JSON representation of a workload specification and writes the workload to a file.
pub fn generate_workload(workload_spec_string: &str, output_file: &PathBuf) -> Result<()> {
    let workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string).context("Parsing spec file")?;
    let mut buf_writer = BufWriter::with_capacity(1024 * 1024, File::create(output_file)?);
    let write_handler = WriteHandler(&mut buf_writer);
    generate_operations(write_handler, &workload_spec)?;
    buf_writer.flush()?;

    Ok(())
}

pub fn generate_workload_spec_schema() -> serde_json::Result<String> {
    let schema = schemars::schema_for!(WorkloadSpec);
    return serde_json::to_string_pretty(&schema);
}

pub fn benchmark_workload(workload_spec_string: &str, database_name: &str) -> Result<()> {
    let workload_spec: WorkloadSpec =
        serde_json::from_str(workload_spec_string).context("Parsing spec file")?;
    let mut benchmarker = Benchmarker::new(Db::new(database_name)?);
    benchmarker.start();
    generate_operations(DBHandler(&mut benchmarker), &workload_spec)?;
    benchmarker.end();
    benchmarker.print_summary();
    Ok(())
}
