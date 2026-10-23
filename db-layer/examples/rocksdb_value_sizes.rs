//! Prints "key,value_len" for every live key of a RocksDB database, opened read-only.
//!
//! cargo run --release -p db-layer --example rocksdb_value_sizes -- <db_path> [--values]

fn main() -> anyhow::Result<()> {
    let mut args = std::env::args().skip(1);
    let path = args.next().ok_or_else(|| anyhow::anyhow!("usage: rocksdb_value_sizes <db_path> [--values]"))?;
    let print_values = args.next().as_deref() == Some("--values");

    let db = rocksdb::DB::open_for_read_only(&rocksdb::Options::default(), &path, false)?;
    let mut iter = db.raw_iterator();
    iter.seek_to_first();
    let stdout = std::io::stdout();
    let mut out = std::io::BufWriter::new(stdout.lock());
    use std::io::Write;
    while let (Some(key), Some(value)) = (iter.key(), iter.value()) {
        out.write_all(key)?;
        write!(out, ",{}", value.len())?;
        if print_values {
            out.write_all(b",")?;
            out.write_all(value)?;
        }
        out.write_all(b"\n")?;
        iter.next();
    }
    iter.status()?;
    Ok(())
}
