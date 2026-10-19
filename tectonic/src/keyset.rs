#![allow(clippy::needless_return)]
#![allow(dead_code)]

use rand::Rng;

use crate::spec::KeySelection;
use bloom::{ASMS, BloomFilter};
use std::cmp::max;
use std::collections::{HashMap, HashSet};
use std::ops::{Bound, Range};
use std::sync::Arc;
use sweep_bptree::argument::count::Count;
use sweep_bptree::{BPlusTree, BPlusTreeSet, NodeStoreVec};

// pub type Key = Box<[u8]>;
pub type Key = Arc<[u8]>;
// TODO: Fix lifetime issue by maybe keeping the vec separate from the keysets so the lifetimes are
// tied to it?

pub trait KeySet {
    fn new(capacity: usize) -> Self;

    fn len(&self) -> usize;

    fn is_empty(&self) -> bool;

    fn push(&mut self, key: Key);

    /// Makes pushed keys visible to index based accesses (`len`, `get`, `remove`, ...). Must be
    /// called after pushing keys and before accessing the keyset by index.
    fn flush(&mut self) {}

    fn remove(&mut self, idx: usize) -> Key;

    fn remove_random(&mut self, rng: &mut impl Rng, selection: &KeySelection) -> Key {
        let idx = selection.select(rng, self.len());
        return self.remove(idx);
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key);
    fn remove_range_random(
        &mut self,
        range_len: usize,
        rng: &mut impl Rng,
        selection: &KeySelection,
    ) -> (Key, Key) {
        let start_idx = self.random_range_start(range_len, rng, selection);
        let end_idx = (start_idx + range_len.max(1)).min(self.len());

        return self.remove_range(start_idx..end_idx);
    }

    fn get(&self, idx: usize) -> &Key;

    fn get_random(&self, rng: &mut impl Rng, selection: &KeySelection) -> &Key {
        let idx = selection.select(rng, self.len());
        return self.get(idx);
    }

    /// Selects the index of the first key of a range of `range_len` keys, so that the whole range
    /// fits in the keyset. Scrambled selections hash over all keys instead, like YCSB, so that a
    /// rank always maps to the same start key; ranges near the end then cover fewer keys.
    fn random_range_start(
        &self,
        range_len: usize,
        rng: &mut impl Rng,
        selection: &KeySelection,
    ) -> usize {
        if selection.scramble {
            return selection.select(rng, self.len());
        }
        let valid_len = self.len().saturating_sub(range_len).max(1);
        return selection.select(rng, valid_len);
    }

    fn get_random_range_start(
        &self,
        range_len: usize,
        rng: &mut impl Rng,
        selection: &KeySelection,
    ) -> (usize, &Key) {
        let start_idx = self.random_range_start(range_len, rng, selection);

        return (start_idx, self.get(start_idx));
    }

    fn get_range_random(
        &mut self,
        range_len: usize,
        rng: &mut impl Rng,
        selection: &KeySelection,
    ) -> (&Key, &Key) {
        let start_idx = self.random_range_start(range_len, rng, selection);
        let end_idx = (start_idx + range_len).min(self.len() - 1);

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

pub struct EmptyKeySet {
    len: usize,
}

impl KeySet for EmptyKeySet {
    fn new(_capacity: usize) -> Self {
        Self { len: 0 }
    }

    fn len(&self) -> usize {
        self.len
    }

    fn is_empty(&self) -> bool {
        // NOTE: This needs to be false so that we don't try to do the initial inserts into the
        // keyset
        false
    }

    fn push(&mut self, _key: Key) {
        self.len += 1;
    }

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

pub struct VecOptionHashSetKeySet {
    keys: Vec<Option<Key>>,
    set: HashSet<Key>,
    sorted: bool,
    none_count: usize,
}

/// The threshold for the percentage of None values to trigger a filter operation.
const VEC_OPTION_KEY_SET_FILTER_THRESHOLD: f64 = 0.01;

// FIXME: this needs to implemented with "generation indexing" / "slotmap"
impl VecOptionHashSetKeySet {
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

impl KeySet for VecOptionHashSetKeySet {
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

pub struct VecOptionKeySet {
    keys: Vec<Option<Key>>,
    sorted: bool,
    none_count: usize,
}

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
        self.keys.push(Some(key));
    }

    fn remove(&mut self, mut idx: usize) -> Key {
        for _ in 0..self.keys.len() {
            match self.maybe_remove(idx) {
                Some(key) => {
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
        let start = idx_range.start;
        let end = idx_range.end;

        for idx in start..end {
            if let Some(key) = self.maybe_remove(idx) {
                key1 = key1.or(Some(key.clone()));
                key2 = Some(key);
            }
        }

        if key1.is_none() && !self.keys.is_empty() {
            let mut idx = start % self.keys.len();
            for _ in 0..self.keys.len() {
                if let Some(key) = self.maybe_remove(idx) {
                    key1 = Some(key.clone());
                    key2 = Some(key);
                    break;
                }
                idx = (idx + 1) % self.keys.len();
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
    fn contains(&self, _key: &Key) -> bool {
        panic!("VecOptionKeySet is not meant to support contains operations");
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

pub struct BloomFilterKeySet {
    bf: BloomFilter,
    len: usize,
}

impl KeySet for BloomFilterKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            bf: BloomFilter::with_rate(0.01, max(1, capacity) as u32),
            len: 0,
        };
    }

    fn len(&self) -> usize {
        return self.len;
    }

    fn is_empty(&self) -> bool {
        return self.len == 0;
    }

    fn push(&mut self, key: Key) {
        self.bf.insert(&key);
        self.len += 1
    }

    fn remove(&mut self, _idx: usize) -> Key {
        panic!("BloomFilterKeySet does not support deletion")
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn remove_range(&mut self, _idx_range: Range<usize>) -> (Key, Key) {
        panic!("BloomFilterKeySet does not support deletion")
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn get(&self, _idx: usize) -> &Key {
        panic!("BloomFilterKeySet does not support get")
    }

    fn contains(&self, key: &Key) -> bool {
        return self.bf.contains(key);
    }

    fn sort(&mut self) {
        panic!("BloomFilterKeySet does not support sorting")
    }
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

pub struct BPlusTreeKeySet {
    keys: BPlusTreeSet<Key>,
}

impl KeySet for BPlusTreeKeySet {
    fn new(_capacity: usize) -> Self {
        let keys = BPlusTreeSet::new();
        Self { keys }
    }

    fn len(&self) -> usize {
        self.keys.len()
    }

    fn is_empty(&self) -> bool {
        self.keys.is_empty()
    }

    fn push(&mut self, key: Key) {
        self.keys.insert(key);
    }

    fn remove(&mut self, idx: usize) -> Key {
        let iter = self.keys.iter();
        let mut iter = iter.skip(idx - 1);
        return iter
            .next()
            .cloned()
            .expect("Tried to remove an index not in the keyset");
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        todo!()
    }

    fn get(&self, idx: usize) -> &Key {
        todo!()
    }

    fn contains(&self, key: &Key) -> bool {
        todo!()
    }

    fn sort(
        &mut self,
        // sort_by: SortBy
    ) {
        // NOTE: Nothing to do here because the tree is sorted by default
    }
}

/// Keeps the keys sorted in an order statistic tree, so that index `i` is always the `i`-th
/// smallest key. Key selection distributions are laid over the sorted key domain, so this is the
/// keyset to use whenever keys are selected by index.
///
/// Pushed keys are buffered until the next [`KeySet::flush`], so that loading many keys costs one
/// sort and bulk load instead of a tree insertion per key. Duplicate keys are stored once.
pub struct OrderedKeySet {
    keys: BPlusTree<NodeStoreVec<Key, (), Count>>,
    pending: Vec<Key>,
    /// Index over `pending` so that `contains` stays fast while keys are buffered. Only kept when
    /// the keyset is created with [`OrderedKeySet::with_contains_index`].
    pending_index: Option<HashSet<Key>>,
}

/// Sort key that avoids dereferencing keys for most comparisons.
fn key_prefix(key: &Key) -> u64 {
    let mut prefix = [0u8; 8];
    let len = key.len().min(8);
    prefix[..len].copy_from_slice(&key[..len]);
    return u64::from_be_bytes(prefix);
}

impl OrderedKeySet {
    pub fn with_contains_index(capacity: usize) -> Self {
        let mut keyset = Self::new(capacity);
        keyset.pending_index = Some(HashSet::with_capacity(capacity));
        return keyset;
    }

    fn assert_flushed(&self) {
        assert!(
            self.pending.is_empty(),
            "OrderedKeySet must be flushed before it is accessed by index"
        );
    }
}

impl KeySet for OrderedKeySet {
    fn new(capacity: usize) -> Self {
        return Self {
            keys: BPlusTree::new(NodeStoreVec::default()),
            pending: Vec::with_capacity(capacity),
            pending_index: None,
        };
    }

    fn len(&self) -> usize {
        self.assert_flushed();
        return self.keys.len();
    }

    fn is_empty(&self) -> bool {
        return self.keys.is_empty() && self.pending.is_empty();
    }

    fn push(&mut self, key: Key) {
        if let Some(index) = &mut self.pending_index {
            index.insert(key.clone());
        }
        self.pending.push(key);
    }

    fn flush(&mut self) {
        if self.pending.is_empty() {
            return;
        }
        if let Some(index) = &mut self.pending_index {
            index.clear();
        }
        // Inserting is O(p log n), rebuilding is O(n + p log p).
        if self.pending.len() * 16 < self.keys.len() {
            for key in self.pending.drain(..) {
                self.keys.insert(key, ());
            }
            return;
        }

        let mut pending: Vec<(u64, Key)> = self
            .pending
            .drain(..)
            .map(|key| (key_prefix(&key), key))
            .collect();
        pending.sort_unstable_by(|(pa, a), (pb, b)| pa.cmp(pb).then_with(|| a.cmp(b)));

        // Merge the sorted pending keys into the (sorted) keys of the tree.
        let mut merged: Vec<(Key, ())> = Vec::with_capacity(self.keys.len() + pending.len());
        let mut existing = self.keys.iter().map(|(key, _)| key).peekable();
        for (_, key) in pending {
            while let Some(smaller) = existing.next_if(|&k| *k < key) {
                merged.push((smaller.clone(), ()));
            }
            if existing.peek().is_some_and(|&k| *k == key)
                || merged.last().is_some_and(|(last, _)| *last == key)
            {
                continue;
            }
            merged.push((key, ()));
        }
        merged.extend(existing.map(|key| (key.clone(), ())));
        self.keys = BPlusTree::bulk_load(merged);
    }

    fn remove(&mut self, idx: usize) -> Key {
        self.assert_flushed();
        return self
            .keys
            .remove_by_argument(idx)
            .expect("Tried to remove an index not in the keyset")
            .0;
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {
        let mut key1 = None;
        let mut key2 = None;
        for _ in idx_range.clone() {
            let key = self.remove(idx_range.start);
            key1 = key1.or_else(|| Some(key.clone()));
            key2 = Some(key);
        }
        return (
            key1.expect("to have at least one element"),
            key2.expect("to have at least one element"),
        );
    }

    fn get(&self, idx: usize) -> &Key {
        self.assert_flushed();
        return self
            .keys
            .get_by_argument(idx)
            .expect("Tried to get an index not in the keyset")
            .0;
    }

    fn contains(&self, key: &Key) -> bool {
        let pending = match &self.pending_index {
            Some(index) => index.contains(key),
            None => self.pending.contains(key),
        };
        return pending || self.keys.get(key).is_some();
    }

    fn sort(&mut self) {
        self.flush();
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use rand::SeedableRng;
    use rand_xoshiro::Xoshiro256Plus;

    #[test]
    fn ordered_keyset_index_is_rank_in_sorted_key_order() {
        check_against_reference(OrderedKeySet::new(0));
    }

    #[test]
    fn ordered_keyset_with_contains_index_is_rank_in_sorted_key_order() {
        check_against_reference(OrderedKeySet::with_contains_index(0));
    }

    fn check_against_reference(mut keyset: OrderedKeySet) {
        let mut rng = Xoshiro256Plus::seed_from_u64(42);
        // Reference: sorted, deduplicated vector.
        let mut reference: Vec<Key> = Vec::new();

        for step in 0..50_000 {
            let op = rng.random_range(0..10);
            if reference.is_empty() || op < 5 {
                // Small key space, so duplicates are common.
                let key: Key = format!("{:05}", rng.random_range(0..20_000))
                    .as_bytes()
                    .into();
                keyset.push(key.clone());
                assert!(keyset.contains(&key));
                if let Err(pos) = reference.binary_search(&key) {
                    reference.insert(pos, key);
                }
                // Batch some pushes to exercise both the insert and the rebuild path of `flush`.
                if rng.random_bool(0.9) {
                    keyset.flush();
                }
            } else if op < 7 {
                let idx = rng.random_range(0..reference.len());
                assert_eq!(keyset.remove(idx), reference.remove(idx), "step {step}");
            } else if op < 8 {
                let start = rng.random_range(0..reference.len());
                let end = rng.random_range(start + 1..=reference.len().min(start + 50));
                let removed: Vec<Key> = reference.drain(start..end).collect();
                let (first, last) = keyset.remove_range(start..end);
                assert_eq!((&first, &last), (&removed[0], removed.last().unwrap()));
            } else {
                let idx = rng.random_range(0..reference.len());
                assert_eq!(keyset.get(idx), &reference[idx], "step {step}");
                assert!(keyset.contains(&reference[idx]));
            }
            keyset.flush();
            assert_eq!(keyset.len(), reference.len());
        }
        let missing: Key = b"missing".as_slice().into();
        assert!(!keyset.contains(&missing));
    }

    #[test]
    fn scrambled_range_starts_cover_all_keys() {
        // Like YCSB, a scrambled rank maps to the same start key whatever the scan length, so the
        // start can be any key; ranges starting near the end are shorter.
        let selection: KeySelection =
            serde_json::from_str(r#"{"zipf": {"n": 10000000000, "s": 0.99}, "scramble": true}"#)
                .unwrap();
        let mut keyset = OrderedKeySet::new(0);
        for i in 0..100 {
            keyset.push(format!("{i:03}").as_bytes().into());
        }
        keyset.flush();
        let starts = |range_len: usize| {
            let mut rng = Xoshiro256Plus::seed_from_u64(3);
            (0..2000)
                .map(|_| keyset.random_range_start(range_len, &mut rng, &selection))
                .collect::<Vec<_>>()
        };
        assert_eq!(starts(1), starts(50));
        assert!(starts(50).iter().any(|&s| s > 50));
        let mut rng = Xoshiro256Plus::seed_from_u64(4);
        for _ in 0..2000 {
            let (k1, k2) = keyset.get_range_random(50, &mut rng, &selection);
            assert!(k1 <= k2);
        }
    }

    #[test]
    fn range_selection_stays_within_the_keyset() {
        let mut rng = Xoshiro256Plus::seed_from_u64(7);
        let selection: KeySelection =
            serde_json::from_str(r#"{"zipf": {"n": 100, "s": 1.2}, "offset": 95}"#).unwrap();
        let mut keyset = OrderedKeySet::new(0);
        for i in 0..100 {
            keyset.push(format!("{i:03}").as_bytes().into());
        }
        keyset.flush();
        for range_len in [0, 1, 10, 99, 100, 150] {
            for _ in 0..200 {
                let start = keyset.random_range_start(range_len, &mut rng, &selection);
                assert!(start + range_len <= 100 || start == 0);
                let (k1, k2) = keyset.get_range_random(range_len, &mut rng, &selection);
                assert!(k1 <= k2);
            }
        }
    }
}
