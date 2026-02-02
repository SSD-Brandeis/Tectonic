#![allow(clippy::needless_return)]
#![allow(dead_code)]

use rand::Rng;

use crate::spec::Distribution;
use bloom::{ASMS, BloomFilter};
use std::cmp::max;
use std::collections::{HashMap, HashSet};
use std::ops::{Bound, Range};
use std::rc::Rc;

// pub type Key = Box<[u8]>;
pub type Key = Rc<[u8]>;
// TODO: Fix lifetime issue by maybe keeping the vec separate from the keysets so the lifetimes are
// tied to it?

// Modified from https://github.com/servo/rust-fnv/blob/main/lib.rs#L146-L157 (MIT)
const INITIAL_STATE: u64 = 0xcbf2_9ce4_8422_2325;
const PRIME: u64 = 0x0100_0000_01b3;
#[inline]
#[must_use]
pub const fn fnv_hash(mut bytes: u64) -> u64 {
    let mut hash = INITIAL_STATE;
    let mut i = 0;
    while i < u64::BITS {
        hash ^= bytes & 0xFF;
        hash = hash.wrapping_mul(PRIME);
        bytes >>= 8;
        i += 1;
    }
    hash
}

fn unbiased_index_(mut idx: usize, len: usize) -> usize {
    let range = usize::MAX - usize::MAX % len;
    loop {
        idx ^= idx >> 33;
        idx = idx.wrapping_mul(0xff51afd7ed558ccd);
        idx ^= idx >> 33;
        idx = idx.wrapping_mul(0xc4ceb9fe1a85ec53);
        idx ^= idx >> 33;

        if idx < range {
            return idx % len;
        }
    }
}

// TODO: How does this hold up when there are interleaved inserts? Are the same keys targed or does
// it get "spread out".
#[inline]
#[must_use]
fn unbiased_index(idx: usize, len: usize) -> usize {
    // return unbiased_index_(idx + 1, len + 1) - 1;
    return (fnv_hash(idx as u64) as usize) % len;
}

pub trait KeySet {
    fn new(capacity: usize) -> Self;

    fn len(&self) -> usize;

    fn is_empty(&self) -> bool;

    fn push(&mut self, key: Key);

    fn remove(&mut self, idx: usize) -> Key;

    fn remove_random(&mut self, rng: &mut impl Rng, distribution: &Distribution) -> Key {
        let x = distribution.evaluate(rng).clamp(0., 1. - f64::EPSILON);
        let idx = (x * self.len() as f64) as usize;
        let idx_hashed = unbiased_index(idx, self.len());
        return self.remove(idx_hashed);
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key);
    fn remove_range_random(
        &mut self,
        selectivity: f64,
        rng: &mut impl Rng,
        distribution: &Distribution,
    ) -> (Key, Key) {
        let num_keys = self.len();
        let range_len = (selectivity * (num_keys as f64)).floor() as usize;
        let valid_len = num_keys - range_len;

        let x = distribution.evaluate(rng).clamp(0., 1. - f64::EPSILON);
        let start_idx = (x * valid_len as f64) as usize;
        let end_idx = start_idx + range_len;

        return self.remove_range(start_idx..end_idx);
    }

    fn get(&self, idx: usize) -> &Key;

    fn get_random(&self, rng: &mut impl Rng, distribution: &Distribution) -> &Key {
        let x = distribution.evaluate(rng).clamp(0., 1. - f64::EPSILON);
        let idx = (x * self.len() as f64) as usize;
        let idx_hashed = unbiased_index(idx, self.len());
        return self.get(idx_hashed);
    }

    fn get_range_random(
        &mut self,
        selectivity: f64,
        rng: &mut impl Rng,
        distribution: &Distribution,
    ) -> (&Key, &Key) {
        let num_keys = self.len();
        let range_len = (selectivity * (num_keys as f64)).floor() as usize;
        let valid_len = num_keys - range_len;

        let x = distribution.evaluate(rng).clamp(0., 1. - f64::EPSILON);
        let start_idx = (x * valid_len as f64) as usize;
        let end_idx = start_idx + range_len;

        let key1 = self.get(start_idx);
        let key2 = self.get(end_idx);
        return (key1, key2);
    }

    fn contains(&self, key: &Key) -> bool;

    fn sort(
        &mut self,
        // sort_by: SortBy
    );
}

pub struct EmptyKeySet();

impl KeySet for EmptyKeySet {
    fn new(_capacity: usize) -> Self {
        Self()
    }

    fn len(&self) -> usize {
        0
    }

    fn is_empty(&self) -> bool {
        true
    }

    fn push(&mut self, _key: Key) {}

    fn remove(&mut self, _idx: usize) -> Key {
        panic!("Tried to remove from empty keyset")
    }

    fn remove_range(&mut self, _idx_range: Range<usize>) -> (Key, Key) {
        panic!("Tried to remove range from empty keyset")
    }

    fn get(&self, _idx: usize) -> &Key {
        panic!("Tried to get from empty keyset")
    }

    fn contains(&self, _key: &Key) -> bool {
        false
    }

    fn sort(
        &mut self,
        // sort_by: SortBy
    ) {
        panic!("Tried to sort empty keyset")
    }
}

pub struct VecKeySet {
    keys: Vec<Key>,
    sorted: bool,
}

impl KeySet for VecKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: Vec::with_capacity(capacity),
            sorted: true,
        };
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    fn push(&mut self, key: Key) {
        if self.sorted && self.keys.last().is_some_and(|last_key| last_key > &key) {
            self.sorted = false;
        }
        self.keys.push(key);
    }

    fn remove(&mut self, idx: usize) -> Key {
        let len = self.keys.len();
        self.keys.swap(idx, len - 1);
        return self.keys.remove(idx);
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        // TODO: we could maybe optimize this by copying elements into the range, or shrinking the vector length of the range is large enough/at the end
        let mut drain = self.keys.drain(idx_range);
        let key1 = drain.next().expect("to have at least one element");
        match drain.next_back() {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        }
    }

    fn get(&self, idx: usize) -> &Key {
        return &self.keys[idx];
    }

    fn contains(&self, key: &Key) -> bool {
        return self.keys.contains(key);
    }

    fn sort(&mut self) {
        if !self.sorted {
            self.keys.sort();
            self.sorted = true;
        }
    }
}

pub struct VecOptionKeySet {
    keys: Vec<Option<Key>>,
    set: HashSet<Key>,
    sorted: bool,
    none_count: usize,
}

/// The threshold for the percentage of None values to trigger a filter operation.
const VEC_OPTION_KEY_SET_FILTER_THRESHOLD: f64 = 0.01;

// FIXME: this needs to implemented with "generation indexing" / "slotmap"
impl VecOptionKeySet {
    fn maybe_flatten_in_place(&mut self) {
        if (self.none_count as f64 / self.keys.len() as f64) < VEC_OPTION_KEY_SET_FILTER_THRESHOLD {
            return;
        }
        self.keys.retain(Option::is_some);
        self.none_count = 0;
    }

    fn maybe_remove(&mut self, idx: usize) -> Option<Key> {
        let key = self.keys[idx].take()?;
        self.none_count += 1;
        // self.maybe_flatten_in_place();
        return Some(key);
    }

    fn maybe_get(&self, idx: usize) -> Option<&Key> {
        return self.keys.get(idx).and_then(|k| k.as_ref());
    }
}

impl KeySet for VecOptionKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: Vec::with_capacity(capacity),
            set: HashSet::with_capacity(capacity),
            sorted: true,
            none_count: 0,
        };
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    // TODO: maybe this can be improved to binary search and then "fill a hole"
    fn push(&mut self, key: Key) {
        if self.sorted
            && self
                .keys
                .last()
                .is_some_and(|last_key| last_key.as_ref() > Some(&key))
        {
            self.sorted = false;
        }
        self.set.insert(key.clone());
        self.keys.push(Some(key));
    }

    fn remove(&mut self, mut idx: usize) -> Key {
        for _ in 0..self.keys.len() {
            match self.maybe_remove(idx) {
                Some(key) => {
                    self.set.remove(&key);
                    self.maybe_flatten_in_place();
                    return key;
                }
                None => {
                    idx = (idx + 1) % self.keys.len();
                }
            }
        }
        panic!("Called remove on an empty keyset");
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        let mut key1 = None;
        let mut key2 = None;
        for idx in idx_range {
            if let Some(key) = self.maybe_remove(idx) {
                self.set.remove(&key);
                key1 = key1.or(Some(key.clone()));
                key2 = Some(key);
            }
        }

        self.maybe_flatten_in_place();

        return (key1.expect("to not be none"), key2.expect("to not be none"));
    }

    // fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
    //     // FIXME: This is technically incorrect, because the range could contain `None` values.
    //     // Never more than VEC_OPTION_KEY_SET_FILTER_THRESHOLD*100 % of the keys tho, so
    //     // it might be ok.
    //     // We can maybe do better with a while loop, using Option::take, and then call
    //     // maybe_flatten_in_place.
    //     let mut drain = self.keys.drain(idx_range.clone()).flatten();
    //     let key1 = drain.next().expect("to have at least one element");
    //     let (key1, key2) = match drain.next_back() {
    //         Some(key2) => (key1, key2),
    //         None => (key1.clone(), key1),
    //     };
    //
    //     let some_count = drain.count() + 2;
    //     let none_count = idx_range.len() - some_count;
    //     self.none_count += none_count;
    //     self.maybe_flatten_in_place();
    //
    //     return (key1, key2);
    // }

    fn get(&self, mut idx: usize) -> &Key {
        for _ in 0..self.keys.len() {
            match self.maybe_get(idx) {
                Some(key) => {
                    return key;
                }
                None => {
                    idx = (idx + 1) % self.keys.len();
                }
            }
        }
        panic!("Called get on an empty keyset");
    }

    // TODO: this can be binary search if it is sorted
    fn contains(&self, key: &Key) -> bool {
        return self.set.contains(key);
        // return self.keys.iter().any(|k| k.as_ref() == Some(key));
    }

    fn sort(&mut self) {
        if !self.sorted {
            self.maybe_flatten_in_place();
            self.keys.sort();
            self.sorted = true;
        }
    }
}

pub struct VecHashSetKeySet {
    keys: Vec<Key>,
    key_set: HashSet<Key>,
    sorted: bool,
}

impl KeySet for VecHashSetKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: Vec::with_capacity(capacity),
            key_set: HashSet::with_capacity(capacity),
            sorted: true,
        };
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    fn push(&mut self, key: Key) {
        if self.sorted && self.keys.last().is_some_and(|last_key| last_key > &key) {
            self.sorted = false;
        }
        self.keys.push(key.clone());
        self.key_set.insert(key);
    }

    fn remove(&mut self, idx: usize) -> Key {
        let key = self.keys.remove(idx);
        self.key_set.remove(&key);
        return key;
    }
    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        for idx in idx_range.clone() {
            self.key_set.remove(&self.keys[idx]);
        }
        let mut drain = self.keys.drain(idx_range);
        let key1 = drain.next().expect("to have at least one element");
        match drain.next_back() {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        }
    }

    fn get(&self, idx: usize) -> &Key {
        return &self.keys[idx];
    }

    fn contains(&self, key: &Key) -> bool {
        return self.key_set.contains(key);
    }

    fn sort(&mut self) {
        if !self.sorted {
            self.keys.sort();
            self.sorted = true;
        }
    }
}

pub struct VecBloomFilterKeySet {
    keys: Vec<Key>,
    bf: BloomFilter,
    sorted: bool,
}

impl KeySet for VecBloomFilterKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: Vec::with_capacity(capacity),
            bf: BloomFilter::with_rate(0.01, max(1, capacity) as u32),
            sorted: true,
        };
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    fn push(&mut self, key: Key) {
        if self.sorted && self.keys.last().is_some_and(|last_key| last_key > &key) {
            self.sorted = false;
        }
        self.bf.insert(&key);
        self.keys.push(key);
    }

    fn remove(&mut self, idx: usize) -> Key {
        return self.keys.remove(idx);
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        let mut drain = self.keys.drain(idx_range);
        let key1 = drain.next().expect("to have at least one element");
        match drain.next_back() {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        }
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn get(&self, idx: usize) -> &Key {
        return &self.keys[idx];
    }

    fn contains(&self, key: &Key) -> bool {
        return self.bf.contains(key);
    }

    fn sort(&mut self) {
        if !self.sorted {
            self.keys.sort();
            self.sorted = true;
        }
    }
}

pub struct VecHashMapIndexKeySet {
    keys: Vec<Key>,
    key_to_index: HashMap<Key, usize>,
    sorted: bool,
}

// TODO: Is this keyset useless because we always need to sort when doing a point delete?
impl KeySet for VecHashMapIndexKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: Vec::with_capacity(capacity),
            key_to_index: HashMap::with_capacity(capacity),
            sorted: true,
        };
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    fn push(&mut self, key: Key) {
        if !self.key_to_index.contains_key(&key) {
            if self.sorted && self.keys.last().is_some_and(|last_key| last_key > &key) {
                self.sorted = false;
            }
            self.key_to_index.insert(key.clone(), self.keys.len());
            self.keys.push(key);
        }
    }

    fn remove(&mut self, idx: usize) -> Key {
        assert!(idx < self.keys.len());

        // Swap with last, pop, and update hashmap
        let swap_idx = self.keys.len() - 1;
        self.keys.swap(idx, swap_idx);
        let removed = self.keys.pop().unwrap();
        self.key_to_index.remove(&removed);

        // Update index of swapped element if necessary
        if idx < self.keys.len() {
            let swapped_key = &self.keys[idx];
            self.key_to_index.insert(swapped_key.clone(), idx);
        }

        return removed;
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        let mut iter = idx_range.rev();
        let key1 = self.remove(iter.next().expect("to have at least one element"));
        let mut key2 = None;
        for idx in iter {
            key2 = Some(self.remove(idx));
        }
        match key2 {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        }
    }

    fn get(&self, idx: usize) -> &Key {
        return &self.keys[idx];
    }

    fn contains(&self, key: &Key) -> bool {
        return self.key_to_index.contains_key(key);
    }

    fn sort(&mut self) {
        if !self.sorted {
            self.keys.sort();
            self.key_to_index.clear();
            for (i, key) in self.keys.iter().enumerate() {
                self.key_to_index.insert(key.clone(), i);
            }
            self.sorted = true;
        }
    }
}

pub struct BTreeSetKeySet {
    keys: std::collections::BTreeSet<Key>,
}

impl KeySet for BTreeSetKeySet {
    fn new(capacity: usize) -> Self {
        let mut set = Self {
            keys: std::collections::BTreeSet::new(),
        };
        set.keys.extend_reserve(capacity);
        return set;
    }

    fn len(&self) -> usize {
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty();
    }

    fn push(&mut self, key: Key) {
        self.keys.insert(key);
    }

    fn remove(&mut self, idx: usize) -> Key {
        let key = self.keys.iter().nth(idx).unwrap().clone();
        self.keys.remove(&key);
        return key;
        // let mut cursor = self.keys.lower_bound_mut(Bound::Included(&idx));
        // return Some(cursor
        //     .remove_next()
        //     .expect("to be a valid key because idx is in range"));
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        let key1 = self
            .keys
            .iter()
            .nth(idx_range.start)
            .expect("idx to be in range")
            .clone();
        let mut cursor = self.keys.lower_bound_mut(Bound::Included(&key1));
        let count = idx_range.end - idx_range.start;
        let mut key2 = None;
        for _ in 0..count {
            key2 = cursor.remove_next().or(key2);
        }
        match key2 {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        }
    }

    fn get(&self, idx: usize) -> &Key {
        return self.keys.iter().nth(idx).expect("idx to be in range");
    }

    // fn get_random(&self, _rng: &mut impl Rng) -> &Key {
    //     // TODO: check if this is random enough
    //     return self.keys.iter().next().unwrap();
    // }

    fn contains(&self, key: &Key) -> bool {
        return self.keys.contains(key);
    }

    fn sort(&mut self) {
        /* no op -- already sorted */
        // match sort_by {
        //     SortBy::Value => (),
        //     SortBy::InsertOrder => (),
        // }
    }
}
