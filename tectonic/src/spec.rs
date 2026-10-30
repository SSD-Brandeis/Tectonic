#![allow(clippy::needless_return)]

use anyhow::{Context, Result};
use rand::{Rng, SeedableRng};

use crate::keyset::Key;
use rand::distr::weighted::WeightedIndex;
use rand::distr::{Alphabetic, Alphanumeric};
use rand_distr::Distribution as _;
use rand_xoshiro::Xoshiro256Plus;
use schemars::JsonSchema;
use schemars::Schema;
use schemars::SchemaGenerator;
use statrs::function::gamma::gamma;
use statrs::function::harmonic::gen_harmonic;
use std::borrow::Cow;
use std::io::Write;
use std::sync::atomic::{AtomicU64, Ordering};
use std::collections::HashMap;
use std::sync::{Arc, Mutex, OnceLock};

struct Numeric;
impl rand::distr::Distribution<u8> for Numeric {
    fn sample<R: Rng + ?Sized>(&self, rng: &mut R) -> u8 {
        const RANGE: u8 = 10;
        rng.random_range(0..RANGE) + b'0'
    }
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
#[serde(rename_all = "snake_case")]
enum DistributionConfig {
    Uniform { min: f64, max: f64 },
    Normal { mean: f64, std_dev: f64 },
    Beta { alpha: f64, beta: f64 },
    Zipf { n: f64, s: f64 },
    Latest { n: f64, s: f64 },
    Exponential { lambda: f64 },
    LogNormal { mean: f64, std_dev: f64 },
    Poisson { lambda: f64 },
    Weibull { scale: f64, shape: f64 },
    Pareto { scale: f64, shape: f64 },
    /// `location + scale * ((1 - u)^-shape - 1) / shape` for `u` uniform in `[0, 1)` (shape 0:
    /// `location - scale * ln(1 - u)`), e.g. the value sizes and scan lengths of db_bench's mixgraph;
    /// with `max`, conditioned on being below `max` (truncated)
    GeneralizedPareto { location: f64, scale: f64, shape: f64, #[serde(default)] max: Option<f64> },
}

#[derive(serde::Deserialize, Clone, Debug)]
#[serde(try_from = "DistributionConfig")]
/// Different types of distributions that can be used to sample values.
pub enum Distribution {
    /// Uniform distribution over the range [min, max).
    Uniform {
        min: f64,
        max: f64,
        distr: rand_distr::Uniform<f64>,
    },
    /// Normal distribution with the given mean and standard deviation.
    Normal {
        mean: f64,
        std_dev: f64,
        distr: rand_distr::Normal<f64>,
    },
    /// Exponential distribution with the given lambda parameter.
    Exponential {
        lambda: f64,
        distr: rand_distr::Exp<f64>,
    },
    /// Beta distribution with the given alpha and beta parameters.
    Beta {
        alpha: f64,
        beta: f64,
        distr: rand_distr::Beta<f64>,
    },
    /// Zipf distribution with the given n and s parameters.
    Zipf {
        n: f64,
        s: f64,
        distr: rand_distr::Zipf<f64>,
    },
    /// Inverse Zipf distribution with the given n and s parameters. Tends to pick most recently
    /// generated values
    Latest {
        n: f64,
        s: f64,
        distr: rand_distr::Zipf<f64>,
    },
    LogNormal {
        mean: f64,
        std_dev: f64,
        distr: rand_distr::LogNormal<f64>,
    },
    Poisson {
        lambda: f64,
        distr: rand_distr::Poisson<f64>,
    },
    Weibull {
        scale: f64,
        shape: f64,
        distr: rand_distr::Weibull<f64>,
    },
    Pareto {
        scale: f64,
        shape: f64,
        distr: rand_distr::Pareto<f64>,
    },
    GeneralizedPareto {
        location: f64,
        scale: f64,
        shape: f64,
        /// The probability below `max` (1 without `max`): `u` is drawn uniform in `[0, top)`.
        top: f64,
    },
}

/// The CDF of the generalized Pareto distribution.
fn generalized_pareto_cdf(location: f64, scale: f64, shape: f64, x: f64) -> f64 {
    let z = ((x - location) / scale).max(0.0);
    if shape == 0.0 { 1.0 - (-z).exp() } else { 1.0 - (1.0 + shape * z).powf(-1.0 / shape) }
}

/// The `q`-quantile of the generalized Pareto distribution.
fn generalized_pareto_quantile(location: f64, scale: f64, shape: f64, q: f64) -> f64 {
    if shape == 0.0 {
        location - scale * (1.0 - q).ln()
    } else {
        location + scale * ((1.0 - q).powf(-shape) - 1.0) / shape
    }
}

impl Scalable for Distribution {
    fn scale(&mut self, factor: f64) {
        match self {
            Distribution::Uniform { min, max, distr } => {
                *min *= factor;
                *max *= factor;
                *distr = rand_distr::Uniform::new(*min, *max)
                    .expect("Failed to scale uniform distribution")
            }
            Distribution::Zipf { n, s, distr } => {
                *n *= factor;
                *distr = rand_distr::Zipf::new(*n, *s).expect("Failed to scale Zipf Distribution");
            }
            Distribution::Latest { n, s, distr } => {
                *n *= factor;
                *distr =
                    rand_distr::Zipf::new(*n, *s).expect("Failed to scale latest Distribution");
            }
            Distribution::Weibull {
                scale,
                shape,
                distr,
            } => {
                *scale *= factor;
                *distr = rand_distr::Weibull::new(*scale, *shape)
                    .expect("Failed to scale Weibull Distribution");
            }
            Distribution::Pareto {
                scale,
                shape,
                distr,
            } => {
                *scale *= factor;
                *distr = rand_distr::Pareto::new(*scale, *shape)
                    .expect("Failed to scale pareto Distribution");
            }
            Distribution::GeneralizedPareto { location, scale, .. } => {
                *location *= factor;
                *scale *= factor;
            }
            _ => (),
        }
    }
}

impl TryFrom<DistributionConfig> for Distribution {
    type Error = anyhow::Error;

    fn try_from(value: DistributionConfig) -> Result<Self, Self::Error> {
        use DistributionConfig as DC;
        let distr = match value {
            DC::Uniform { min, max } => Self::Uniform {
                min,
                max,
                distr: rand_distr::Uniform::new(min, max)?,
            },
            DC::Normal { mean, std_dev } => Self::Normal {
                mean,
                std_dev,
                distr: rand_distr::Normal::new(mean, std_dev)?,
            },
            DC::Exponential { lambda } => Self::Exponential {
                lambda,
                distr: rand_distr::Exp::new(lambda)?,
            },
            DC::Beta { alpha, beta } => Self::Beta {
                alpha,
                beta,
                distr: rand_distr::Beta::new(alpha, beta)?,
            },
            DC::Zipf { n, s } => Self::Zipf {
                n,
                s,
                distr: rand_distr::Zipf::new(n, s)?,
            },
            DC::Latest { n, s } => Self::Latest {
                n,
                s,
                distr: rand_distr::Zipf::new(n, s)?,
            },
            DC::LogNormal {
                mean: mu,
                std_dev: sigma,
            } => Self::LogNormal {
                mean: mu,
                std_dev: sigma,
                distr: rand_distr::LogNormal::new(mu, sigma)?,
            },
            DC::Poisson { lambda } => Self::Poisson {
                lambda,
                distr: rand_distr::Poisson::new(lambda)?,
            },
            DC::Weibull { scale, shape } => {
                assert!(shape > 0.0);
                Self::Weibull {
                    scale,
                    shape,
                    distr: rand_distr::Weibull::new(scale, shape)?,
                }
            }
            DC::Pareto { scale, shape } => {
                assert!(shape > 1.0);
                Self::Pareto {
                    scale,
                    shape,
                    distr: rand_distr::Pareto::new(scale, shape)?,
                }
            }
            DC::GeneralizedPareto { location, scale, shape, max } => {
                anyhow::ensure!(scale > 0.0, "generalized_pareto: scale must be positive");
                let top = max.map_or(1.0, |max| generalized_pareto_cdf(location, scale, shape, max));
                anyhow::ensure!(top > 0.0, "generalized_pareto: max must be above location");
                Self::GeneralizedPareto { location, scale, shape, top }
            }
        };
        return Ok(distr);
    }
}

impl JsonSchema for Distribution {
    fn schema_name() -> Cow<'static, str> {
        "Distribution".into()
    }

    fn json_schema(generator: &mut SchemaGenerator) -> Schema {
        return DistributionConfig::json_schema(generator);
    }
}

// Modified from https://github.com/servo/rust-fnv/blob/main/lib.rs#L146-L157 (MIT)
const INITIAL_STATE: u64 = 0xcbf2_9ce4_8422_2325;
const PRIME: u64 = 0x0100_0000_01b3;
#[inline]
#[must_use]
/// YCSB's `Utils.fnvhash64`: FNV-1 over the 8 bytes of `bytes`, as a non-negative `long`.
pub const fn fnv_hash(mut bytes: u64) -> u64 {
    let mut hash = INITIAL_STATE;
    let mut i = 0;
    while i < 8 {
        hash ^= bytes & 0xFF;
        hash = hash.wrapping_mul(PRIME);
        bytes >>= 8;
        i += 1;
    }
    (hash as i64).unsigned_abs()
}

// TODO: How does this hold up when there are interleaved inserts? Are the same keys targed or does
// it get "spread out".
/// Spreads out key indicies more evenly throughout the keyspace through hashing
#[inline]
#[must_use]
fn unbiased_index(idx: usize, len: usize) -> usize {
    // return unbiased_index_(idx + 1, len + 1) - 1;
    if len == 0 {
        return 0;
    }
    return (fnv_hash(idx as u64) as usize) % len;
}

impl Distribution {
    /// Maps a sample to the index of a key in `[0, len)`. Uniform samples are fractions of the
    /// keys; every other distribution's sample is scattered over the keys with FNV hashing, so
    /// selection does not depend on the order of the keys. With `zipf` and a large `n` (e.g. 10^10)
    /// this is YCSB's scrambled zipfian distribution.
    pub fn evaluate_index(&self, rng: &mut impl Rng, len: usize) -> usize {
        match self {
            Self::Uniform { min, max, distr } => {
                let x = (distr.sample(rng) - min) / (max - min);
                (x.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize
            }
            Self::Normal {
                mean,
                std_dev,
                distr,
            } => {
                let x = distr.sample(rng);
                let low = mean - 3.0 * std_dev;
                let high = mean + 3.0 * std_dev;
                let normalized = (x - low) / (high - low);
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Exponential { lambda, distr } => {
                let x = distr.sample(rng);
                let normalized = x / (5.0 / lambda);
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Beta { distr, .. } => {
                let x = distr.sample(rng);
                unbiased_index(
                    (x.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Zipf { distr, .. } => {
                let raw_rank = distr.sample(rng) - 1.0;
                unbiased_index(raw_rank as usize, len)
            }
            Self::Latest { distr, n, .. } => {
                let x = (n - distr.sample(rng)) / *n;
                unbiased_index(
                    (x.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::LogNormal {
                mean,
                std_dev,
                distr,
            } => {
                let x = distr.sample(rng);
                let high = (mean + 3.0 * std_dev).exp();
                let normalized = x / high;
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Poisson { lambda, distr } => {
                let x = distr.sample(rng);
                let normalized = x / (3.0 * lambda);
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Weibull {
                scale,
                shape,
                distr,
            } => {
                let x = distr.sample(rng);
                let high = scale * (-0.01f64.ln()).powf(1.0 / shape);
                let normalized = x / high;
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::Pareto {
                scale,
                shape,
                distr,
            } => {
                let x = distr.sample(rng);
                let high = scale * 100f64.powf(1.0 / shape);
                let normalized = (x - scale) / (high - scale);
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
            Self::GeneralizedPareto { location, scale, shape, top } => {
                let x = generalized_pareto_quantile(*location, *scale, *shape, top * rng.random::<f64>());
                let high = generalized_pareto_quantile(*location, *scale, *shape, 0.99);
                let normalized = (x - location) / (high - location);
                unbiased_index(
                    (normalized.clamp(0.0, 1.0 - f64::EPSILON) * len as f64) as usize,
                    len,
                )
            }
        }
    }
}

impl Distribution {
    pub fn evaluate(&self, rng: &mut impl Rng) -> f64 {
        return match self {
            Self::Uniform { distr, .. } => distr.sample(rng),
            Self::Normal { distr, .. } => distr.sample(rng),
            Self::Exponential { distr, .. } => distr.sample(rng),
            Self::Beta { distr, .. } => distr.sample(rng),
            Self::Zipf { distr, .. } => distr.sample(rng),
            Self::Latest { distr, n, .. } => *n - distr.sample(rng),
            Self::LogNormal { distr, .. } => distr.sample(rng),
            Self::Poisson { distr, .. } => distr.sample(rng),
            Self::Weibull { distr, .. } => distr.sample(rng),
            Self::Pareto { distr, .. } => distr.sample(rng),
            Self::GeneralizedPareto { location, scale, shape, top } => {
                generalized_pareto_quantile(*location, *scale, *shape, top * rng.random::<f64>())
            }
        };
    }

    pub fn expected_value(&self) -> f64 {
        return match self {
            Self::Uniform { min, max, .. } => min + max / 2.0,
            Self::Normal { mean, .. } => *mean,
            Self::Exponential { lambda, .. } => 1.0 / lambda,
            Self::Beta { alpha, beta, .. } => alpha / (alpha + beta),
            Self::Zipf { s, n, .. } => {
                let hs = gen_harmonic(*n as u64, *s);
                let hs_minus1 = gen_harmonic(*n as u64, *s - 1.0);
                return hs_minus1 / hs;
            }
            Self::Latest { n, s, .. } => {
                let hs = gen_harmonic(*n as u64, *s);
                let hs_minus1 = gen_harmonic(*n as u64, *s - 1.0);
                return *n - (hs_minus1 / hs);
            }
            Self::LogNormal {
                mean: mu,
                std_dev: sigma,
                ..
            } => (mu + 0.5 * sigma.powi(2)).exp(),

            Self::Poisson { lambda, .. } => *lambda,

            Self::Weibull { scale, shape, .. } => *scale * gamma(1.0 + 1.0 / *shape),
            Self::Pareto { scale, shape, .. } => (shape * scale) / (shape - 1.0),
            Self::GeneralizedPareto { location, scale, shape, top } if *top < 1.0 => {
                // truncated: the mean of the quantile function over [0, top), midpoint rule
                let n = 10_000;
                (0..n).map(|i| generalized_pareto_quantile(*location, *scale, *shape, top * (i as f64 + 0.5) / n as f64)).sum::<f64>() / n as f64
            }
            Self::GeneralizedPareto { location, scale, shape, .. } => {
                if *shape < 1.0 { location + scale / (1.0 - shape) } else { f64::INFINITY }
            }
        };
    }

    pub fn default_key_selection() -> Self {
        let min = 0.;
        let max = 1.;
        return Self::Uniform {
            min,
            max,
            distr: rand_distr::Uniform::new(min, max)
                .expect("to be able to construct a uniform distribution"),
        };
    }
}

impl Default for Distribution {
    fn default() -> Self {
        Self::default_key_selection()
    }
}

/// Selects existing keys with a distribution (see [`Distribution::evaluate_index`]).
///
/// ```json
/// { "zipf": { "n": 10000000000, "s": 0.99 }, "scramble": true }
/// ```
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct KeySelection {
    #[serde(flatten)]
    pub distribution: Distribution,
    /// Makes range starts, like the keys of other operations, hash over all keys as in YCSB, so
    /// that a rank always maps to the same start key; otherwise range starts are selected among
    /// the keys from which the whole range fits. Also keeps a `zipf`'s `n` when the spec is
    /// scaled, like YCSB's scrambled zipfian, which always draws from 10^10 items.
    #[serde(default)]
    pub scramble: bool,
}

impl KeySelection {
    /// Returns the index in `[0, len)` of the selected key.
    pub fn select<R: Rng>(&self, rng: &mut R, len: usize) -> usize {
        return self.distribution.evaluate_index(rng, len);
    }
}

impl Default for KeySelection {
    fn default() -> Self {
        Self {
            distribution: Distribution::default_key_selection(),
            scramble: false,
        }
    }
}

impl Scalable for KeySelection {
    /// A `zipf` or `latest` selection's `n` is scaled with the number of keys, unless the
    /// selection is scrambled: YCSB's scrambled zipfian always draws from 10^10 items regardless of
    /// the number of records.
    fn scale(&mut self, factor: f64) {
        if self.scramble {
            return;
        }
        if let Distribution::Zipf { n, s, distr } | Distribution::Latest { n, s, distr } =
            &mut self.distribution
        {
            *n = (*n * factor).max(1.0);
            *distr = rand_distr::Zipf::new(*n, *s).expect("Failed to scale Zipf Distribution");
        }
    }
}

// No docstring
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
#[serde(untagged)]
pub enum NumberExpr {
    Constant(f64),
    Sampled(Distribution),
    Weighted(WeightedNumbers),
    Scattered(ScatteredNumber),
    Modulo(ModuloNumber),
    Ranges(RangesNumber),
    Pool(PoolNumber),
}

/// One of the values, picked with probability proportional to its weight, e.g. an empirical
/// histogram: `{ "weighted": [{ "weight": 3, "value": 7 }, { "weight": 1, "value": 27 }] }`. A value can
/// itself be any number expression, e.g. `{ "uniform": { "min": 8, "max": 16 } }` for a histogram bucket.
/// With `pick_seed`, the values are picked with their own random generator, like a `pool`'s: weighted
/// numbers with the same weights and `pick_seed` pick the same entries in the same order, e.g. the class
/// of an operation's key and of its scan length.
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct WeightedNumbers {
    pub weighted: Vec<WeightedNumber>,
    #[serde(default)]
    pub pick_seed: Option<u64>,
    #[serde(skip)]
    distr: OnceLock<WeightedIndex<f64>>,
    #[serde(skip)]
    picker: Arc<OnceLock<Mutex<Xoshiro256Plus>>>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct WeightedNumber {
    /// The weight of the value.
    pub weight: f64,
    /// The value.
    pub value: Box<NumberExpr>,
}

impl WeightedNumbers {
    fn sample(&self, rng: &mut impl Rng) -> f64 {
        let distr = self.distr.get_or_init(|| {
            WeightedIndex::new(self.weighted.iter().map(|w| w.weight)).expect("weights of a weighted number")
        });
        let i = match self.pick_seed {
            Some(seed) => {
                let picker = self.picker.get_or_init(|| Mutex::new(Xoshiro256Plus::seed_from_u64(seed)));
                distr.sample(&mut *picker.lock().expect("weighted picker"))
            }
            None => distr.sample(rng),
        };
        self.weighted[i].value.evaluate(rng)
    }

    fn mean(&self) -> f64 {
        let total: f64 = self.weighted.iter().map(|w| w.weight).sum();
        self.weighted.iter().map(|w| w.weight * w.value.expected_value()).sum::<f64>() / total
    }
}

/// `start` plus a fixed one-to-one shuffle of the number (rounded down, modulo `len`), e.g. so that the
/// hot ranks of a skewed distribution land at scattered keys of the key range `[start, start + len)`
/// while every rank keeps its frequency:
/// `{ "scatter": { "start": 1000, "len": 1000, "value": { "zipf": { "n": 1000, "s": 0.9 } } } }`
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct ScatteredNumber {
    pub scatter: Scatter,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Scatter {
    #[serde(default)]
    pub start: u64,
    pub len: u64,
    pub value: Box<NumberExpr>,
}

impl Scatter {
    /// `x mod len` -> `start + (x * a) mod len`, `a` the integer nearest `len` / golden ratio that is
    /// coprime with `len`: one-to-one on `[0, len)`, and consecutive numbers land far apart.
    fn apply(&self, x: f64) -> f64 {
        let len = self.len.max(1);
        let mut a = ((len as f64) * 0.618_033_988_749_894_9).round() as u64;
        while a > 1 && gcd(a, len) != 1 {
            a -= 1;
        }
        let x = (x.floor() as u64) % len;
        (self.start + ((x as u128 * a.max(1) as u128) % len as u128) as u64) as f64
    }
}

/// The number rounded down, modulo `len`, e.g. a scan length capped like db_bench's
/// `mix_max_scan_len`: `{ "modulo": { "len": 10000, "value": { "pareto": { "scale": 1, "shape": 1.5 } } } }`
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct ModuloNumber {
    pub modulo: Modulo,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Modulo {
    pub len: u64,
    pub value: Box<NumberExpr>,
}

/// A key space cut into consecutive ranges of `len` numbers: range `i`, picked with probability
/// proportional to `weights[i]`, plus the number (rounded down, modulo `len`) as the offset in it, e.g.
/// key ranges of different hotness that share how keys are picked inside a range:
/// Weights for `ranges`: either an explicit list of weights, or a distribution specification
/// like `two_term_exponential` (Cao et al. FAST '20 prefix distribution).
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
#[serde(untagged)]
pub enum RangeWeights {
    Explicit(Vec<f64>),
    Distribution(Box<RangeDistribution>),
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub enum RangeDistribution {
    #[serde(rename = "two_term_exponential")]
    TwoTermExponential {
        a: f64,
        b: f64,
        c: f64,
        d: f64,
        #[serde(default = "default_n_30")]
        n: usize,
        #[serde(default)]
        exclude: Vec<usize>,
    },
}

fn default_n_30() -> usize {
    30
}

#[derive(Clone, Debug)]
struct Mt19937_64 {
    mt: [u64; 312],
    mti: usize,
}

impl Mt19937_64 {
    const NN: usize = 312;
    const MM: usize = 156;
    const MATRIX_A: u64 = 0xB5026F5AA96619E9;
    const UM: u64 = 0xFFFFFFFF80000000;
    const LM: u64 = 0x7FFFFFFF;

    fn new(seed: u64) -> Self {
        let mut mt = [0u64; Self::NN];
        mt[0] = seed;
        for i in 1..Self::NN {
            mt[i] = 6364136223846793005u64
                .wrapping_mul(mt[i - 1] ^ (mt[i - 1] >> 62))
                .wrapping_add(i as u64);
        }
        Self { mt, mti: Self::NN }
    }

    fn next_u64(&mut self) -> u64 {
        const MAG01: [u64; 2] = [0, Mt19937_64::MATRIX_A];
        if self.mti >= Self::NN {
            for i in 0..(Self::NN - Self::MM) {
                let x = (self.mt[i] & Self::UM) | (self.mt[i + 1] & Self::LM);
                self.mt[i] = self.mt[i + Self::MM] ^ (x >> 1) ^ MAG01[(x & 1) as usize];
            }
            for i in (Self::NN - Self::MM)..(Self::NN - 1) {
                let x = (self.mt[i] & Self::UM) | (self.mt[i + 1] & Self::LM);
                self.mt[i] = self.mt[i + Self::MM - Self::NN] ^ (x >> 1) ^ MAG01[(x & 1) as usize];
            }
            let x = (self.mt[Self::NN - 1] & Self::UM) | (self.mt[0] & Self::LM);
            self.mt[Self::NN - 1] = self.mt[Self::MM - 1] ^ (x >> 1) ^ MAG01[(x & 1) as usize];
            self.mti = 0;
        }

        let mut x = self.mt[self.mti];
        self.mti += 1;
        x ^= (x >> 29) & 0x5555555555555555;
        x ^= (x << 17) & 0x71D67FFFEDA60000;
        x ^= (x << 37) & 0xFFF7EEE000000000;
        x ^= x >> 43;
        x
    }
}

impl RangeWeights {
    pub fn to_vec(&self) -> Vec<f64> {
        match self {
            RangeWeights::Explicit(w) => w.clone(),
            RangeWeights::Distribution(d) => match **d {
                RangeDistribution::TwoTermExponential { a, b, c, d: d_param, n, ref exclude } => {
                    let mut amplify = 0i64;
                    let mut keyrange_access = Vec::with_capacity(n);
                    let mut total_access = 0i64;
                    for pfx in (1..=n).rev() {
                        let pfx = pfx as f64;
                        let mut p = a * (b * pfx).exp() + c * (d_param * pfx).exp();
                        if p < 1e-16 {
                            p = 0.0;
                        }
                        if amplify == 0 && p > 0.0 {
                            amplify = (1.0 / p).floor() as i64 + 1;
                        }
                        let access = if p <= 0.0 { 0 } else { (amplify as f64 * p).floor() as i64 };
                        keyrange_access.push(access);
                        total_access += access;
                    }
                    let mut mt = Mt19937_64::new(total_access as u64);
                    for i in 0..n {
                        let pos = (mt.next_u64() % n as u64) as usize;
                        keyrange_access.swap(i, pos);
                    }
                    for &ex in exclude {
                        if ex < n {
                            keyrange_access[ex] = 0;
                        }
                    }
                    let sum: f64 = keyrange_access.iter().map(|&x| x as f64).sum();
                    if sum <= 0.0 {
                        vec![1.0 / n as f64; n]
                    } else {
                        keyrange_access.iter().map(|&x| x as f64 / sum).collect()
                    }
                }
            },
        }
    }
}

/// `{ "ranges": { "len": 1000, "weights": [1, 8, 1], "value": { "zipf": { "n": 1000, "s": 0.9 } } } }`
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct RangesNumber {
    pub ranges: Ranges,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Ranges {
    pub len: u64,
    pub weights: RangeWeights,
    pub value: Box<NumberExpr>,
    #[serde(skip)]
    distr: OnceLock<WeightedIndex<f64>>,
    #[serde(skip)]
    resolved_weights: OnceLock<Vec<f64>>,
}

impl Ranges {
    fn weights_slice(&self) -> &[f64] {
        self.resolved_weights.get_or_init(|| self.weights.to_vec())
    }

    fn sample(&self, rng: &mut impl Rng) -> f64 {
        let distr = self
            .distr
            .get_or_init(|| WeightedIndex::new(self.weights_slice()).expect("weights of ranges"));
        let len = self.len.max(1);
        let range = distr.sample(rng) as u64;
        (range * len + (self.value.evaluate(rng).floor() as u64) % len) as f64
    }

    fn mean(&self) -> f64 {
        let weights = self.weights_slice();
        let total: f64 = weights.iter().sum();
        let start: f64 = weights.iter().enumerate().map(|(i, w)| w * (i as u64 * self.len) as f64).sum();
        start / total + self.value.expected_value().min((self.len as f64 - 1.0).max(0.0))
    }
}

/// `size` numbers drawn once from `value`; every evaluation returns one of them, uniformly at random:
/// a fixed population of items that is accessed over and over, e.g. db_bench's mixgraph, which draws
/// every query from `num` precomputed slots. Pools with the same `seed` (and the same `value`) draw the
/// same numbers, a smaller one the first numbers of a larger one; without a seed every pool draws its own.
/// With `pick_seed`, the pool picks its numbers with its own random generator: pools of the same size and
/// `pick_seed` pick the same positions in the same order, so that, e.g., the n-th key and the n-th scan
/// length of an operation's pools come from the same row (generate such operations with one thread).
/// `{ "pool": { "size": 1000, "seed": 7, "value": { "zipf": { "n": 1000000, "s": 0.9 } } } }`
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct PoolNumber {
    pub pool: Pool,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Pool {
    pub size: u64,
    #[serde(default)]
    pub seed: Option<u64>,
    #[serde(default)]
    pub pick_seed: Option<u64>,
    pub value: Box<NumberExpr>,
    /// Shared by the clones of the pool, so that it is drawn once.
    #[serde(skip)]
    items: Arc<OnceLock<Vec<f64>>>,
    /// The generator of `pick_seed`, shared by the clones of the pool.
    #[serde(skip)]
    picker: Arc<OnceLock<Mutex<Xoshiro256Plus>>>,
}

impl Pool {
    fn sample(&self, rng: &mut impl Rng) -> f64 {
        let items = self.items.get_or_init(|| {
            let mut own = Xoshiro256Plus::seed_from_u64(self.seed.unwrap_or_else(|| rng.random()));
            (0..self.size.max(1)).map(|_| self.value.evaluate(&mut own)).collect()
        });
        match self.pick_seed {
            Some(seed) => {
                let picker = self.picker.get_or_init(|| Mutex::new(Xoshiro256Plus::seed_from_u64(seed)));
                items[picker.lock().expect("pool picker").random_range(0..items.len())]
            }
            None => items[rng.random_range(0..items.len())],
        }
    }
}

fn gcd(mut a: u64, mut b: u64) -> u64 {
    while b != 0 {
        (a, b) = (b, a % b);
    }
    a
}

impl NumberExpr {
    /// Evaluates the expression to a value.
    pub fn evaluate(&self, rng: &mut impl Rng) -> f64 {
        match self {
            Self::Constant(val) => *val,
            Self::Sampled(dist) => dist.evaluate(rng),
            Self::Weighted(weighted) => weighted.sample(rng),
            Self::Scattered(ScatteredNumber { scatter }) => scatter.apply(scatter.value.evaluate(rng)),
            Self::Modulo(ModuloNumber { modulo }) => {
                (modulo.value.evaluate(rng).floor() as u64 % modulo.len.max(1)) as f64
            }
            Self::Ranges(RangesNumber { ranges }) => ranges.sample(rng),
            Self::Pool(PoolNumber { pool }) => pool.sample(rng),
        }
    }

    /// Expected value of the expression.
    pub fn expected_value(&self) -> f64 {
        match self {
            Self::Constant(val) => *val,
            Self::Sampled(dist) => dist.expected_value(),
            Self::Weighted(weighted) => weighted.mean(),
            // the shuffle keeps the values within [start, start + len) but not their mean
            Self::Scattered(ScatteredNumber { scatter }) => scatter.start as f64 + (scatter.len as f64 - 1.0) / 2.0,
            Self::Modulo(ModuloNumber { modulo }) => {
                modulo.value.expected_value().min((modulo.len as f64 - 1.0).max(0.0))
            }
            Self::Ranges(RangesNumber { ranges }) => ranges.mean(),
            Self::Pool(PoolNumber { pool }) => pool.value.expected_value(),
        }
    }

    /// Scales the expression, e.g. an operation count, by `factor`.
    pub fn scale(&mut self, factor: f64) {
        match self {
            Self::Constant(val) => *val *= factor,
            Self::Sampled(distr) => distr.scale(factor),
            Self::Weighted(weighted) => {
                for w in &mut weighted.weighted {
                    w.value.scale(factor);
                }
            }
            Self::Scattered(ScatteredNumber { scatter }) => scatter.value.scale(factor),
            Self::Modulo(ModuloNumber { modulo }) => modulo.value.scale(factor),
            Self::Ranges(RangesNumber { ranges }) => ranges.value.scale(factor),
            Self::Pool(PoolNumber { pool }) => pool.value.scale(factor),
        }
    }
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Weight {
    /// The weight of the item.
    pub weight: f64,
    /// The value of the item.
    pub value: StringExpr,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
#[serde(rename_all = "snake_case")]
pub enum StringExprInnerConfig {
    Uniform {
        /// The length of the string to sample.
        len: NumberExpr,
        #[serde(default)]
        /// The character set to use for sampling the string.
        character_set: Option<CharacterSet>,
    },
    Weighted(Vec<Weight>),
    Segmented {
        separator: String,
        /// The segments to use for the string.
        segments: Vec<StringExpr>,
    },
    /// With `probability`, one of `amount` hot keys; otherwise a random key. All keys are `len`
    /// characters long.
    HotRange {
        len: usize,
        amount: usize,
        probability: f64,
        #[serde(default)]
        /// The character set of the keys (default: the enclosing one).
        character_set: Option<CharacterSet>,
        #[serde(default)]
        /// Every `hot_range` with the same `seed`, `len`, `amount` and character set has the same hot
        /// keys, e.g. so that point queries read the hot keys that inserts write. Without a seed,
        /// each `hot_range` has its own random hot keys.
        seed: Option<u64>,
    },
    /// A sampled number, rounded down and written in decimal, zero-padded to `width` digits, e.g. a
    /// timestamp: `{ "number": { "value": { "uniform": { "min": 1546300800000, "max": 1577836800000 } }, "width": 13 } }`
    Number { value: NumberExpr, width: usize },
    /// Consecutive numbers `start`, `start + step`, ... written like `number`, e.g. timestamps of
    /// points recorded at a fixed rate.
    Counter {
        start: u64,
        #[serde(default = "default_counter_step")]
        step: u64,
        width: usize,
    },
}

fn default_counter_step() -> u64 {
    1
}

#[derive(serde::Deserialize, Clone, Debug)]
#[serde(try_from = "StringExprInnerConfig")]
pub enum StringExprInner {
    Uniform {
        /// The distribution to use for sampling the string.
        // distribution: Distribution,
        /// The length of the string to sample.
        len: NumberExpr,
        #[serde(default)]
        /// The character set to use for sampling the string.
        character_set: Option<CharacterSet>,
    },
    Weighted {
        items: Vec<Weight>,
        distr: WeightedIndex<f64>,
    },
    Segmented {
        separator: String,
        /// The segments to use for the string.
        segments: Vec<StringExpr>,
    },
    HotRange {
        len: usize,
        amount: usize,
        probability: f64,
        character_set: Option<CharacterSet>,
        seed: Option<u64>,
        /// Built on first use, when the enclosing character set is known.
        hot_set: Arc<OnceLock<Arc<HotSet>>>,
    },
    Number {
        value: NumberExpr,
        width: usize,
    },
    Counter {
        step: u64,
        width: usize,
        next: Arc<AtomicU64>,
    },
}

impl JsonSchema for StringExprInner {
    fn schema_name() -> Cow<'static, str> {
        "StringExprInner".into()
    }

    fn json_schema(generator: &mut SchemaGenerator) -> Schema {
        return StringExprInnerConfig::json_schema(generator);
    }
}
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
#[serde(rename_all = "snake_case", untagged)]
pub enum StringExpr {
    Constant(String),
    Inner(StringExprInner),
}

impl TryFrom<StringExprInnerConfig> for StringExprInner {
    type Error = anyhow::Error;

    fn try_from(value: StringExprInnerConfig) -> Result<Self, Self::Error> {
        use StringExprInnerConfig as S;
        return match value {
            S::Uniform {
                len: length,
                character_set,
            } => Ok(Self::Uniform {
                len: length,
                character_set,
            }),
            S::Weighted(items) => {
                let weights = items.iter().map(|w| w.weight).collect::<Vec<_>>();
                let distr = WeightedIndex::new(&weights).context("Building weighted index")?;
                Ok(Self::Weighted { items, distr })
            }
            S::Segmented {
                separator,
                segments,
            } => Ok(Self::Segmented {
                separator,
                segments,
            }),
            S::HotRange {
                len,
                amount,
                probability,
                character_set,
                seed,
            } => Ok(Self::HotRange {
                len,
                amount,
                probability,
                character_set,
                seed,
                hot_set: Arc::new(OnceLock::new()),
            }),
            S::Number { value, width } => Ok(Self::Number { value, width }),
            S::Counter { start, step, width } => Ok(Self::Counter {
                step,
                width,
                next: Arc::new(AtomicU64::new(start)),
            }),
        };
    }
}

#[derive(serde::Deserialize, JsonSchema, Copy, Clone, Debug, Default)]
pub enum RangeFormat {
    /// The start key and the number of keys to scan
    #[default]
    StartCount,
    /// The start key and end key
    StartEnd,
}

/// Writes a `number`/`counter` value: rounded down, in decimal, zero-padded to `width` digits.
fn format_number(value: f64, width: usize) -> Vec<u8> {
    format!("{:0width$}", value as u64, width = width).into_bytes()
}

fn sample_string(rng: &mut impl Rng, character_set: CharacterSet, len: usize) -> Key {
    match character_set {
        CharacterSet::Alphanumeric => Key::from_iter(rng.sample_iter(Alphanumeric).take(len)),
        CharacterSet::Alphabetic => Key::from_iter(rng.sample_iter(Alphabetic).take(len)),
        CharacterSet::Numeric => Key::from_iter(rng.sample_iter(Numeric).take(len)),
    }
}

/// The hot keys of a `hot_range`, and which of them it has generated so far.
#[derive(Debug)]
pub struct HotSet {
    keys: Vec<Key>,
    /// Whether each hot key was generated, and the indices of the generated ones.
    generated: Mutex<(Vec<bool>, Vec<usize>)>,
}

impl HotSet {
    fn new(rng: &mut impl Rng, character_set: CharacterSet, len: usize, amount: usize) -> Self {
        Self {
            keys: (0..amount).map(|_| sample_string(rng, character_set, len)).collect(),
            generated: Mutex::new((vec![false; amount], Vec::new())),
        }
    }

    /// One of the hot keys, uniformly.
    fn generate(&self, rng: &mut impl Rng) -> Key {
        let index = rng.random_range(0..self.keys.len());
        let mut generated = self.generated.lock().expect("hot set lock");
        if !generated.0[index] {
            generated.0[index] = true;
            generated.1.push(index);
        }
        self.keys[index].clone()
    }

    /// One of the hot keys generated so far, uniformly; None if there is none yet.
    fn sample_generated(&self, rng: &mut impl Rng) -> Option<Key> {
        let generated = self.generated.lock().expect("hot set lock");
        if generated.1.is_empty() {
            return None;
        }
        Some(self.keys[generated.1[rng.random_range(0..generated.1.len())]].clone())
    }
}

/// Hot sets of seeded `hot_range`s, shared by seed, length, amount and character set.
static SEEDED_HOT_SETS: OnceLock<Mutex<HashMap<(u64, usize, usize, CharacterSet), Arc<HotSet>>>> =
    OnceLock::new();

impl StringExprInner {
    fn hot_range_character_set(&self, character_set_parent: Option<CharacterSet>) -> CharacterSet {
        match self {
            Self::HotRange { character_set, .. } => character_set.or(character_set_parent).unwrap_or_default(),
            _ => unreachable!("only called on hot_range"),
        }
    }

    fn hot_set(&self, character_set: CharacterSet) -> Arc<HotSet> {
        let Self::HotRange { len, amount, seed, hot_set, .. } = self else {
            unreachable!("only called on hot_range")
        };
        hot_set
            .get_or_init(|| match seed {
                Some(seed) => SEEDED_HOT_SETS
                    .get_or_init(Default::default)
                    .lock()
                    .expect("hot set registry lock")
                    .entry((*seed, *len, *amount, character_set))
                    .or_insert_with(|| {
                        let mut rng = Xoshiro256Plus::seed_from_u64(*seed);
                        Arc::new(HotSet::new(&mut rng, character_set, *len, *amount))
                    })
                    .clone(),
                None => {
                    let mut rng = Xoshiro256Plus::from_os_rng();
                    Arc::new(HotSet::new(&mut rng, character_set, *len, *amount))
                }
            })
            .clone()
    }
}

impl StringExpr {
    /// For the `key` of point queries, which must be a `hot_range`: with its probability, one of
    /// its hot keys that has already been generated (e.g. inserted by a `hot_range` with the same
    /// seed), uniformly; otherwise None, and the query selects an existing key as usual.
    pub fn sample_generated_hot_key(
        &self,
        rng: &mut impl Rng,
        character_set_parent: Option<CharacterSet>,
    ) -> Result<Option<Key>> {
        let Self::Inner(inner @ StringExprInner::HotRange { probability, .. }) = self else {
            anyhow::bail!("the key of point queries must be a hot_range");
        };
        if !rng.random_bool(*probability) {
            return Ok(None);
        }
        let character_set = inner.hot_range_character_set(character_set_parent);
        Ok(inner.hot_set(character_set).sample_generated(rng))
    }

    pub fn generate(&self, rng: &mut impl Rng, character_set_parent: Option<CharacterSet>, thread_id: Option<usize>) -> Key {
        self.generate_shifted(rng, character_set_parent, thread_id, 0.0, &mut None)
    }

    /// Generates a key range spanning `span` of the first `number` segment: the start key and the
    /// same key with that number increased by `span`. Returns None if the expression has no
    /// `number` segment.
    pub fn generate_window<R: Rng + Clone>(
        &self,
        rng: &mut R,
        character_set_parent: Option<CharacterSet>,
        thread_id: Option<usize>,
        span: f64,
    ) -> Option<(Key, Key)> {
        // Rendering the end key from the same random state repeats every other segment.
        let mut replay = rng.clone();
        let mut number = None;
        let start = self.generate_shifted(rng, character_set_parent, thread_id, 0.0, &mut number);
        number?;
        let end = self.generate_shifted(&mut replay, character_set_parent, thread_id, span, &mut None);
        Some((start, end))
    }

    /// `generate` with the first `number` segment increased by `shift`; `number` receives the
    /// unshifted value of that segment.
    fn generate_shifted(
        &self,
        rng: &mut impl Rng,
        character_set_parent: Option<CharacterSet>,
        thread_id: Option<usize>,
        shift: f64,
        number: &mut Option<f64>,
    ) -> Key {
        let key = match self {
            Self::Constant(val) => Key::from(val.as_bytes()),
            Self::Inner(inner) => {
                use StringExprInner as S;
                match inner {
                    S::Uniform {
                        // distribution: _,
                        len: length,
                        character_set,
                    } => {
                        let character_set =
                            character_set.or(character_set_parent).unwrap_or_default();
                        let len = length.evaluate(rng) as usize;
                        match character_set {
                            CharacterSet::Alphanumeric => {
                                Key::from_iter(rng.sample_iter(Alphanumeric).take(len))
                            }
                            CharacterSet::Alphabetic => {
                                Key::from_iter(rng.sample_iter(Alphabetic).take(len))
                            }
                            CharacterSet::Numeric => {
                                Key::from_iter(rng.sample_iter(Numeric).take(len))
                            }
                        }
                    }
                    S::Weighted { items, distr } => {
                        let random_value = rng.sample(distr);
                        let item = &items[random_value];
                        item.value.generate_shifted(rng, None, thread_id, shift, number)
                    }
                    S::Segmented {
                        separator,
                        segments,
                    } => {
                        let mut buf = Vec::new();
                        for (i, segment) in segments.iter().enumerate() {
                            segment
                                .write_all_shifted(&mut buf, rng, None, shift, number)
                                .context("Writing weighted string")
                                .expect("to be able to write to a vec");
                            if i != segments.len() - 1 {
                                buf.extend(separator.as_bytes());
                            }
                        }
                        Key::from(buf)
                    }
                    S::HotRange { probability, len, .. } => {
                        let character_set = inner.hot_range_character_set(character_set_parent);
                        if rng.random_bool(*probability) {
                            inner.hot_set(character_set).generate(rng)
                        } else {
                            sample_string(rng, character_set, *len)
                        }
                    }
                    S::Number { value, width } => {
                        let value = value.evaluate(rng).floor();
                        let shifted = value + if number.is_none() { shift } else { 0.0 };
                        number.get_or_insert(value);
                        Key::from(format_number(shifted, *width))
                    }
                    S::Counter { step, width, next } => {
                        let value = next.fetch_add(*step, Ordering::Relaxed) as f64;
                        number.get_or_insert(value);
                        Key::from(format_number(value, *width))
                    }
                }
            }
        };

        if let Some(tid) = thread_id {
            let mut vec = key.to_vec();
            enforce_thread_uniqueness(&mut vec, tid);
            Key::from(vec)
        } else {
            key
        }
    }
    /// Evaluates the expression to a value.
    pub fn write_all(
        &self,
        writer: &mut impl Write,
        rng: &mut impl Rng,
        character_set_parent: Option<CharacterSet>,
    ) -> Result<()> {
        self.write_all_shifted(writer, rng, character_set_parent, 0.0, &mut None)
    }

    /// `write_all` with the first `number` segment increased by `shift` (see `generate_shifted`).
    fn write_all_shifted(
        &self,
        writer: &mut impl Write,
        rng: &mut impl Rng,
        character_set_parent: Option<CharacterSet>,
        shift: f64,
        number: &mut Option<f64>,
    ) -> Result<()> {
        match self {
            Self::Constant(val) => writer
                .write_all(val.as_bytes())
                .context("Writing constant string"),
            Self::Inner(inner) => {
                use StringExprInner as S;
                match inner {
                    S::Uniform {
                        // distribution: _,
                        len: length,
                        character_set,
                    } => {
                        let character_set =
                            character_set.or(character_set_parent).unwrap_or_default();
                        let len = length.evaluate(rng) as usize;
                        fn write_all(
                            writer: &mut impl Write,
                            rng: &mut impl Rng,
                            distr: impl rand::distr::Distribution<u8>,
                            len: usize,
                        ) -> Result<()> {
                            let buf: Vec<u8> = rng.sample_iter(distr).take(len).collect();
                            writer.write_all(&buf).context("Writing sampled string")?;
                            Ok(())
                        }
                        return match character_set {
                            CharacterSet::Alphanumeric => write_all(writer, rng, Alphanumeric, len),
                            CharacterSet::Alphabetic => write_all(writer, rng, Alphabetic, len),
                            CharacterSet::Numeric => write_all(writer, rng, Numeric, len),
                        };
                    }
                    S::Weighted { items, distr } => {
                        let random_value = rng.sample(distr);
                        let item = &items[random_value];
                        return item
                            .value
                            .write_all_shifted(writer, rng, None, shift, number)
                            .context("Writing weighted string");
                    }
                    S::Segmented {
                        separator,
                        segments,
                    } => {
                        for segment in segments {
                            segment
                                .write_all_shifted(writer, rng, None, shift, number)
                                .context("Writing weighted string")?;
                            writer
                                .write_all(separator.as_bytes())
                                .context("Writing separator")?;
                        }
                        return Ok(());
                    }
                    S::HotRange { probability, len, .. } => {
                        let character_set = inner.hot_range_character_set(character_set_parent);
                        let key = if rng.random_bool(*probability) {
                            inner.hot_set(character_set).generate(rng)
                        } else {
                            sample_string(rng, character_set, *len)
                        };
                        writer.write_all(&key).context("Writing weighted string")
                    }
                    S::Number { value, width } => {
                        let value = value.evaluate(rng).floor();
                        let shifted = value + if number.is_none() { shift } else { 0.0 };
                        number.get_or_insert(value);
                        writer.write_all(&format_number(shifted, *width)).context("Writing number")
                    }
                    S::Counter { step, width, next } => {
                        let value = next.fetch_add(*step, Ordering::Relaxed) as f64;
                        number.get_or_insert(value);
                        writer.write_all(&format_number(value, *width)).context("Writing counter")
                    }
                }
            }
        }
    }
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Inserts specification.
pub struct Inserts {
    /// Number of inserts
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    /// Value
    pub val: Option<StringExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Updates specification.
pub struct Updates {
    /// Number of updates
    pub op_count: NumberExpr,
    /// Value
    pub val: Option<StringExpr>,
    /// Key selection strategy
    pub selection: Option<KeySelection>,
    ///// Key sort order
    //pub sort_by: SortBy,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Merges (read-modify-write) specification.
pub struct Merges {
    /// Number of merges
    pub op_count: NumberExpr,
    /// Value
    pub val: Option<StringExpr>,
    /// Key selection strategy
    pub selection: Option<KeySelection>,
    ///// Key sort order
    //pub sort_by: SortBy,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Non-empty point deletes specification.
pub struct PointDeletes {
    /// Number of non-empty point deletes
    pub op_count: NumberExpr,
    /// Key selection strategy
    pub selection: Option<KeySelection>,
    ///// Key sort order
    //pub sort_by: SortBy,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Empty point deletes specification.
pub struct EmptyPointDeletes {
    /// Number of empty point deletes
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Range deletes specification.
pub struct RangeDeletes {
    /// Number of range deletes
    pub op_count: NumberExpr,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    /// Mutally exclusive with scan_length
    pub selectivity: Option<NumberExpr>,
    /// Specifies an exact scan length for range queries. Mutally exclusive with selectivity.
    pub scan_length: Option<NumberExpr>,
    /// Key selection strategy of the start key
    pub selection: Option<KeySelection>,
    /// The format for the range
    #[serde(default)]
    pub range_format: RangeFormat,
    ///// Key sort order
    //pub sort_by: SortBy,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Non-empty point queries specification.
pub struct PointQueries {
    /// Number of point queries
    pub op_count: NumberExpr,
    /// Key selection strategy of the start key
    pub selection: Option<KeySelection>,
    /// Hot keys: a `hot_range`. With its probability, a query reads one of its hot keys that has
    /// already been written (give the inserts' `hot_range` the same `seed`), uniformly; otherwise
    /// it reads a key chosen with `selection`.
    pub key: Option<StringExpr>,
    ///// Key sort order
    //pub sort_by: SortBy,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Range queries specification.
pub struct RangeQueries {
    /// Number of range queries
    pub op_count: NumberExpr,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    /// Mutally exclusive with scan_length
    pub selectivity: Option<NumberExpr>,
    /// Specifies an exact scan length for range queries. Mutally exclusive with selectivity.
    pub scan_length: Option<NumberExpr>,
    /// Key selection strategy of the start key
    pub selection: Option<KeySelection>,
    /// The format for the range
    #[serde(default)]
    pub range_format: RangeFormat,
    ///// Key sort order
    //pub sort_by: SortBy,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

// impl RangeQueries {
//     pub fn get_range_length(&self, rng: &mut impl Rng, num_keys: usize) -> usize {
//         if let Some(sel) = &self.selectivity {
//             (sel.evaluate(rng) * num_keys as f64) as usize
//         } else {
//             self.scan_length
//                 .as_ref()
//                 .expect("Scan length should be specified if selectivity is not")
//                 .evaluate(rng) as usize
//         }
//     }
// }

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Empty point queries specification.
pub struct EmptyPointQueries {
    /// Number of point queries
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Untracked Point Query Specification
pub struct UntrackedPointQueries {
    /// Number of untracked point queries
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct UntrackedRangeQueries {
    /// Number of untracked range queries
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    /// Mutally exclusive with scan_length
    pub selectivity: Option<NumberExpr>,
    /// Specifies an exact scan length for range queries. Mutally exclusive with selectivity.
    pub scan_length: Option<NumberExpr>,
    /// Makes each query a window over the values of the first `number` segment of `key` instead of a
    /// number of keys: the range is [key, the same key with that number increased by `span`), e.g. a
    /// time window over timestamp keys. Written with a start and an end key. Mutually exclusive with
    /// selectivity and scan_length.
    pub span: Option<NumberExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

pub trait RangeQuery {
    fn get_range_length(&self, rng: &mut impl Rng, num_keys: usize);
}

macro_rules! impl_range_query {
    ($($t:ty), *) => {
        $(impl $t {
            pub fn get_range_length(&self, rng: &mut impl Rng, num_keys: usize) -> usize {
                let len = if let Some(sel) = &self.selectivity {
                    (sel.evaluate(rng) * num_keys as f64) as usize
                } else {
                    self.scan_length
                        .as_ref()
                        .expect("Scan length should be specified if selectivity is not")
                        .evaluate(rng) as usize
                };
                if num_keys == 0 {
                    1
                } else {
                    std::cmp::min(num_keys, std::cmp::max(1, len))
                }
            }
        })*
    };
}

impl_range_query!(RangeQueries, RangeDeletes, UntrackedRangeQueries);

pub trait Scalable {
    fn scale(&mut self, factor: f64);
}

macro_rules! impl_scalable_regular {
    ($($t:ty),*) => {
        $(impl Scalable for $t {
            fn scale(&mut self, factor: f64) {
                self.op_count.scale(factor);
            }
        })*
    }
}

macro_rules! impl_scalable_with_selection {
    ($($t:ty),*) => {
        $(impl Scalable for $t {
            fn scale(&mut self, factor: f64) {
                self.op_count.scale(factor);

                if let Some(distr) = &mut self.selection {
                    distr.scale(factor);
                }
            }
        })*
    }
}

impl_scalable_regular!(
    Inserts,
    EmptyPointDeletes,
    EmptyPointQueries,
    UntrackedPointQueries,
    UntrackedRangeQueries
);

impl_scalable_with_selection!(
    Updates,
    Merges,
    PointDeletes,
    RangeDeletes,
    PointQueries,
    RangeQueries
);

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct Sorted {
    /// The number of displaced operations.
    pub k: NumberExpr,
    /// The distance between swapped elements.
    pub l: NumberExpr,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct WorkloadSpecGroup {
    pub sorted: Option<Sorted>,
    pub unique_inserts: Option<Inserts>,
    pub inserts: Option<Inserts>,
    pub updates: Option<Updates>,
    pub merges: Option<Merges>,
    pub point_deletes: Option<PointDeletes>,
    pub empty_point_deletes: Option<EmptyPointDeletes>,
    pub range_deletes: Option<RangeDeletes>,
    pub point_queries: Option<PointQueries>,
    pub empty_point_queries: Option<EmptyPointQueries>,
    pub range_queries: Option<RangeQueries>,
    // formerly blind_*: specs written with the old names still load
    #[serde(alias = "blind_point_queries")]
    pub untracked_point_queries: Option<UntrackedPointQueries>,
    #[serde(alias = "blind_point_deletes")]
    pub untracked_point_deletes: Option<UntrackedPointQueries>,
    #[serde(alias = "blind_range_queries")]
    pub untracked_range_queries: Option<UntrackedRangeQueries>,

    /// Defaults for the group
    pub defaults: Option<Defaults>,

    /// Used for displaying stats and gui
    pub name: Option<String>,
    /// Whether or not to save stats for a group
    #[serde(default)]
    pub enable_granular_stats: bool,
}

#[derive(serde::Deserialize, JsonSchema, Default, Copy, Clone, Debug, PartialEq, Eq, Hash)]
#[serde(rename_all = "snake_case")]
pub enum CharacterSet {
    #[default]
    Alphanumeric,
    // AlphanumericLower,
    // AlphanumericUpper,
    Alphabetic,
    // AlphabeticLower,
    // AlphabeticUpper,
    Numeric,
    // Hexadecimal,
    // Utf8,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct WorkloadSpecSection {
    /// A list of groups. Groups share valid keys between operations.
    ///
    /// E.g., non-empty point queries will use a key from an insert in this group.
    pub groups: Vec<WorkloadSpecGroup>,
    /// Whether to skip the check that a generated key is in the valid key set for inserts and empty point queries/deletes.
    ///
    /// This is useful when the keyspace is much larger than the number of keys being generated, as it can greatly decrease generation time.
    #[serde(default)]
    pub skip_key_contains_check: bool,

    /// Defaults for the section
    pub defaults: Option<Defaults>,
    /// Default distributions for the section
    #[serde(default)]
    pub default_distributions: DefaultDistributionsOptional,

    /// Used for displaying stats and gui
    pub name: Option<String>,
    #[serde(default)]
    /// Whether or not to save stats for a section
    pub enable_granular_stats: bool,
}

impl WorkloadSpecSection {
    pub fn has_unique_insert(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .unique_inserts
                .as_ref()
                .is_some_and(|is| is.op_count.expected_value() > 0.)
        });
    }

    pub fn has_upsert(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .inserts
                .as_ref()
                .is_some_and(|is| is.op_count.expected_value() > 0.)
        });
    }

    pub fn has_update(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .updates
                .as_ref()
                .is_some_and(|us| us.op_count.expected_value() > 0.)
        });
    }
    pub fn has_merge(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .merges
                .as_ref()
                .is_some_and(|ms| ms.op_count.expected_value() > 0.)
        });
    }
    pub fn has_delete_point(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .point_deletes
                .as_ref()
                .is_some_and(|pds| pds.op_count.expected_value() > 0.)
        });
    }
    pub fn has_delete_point_empty(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .empty_point_deletes
                .as_ref()
                .is_some_and(|epds| epds.op_count.expected_value() > 0.)
        });
    }
    pub fn has_delete_range(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .range_deletes
                .as_ref()
                .is_some_and(|rds| rds.op_count.expected_value() > 0.)
        });
    }

    // pub fn has_delete_range_count(&self) -> bool {
    //     return self.groups.iter().any(|group| {
    //         group.range_deletes.as_ref().is_some_and(|rds| {
    //             rds.op_count.expected_value() > 0.
    //                 && matches!(rds.range_format, RangeFormat::StartCount)
    //         })
    //     });
    // }

    pub fn has_query_point(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .point_queries
                .as_ref()
                .is_some_and(|pqs| pqs.op_count.expected_value() > 0.)
        });
    }
    pub fn has_query_point_empty(&self) -> bool {
        return self.groups.iter().any(|group| {
            group
                .empty_point_queries
                .as_ref()
                .is_some_and(|epqs| epqs.op_count.expected_value() > 0.)
        });
    }
    pub fn has_query_range(&self) -> bool {
        return self.groups.iter().any(|group| {
            group.range_queries.as_ref().is_some_and(|rqs| {
                rqs.op_count.expected_value() > 0.
                    && matches!(rqs.range_format, RangeFormat::StartEnd)
            })
        });
    }

    pub fn has_query_range_count(&self) -> bool {
        return self.groups.iter().any(|group| {
            group.range_queries.as_ref().is_some_and(|rqs| {
                rqs.op_count.expected_value() > 0.
                    && matches!(rqs.range_format, RangeFormat::StartCount)
            })
        });
    }

    pub fn skip_contains_check(&self) -> bool {
        return self.skip_key_contains_check;
    }
}

#[derive(serde::Deserialize, JsonSchema, Debug, Clone)]
pub struct WorkloadSpec {
    /// Sections of a workload where a key from one will (probably) not appear in another.
    pub sections: Vec<WorkloadSpecSection>,
    /// Defaults for the workload
    #[serde(default)]
    pub defaults: Option<Defaults>,
    /// Default distributions for the workload
    #[serde(default)]
    pub default_distributions: DefaultDistributions,
}

#[derive(serde::Deserialize, JsonSchema, Debug, Clone, Default)]
pub struct Defaults {
    /// Default Key
    pub key: Option<StringExpr>,
    /// Default Value
    pub val: Option<StringExpr>,

    /// The domain from which the keys will be created from.
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Debug, Clone, Default)]
pub struct DefaultDistributions {
    /// Default distribution for updates
    #[serde(default)]
    pub updates_selection: KeySelection,
    /// Default distribution for merges
    #[serde(default)]
    pub merges_selection: KeySelection,
    /// Default distribution for point deletes
    #[serde(default)]
    pub point_deletes_selection: KeySelection,
    /// Default distribution for range queries
    #[serde(default)]
    pub range_queries_selection: KeySelection,
    /// Default distribution for range deletes
    #[serde(default)]
    pub range_deletes_selection: KeySelection,
    /// Default distribution for point queries
    #[serde(default)]
    pub point_queries_selection: KeySelection,
}

#[derive(serde::Deserialize, JsonSchema, Debug, Clone, Default)]
pub struct DefaultDistributionsOptional {
    /// Default distribution for updates
    pub updates_selection: Option<KeySelection>,
    /// Default distribution for merges
    pub merges_selection: Option<KeySelection>,
    /// Default distribution for point deletes
    pub point_deletes_selection: Option<KeySelection>,
    /// Default distribution for range queries
    pub range_queries_selection: Option<KeySelection>,
    /// Default distribution for range deletes
    pub range_deletes_selection: Option<KeySelection>,
    /// Default distribution for point queries
    pub point_queries_selection: Option<KeySelection>,
}

impl WorkloadSpec {
    pub fn has_unique_insert(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_unique_insert);
    }

    pub fn has_update(&self) -> bool {
        return self.sections.iter().any(WorkloadSpecSection::has_update);
    }
    pub fn has_merge(&self) -> bool {
        return self.sections.iter().any(WorkloadSpecSection::has_merge);
    }
    pub fn has_delete_point(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_delete_point);
    }
    pub fn has_delete_point_empty(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_delete_point_empty);
    }
    pub fn has_delete_range(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_delete_range);
    }
    pub fn has_query_point(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_query_point);
    }
    pub fn has_query_point_empty(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_query_point_empty);
    }
    pub fn has_query_range(&self) -> bool {
        return self
            .sections
            .iter()
            .any(WorkloadSpecSection::has_query_range);
    }

    pub fn skip_contains_check_all(&self) -> bool {
        return self
            .sections
            .iter()
            .all(|section| section.skip_contains_check());
    }
}

fn enforce_thread_uniqueness(key: &mut [u8], thread_id: usize) {
    let len = key.len();
    if len == 0 {
        return;
    }
    
    let mut t_temp = thread_id;
    let mut digits = [0u8; 4];
    for i in (0..4).rev() {
        digits[i] = (t_temp % 10) as u8;
        t_temp /= 10;
    }
    
    let mut t_temp = thread_id;
    let mut letters_upper = [0u8; 4];
    for i in (0..4).rev() {
        letters_upper[i] = (t_temp % 26) as u8;
        t_temp /= 26;
    }
    
    let count = 4.min(len);
    for i in 0..count {
        let pos = len - 1 - i;
        let c = key[pos];
        if c >= b'0' && c <= b'9' {
            key[pos] = b'0' + digits[3 - i];
        } else if c >= b'A' && c <= b'Z' {
            key[pos] = b'A' + letters_upper[3 - i];
        } else if c >= b'a' && c <= b'z' {
            key[pos] = b'a' + letters_upper[3 - i];
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn selection(json: &str) -> KeySelection {
        serde_json::from_str(json).expect("valid key selection")
    }

    fn rng() -> Xoshiro256Plus {
        Xoshiro256Plus::seed_from_u64(0xdead_beef)
    }

    #[test]
    fn selections_stay_in_bounds_for_tiny_key_domains() {
        for json in [
            r#"{"zipf": {"n": 100, "s": 1.2}}"#,
            r#"{"normal": {"mean": 0.5, "std_dev": 10}}"#,
            r#"{"pareto": {"scale": 1, "shape": 1.5}}"#,
            r#"{"uniform": {"min": 0.99, "max": 1.0}}"#,
        ] {
            let sel = selection(json);
            let mut rng = rng();
            for len in 1..20 {
                for _ in 0..1000 {
                    assert!(sel.select(&mut rng, len) < len, "{json}");
                }
            }
        }
    }

    #[test]
    fn scatter_shuffles_a_number_one_to_one_into_its_range() {
        let number = |json: &str| serde_json::from_str::<NumberExpr>(json).expect("valid number");
        // every rank of [0, len) lands at a distinct number of [start, start + len)
        let all = number(r#"{"scatter": {"start": 1000, "len": 97, "value": {"uniform": {"min": 0, "max": 97}}}}"#);
        let mut rng = rng();
        let mut seen = std::collections::HashSet::new();
        for _ in 0..20_000 {
            let x = all.evaluate(&mut rng);
            assert!((1000.0..1097.0).contains(&x) && x.fract() == 0.0, "{x}");
            seen.insert(x as u64);
        }
        assert_eq!(seen.len(), 97);
        // a rank goes to start + (rank * a) mod len, a = 60 (nearest 97 / golden ratio, coprime with 97)
        let rank = number(r#"{"scatter": {"start": 1000, "len": 97, "value": 3.7}}"#);
        assert_eq!(rank.evaluate(&mut rng), 1000.0 + (3 * 60 % 97) as f64);
        // ranks beyond len wrap around
        let wrapped = number(r#"{"scatter": {"len": 97, "value": 100}}"#);
        assert_eq!(wrapped.evaluate(&mut rng), (3 * 60 % 97) as f64);
    }

    #[test]
    fn generalized_pareto_has_its_quantiles_and_mean() {
        let number = |json: &str| serde_json::from_str::<NumberExpr>(json).expect("valid number");
        let gpd = number(r#"{"generalized_pareto": {"location": 10, "scale": 25, "shape": 0.25}}"#);
        let mut rng = rng();
        let mut xs: Vec<f64> = (0..200_000).map(|_| gpd.evaluate(&mut rng)).collect();
        xs.sort_by(f64::total_cmp);
        assert!(xs[0] >= 10.0);
        // median: 10 + 25 * (2^0.25 - 1) / 0.25 = 28.92
        assert!((xs[100_000] - 28.92).abs() < 0.3, "{}", xs[100_000]);
        // mean: 10 + 25 / 0.75
        let mean = xs.iter().sum::<f64>() / xs.len() as f64;
        assert!((mean - gpd.expected_value()).abs() < 0.5 && (gpd.expected_value() - 43.333).abs() < 1e-3, "{mean}");
        // truncated at max: never above it, and the median of what is left
        let truncated = number(r#"{"generalized_pareto": {"location": 10, "scale": 25, "shape": 0.25, "max": 50}}"#);
        let mut ys: Vec<f64> = (0..200_000).map(|_| truncated.evaluate(&mut rng)).collect();
        ys.sort_by(f64::total_cmp);
        assert!(ys[ys.len() - 1] < 50.0 && ys[0] >= 10.0);
        let top = generalized_pareto_cdf(10.0, 25.0, 0.25, 50.0);
        let median = generalized_pareto_quantile(10.0, 25.0, 0.25, top / 2.0);
        assert!((ys[100_000] - median).abs() < 0.3, "{} {median}", ys[100_000]);
        let mean = ys.iter().sum::<f64>() / ys.len() as f64;
        assert!((mean - truncated.expected_value()).abs() < 0.3, "{mean}");
    }

    #[test]
    fn modulo_and_ranges_place_numbers() {
        let number = |json: &str| serde_json::from_str::<NumberExpr>(json).expect("valid number");
        let mut rng = rng();
        assert_eq!(number(r#"{"modulo": {"len": 10, "value": 123.9}}"#).evaluate(&mut rng), 3.0);
        // range 1 only (weight 0 elsewhere), offset 1234 mod 100
        let one = number(r#"{"ranges": {"len": 100, "weights": [0, 1, 0], "value": 1234}}"#);
        assert_eq!(one.evaluate(&mut rng), 134.0);
        // ranges are picked in proportion to their weights
        let ranges = number(r#"{"ranges": {"len": 100, "weights": [1, 3], "value": {"uniform": {"min": 0, "max": 100}}}}"#);
        let n = 40_000;
        let second = (0..n).filter(|_| ranges.evaluate(&mut rng) >= 100.0).count();
        assert!((second as f64 / n as f64 - 0.75).abs() < 0.01, "{second}");

        // two_term_exponential matches db_bench's FAST '20 prefix distribution
        let exp_ranges = number(r#"{"ranges": {"len": 1000, "weights": {"two_term_exponential": {"a": 14.18, "b": -2.917, "c": 0.0164, "d": -0.08082, "n": 30}}, "value": 0}}"#);
        let n = 50_000;
        let in_hot_29 = (0..n).filter(|_| exp_ranges.evaluate(&mut rng) >= 29_000.0).count();
        assert!((in_hot_29 as f64 / n as f64 - 0.8066).abs() < 0.01, "{in_hot_29}");
    }

    #[test]
    fn pools_draw_a_fixed_population_shared_by_seed() {
        let number = |json: &str| serde_json::from_str::<NumberExpr>(json).expect("valid number");
        let mut rng = rng();
        let population = |expr: &NumberExpr, rng: &mut Xoshiro256Plus| -> std::collections::BTreeSet<u64> {
            (0..50_000).map(|_| expr.evaluate(rng) as u64).collect()
        };
        // an unseeded pool keeps its numbers, also in its clones
        let pool = number(r#"{"pool": {"size": 100, "value": {"uniform": {"min": 0, "max": 1e12}}}}"#);
        let first = population(&pool, &mut rng);
        assert_eq!(first.len(), 100);
        assert_eq!(population(&pool.clone(), &mut rng), first);
        // pools with the same seed: the smaller holds the first numbers of the larger
        let large = number(r#"{"pool": {"size": 100, "seed": 3, "value": {"uniform": {"min": 0, "max": 1e12}}}}"#);
        let small = number(r#"{"pool": {"size": 40, "seed": 3, "value": {"uniform": {"min": 0, "max": 1e12}}}}"#);
        let (large, small) = (population(&large, &mut rng), population(&small, &mut rng));
        assert_eq!((large.len(), small.len()), (100, 40));
        assert!(small.is_subset(&large));
        // pools with the same size and pick_seed pick the same rows: key k always comes with length 2k
        let keys = number(r#"{"pool": {"size": 50, "seed": 4, "pick_seed": 9, "value": {"uniform": {"min": 0, "max": 1e9}}}}"#);
        let lengths = number(r#"{"pool": {"size": 50, "seed": 4, "pick_seed": 9, "value": {"uniform": {"min": 0, "max": 1e9}}}}"#);
        for _ in 0..1000 {
            assert_eq!(keys.evaluate(&mut rng), lengths.evaluate(&mut rng));
        }
        // weighted numbers with the same weights and pick_seed pick the same entries
        let classes = number(r#"{"weighted": [{"weight": 1, "value": 0}, {"weight": 2, "value": {"uniform": {"min": 1, "max": 9}}}], "pick_seed": 5}"#);
        let lengths = number(r#"{"weighted": [{"weight": 1, "value": 100}, {"weight": 2, "value": 200}], "pick_seed": 5}"#);
        let picks: Vec<(f64, f64)> = (0..1000).map(|_| (classes.evaluate(&mut rng), lengths.evaluate(&mut rng))).collect();
        assert!(picks.iter().all(|&(c, l)| (c == 0.0) == (l == 100.0)));
        assert!(picks.iter().any(|&(c, _)| c == 0.0) && picks.iter().any(|&(c, _)| c != 0.0));
        let unlinked = number(r#"{"pool": {"size": 50, "seed": 4, "value": {"uniform": {"min": 0, "max": 1e9}}}}"#);
        assert!((0..100).any(|_| keys.evaluate(&mut rng) != unlinked.evaluate(&mut rng)));
    }

    #[test]
    fn scramble_reproduces_ycsb_scrambled_zipfian() {
        // YCSB's ScrambledZipfianGenerator: a zipf rank over 10^10 items, hashed onto the existing
        // keys with Utils.fnvhash64 (FNV-1 over 8 bytes, then Math.abs).
        fn fnvhash64(mut x: u64) -> u64 {
            let mut hash: u64 = 0xcbf2_9ce4_8422_2325;
            for _ in 0..8 {
                hash ^= x & 0xFF;
                hash = hash.wrapping_mul(1_099_511_628_211);
                x >>= 8;
            }
            (hash as i64).unsigned_abs()
        }
        // Values computed by YCSB's site.ycsb.Utils.fnvhash64 (Java).
        for (x, ycsb) in [
            (0, 6284781860667377211),
            (1, 8517097267634966620),
            (2, 1820151046732198393),
            (255, 8064062821143829734),
            (256, 2056600594528442646),
            (123456789, 2350796791908741607),
            (9999999999, 3605131173811637474),
            (i64::MAX as u64, 8289549613075766851),
        ] {
            assert_eq!(fnvhash64(x), ycsb);
            assert_eq!(fnv_hash(x), ycsb);
        }
        let sel = selection(r#"{"zipf": {"n": 10000000000, "s": 0.99}, "scramble": true}"#);
        assert!(sel.scramble);
        let zipf = rand_distr::Zipf::new(1e10, 0.99).unwrap();
        let (mut rng_a, mut rng_b) = (rng(), rng());
        for len in [1usize, 7, 10_000, 1_000_000] {
            for _ in 0..10_000 {
                let rank = zipf.sample(&mut rng_a) as u64 - 1;
                let expected = (fnvhash64(rank) % len as u64) as usize;
                assert_eq!(sel.select(&mut rng_b, len), expected);
            }
        }
    }

    #[test]
    fn seeded_hot_ranges_share_hot_keys() {
        let insert = string_expr(r#"{"hot_range": {"len": 8, "amount": 10, "probability": 1.0, "seed": 1}}"#);
        let query = string_expr(r#"{"hot_range": {"len": 8, "amount": 10, "probability": 1.0, "seed": 1}}"#);
        let mut rng = rng();
        // nothing inserted yet: no hot key to read
        assert!(query.sample_generated_hot_key(&mut rng, None).unwrap().is_none());
        let inserted: Vec<Key> = (0..3).map(|_| insert.generate(&mut rng, None, None)).collect();
        for _ in 0..100 {
            let key = query.sample_generated_hot_key(&mut rng, None).unwrap().expect("a hot key");
            assert!(inserted.contains(&key));
        }
    }

    #[test]
    fn unseeded_hot_ranges_have_own_hot_keys() {
        let a = string_expr(r#"{"hot_range": {"len": 16, "amount": 5, "probability": 1.0}}"#);
        let b = string_expr(r#"{"hot_range": {"len": 16, "amount": 5, "probability": 1.0}}"#);
        let mut rng = rng();
        let keys_a: Vec<Key> = (0..50).map(|_| a.generate(&mut rng, None, None)).collect();
        assert!((0..50).all(|_| !keys_a.contains(&b.generate(&mut rng, None, None))));
    }

    #[test]
    fn hot_range_uses_character_set() {
        let hot = string_expr(r#"{"hot_range": {"len": 16, "amount": 5, "probability": 0.5}}"#);
        let mut rng = rng();
        for _ in 0..100 {
            let key = hot.generate(&mut rng, Some(CharacterSet::Numeric), None);
            assert!(key.iter().all(u8::is_ascii_digit));
        }
    }

    #[test]
    fn point_query_key_must_be_hot_range() {
        let key = string_expr(r#"{"uniform": {"len": 8}}"#);
        assert!(key.sample_generated_hot_key(&mut rng(), None).is_err());
    }

    #[test]
    fn selection_does_not_depend_on_scramble() {
        // Keys are always selected by scattering the sample over the keys, like the paper's design.
        for distr in [
            r#""zipf": {"n": 100000, "s": 1.2}"#,
            r#""normal": {"mean": 0.5, "std_dev": 0.1}"#,
            r#""uniform": {"min": 0.0, "max": 1.0}"#,
        ] {
            let plain = selection(&format!("{{{distr}}}"));
            let scrambled = selection(&format!(r#"{{{distr}, "scramble": true}}"#));
            let (mut rng_a, mut rng_b) = (rng(), rng());
            for _ in 0..10_000 {
                assert_eq!(plain.select(&mut rng_a, 7_919), scrambled.select(&mut rng_b, 7_919));
            }
        }
    }

    #[test]
    fn scaling_a_scrambled_selection_keeps_n() {
        // YCSB's scrambled zipfian always draws from 10^10 items, whatever the number of records.
        let mut sel = selection(r#"{"zipf": {"n": 10000000000, "s": 0.99}, "scramble": true}"#);
        sel.scale(0.05);
        assert!(matches!(sel.distribution, Distribution::Zipf { n, .. } if n == 1e10));
    }

    #[test]
    fn scaling_a_selection_scales_only_key_counts() {
        let mut sel = selection(r#"{"zipf": {"n": 1000, "s": 1.2}}"#);
        sel.scale(10.0);
        assert!(matches!(sel.distribution, Distribution::Zipf { n, .. } if n == 10_000.0));

        let mut sel = selection(r#"{"uniform": {"min": 0.0, "max": 1.0}}"#);
        sel.scale(10.0);
        assert!(
            matches!(sel.distribution, Distribution::Uniform { min, max, .. } if min == 0.0 && max == 1.0)
        );
    }

    #[test]
    fn scaling_a_zipf_value_distribution_scales_n_once() {
        let mut distr: Distribution =
            serde_json::from_str(r#"{"zipf": {"n": 1000, "s": 1.2}}"#).unwrap();
        distr.scale(10.0);
        assert!(matches!(distr, Distribution::Zipf { n, .. } if n == 10_000.0));
    }

    fn string_expr(json: &str) -> StringExpr {
        serde_json::from_str(json).expect("valid string expression")
    }

    #[test]
    fn number_is_zero_padded_and_counter_counts() {
        let mut rng = rng();
        let expr = string_expr(r#"{"number": {"value": 42.9, "width": 5}}"#);
        assert_eq!(&*expr.generate(&mut rng, None, None), b"00042");

        let expr = string_expr(
            r#"{"segmented": {"separator": ":", "segments": ["m", {"counter": {"start": 7, "step": 2, "width": 3}}]}}"#,
        );
        let keys: Vec<_> = (0..3).map(|_| expr.generate(&mut rng, None, None)).collect();
        assert_eq!(keys, [b"m:007".as_slice(), b"m:009", b"m:011"].map(Key::from));
    }

    #[test]
    fn windows_shift_only_the_first_number() {
        let mut rng = rng();
        let expr = string_expr(
            r#"{"segmented": {"separator": ":", "segments": [
                {"number": {"value": {"uniform": {"min": 1000, "max": 2000}}, "width": 4}},
                {"uniform": {"len": 6}},
                {"number": {"value": {"uniform": {"min": 0, "max": 100}}, "width": 3}}]}}"#,
        );
        for _ in 0..1000 {
            let (start, end) = expr.generate_window(&mut rng, None, None, 300.0).unwrap();
            let (start, end) = (std::str::from_utf8(&start).unwrap(), std::str::from_utf8(&end).unwrap());
            let s: u64 = start[..4].parse().unwrap();
            let e: u64 = end[..4].parse().unwrap();
            assert_eq!(e, s + 300);
            assert_eq!(start[4..], end[4..], "every other segment repeats");
        }
        let no_number = string_expr(r#"{"uniform": {"len": 6}}"#);
        assert!(no_number.generate_window(&mut rng, None, None, 1.0).is_none());
    }
}
