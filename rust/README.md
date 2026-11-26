# last30days-core (Native Rust Compute Core)

Built-in standalone native binary that powers the measured CPU hotspots of
`last30days-skill` by default:

- Parallel library Markdown privacy redaction, metadata parsing, and SHA-256 fingerprinting (`library.py`, `library_index.py`)
- Near-duplicate detection with 64-bit trigram/token fingerprints (`dedupe.py`)
- Greedy leader clustering, MMR representative selection, and cross-source entity merging (`cluster.py`, `entity_extract.py`)
- Token-overlap relevance and local signal annotation (`relevance.py`, `signals.py`, `cjk.py`)

## Zero Rust required for end users

End users never install Rust, Cargo, or `rustup`.

- `setup` and the first live local run download the matching prebuilt binary
  from GitHub Releases, verify its SHA-256 checksum against
  `last30days-core-SHA256SUMS.txt` and its protocol handshake, and install it to
  `~/.local/share/last30days/bin/last30days-core`.
- Linux binaries build statically against `musl`; macOS and Windows binaries
  link only to standard OS libraries already present on every machine.
- They have no Python C-extension ABI coupling (one binary works across Python
  3.12, 3.13, 3.14, and 3.15).
- Pure Python stays in place as an automatic fallback for offline sandboxes or
  unsupported architectures, and `doctor` reports which backend is active.

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

# Override per-request worker timeout in seconds (default: 30)
LAST30DAYS_RUST_TIMEOUT=15 uv run python skills/last30days/scripts/last30days.py "topic" --quick

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
