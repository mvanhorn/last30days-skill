//! Error types for `last30days-core`.

use std::path::PathBuf;

use thiserror::Error;

/// Errors returned by fallible operations in `last30days-core`.
#[derive(Debug, Error)]
#[non_exhaustive]
pub enum CoreError {
    /// A threshold or numeric parameter was outside its valid range.
    #[error("invalid threshold {value}: expected a finite value in [0.0, 1.0]")]
    InvalidThreshold {
        /// The rejected threshold value.
        value: f64,
    },

    /// An n-gram size was zero.
    #[error("invalid n-gram size: must be at least 1")]
    InvalidNgramSize,

    /// An ISO date string could not be parsed.
    #[error("invalid ISO date `{input}`: expected YYYY-MM-DD")]
    InvalidDate {
        /// The malformed date string.
        input: String,
    },

    /// A saved Markdown file had no extractable title.
    #[error("no markdown title found")]
    MissingMarkdownTitle,

    /// A saved Markdown file had an empty title after inline cleanup.
    #[error("empty markdown title")]
    EmptyMarkdownTitle,

    /// Reading a file from disk failed.
    #[error("failed to read `{path}`")]
    ReadFile {
        /// Path that failed to read.
        path: PathBuf,
        /// Underlying I/O error.
        #[source]
        source: std::io::Error,
    },
}

const _: () = {
    const fn assert_send_sync<T: Send + Sync + 'static>() {}
    assert_send_sync::<CoreError>();
};
