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

/// How many times an out-of-range sample is redrawn before it is clamped into the key domain.
const MAX_REDRAWS: usize = 1000;

/// Draws samples until one falls inside `[low, high)` and returns where it falls in that window as
/// a fraction in `[0, 1)`. Redrawing (instead of clamping) truncates the distribution to the key
/// domain without piling the out-of-range probability mass onto the first or last key.
fn sample_in_window<R: Rng>(
    rng: &mut R,
    mut sample: impl FnMut(&mut R) -> f64,
    low: f64,
    high: f64,
) -> f64 {
    let mut x = 0.0;
    for _ in 0..MAX_REDRAWS {
        x = (sample(rng) - low) / (high - low);
        if (0.0..1.0).contains(&x) {
            return x;
        }
    }
    x.clamp(0.0, 1.0 - f64::EPSILON)
}

#[inline]
fn fraction_to_position(fraction: f64, len: usize) -> usize {
    ((fraction * len as f64) as usize).min(len - 1)
}

/// Samples a 0-based Zipf rank over the first `min(n, len)` positions of the key domain.
fn zipf_rank<R: Rng>(
    rng: &mut R,
    n: f64,
    s: f64,
    distr: &rand_distr::Zipf<f64>,
    len: usize,
) -> usize {
    let rank = if (len as f64) < n {
        // Fewer keys exist than the distribution has ranks: truncate it to the existing keys.
        rand_distr::Zipf::new(len as f64, s)
            .expect("to be able to construct a truncated zipf distribution")
            .sample(rng)
    } else {
        distr.sample(rng)
    };
    (rank as usize).saturating_sub(1).min(len - 1)
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
    /// The key selection used before selections were laid over the sorted key domain: a sample is
    /// mapped to an index and, for every distribution except `uniform`, scattered over the key
    /// domain with FNV hashing. With `zipf` and a large `n` (e.g. 10^10), this is YCSB's scrambled
    /// zipfian distribution.
    pub fn scrambled_index(&self, rng: &mut impl Rng, len: usize) -> usize {
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
        };
    }

    /// Maps a sample to a position in `[0, len)` of the *sorted* key domain (0 = smallest key).
    ///
    /// - `zipf`: rank `r` selects position `r - 1`, so the most frequent key is position 0, the
    ///   second most frequent is position 1, and so on. Only the first `n` keys can be selected; if
    ///   fewer than `n` keys exist, the distribution is truncated to the existing keys.
    /// - `latest`: like `zipf`, but anchored at the largest key.
    /// - `uniform`, `normal`, `beta`: samples are fractions of the key domain (0.0 = smallest key,
    ///   1.0 = largest key).
    /// - `exponential`, `log_normal`, `poisson`, `weibull`, `pareto`: the distribution is stretched
    ///   over the key domain (see [`Self::key_domain_window`]).
    ///
    /// Samples outside the key domain are redrawn.
    pub fn key_position<R: Rng>(&self, rng: &mut R, len: usize) -> usize {
        assert!(len > 0, "Cannot select a key from an empty key domain");
        let (low, high) = self.key_domain_window();
        match self {
            Self::Zipf { n, s, distr } => zipf_rank(rng, *n, *s, distr, len),
            Self::Latest { n, s, distr } => len - 1 - zipf_rank(rng, *n, *s, distr, len),
            Self::Poisson { distr, .. } => {
                // Spread each integer outcome uniformly over its slice of the key domain, instead
                // of always landing on the first key of the slice.
                let x = sample_in_window(
                    rng,
                    |rng| distr.sample(rng) + rng.random::<f64>(),
                    low,
                    high,
                );
                fraction_to_position(x, len)
            }
            _ => fraction_to_position(
                sample_in_window(rng, |rng| self.evaluate(rng), low, high),
                len,
            ),
        }
    }

    /// The range of sample values that is mapped onto the whole key domain.
    pub fn key_domain_window(&self) -> (f64, f64) {
        match self {
            Self::Uniform { .. } | Self::Normal { .. } | Self::Beta { .. } => (0.0, 1.0),
            Self::Zipf { n, .. } | Self::Latest { n, .. } => (1.0, n + 1.0),
            Self::Exponential { lambda, .. } => (0.0, 5.0 / lambda),
            Self::LogNormal { mean, std_dev, .. } => (0.0, (mean + 3.0 * std_dev).exp()),
            Self::Poisson { lambda, .. } => (0.0, (lambda + 5.0 * lambda.sqrt()).ceil() + 1.0),
            // 99th percentile
            Self::Weibull { scale, shape, .. } => (0.0, scale * (-0.01f64.ln()).powf(1.0 / shape)),
            // 99th percentile
            Self::Pareto { scale, shape, .. } => (*scale, scale * 100f64.powf(1.0 / shape)),
        }
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

/// Selects existing keys by laying a distribution over the sorted key domain (see
/// [`Distribution::key_position`]), shifted by `offset` keys.
///
/// ```json
/// { "zipf": { "n": 1000000, "s": 0.99 }, "offset": 1000 }
/// ```
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct KeySelection {
    #[serde(flatten)]
    pub distribution: Distribution,
    /// Shifts the selected position by this many keys in sorted key order, wrapping around at the
    /// end of the key domain (negative values shift towards smaller keys). For example, with
    /// `offset: 1000` the key a `zipf` selection picks most often is the 1001st smallest key
    /// (position 1000) instead of the smallest key (position 0).
    #[serde(default)]
    pub offset: i64,
    /// Scatters the selected keys over the key domain with FNV hashing (see
    /// [`Distribution::scrambled_index`]) instead of laying the distribution over the sorted key
    /// domain. Use it to reproduce YCSB's scrambled zipfian distribution.
    #[serde(default)]
    pub scramble: bool,
}

impl KeySelection {
    /// Returns a position in `[0, len)` of the sorted key domain.
    pub fn select<R: Rng>(&self, rng: &mut R, len: usize) -> usize {
        let position = match self.scramble {
            true => self.distribution.scrambled_index(rng, len),
            false => self.distribution.key_position(rng, len),
        };
        let position = position as i64 + self.offset;
        return position.rem_euclid(len as i64) as usize;
    }
}

impl Default for KeySelection {
    fn default() -> Self {
        Self {
            distribution: Distribution::default_key_selection(),
            offset: 0,
            scramble: false,
        }
    }
}

impl Scalable for KeySelection {
    /// Only quantities measured in keys are scaled. Fractions of the key domain and distribution
    /// shapes that are stretched over it are independent of the number of keys. Scrambled
    /// selections keep their `n`, like YCSB's scrambled zipfian, which always draws from 10^10
    /// items regardless of the number of records.
    fn scale(&mut self, factor: f64) {
        self.offset = (self.offset as f64 * factor).round() as i64;
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
}

impl NumberExpr {
    /// Evaluates the expression to a value.
    pub fn evaluate(&self, rng: &mut impl Rng) -> f64 {
        match self {
            Self::Constant(val) => *val,
            Self::Sampled(dist) => dist.evaluate(rng),
        }
    }

    /// Expected value of the expression.
    pub fn expected_value(&self) -> f64 {
        match self {
            Self::Constant(val) => *val,
            Self::Sampled(dist) => dist.expected_value(),
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
    HotRange {
        len: usize,
        amount: usize,
        probability: f64,
    },
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
        hot_ranges: Vec<Key>,
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
            } => {
                let mut rng = Xoshiro256Plus::from_os_rng();
                let rng_ref = &mut rng;
                let mut hot_ranges = Vec::with_capacity(amount);
                for _ in 0..amount {
                    let key: Key = rng_ref.sample_iter(Alphanumeric).take(len).collect();
                    hot_ranges.push(key);
                }
                Ok(Self::HotRange {
                    len,
                    amount,
                    probability,
                    hot_ranges,
                })
            }
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

impl StringExpr {
    pub fn generate(&self, rng: &mut impl Rng, character_set_parent: Option<CharacterSet>, thread_id: Option<usize>) -> Key {
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
                        item.value.generate(rng, None, thread_id)
                    }
                    S::Segmented {
                        separator,
                        segments,
                    } => {
                        let mut buf = Vec::new();
                        for (i, segment) in segments.iter().enumerate() {
                            segment
                                .write_all(&mut buf, rng, None)
                                .context("Writing weighted string")
                                .expect("to be able to write to a vec");
                            if i != segments.len() - 1 {
                                buf.extend(separator.as_bytes());
                            }
                        }
                        Key::from(buf)
                    }
                    S::HotRange {
                        hot_ranges,
                        probability,
                        len,
                        ..
                    } => {
                        let is_hot = rng.random_bool(*probability);
                        if is_hot {
                            let index = rng.random_range(0..hot_ranges.len());
                            hot_ranges[index].clone()
                        } else {
                            let key: Key = rng.sample_iter(Alphanumeric).take(*len).collect();
                            Key::from(key)
                        }
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
                            .write_all(writer, rng, None)
                            .context("Writing weighted string");
                    }
                    S::Segmented {
                        separator,
                        segments,
                    } => {
                        for segment in segments {
                            segment
                                .write_all(writer, rng, None)
                                .context("Writing weighted string")?;
                            writer
                                .write_all(separator.as_bytes())
                                .context("Writing separator")?;
                        }
                        return Ok(());
                    }
                    S::HotRange {
                        hot_ranges,
                        probability,
                        len,
                        ..
                    } => {
                        let is_hot = rng.random_bool(*probability);
                        let key = if is_hot {
                            let index = rng.random_range(0..hot_ranges.len());
                            hot_ranges[index].clone()
                        } else {
                            let key: Key = rng.sample_iter(Alphanumeric).take(*len).collect();
                            Key::from(key)
                        };
                        writer.write_all(&key).context("Writing weighted string")
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
/// Blind Point Query Specification
pub struct BlindPointQueries {
    /// Number of blind point queries
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct BlindRangeQueries {
    /// Number of blind range queries
    pub op_count: NumberExpr,
    /// Key
    pub key: Option<StringExpr>,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    /// Mutally exclusive with scan_length
    pub selectivity: Option<NumberExpr>,
    /// Specifies an exact scan length for range queries. Mutally exclusive with selectivity.
    pub scan_length: Option<NumberExpr>,
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

impl_range_query!(RangeQueries, RangeDeletes, BlindRangeQueries);

pub trait Scalable {
    fn scale(&mut self, factor: f64);
}

macro_rules! impl_scalable_regular {
    ($($t:ty),*) => {
        $(impl Scalable for $t {
            fn scale(&mut self, factor: f64) {
                match &mut self.op_count {
                    NumberExpr::Constant(op_count) => *op_count *= factor,
                    NumberExpr::Sampled(distr) => distr.scale(factor),
                }
            }
        })*
    }
}

macro_rules! impl_scalable_with_selection {
    ($($t:ty),*) => {
        $(impl Scalable for $t {
            fn scale(&mut self, factor: f64) {
                match &mut self.op_count {
                    NumberExpr::Constant(op_count) => *op_count *= factor,
                    NumberExpr::Sampled(distr) => distr.scale(factor),
                }

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
    BlindPointQueries,
    BlindRangeQueries
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
    pub blind_point_queries: Option<BlindPointQueries>,
    pub blind_point_deletes: Option<BlindPointQueries>,
    pub blind_range_queries: Option<BlindRangeQueries>,

    /// Defaults for the group
    pub defaults: Option<Defaults>,

    /// Used for displaying stats and gui
    pub name: Option<String>,
    /// Whether or not to save stats for a group
    #[serde(default)]
    pub enable_granular_stats: bool,
}

#[derive(serde::Deserialize, JsonSchema, Default, Copy, Clone, Debug)]
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
    use statrs::distribution::{
        Beta, ContinuousCDF, Exp, LogNormal, Normal, Pareto, Uniform, Weibull,
    };

    fn selection(json: &str) -> KeySelection {
        serde_json::from_str(json).expect("valid key selection")
    }

    fn rng() -> Xoshiro256Plus {
        Xoshiro256Plus::seed_from_u64(0xdead_beef)
    }

    fn histogram(sel: &KeySelection, len: usize, samples: usize) -> Vec<usize> {
        let mut rng = rng();
        let mut counts = vec![0; len];
        for _ in 0..samples {
            counts[sel.select(&mut rng, len)] += 1;
        }
        counts
    }

    fn zipf_pmf(n: usize, s: f64) -> Vec<f64> {
        let weights: Vec<f64> = (1..=n).map(|r| (r as f64).powf(-s)).collect();
        let total: f64 = weights.iter().sum();
        weights.into_iter().map(|w| w / total).collect()
    }

    /// Pearson's chi-square test of `counts` against `pmf`, failing far beyond the 99.9th percentile
    /// (df + 5 * sqrt(2 df)) so the test is not flaky, but still catches any systematic deviation.
    fn assert_chi_square(counts: &[usize], pmf: &[f64]) {
        let total: usize = counts.iter().sum();
        let (mut chi2, mut df) = (0.0, 0usize);
        let (mut obs_tail, mut exp_tail) = (0.0, 0.0);
        for (&c, &p) in counts.iter().zip(pmf) {
            let e = p * total as f64;
            if e >= 5.0 {
                chi2 += (c as f64 - e).powi(2) / e;
                df += 1;
            } else {
                obs_tail += c as f64;
                exp_tail += e;
            }
        }
        if exp_tail >= 5.0 {
            chi2 += (obs_tail - exp_tail).powi(2) / exp_tail;
            df += 1;
        }
        let df = (df - 1) as f64;
        let limit = df + 5.0 * (2.0 * df).sqrt();
        assert!(
            chi2 < limit,
            "chi2 = {chi2:.1} exceeds {limit:.1} (df = {df})"
        );
    }

    /// One-sample Kolmogorov-Smirnov test of the selected positions against the distribution's CDF
    /// truncated to its key domain window. Fails at alpha = 0.001.
    fn assert_ks(json: &str, cdf: impl Fn(f64) -> f64) {
        let sel = selection(json);
        let len = 1_000_000;
        let samples = 100_000;
        let (low, high) = sel.distribution.key_domain_window();
        let (cdf_low, cdf_high) = (cdf(low), cdf(high));
        let truncated_cdf =
            |fraction: f64| (cdf(low + fraction * (high - low)) - cdf_low) / (cdf_high - cdf_low);

        let mut rng = rng();
        let mut fractions: Vec<f64> = (0..samples)
            .map(|_| (sel.select(&mut rng, len) as f64 + 0.5) / len as f64)
            .collect();
        fractions.sort_by(f64::total_cmp);
        let d = fractions
            .iter()
            .enumerate()
            .map(|(i, &x)| {
                let f = truncated_cdf(x);
                (f - i as f64 / samples as f64)
                    .abs()
                    .max(((i + 1) as f64 / samples as f64 - f).abs())
            })
            .fold(0.0, f64::max);
        let critical = 1.95 / (samples as f64).sqrt();
        assert!(d < critical, "{json}: KS statistic {d:.5} >= {critical:.5}");
    }

    #[test]
    fn continuous_distributions_follow_their_cdf_over_the_key_domain() {
        let normal = Normal::new(0.3, 0.1).unwrap();
        assert_ks(r#"{"normal": {"mean": 0.3, "std_dev": 0.1}}"#, |x| {
            normal.cdf(x)
        });
        // Wide normal, heavily truncated at both ends of the key domain.
        let normal = Normal::new(0.5, 0.5).unwrap();
        assert_ks(r#"{"normal": {"mean": 0.5, "std_dev": 0.5}}"#, |x| {
            normal.cdf(x)
        });
        let uniform = Uniform::new(0.2, 0.4).unwrap();
        assert_ks(r#"{"uniform": {"min": 0.2, "max": 0.4}}"#, |x| {
            uniform.cdf(x)
        });
        let beta = Beta::new(2.0, 5.0).unwrap();
        assert_ks(r#"{"beta": {"alpha": 2, "beta": 5}}"#, |x| beta.cdf(x));
        let exp = Exp::new(2.0).unwrap();
        assert_ks(r#"{"exponential": {"lambda": 2}}"#, |x| exp.cdf(x));
        let log_normal = LogNormal::new(10.0, 2.0).unwrap();
        assert_ks(r#"{"log_normal": {"mean": 10, "std_dev": 2}}"#, |x| {
            log_normal.cdf(x)
        });
        let weibull = Weibull::new(1.5, 3.0).unwrap();
        assert_ks(r#"{"weibull": {"scale": 3, "shape": 1.5}}"#, |x| {
            weibull.cdf(x)
        });
        let pareto = Pareto::new(1.0, 2.0).unwrap();
        assert_ks(r#"{"pareto": {"scale": 1, "shape": 2}}"#, |x| pareto.cdf(x));
    }

    #[test]
    fn poisson_outcomes_cover_their_slice_of_the_key_domain() {
        let sel = selection(r#"{"poisson": {"lambda": 4}}"#);
        let (_, high) = sel.distribution.key_domain_window();
        let len = 100_000;
        let counts = histogram(&sel, len, 400_000);
        let pmf: Vec<f64> = (0..len)
            .map(|pos| {
                // Each outcome k is spread uniformly over [k, k + 1) of the window.
                let k = (pos as f64 / len as f64 * high).floor() as u64;
                let p = (-4.0f64).exp() * 4f64.powi(k as i32)
                    / (1..=k).map(|i| i as f64).product::<f64>();
                p / (len as f64 / high)
            })
            .collect();
        let total: f64 = pmf.iter().sum();
        let pmf: Vec<f64> = pmf.iter().map(|p| p / total).collect();
        // Compare per slice of 1000 keys.
        let bucket = |v: &[f64]| v.chunks(1000).map(|c| c.iter().sum()).collect::<Vec<f64>>();
        let counts_b: Vec<usize> = counts.chunks(1000).map(|c| c.iter().sum()).collect();
        assert_chi_square(&counts_b, &bucket(&pmf));
    }

    #[test]
    fn zipf_ranks_map_to_consecutive_keys_starting_at_offset() {
        let len = 10_000;
        let pmf = zipf_pmf(1000, 1.1);
        for offset in [0usize, 1, 1000, 5000] {
            let sel = selection(&format!(
                r#"{{"zipf": {{"n": 1000, "s": 1.1}}, "offset": {offset}}}"#
            ));
            let counts = histogram(&sel, len, 500_000);
            let hottest = (0..len).max_by_key(|&i| counts[i]).unwrap();
            assert_eq!(
                hottest, offset,
                "the most frequent key must be at the offset"
            );
            // Only the n keys starting at the offset are selected.
            assert!(counts[..offset].iter().all(|&c| c == 0));
            assert!(counts[offset + 1000..].iter().all(|&c| c == 0));
            assert_chi_square(&counts[offset..offset + 1000], &pmf);
        }
    }

    #[test]
    fn zipf_is_truncated_when_there_are_fewer_keys_than_ranks() {
        let sel = selection(r#"{"zipf": {"n": 1000000, "s": 0.99}}"#);
        let counts = histogram(&sel, 500, 500_000);
        assert_chi_square(&counts, &zipf_pmf(500, 0.99));
    }

    #[test]
    fn latest_is_anchored_at_the_largest_key() {
        let len = 10_000;
        let pmf = zipf_pmf(1000, 1.1);
        let sel = selection(r#"{"latest": {"n": 1000, "s": 1.1}}"#);
        let counts = histogram(&sel, len, 500_000);
        let reversed: Vec<usize> = counts[len - 1000..].iter().rev().copied().collect();
        assert!(counts[..len - 1000].iter().all(|&c| c == 0));
        assert_chi_square(&reversed, &pmf);
    }

    #[test]
    fn offset_shifts_every_distribution_and_wraps_around() {
        let distributions = [
            r#""uniform": {"min": 0.0, "max": 1.0}"#,
            r#""uniform": {"min": 0.2, "max": 0.4}"#,
            r#""normal": {"mean": 0.5, "std_dev": 0.1}"#,
            r#""beta": {"alpha": 2, "beta": 5}"#,
            r#""zipf": {"n": 10000, "s": 1.2}"#,
            r#""latest": {"n": 10000, "s": 1.2}"#,
            r#""exponential": {"lambda": 1}"#,
            r#""log_normal": {"mean": 0.4, "std_dev": 0.2}"#,
            r#""poisson": {"lambda": 10}"#,
            r#""weibull": {"scale": 1, "shape": 2}"#,
            r#""pareto": {"scale": 1, "shape": 2}"#,
        ];
        let len = 7_919;
        for distr in distributions {
            let base = selection(&format!("{{{distr}}}"));
            for offset in [1i64, 1000, 7_918, 20_000, -1, -1000] {
                let shifted = selection(&format!(r#"{{{distr}, "offset": {offset}}}"#));
                let (mut rng_a, mut rng_b) = (rng(), rng());
                for _ in 0..10_000 {
                    let a = base.select(&mut rng_a, len) as i64;
                    let b = shifted.select(&mut rng_b, len) as i64;
                    assert_eq!(
                        b,
                        (a + offset).rem_euclid(len as i64),
                        "{distr} offset {offset}"
                    );
                }
            }
        }
    }

    #[test]
    fn selections_stay_in_bounds_for_tiny_key_domains() {
        for json in [
            r#"{"zipf": {"n": 100, "s": 1.2}, "offset": 7}"#,
            r#"{"normal": {"mean": 0.5, "std_dev": 10}}"#,
            r#"{"pareto": {"scale": 1, "shape": 1.5}, "offset": -3}"#,
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
    fn heavy_tails_do_not_pile_up_on_the_last_key() {
        // Before, samples beyond the window were clamped onto the last key (1% of all selections for
        // pareto and weibull).
        let len = 10_000;
        for json in [
            r#"{"pareto": {"scale": 1, "shape": 2}}"#,
            r#"{"weibull": {"scale": 1, "shape": 2}}"#,
            r#"{"exponential": {"lambda": 1}}"#,
        ] {
            let counts = histogram(&selection(json), len, 200_000);
            assert!(
                counts[len - 1] < 20,
                "{json}: {} selections of the last key",
                counts[len - 1]
            );
        }
    }

    #[test]
    fn key_selection_deserializes_with_and_without_offset() {
        let sel = selection(r#"{"zipf": {"n": 100, "s": 1.2}}"#);
        assert_eq!(sel.offset, 0);
        let sel = selection(r#"{"offset": 1000, "normal": {"mean": 0.5, "std_dev": 0.1}}"#);
        assert_eq!(sel.offset, 1000);
        assert!(matches!(sel.distribution, Distribution::Normal { .. }));
        assert_eq!(KeySelection::default().offset, 0);
        assert!(serde_json::from_str::<KeySelection>(r#"{"offset": 5}"#).is_err());
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
    fn scaling_a_scrambled_selection_keeps_n() {
        // YCSB's scrambled zipfian always draws from 10^10 items, whatever the number of records.
        let mut sel = selection(r#"{"zipf": {"n": 10000000000, "s": 0.99}, "scramble": true}"#);
        sel.scale(0.05);
        assert!(matches!(sel.distribution, Distribution::Zipf { n, .. } if n == 1e10));
    }

    #[test]
    fn scramble_is_opt_in_and_composes_with_offset() {
        assert!(!selection(r#"{"zipf": {"n": 100, "s": 1.2}}"#).scramble);
        assert!(!KeySelection::default().scramble);
        for distr in [
            r#""zipf": {"n": 100000, "s": 1.2}"#,
            r#""normal": {"mean": 0.5, "std_dev": 0.1}"#,
            r#""uniform": {"min": 0.0, "max": 1.0}"#,
            r#""latest": {"n": 100000, "s": 1.2}"#,
        ] {
            let base = selection(&format!(r#"{{{distr}, "scramble": true}}"#));
            let shifted = selection(&format!(r#"{{{distr}, "scramble": true, "offset": 1000}}"#));
            let (mut rng_a, mut rng_b) = (rng(), rng());
            for _ in 0..10_000 {
                let a = base.select(&mut rng_a, 7_919);
                let b = shifted.select(&mut rng_b, 7_919);
                assert_eq!(b, (a + 1000) % 7_919, "{distr}");
            }
        }
    }

    #[test]
    fn scaling_a_selection_scales_only_key_counts() {
        let mut sel = selection(r#"{"zipf": {"n": 1000, "s": 1.2}, "offset": 100}"#);
        sel.scale(10.0);
        assert!(matches!(sel.distribution, Distribution::Zipf { n, .. } if n == 10_000.0));
        assert_eq!(sel.offset, 1000);

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
}
