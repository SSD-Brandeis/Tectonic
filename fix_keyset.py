import re

with open('tectonic/src/keyset.rs', 'r') as f:
    content = f.read()

# 1. Update trait signatures
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key;',
    'fn remove(&mut self, idx: usize) -> Option<Key>;'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key);',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)>;'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key;',
    'fn get(&self, idx: usize) -> Option<&Key>;'
)

# 2. Update default methods in KeySet trait to loop
remove_random_new = """    fn remove_random(&mut self, rng: &mut impl Rng, distribution: &Distribution) -> Key {
        loop {
            let idx = distribution.evaluate_index(rng, self.len());
            if let Some(key) = self.remove(idx) {
                return key;
            }
        }
    }"""
content = re.sub(r'\n    fn remove_random\([^)]+\)\s*->\s*Key\s*\{.*?\n    \}', '\n' + remove_random_new, content, flags=re.DOTALL)

remove_range_random_new = """    fn remove_range_random(
        &mut self,
        range_len: usize,
        rng: &mut impl Rng,
        distribution: &Distribution,
    ) -> (Key, Key) {
        loop {
            let num_keys = self.len();
            let valid_len = num_keys.saturating_sub(range_len);
            let start_idx = distribution.evaluate_index(rng, valid_len);
            let end_idx = start_idx + range_len;

            if let Some(keys) = self.remove_range(start_idx..end_idx) {
                return keys;
            }
        }
    }"""
content = re.sub(r'\n    fn remove_range_random\([^)]+\)\s*->\s*\(Key,\s*Key\)\s*\{.*?\n    \}', '\n' + remove_range_random_new, content, flags=re.DOTALL)

get_random_new = """    fn get_random(&self, rng: &mut impl Rng, distribution: &Distribution) -> &Key {
        loop {
            let idx = distribution.evaluate_index(rng, self.len());
            if let Some(key) = self.get(idx) {
                return key;
            }
        }
    }"""
content = re.sub(r'\n    fn get_random\([^)]+\)\s*->\s*&Key\s*\{.*?\n    \}', '\n' + get_random_new, content, flags=re.DOTALL)

get_random_range_start_new = """    fn get_random_range_start(
        &self,
        range_len: usize,
        rng: &mut impl Rng,
        distribution: &Distribution,
    ) -> (usize, &Key) {
        loop {
            let num_keys = self.len();
            let valid_len = num_keys.saturating_sub(range_len);
            let start_idx = distribution.evaluate_index(rng, valid_len);
            if let Some(key) = self.get(start_idx) {
                return (start_idx, key);
            }
        }
    }"""
content = re.sub(r'\n    fn get_random_range_start\([^)]+\)\s*->\s*\(usize,\s*&Key\)\s*\{.*?\n    \}', '\n' + get_random_range_start_new, content, flags=re.DOTALL)

get_range_random_new = """    fn get_range_random(
        &mut self,
        range_len: usize,
        rng: &mut impl Rng,
        distribution: &Distribution,
    ) -> (&Key, &Key) {
        loop {
            let num_keys = self.len();
            let valid_len = num_keys.saturating_sub(range_len);
            let start_idx = distribution.evaluate_index(rng, valid_len);
            let end_idx = start_idx + range_len;

            let key1 = self.get(start_idx);
            let key2 = self.get(end_idx);
            if let (Some(k1), Some(k2)) = (key1, key2) {
                return (k1, k2);
            }
        }
    }"""
content = re.sub(r'\n    fn get_range_random\([^)]+\)\s*->\s*\(&Key,\s*&Key\)\s*\{.*?\n    \}', '\n' + get_range_random_new, content, flags=re.DOTALL)

# 3. Update all impl KeySet blocks (get, remove, remove_range)
# We will just replace all implementors' return types and wrap their return statements in `Some()`
def repl_get(m):
    body = m.group(1)
    if "Option<&Key>" in body: return m.group(0) # already processed
    return f"fn get(&self, idx: usize) -> Option<&Key> {{\n{body}\n        unreachable!()\n    }}"

def repl_remove(m):
    body = m.group(1)
    if "Option<Key>" in body: return m.group(0) # already processed
    return f"fn remove(&mut self, idx: usize) -> Option<Key> {{\n{body}\n        unreachable!()\n    }}"

def repl_remove_range(m):
    body = m.group(1)
    if "Option<(Key, Key)>" in body: return m.group(0) # already processed
    return f"fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {{\n{body}\n        unreachable!()\n    }}"

# Simple string replacements for implementors
# Instead of regex for method bodies which is brittle, I'll replace known exact strings for all 10 implementors.

# EmptyKeySet
content = content.replace(
    'fn get(&self, _idx: usize) -> &Key {\n        unimplemented!()\n    }',
    'fn get(&self, _idx: usize) -> Option<&Key> {\n        unimplemented!()\n    }'
)
content = content.replace(
    'fn remove(&mut self, _idx: usize) -> Key {\n        unimplemented!()\n    }',
    'fn remove(&mut self, _idx: usize) -> Option<Key> {\n        unimplemented!()\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> (Key, Key) {\n        unimplemented!()\n    }',
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> Option<(Key, Key)> {\n        unimplemented!()\n    }'
)

# VecKeySet
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        return self.keys.remove(idx);\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        if idx < self.keys.len() { return Some(self.keys.remove(idx)); } None\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut drain = self.keys.drain(idx_range.clone());\n        let key1 = drain.next().expect("to have at least one element");\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        return (key1, key2);\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        if idx_range.start >= self.keys.len() || idx_range.start == idx_range.end { return None; }\n        let mut drain = self.keys.drain(idx_range.clone());\n        let key1 = drain.next()?;\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        return Some((key1, key2));\n    }'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return &self.keys[idx];\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.keys.get(idx);\n    }'
)

# VecOptionHashSetKeySet
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return self.vec.maybe_get(idx).expect("to not be none");\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.vec.maybe_get(idx);\n    }'
)
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        let key = self.vec.maybe_remove(idx).expect("to not be none");\n        self.hash_set.remove(&key);\n        return key;\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        let key = self.vec.maybe_remove(idx)?;\n        self.hash_set.remove(&key);\n        return Some(key);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut key1 = None;\n        let mut key2 = None;\n        for idx in idx_range {\n            if let Some(key) = self.vec.maybe_remove(idx) {\n                key1 = key1.or(Some(key.clone()));\n                key2 = Some(key.clone());\n                self.hash_set.remove(&key);\n            }\n        }\n        self.vec.maybe_flatten_in_place();\n\n        return (key1.expect("to not be none"), key2.expect("to not be none"));\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        let mut key1 = None;\n        let mut key2 = None;\n        for idx in idx_range {\n            if let Some(key) = self.vec.maybe_remove(idx) {\n                key1 = key1.or(Some(key.clone()));\n                key2 = Some(key.clone());\n                self.hash_set.remove(&key);\n            }\n        }\n        self.vec.maybe_flatten_in_place();\n        if key1.is_none() { return None; }\n        return Some((key1.unwrap(), key2.unwrap()));\n    }'
)

# VecOptionKeySet
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return self.maybe_get(idx).expect("to not be none");\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.maybe_get(idx);\n    }'
)
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        return self.maybe_remove(idx).expect("to not be none");\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        return self.maybe_remove(idx);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut key1 = None;\n        let mut key2 = None;\n        for idx in idx_range {\n            if let Some(key) = self.maybe_remove(idx) {\n                key1 = key1.or(Some(key.clone()));\n                key2 = Some(key);\n            }\n        }\n\n        self.maybe_flatten_in_place();\n\n        return (key1.expect("to not be none"), key2.expect("to not be none"));\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        let mut key1 = None;\n        let mut key2 = None;\n        for idx in idx_range {\n            if let Some(key) = self.maybe_remove(idx) {\n                key1 = key1.or(Some(key.clone()));\n                key2 = Some(key);\n            }\n        }\n        self.maybe_flatten_in_place();\n        if key1.is_none() { return None; }\n        return Some((key1.unwrap(), key2.unwrap()));\n    }'
)

# VecHashSetKeySet
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        let key = self.vec.remove(idx);\n        self.hash_set.remove(&key);\n        return key;\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        if idx >= self.vec.len() { return None; }\n        let key = self.vec.remove(idx);\n        self.hash_set.remove(&key);\n        return Some(key);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next().expect("to have at least one element");\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        self.hash_set.remove(&key1);\n        self.hash_set.remove(&key2);\n        return (key1, key2);\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        if idx_range.start >= self.vec.len() || idx_range.start == idx_range.end { return None; }\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next()?;\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        self.hash_set.remove(&key1);\n        self.hash_set.remove(&key2);\n        return Some((key1, key2));\n    }'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return &self.vec[idx];\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.vec.get(idx);\n    }'
)

# BloomFilterKeySet
content = content.replace(
    'fn remove(&mut self, _idx: usize) -> Key {\n        unimplemented!()\n    }',
    'fn remove(&mut self, _idx: usize) -> Option<Key> {\n        unimplemented!()\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> (Key, Key) {\n        unimplemented!()\n    }',
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> Option<(Key, Key)> {\n        unimplemented!()\n    }'
)
content = content.replace(
    'fn get(&self, _idx: usize) -> &Key {\n        unimplemented!()\n    }',
    'fn get(&self, _idx: usize) -> Option<&Key> {\n        unimplemented!()\n    }'
)

# VecBloomFilterKeySet
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        let key = self.vec.remove(idx);\n        // Note: Bloom filter cannot remove elements\n        return key;\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        if idx >= self.vec.len() { return None; }\n        let key = self.vec.remove(idx);\n        return Some(key);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next().expect("to have at least one element");\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        // Note: Bloom filter cannot remove elements\n        return (key1, key2);\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        if idx_range.start >= self.vec.len() || idx_range.start == idx_range.end { return None; }\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next()?;\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        return Some((key1, key2));\n    }'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return &self.vec[idx];\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.vec.get(idx);\n    }'
)

# VecHashMapIndexKeySet
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        let key = self.vec.remove(idx);\n        self.hash_map.remove(&key);\n        return key;\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        if idx >= self.vec.len() { return None; }\n        let key = self.vec.remove(idx);\n        self.hash_map.remove(&key);\n        return Some(key);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next().expect("to have at least one element");\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        self.hash_map.remove(&key1);\n        self.hash_map.remove(&key2);\n        return (key1, key2);\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        if idx_range.start >= self.vec.len() || idx_range.start == idx_range.end { return None; }\n        let mut drain = self.vec.drain(idx_range.clone());\n        let key1 = drain.next()?;\n        let (key1, key2) = match drain.next_back() {\n            Some(key2) => (key1, key2),\n            None => (key1.clone(), key1),\n        };\n        self.hash_map.remove(&key1);\n        self.hash_map.remove(&key2);\n        return Some((key1, key2));\n    }'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return &self.vec[idx];\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.vec.get(idx);\n    }'
)

# BTreeSetKeySet
content = content.replace(
    'fn remove(&mut self, _idx: usize) -> Key {\n        unimplemented!("BTreeSet does not support removing by index");\n    }',
    'fn remove(&mut self, _idx: usize) -> Option<Key> {\n        unimplemented!("BTreeSet does not support removing by index");\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> (Key, Key) {\n        unimplemented!("BTreeSet does not support removing by index");\n    }',
    'fn remove_range(&mut self, _idx_range: Range<usize>) -> Option<(Key, Key)> {\n        unimplemented!("BTreeSet does not support removing by index");\n    }'
)
content = content.replace(
    'fn get(&self, _idx: usize) -> &Key {\n        unimplemented!("BTreeSet does not support getting by index");\n    }',
    'fn get(&self, _idx: usize) -> Option<&Key> {\n        unimplemented!("BTreeSet does not support getting by index");\n    }'
)

# BPlusTreeKeySet
content = content.replace(
    'fn remove(&mut self, idx: usize) -> Key {\n        return self.tree.remove_index(idx).unwrap();\n    }',
    'fn remove(&mut self, idx: usize) -> Option<Key> {\n        return self.tree.remove_index(idx);\n    }'
)
content = content.replace(
    'fn remove_range(&mut self, idx_range: Range<usize>) -> (Key, Key) {\n        let key1 = self.tree.remove_index(idx_range.start).unwrap();\n        for _ in 0..idx_range.len() - 2 {\n            self.tree.remove_index(idx_range.start);\n        }\n        let key2 = self.tree.remove_index(idx_range.start).unwrap_or(key1.clone());\n        return (key1, key2);\n    }',
    'fn remove_range(&mut self, idx_range: Range<usize>) -> Option<(Key, Key)> {\n        if idx_range.start >= self.tree.len() || idx_range.start == idx_range.end { return None; }\n        let key1 = self.tree.remove_index(idx_range.start)?;\n        for _ in 0..idx_range.len() - 2 {\n            self.tree.remove_index(idx_range.start);\n        }\n        let key2 = self.tree.remove_index(idx_range.start).unwrap_or(key1.clone());\n        return Some((key1, key2));\n    }'
)
content = content.replace(
    'fn get(&self, idx: usize) -> &Key {\n        return self.tree.get_index(idx).unwrap();\n    }',
    'fn get(&self, idx: usize) -> Option<&Key> {\n        return self.tree.get_index(idx);\n    }'
)

with open('tectonic/src/keyset.rs', 'w') as f:
    f.write(content)


