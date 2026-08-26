use std::io::{self, BufRead, Write};

use harper_core::linting::{LintGroup, Linter, Suggestion};
use harper_core::parsers::{Markdown, PlainEnglish};
use harper_core::spell::FstDictionary;
use harper_core::{Dialect, Document};
use serde::{Deserialize, Serialize};

const HARPER_VERSION: &str = "2.5.0";

#[derive(Deserialize)]
struct Request {
    text: String,
    #[serde(default = "default_format")]
    format: String,
    #[serde(default = "default_dialect")]
    dialect: String,
    #[serde(default = "default_max_findings")]
    max_findings: usize,
    #[serde(default)]
    include_spelling: bool,
}

#[derive(Serialize)]
struct Finding {
    start: usize,
    end: usize,
    kind: String,
    message: String,
    priority: u8,
    replacements: Vec<String>,
}

#[derive(Serialize)]
struct Response {
    engine: &'static str,
    version: &'static str,
    findings: Vec<Finding>,
}

fn default_format() -> String {
    "markdown".to_owned()
}

fn default_dialect() -> String {
    "en-US".to_owned()
}

fn default_max_findings() -> usize {
    500
}

fn dialect(value: &str) -> Dialect {
    match value {
        "en-GB" => Dialect::British,
        "en-CA" => Dialect::Canadian,
        "en-AU" => Dialect::Australian,
        "en-IN" => Dialect::Indian,
        _ => Dialect::American,
    }
}

fn replacement(suggestion: &Suggestion, problem: &str) -> String {
    match suggestion {
        Suggestion::ReplaceWith(chars) => chars.iter().collect(),
        Suggestion::InsertAfter(chars) => {
            let inserted: String = chars.iter().collect();
            format!("{problem}{inserted}")
        }
        Suggestion::Remove => String::new(),
    }
}

fn run(request: Request, linter: &mut Option<(String, LintGroup)>) -> Response {
    let document = if request.format == "plaintext" {
        Document::new_curated(&request.text, &PlainEnglish)
    } else {
        Document::new_curated(&request.text, &Markdown::default())
    };
    if linter
        .as_ref()
        .is_none_or(|(current, _)| current != &request.dialect)
    {
        *linter = Some((
            request.dialect.clone(),
            LintGroup::new_curated(FstDictionary::curated(), dialect(&request.dialect)),
        ));
    }
    let source: Vec<char> = request.text.chars().collect();
    let findings = linter
        .as_mut()
        .expect("linter was initialized")
        .1
        .lint(&document)
        .into_iter()
        .filter(|lint| request.include_spelling || lint.lint_kind.to_string_key() != "Spelling")
        .take(request.max_findings.min(2_000))
        .map(|lint| {
            let problem: String = lint.span.get_content(&source).iter().collect();
            Finding {
                start: lint.span.start,
                end: lint.span.end,
                kind: lint.lint_kind.to_string_key(),
                message: lint.message,
                priority: lint.priority,
                replacements: lint
                    .suggestions
                    .iter()
                    .map(|suggestion| replacement(suggestion, &problem))
                    .collect(),
            }
        })
        .collect();
    Response {
        engine: "harper",
        version: HARPER_VERSION,
        findings,
    }
}

fn main() {
    let stdin = io::stdin();
    let mut stdout = io::BufWriter::new(io::stdout());
    let mut linter = None;
    for line in stdin.lock().lines() {
        let input = match line {
            Ok(value) => value,
            Err(error) => {
                eprintln!("failed to read request: {error}");
                break;
            }
        };
        let request: Request = match serde_json::from_str(&input) {
            Ok(value) => value,
            Err(error) => {
                eprintln!("invalid request: {error}");
                continue;
            }
        };
        if let Err(error) = serde_json::to_writer(&mut stdout, &run(request, &mut linter)) {
            eprintln!("failed to write response: {error}");
            break;
        }
        if let Err(error) = writeln!(stdout).and_then(|_| stdout.flush()) {
            eprintln!("failed to flush response: {error}");
            break;
        }
    }
}
