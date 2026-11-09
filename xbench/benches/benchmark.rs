use std::io::{BufWriter, Write};

use criterion::{Criterion, criterion_group, criterion_main};
use rand::{Rng, SeedableRng};
use rand_distr::Alphanumeric;

fn buffered_buffered_writer<W: Write>(writer: &mut BufWriter<W>, len: usize) {
    let mut rng = rand_xoshiro::Xoroshiro128PlusPlus::from_seed([
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    ]);
    const BUF_SIZE: usize = 4096;
    let mut buf = [0u8; BUF_SIZE];
    let mut remaining = len;

    while remaining > 0 {
        let n = remaining.min(BUF_SIZE);

        for x in buf.iter_mut().take(n) {
            *x = rng.sample(Alphanumeric)
        }
        writer.write_all(&buf[..n]).unwrap();
        remaining -= n;
    }
}
fn buffered_writer<W: Write>(writer: &mut BufWriter<W>, len: usize) {
    let mut rng = rand_xoshiro::Xoroshiro128PlusPlus::from_seed([
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    ]);

    for ch in (&mut rng).sample_iter(Alphanumeric).take(len) {
        writer.write_all(&[ch]).unwrap();
    }
}

pub fn criterion_benchmark(c: &mut Criterion) {
    // c.bench_function("fib 20", |b| b.iter(|| fibonacci(black_box(20))));
    let mut writer = BufWriter::new(std::io::sink());
    c.bench_function("buf buf write", |b| {
        b.iter(|| buffered_buffered_writer(&mut writer, 1024 * 1024))
    });
    c.bench_function("buf write", |b| {
        b.iter(|| buffered_writer(&mut writer, 1024 * 1024))
    });
}

criterion_group!(benches, criterion_benchmark);
criterion_main!(benches);
