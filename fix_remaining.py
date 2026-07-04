import sys

with open('tectonic/src/keyset.rs', 'r') as f:
    text = f.read()

# 1. VecOptionKeySet
text = text.replace(
'''    fn remove(&mut self, mut idx: usize) -> Key {
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
    }''',
'''    fn remove(&mut self, mut idx: usize) -> Option<Key> {
        for _ in 0..self.keys.len() {
            match self.maybe_remove(idx) {
                Some(key) => {
                    self.maybe_flatten_in_place();
                    return Some(key);
                }
                None => {
                    idx = (idx + 1) % self.keys.len();
                }
            }
        }
        None
    }''')

text = text.replace(
'''    fn get(&self, mut idx: usize) -> &Key {
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
    }''',
'''    fn get(&self, mut idx: usize) -> Option<&Key> {
        for _ in 0..self.keys.len() {
            match self.maybe_get(idx) {
                Some(key) => {
                    return Some(key);
                }
                None => {
                    idx = (idx + 1) % self.keys.len();
                }
            }
        }
        None
    }''')

# 2. BloomFilterKeySet
text = text.replace(
'''    fn remove(&mut self, _idx: usize) -> Key {
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
    }''',
'''    fn remove(&mut self, _idx: usize) -> Option<Key> {
        panic!("BloomFilterKeySet does not support deletion")
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn remove_range(&mut self, _idx_range: Range<usize>) -> Option<(Key, Key)> {
        panic!("BloomFilterKeySet does not support deletion")
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn get(&self, _idx: usize) -> Option<&Key> {
        panic!("BloomFilterKeySet does not support get")
    }''')

# 3. VecBloomFilterKeySet
text = text.replace(
'''    fn remove(&mut self, idx: usize) -> Key {
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
    }''',
'''    fn remove(&mut self, idx: usize) -> Option<Key> {
        if idx >= self.keys.len() { return None; }
        return Some(self.keys.remove(idx));
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }

    fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {
        if idx_range.start >= self.keys.len() || idx_range.start == idx_range.end { return None; }
        let mut drain = self.keys.drain(idx_range);
        let key1 = drain.next()?;
        let (key1, key2) = match drain.next_back() {
            Some(key2) => (key1, key2),
            None => (key1.clone(), key1),
        };
        return Some((key1, key2));
        // NOTE: leaving this out is an optimization for the case when the keyspace is much larger than the number of keys being generated.
        // self.bf.clear();
        // for k in &self.keys {
        //     self.bf.insert(k);
        // }
    }''')

# 4. VecHashMapIndexKeySet
text = text.replace(
'''    fn remove(&mut self, idx: usize) -> Key {
        assert!(idx < self.keys.len());

        // Swap with last, pop, and update hashmap
        let swap_idx = self.keys.len() - 1;
        self.keys.swap(idx, swap_idx);
        let removed = self.keys.pop().unwrap();
        self.key_to_index.remove(&removed);

        // Update index of swapped element if necessary
        if idx < swap_idx {
            let swapped_key = &self.keys[idx];
            self.key_to_index.insert(swapped_key.clone(), idx);
        }

        return removed;
    }''',
'''    fn remove(&mut self, idx: usize) -> Option<Key> {
        if idx >= self.keys.len() { return None; }

        // Swap with last, pop, and update hashmap
        let swap_idx = self.keys.len() - 1;
        self.keys.swap(idx, swap_idx);
        let removed = self.keys.pop().unwrap();
        self.key_to_index.remove(&removed);

        // Update index of swapped element if necessary
        if idx < swap_idx {
            let swapped_key = &self.keys[idx];
            self.key_to_index.insert(swapped_key.clone(), idx);
        }

        return Some(removed);
    }''')

with open('tectonic/src/keyset.rs', 'w') as f:
    f.write(text)
