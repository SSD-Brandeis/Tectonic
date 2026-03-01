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
    Zipf { n: usize, s: f64 },
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
        n: usize,
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
                distr: rand_distr::Zipf::new(n as f64, s)?,
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

impl Distribution {
    pub fn evaluate(&self, rng: &mut impl Rng) -> f64 {
        return match self {
            Self::Uniform { distr, .. } => distr.sample(rng),
            Self::Normal { distr, .. } => distr.sample(rng),
            Self::Exponential { distr, .. } => distr.sample(rng),
            Self::Beta { distr, .. } => distr.sample(rng),
            Self::Zipf { distr, .. } => distr.sample(rng),
            Self::LogNormal { distr, .. } => distr.sample(rng),
            Self::Poisson { distr, .. } => distr.sample(rng),
            Self::Weibull { distr, .. } => distr.sample(rng),
            Self::Pareto { distr, .. } => distr.sample(rng),
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
    pub fn generate(&self, rng: &mut impl Rng, character_set_parent: Option<CharacterSet>) -> Key {
        return match self {
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
                        item.value.generate(rng, None)
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
                        return if is_hot {
                            let index = rng.random_range(0..hot_ranges.len());
                            hot_ranges[index].clone()
                        } else {
                            let key: Key = rng.sample_iter(Alphanumeric).take(*len).collect();
                            Key::from(key)
                        };
                    }
                }
            }
        };
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
                            for ch in rng.sample_iter(distr).take(len) {
                                writer.write_all(&[ch]).context("Writing sampled string")?;
                            }
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
    pub key: StringExpr,
    /// Value
    pub val: StringExpr,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Updates specification.
pub struct Updates {
    /// Number of updates
    pub op_count: NumberExpr,
    /// Value
    pub val: StringExpr,
    /// Key selection strategy
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
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
    pub val: StringExpr,
    /// Key selection strategy
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
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
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
    ///// Key sort order
    //pub sort_by: SortBy,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Empty point deletes specification.
pub struct EmptyPointDeletes {
    /// Number of empty point deletes
    pub op_count: NumberExpr,
    /// Key
    pub key: StringExpr,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}
#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Range deletes specification.
pub struct RangeDeletes {
    /// Number of range deletes
    pub op_count: NumberExpr,
    /// Selectivity of range deletes. Based off of the range of valid keys, not the full key space.
    pub selectivity: NumberExpr,
    /// Key selection strategy of the start key
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
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
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
    ///// Key sort order
    //pub sort_by: SortBy,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Empty point queries specification.
pub struct EmptyPointQueries {
    /// Number of point queries
    pub op_count: NumberExpr,
    /// Key
    pub key: StringExpr,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Range queries specification.
pub struct RangeQueries {
    /// Number of range queries
    pub op_count: NumberExpr,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    pub selectivity: NumberExpr,
    /// Key selection strategy of the start key
    #[serde(default = "Distribution::default_key_selection")]
    pub selection: Distribution,
    /// The format for the range
    #[serde(default)]
    pub range_format: RangeFormat,
    ///// Key sort order
    //pub sort_by: SortBy,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
/// Blind Point Query Specification
pub struct BlindPointQueries {
    /// Number of blind point queries
    pub op_count: NumberExpr,
    /// Key
    pub key: StringExpr,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

#[derive(serde::Deserialize, JsonSchema, Clone, Debug)]
pub struct BlindRangeQueries {
    /// Number of blind range queries
    pub op_count: NumberExpr,
    /// Key
    pub key: StringExpr,
    /// Selectivity of range queries. Based off of the range of valid keys, not the full key-space.
    pub selectivity: NumberExpr,
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
}

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
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
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
    /// The domain from which the keys will be created from.
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
    /// Whether to skip the check that a generated key is in the valid key set for inserts and empty point queries/deletes.
    ///
    /// This is useful when the keyspace is much larger than the number of keys being generated, as it can greatly decrease generation time.
    #[serde(default)]
    pub skip_key_contains_check: bool,
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
            group
                .range_queries
                .as_ref()
                .is_some_and(|rqs| rqs.op_count.expected_value() > 0.)
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
    /// The domain from which the keys will be created from.
    #[serde(default)]
    pub character_set: Option<CharacterSet>,
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
