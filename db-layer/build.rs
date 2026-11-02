fn main() {
    // The IOStatsContext shim for the RocksDB executor, compiled against librocksdb-sys's RocksDB headers.
    if std::env::var_os("CARGO_FEATURE_ROCKSDB").is_some() {
        let rocksdb_sys = std::env::var("DEP_ROCKSDB_CARGO_MANIFEST_DIR").expect("librocksdb-sys build metadata");
        cc::Build::new()
            .cpp(true)
            .flag_if_supported("-std=c++17")
            .include(format!("{rocksdb_sys}/rocksdb/include"))
            .file("src/rocksdb_iostats.cc")
            .compile("xbench_rocksdb_iostats");
        println!("cargo:rerun-if-changed=src/rocksdb_iostats.cc");
    }
}
