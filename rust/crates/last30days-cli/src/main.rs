//! Standalone native accelerator CLI and JSON-RPC stdio worker for `last30days-skill`.

use std::env;
use std::io::{self, BufRead, BufWriter, Write};

use anyhow::{Context, Result};
use last30days_core::cluster::{ClusterCandidate, ClusterOutput, ClusterPlan, cluster_candidates};
use last30days_core::dedupe::{DedupeEntry, dedupe_kept_indices, hybrid_similarity};
use last30days_core::entity::{entity_overlap, extract_text_entities};
use last30days_core::markdown::{
    MarkdownBatchItem, MarkdownBatchResult, MarkdownFileRequest, MarkdownScanOutcome,
    has_private_corpus, is_unbounded_private_brief, public_markdown, sanitize_markdown_batch,
    scan_markdown_files_batch,
};
use last30days_core::relevance::{PreparedQuery, token_overlap_relevance};
use last30days_core::signals::{AnnotatedSignal, FreshnessMode, SignalItem, annotate_stream};
use serde::{Deserialize, Serialize};

const PROTOCOL_VERSION: u32 = 1;

#[derive(Debug, Deserialize)]
#[serde(tag = "op", rename_all = "snake_case")]
enum Request {
    Ping,
    DedupeIndices {
        entries: Vec<DedupeEntry>,
        threshold: f64,
    },
    HybridSimilarity {
        text_a: String,
        text_b: String,
    },
    TokenOverlapRelevance {
        query: String,
        text: String,
        #[serde(default)]
        hashtags: Vec<String>,
    },
    AnnotateStream {
        items: Vec<SignalItem>,
        ranking_query: String,
        freshness_mode: FreshnessMode,
        reference_date: String,
        max_days: i64,
    },
    ClusterCandidates {
        candidates: Vec<ClusterCandidate>,
        plan: ClusterPlan,
    },
    ExtractEntities {
        text: String,
    },
    EntityOverlap {
        text_a: String,
        text_b: String,
    },
    PublicMarkdown {
        markdown: String,
    },
    HasPrivateCorpus {
        markdown: String,
    },
    IsUnboundedPrivateBrief {
        markdown: String,
        source_format: Option<String>,
    },
    SanitizeMarkdownBatch {
        items: Vec<MarkdownBatchItem>,
    },
    ScanMarkdownFiles {
        requests: Vec<MarkdownFileRequest>,
    },
}

#[derive(Debug, Serialize)]
#[serde(tag = "status", rename_all = "snake_case")]
enum Response {
    Ok {
        #[serde(flatten)]
        payload: ResponsePayload,
    },
    Error {
        message: String,
    },
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
enum ResponsePayload {
    Pong {
        version: &'static str,
        protocol: u32,
    },
    Indices {
        indices: Vec<usize>,
    },
    Score {
        score: f64,
    },
    Annotated {
        items: Vec<AnnotatedSignal>,
    },
    Clusters {
        clusters: Vec<ClusterOutput>,
    },
    Entities {
        entities: Vec<String>,
    },
    Markdown {
        markdown: String,
    },
    Flag {
        value: bool,
    },
    SanitizedBatch {
        results: Vec<MarkdownBatchResult>,
    },
    ScanOutcomes {
        outcomes: Vec<MarkdownScanOutcome>,
    },
}

fn ok(payload: ResponsePayload) -> Response {
    Response::Ok { payload }
}

fn err_msg(message: impl Into<String>) -> Response {
    Response::Error {
        message: message.into(),
    }
}

fn handle_request(request: Request) -> Response {
    match request {
        Request::Ping => ok(ResponsePayload::Pong {
            version: env!("CARGO_PKG_VERSION"),
            protocol: PROTOCOL_VERSION,
        }),
        Request::DedupeIndices { entries, threshold } => {
            match dedupe_kept_indices(&entries, threshold) {
                Ok(indices) => ok(ResponsePayload::Indices { indices }),
                Err(err) => err_msg(err.to_string()),
            }
        }
        Request::HybridSimilarity { text_a, text_b } => ok(ResponsePayload::Score {
            score: hybrid_similarity(&text_a, &text_b),
        }),
        Request::TokenOverlapRelevance {
            query,
            text,
            hashtags,
        } => {
            let prepared = PreparedQuery::new(&query);
            ok(ResponsePayload::Score {
                score: token_overlap_relevance(&prepared, &text, &hashtags),
            })
        }
        Request::AnnotateStream {
            items,
            ranking_query,
            freshness_mode,
            reference_date,
            max_days,
        } => match annotate_stream(
            &items,
            &ranking_query,
            freshness_mode,
            &reference_date,
            max_days,
        ) {
            Ok(annotated) => ok(ResponsePayload::Annotated { items: annotated }),
            Err(err) => err_msg(err.to_string()),
        },
        Request::ClusterCandidates { candidates, plan } => ok(ResponsePayload::Clusters {
            clusters: cluster_candidates(&candidates, &plan),
        }),
        Request::ExtractEntities { text } => ok(ResponsePayload::Entities {
            entities: extract_text_entities(&text).into_iter().collect(),
        }),
        Request::EntityOverlap { text_a, text_b } => {
            let a = extract_text_entities(&text_a);
            let b = extract_text_entities(&text_b);
            ok(ResponsePayload::Score {
                score: entity_overlap(&a, &b),
            })
        }
        Request::PublicMarkdown { markdown } => ok(ResponsePayload::Markdown {
            markdown: public_markdown(&markdown),
        }),
        Request::HasPrivateCorpus { markdown } => ok(ResponsePayload::Flag {
            value: has_private_corpus(&markdown),
        }),
        Request::IsUnboundedPrivateBrief {
            markdown,
            source_format,
        } => ok(ResponsePayload::Flag {
            value: is_unbounded_private_brief(&markdown, source_format.as_deref()),
        }),
        Request::SanitizeMarkdownBatch { items } => ok(ResponsePayload::SanitizedBatch {
            results: sanitize_markdown_batch(&items),
        }),
        Request::ScanMarkdownFiles { requests } => ok(ResponsePayload::ScanOutcomes {
            outcomes: scan_markdown_files_batch(&requests),
        }),
    }
}

fn run_serve_loop() -> Result<()> {
    let stdin = io::stdin();
    let stdout = io::stdout();
    let mut writer = BufWriter::new(stdout.lock());

    for line_result in stdin.lock().lines() {
        let line = line_result.context("reading request line from stdin")?;
        let trimmed = line.trim();
        if trimmed.is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Request>(trimmed) {
            Ok(req) => handle_request(req),
            Err(err) => Response::Error {
                message: format!("invalid request JSON: {err}"),
            },
        };
        serde_json::to_writer(&mut writer, &response).context("serializing response JSON")?;
        writer.write_all(b"\n").context("writing newline")?;
        writer.flush().context("flushing stdout")?;
    }
    Ok(())
}

fn main() -> Result<()> {
    let mut args = env::args().skip(1);
    match args.next().as_deref() {
        Some("serve") | None => run_serve_loop(),
        Some("--version" | "-V" | "version") => {
            let mut stdout = io::stdout().lock();
            writeln!(
                stdout,
                "last30days-core {} (protocol {})",
                env!("CARGO_PKG_VERSION"),
                PROTOCOL_VERSION
            )
            .context("writing version")?;
            Ok(())
        }
        Some("--help" | "-h" | "help") => {
            let mut stdout = io::stdout().lock();
            writeln!(
                stdout,
                "last30days-core {} - native CPU accelerator for last30days-skill\n\n\
                 Usage:\n  last30days-core serve      Run newline-delimited JSON worker on stdio\n  \
                 last30days-core version    Print binary and protocol version",
                env!("CARGO_PKG_VERSION")
            )
            .context("writing help")?;
            Ok(())
        }
        Some(unknown) => {
            anyhow::bail!("unknown subcommand `{unknown}`; expected `serve`, `version`, or `help`")
        }
    }
}
