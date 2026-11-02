// RocksDB's IOStatsContext (per-thread file I/O bytes and times) is not in RocksDB's C API, so rust-rocksdb
// cannot read it; this shim returns its report for the calling thread.
#include <rocksdb/iostats_context.h>

#include <cstdlib>
#include <cstring>
#include <string>

extern "C" char* xbench_iostats_context_report(unsigned char exclude_zero_counters) {
  std::string report = ROCKSDB_NAMESPACE::get_iostats_context()->ToString(exclude_zero_counters != 0);
  char* out = static_cast<char*>(std::malloc(report.size() + 1));
  std::memcpy(out, report.c_str(), report.size() + 1);
  return out;
}
