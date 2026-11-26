//! CPU-intensive text, ranking, clustering, and Markdown redaction core for `last30days-skill`.
//!
//! Each module mirrors a pure-Python module under `skills/last30days/scripts/lib/`
//! with matching observable behavior so the Python engine can delegate hot loops
//! to a standalone native binary when available and fall back cleanly to Python
//! otherwise.

pub mod cjk;
pub mod cluster;
pub mod dedupe;
pub mod entity;
pub mod error;
pub mod markdown;
pub mod relevance;
pub mod signals;

pub use error::CoreError;
