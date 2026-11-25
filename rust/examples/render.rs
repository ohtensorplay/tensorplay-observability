//! Parity helper: render the snapshot given on stdin with a chosen prefix.
//! Used by tests to compare the Rust and Python renderers byte for byte.

fn main() {
    let mut input = String::new();
    std::io::Read::read_to_string(&mut std::io::stdin(), &mut input).unwrap();
    let prefix = std::env::args().nth(1).unwrap_or_else(|| "tp".into());
    print!(
        "{}",
        tensorplay_observability::render_prometheus_metrics(&input, &prefix).unwrap()
    );
}
