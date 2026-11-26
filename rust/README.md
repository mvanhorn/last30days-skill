# last30days-core (Rust Accelerator)

Optional standalone native binary that accelerates the measured CPU bottlenecks
of `last30days-skill`:

- Parallel library Markdown privacy redaction, metadata parsing, and SHA-256 fingerprinting (`library.py`, `library_index.py`)
- Near-duplicate detection with 64-bit trigram/token fingerprints (`dedupe.py`)
- Greedy leader clustering, MMR representative selection, and cross-source entity merging (`cluster.py`, `entity_extract.py`)
- Token-overlap relevance and local signal annotation (`relevance.py`, `signals.py`, `cjk.py`)

## Zero Rust required for end users

End users never install Rust, Cargo, or `rustup`.

- Compiled release binaries (`last30days-core`) link only to standard OS
  libraries already present on macOS, Linux, and Windows.
- They have no Python C-extension ABI coupling (one binary works across Python
  3.12, 3.13, 3.14, and 3.15).
- If no binary is present on disk, `skills/last30days/scripts/lib/rust_accel.py`
  falls back silently to the pure-Python implementation.

## Binary discovery

`rust_accel.py` checks, in order:

1. `LAST30DAYS_RUST_BIN` environment variable
2. `~/.local/share/last30days/bin/last30days-core` (`.exe` on Windows)
3. `<repo>/rust/target/release/last30days-core` (dev checkout)
4. `last30days-core` on `PATH`

## A/B testing

```bash
# Force pure Python
LAST30DAYS_ENGINE=python uv run python skills/last30days/scripts/last30days.py "topic" --quick

# Force Rust (fails fast if binary missing)
LAST30DAYS_ENGINE=rust uv run python skills/last30days/scripts/last30days.py "topic" --quick

# Run full parity + speedup benchmark suite
uv run python scripts/bench_rust_vs_python.py
```

## Building from source

Requires stable Rust 1.85+ (no nightly features). Lint configuration follows
[BeautifulRust](https://github.com/morganlinton/BeautifulRust) (`clippy.toml`
and `[workspace.lints]` in `Cargo.toml`).

```bash
cd rust
cargo fmt --check
cargo clippy --all-targets --all-features -- -D warnings
cargo test
RUSTDOCFLAGS="-D warnings" cargo doc --no-deps
cargo build --release
```

See [PORTING.md](PORTING.md) for API mapping, test mapping, and preserved quirks.
