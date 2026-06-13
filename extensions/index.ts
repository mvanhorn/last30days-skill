import { access, mkdir, writeFile } from "node:fs/promises";
import { constants } from "node:fs";
import { homedir } from "node:os";
import { dirname, join } from "node:path";
import { fileURLToPath } from "node:url";
import {
  DEFAULT_MAX_BYTES,
  DEFAULT_MAX_LINES,
  formatSize,
  truncateHead,
  type ExtensionAPI,
} from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Box, Text } from "@earendil-works/pi-tui";
import { Type } from "typebox";

const extensionDir = dirname(fileURLToPath(import.meta.url));
const repoRoot = dirname(extensionDir);
const scriptPath = join(repoRoot, "skills", "last30days", "scripts", "last30days.py");
const configDir = join(homedir(), ".config", "last30days");
const configPath = join(configDir, ".env");
const saveDir = join(homedir(), "Documents", "Last30Days");

type ResearchDepth = "quick" | "balanced" | "deep";
type ResearchEmit = "compact" | "json" | "md" | "html";
type WebBackend = "auto" | "brave" | "exa" | "serper" | "parallel" | "none";

type PythonCommand = {
  command: string;
  prefixArgs: string[];
  label: string;
};

const depthSchema = StringEnum(["quick", "balanced", "deep"] as const);
const emitSchema = StringEnum(["compact", "json", "md", "html"] as const);
const webBackendSchema = StringEnum(["auto", "brave", "exa", "serper", "parallel", "none"] as const);

const configTemplate = `# /last30days configuration for pi
# Add only the sources you want. Leave blank to keep zero-config sources.

# X / Twitter (pick one):
# FROM_BROWSER=auto
# XAI_API_KEY=
# AUTH_TOKEN=
# CT0=

# TikTok, Instagram, Threads, Pinterest, YouTube/TikTok comments:
# SCRAPECREATORS_API_KEY=
# INCLUDE_SOURCES=tiktok,instagram,threads
# EXCLUDE_SOURCES=

# Bluesky:
# BSKY_HANDLE=you.bsky.social
# BSKY_APP_PASSWORD=

# Web and Perplexity / Deep Research:
# BRAVE_API_KEY=
# EXA_API_KEY=
# SERPER_API_KEY=
# PARALLEL_API_KEY=
# OPENROUTER_API_KEY=

# Optional local save location override:
# LAST30DAYS_MEMORY_DIR=${saveDir}

SETUP_COMPLETE=true
`;

async function findPython(pi: ExtensionAPI, signal?: AbortSignal): Promise<PythonCommand> {
  const candidates = ["python3.14", "python3.13", "python3.12", "python3"];

  for (const candidate of candidates) {
    try {
      const result = await pi.exec(
        candidate,
        ["-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
        { signal, timeout: 10_000 },
      );
      if (result.code === 0) return { command: candidate, prefixArgs: [], label: candidate };
    } catch {
      // keep trying
    }
  }

  try {
    const result = await pi.exec(
      "uv",
      ["run", "--project", repoRoot, "python", "-c", "import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)"],
      { signal, timeout: 60_000 },
    );
    if (result.code === 0) return { command: "uv", prefixArgs: ["run", "--project", repoRoot, "python"], label: "uv run python" };
  } catch {
    // fall through
  }

  throw new Error("last30days needs Python 3.12+ or uv. I checked python3.14, python3.13, python3.12, python3, and uv run python.");
}

async function ensureConfigFile(): Promise<void> {
  await mkdir(configDir, { recursive: true });
  try {
    await access(configPath, constants.F_OK);
  } catch {
    await writeFile(configPath, configTemplate, { encoding: "utf8", mode: 0o600 });
  }
}

async function openPath(pi: ExtensionAPI, path: string): Promise<void> {
  if (process.platform === "darwin") {
    await pi.exec("open", [path], { timeout: 10_000 });
    return;
  }
  if (process.platform === "win32") {
    await pi.exec("cmd", ["/c", "start", "", path], { timeout: 10_000 });
    return;
  }
  await pi.exec("xdg-open", [path], { timeout: 10_000 });
}

function depthToFlags(depth: ResearchDepth): string[] {
  if (depth === "quick") return ["--quick"];
  if (depth === "deep") return ["--deep"];
  return [];
}

function tokenizeCommandArgs(input: string): string[] {
  const tokens: string[] = [];
  let current = "";
  let quote: '"' | "'" | undefined;
  let escaping = false;

  for (const char of input.trim()) {
    if (escaping) {
      current += char;
      escaping = false;
      continue;
    }
    if (char === "\\" && quote !== "'") {
      escaping = true;
      continue;
    }
    if ((char === '"' || char === "'") && quote === undefined) {
      quote = char;
      continue;
    }
    if (char === quote) {
      quote = undefined;
      continue;
    }
    if (/\s/.test(char) && quote === undefined) {
      if (current) tokens.push(current);
      current = "";
      continue;
    }
    current += char;
  }

  if (current) tokens.push(current);
  return tokens;
}

function parseDepth(value: string): ResearchDepth | undefined {
  if (value === "quick" || value === "balanced" || value === "deep") return value;
  return undefined;
}

function parseEmit(value: string): ResearchEmit | undefined {
  if (value === "compact" || value === "json" || value === "md" || value === "html") return value;
  return undefined;
}

function parseWebBackend(value: string): WebBackend | undefined {
  if (value === "auto" || value === "brave" || value === "exa" || value === "serper" || value === "parallel" || value === "none") return value;
  return undefined;
}

function parseDays(value: string | undefined): number | undefined {
  const parsed = Number(value);
  if (Number.isFinite(parsed) && parsed >= 1 && parsed <= 365) return parsed;
  return undefined;
}

function parseCompetitorCount(value: string | undefined): number | undefined {
  if (value === undefined || value === "") return 2;
  const parsed = Number(value);
  if (Number.isFinite(parsed) && parsed >= 1 && parsed <= 6) return parsed;
  return undefined;
}

type ParsedCommandArgs = {
  topic: string;
  depth: ResearchDepth;
  days?: number;
  autoResolve: boolean;
  emit: ResearchEmit;
  search?: string;
  webBackend?: WebBackend;
  competitors?: number;
};

function parseCommandArgs(input: string): ParsedCommandArgs {
  const parts = tokenizeCommandArgs(input);
  const topicParts: string[] = [];
  let depth: ResearchDepth = "balanced";
  let days: number | undefined;
  let autoResolve = true;
  let emit: ResearchEmit = "compact";
  let search: string | undefined;
  let webBackend: WebBackend | undefined;
  let competitors: number | undefined;

  for (let i = 0; i < parts.length; i++) {
    const part = parts[i];
    if (part === "--quick" || part === "--balanced" || part === "--deep") {
      depth = parseDepth(part.slice(2)) ?? depth;
      continue;
    }
    if (part === "--depth" && i + 1 < parts.length) {
      depth = parseDepth(parts[i + 1]) ?? depth;
      i += 1;
      continue;
    }
    if (part.startsWith("--depth=")) {
      depth = parseDepth(part.slice("--depth=".length)) ?? depth;
      continue;
    }
    if ((part === "--days" || part === "--lookback" || part === "--lookback-days") && i + 1 < parts.length) {
      days = parseDays(parts[i + 1]) ?? days;
      i += 1;
      continue;
    }
    if (part.startsWith("--days=")) {
      days = parseDays(part.slice("--days=".length)) ?? days;
      continue;
    }
    if (part.startsWith("--lookback=")) {
      days = parseDays(part.slice("--lookback=".length)) ?? days;
      continue;
    }
    if (part.startsWith("--lookback-days=")) {
      days = parseDays(part.slice("--lookback-days=".length)) ?? days;
      continue;
    }
    if (part === "--emit" && i + 1 < parts.length) {
      emit = parseEmit(parts[i + 1]) ?? emit;
      i += 1;
      continue;
    }
    if (part.startsWith("--emit=")) {
      emit = parseEmit(part.slice("--emit=".length)) ?? emit;
      continue;
    }
    if (part === "--search" && i + 1 < parts.length) {
      search = parts[i + 1];
      i += 1;
      continue;
    }
    if (part.startsWith("--search=")) {
      search = part.slice("--search=".length);
      continue;
    }
    if (part === "--web-backend" && i + 1 < parts.length) {
      webBackend = parseWebBackend(parts[i + 1]) ?? webBackend;
      i += 1;
      continue;
    }
    if (part.startsWith("--web-backend=")) {
      webBackend = parseWebBackend(part.slice("--web-backend=".length)) ?? webBackend;
      continue;
    }
    if (part === "--auto-resolve") {
      autoResolve = true;
      continue;
    }
    if (part === "--no-auto-resolve") {
      autoResolve = false;
      continue;
    }
    if (part === "--competitors") {
      competitors = parseCompetitorCount(parts[i + 1]?.startsWith("--") ? undefined : parts[i + 1]) ?? competitors;
      if (parts[i + 1] && !parts[i + 1].startsWith("--")) i += 1;
      continue;
    }
    if (part.startsWith("--competitors=")) {
      competitors = parseCompetitorCount(part.slice("--competitors=".length)) ?? competitors;
      continue;
    }
    topicParts.push(part);
  }

  return { topic: topicParts.join(" ").trim(), depth, days, autoResolve, emit, search, webBackend, competitors };
}

function summarizeDiagnosis(data: any): string {
  const available = Array.isArray(data?.available_sources) && data.available_sources.length > 0
    ? data.available_sources.join(", ")
    : "none";

  const hints: string[] = [];
  if (!Array.isArray(data?.available_sources) || !data.available_sources.includes("x")) {
    hints.push("Unlock X/Twitter by logging into x.com or adding XAI_API_KEY or AUTH_TOKEN + CT0.");
  }
  if (!Array.isArray(data?.available_sources) || !data.available_sources.includes("digg")) {
    hints.push("Install digg-pp-cli to add Digg AI-1000 story clusters without X auth.");
  }
  if (!Array.isArray(data?.available_sources) || !data.available_sources.includes("youtube")) {
    hints.push("Install yt-dlp to enable YouTube search and transcripts.");
  }
  if (!data?.has_scrapecreators) {
    hints.push("Add SCRAPECREATORS_API_KEY for TikTok, Instagram, Threads, Pinterest, and comment sources.");
  }
  if (!data?.native_web_backend) {
    hints.push("Add BRAVE_API_KEY, EXA_API_KEY, SERPER_API_KEY, or PARALLEL_API_KEY for stronger auto-resolve / web coverage.");
  }

  const lines = [
    `Available sources: ${available}`,
    `GitHub: ${data?.has_github ? "yes" : "no"}`,
    `X authenticated: ${data?.bird_authenticated ? "yes" : "no"}`,
    `ScrapeCreators: ${data?.has_scrapecreators ? "yes" : "no"}`,
    `Web backend: ${data?.native_web_backend ?? "none"}`,
    `Config file: ${configPath}`,
  ];

  if (hints.length > 0) {
    lines.push("", "Next unlocks:");
    for (const hint of hints) lines.push(`- ${hint}`);
  }

  return lines.join("\n");
}

function formatToolText(text: string) {
  const truncation = truncateHead(text, {
    maxBytes: DEFAULT_MAX_BYTES,
    maxLines: DEFAULT_MAX_LINES,
  });

  if (!truncation.truncated) {
    return { text, truncation: undefined };
  }

  const notice = truncation.truncatedBy === "lines"
    ? `[last30days output truncated: showing ${truncation.outputLines} of ${truncation.totalLines} lines. Full report files are saved under ${saveDir}.]`
    : `[last30days output truncated: showing ${formatSize(truncation.outputBytes)} of ${formatSize(truncation.totalBytes)} (${formatSize(DEFAULT_MAX_BYTES)} tool-output limit). Full report files are saved under ${saveDir}.]`;

  return {
    text: `${truncation.content.trimEnd()}\n\n${notice}`,
    truncation,
  };
}

type ResearchOptions = {
  topic: string;
  depth?: ResearchDepth;
  days?: number;
  autoResolve?: boolean;
  emit?: ResearchEmit;
  search?: string;
  webBackend?: WebBackend;
  competitors?: number;
};

function buildResearchArgs(options: ResearchOptions): string[] {
  const depth = options.depth ?? "balanced";
  const emit = options.emit ?? "compact";
  const args = [
    scriptPath,
    options.topic,
    "--emit",
    emit,
    "--save-dir",
    saveDir,
    "--save-suffix",
    "pi",
    ...depthToFlags(depth),
  ];

  if (options.days) args.push("--days", String(options.days));
  if (options.search) args.push("--search", options.search);
  if (options.webBackend) args.push("--web-backend", options.webBackend);
  if (options.autoResolve ?? true) args.push("--auto-resolve");
  if (options.competitors) args.push("--competitors", String(options.competitors));
  return args;
}

async function runResearch(pi: ExtensionAPI, options: ResearchOptions, signal?: AbortSignal) {
  const python = await findPython(pi, signal);
  await mkdir(saveDir, { recursive: true });

  const result = await pi.exec(python.command, [...python.prefixArgs, ...buildResearchArgs(options)], {
    signal,
    timeout: options.depth === "deep" || options.competitors ? 900_000 : 420_000,
  });

  const stdout = result.stdout.trim();
  const stderr = result.stderr.trim();
  if (result.code !== 0) {
    throw new Error(stderr || stdout || "last30days failed");
  }

  return {
    text: stderr ? `${stderr}\n\n${stdout}` : stdout,
    details: {
      topic: options.topic,
      depth: options.depth ?? "balanced",
      days: options.days ?? 30,
      emit: options.emit ?? "compact",
      search: options.search,
      webBackend: options.webBackend,
      autoResolve: options.autoResolve ?? true,
      competitors: options.competitors,
      python: python.label,
      scriptPath,
      saveDir,
      configPath,
    },
  };
}

async function runDiagnose(pi: ExtensionAPI, signal?: AbortSignal) {
  const python = await findPython(pi, signal);
  const result = await pi.exec(python.command, [...python.prefixArgs, scriptPath, "--diagnose"], {
    signal,
    timeout: 60_000,
  });

  const stdout = result.stdout.trim();
  const stderr = result.stderr.trim();
  if (result.code !== 0) {
    throw new Error(stderr || stdout || "last30days diagnose failed");
  }

  const data = JSON.parse(stdout);
  return {
    text: summarizeDiagnosis(data),
    details: { ...data, configPath, saveDir, python: python.label },
  };
}

export default function last30daysPiBridge(pi: ExtensionAPI) {
  pi.registerMessageRenderer("last30days-report", (message, _options, theme) => {
    const box = new Box(1, 1, (text) => theme.bg("customMessageBg", text));
    const header = theme.fg("accent", theme.bold("last30days"));
    box.addChild(new Text(`${header}\n\n${message.content}`, 0, 0));
    return box;
  });

  pi.registerMessageRenderer("last30days-doctor", (message, _options, theme) => {
    const box = new Box(1, 1, (text) => theme.bg("customMessageBg", text));
    const header = theme.fg("warning", theme.bold("last30days doctor"));
    box.addChild(new Text(`${header}\n\n${message.content}`, 0, 0));
    return box;
  });

  pi.registerTool({
    name: "last30days_research",
    label: "Last30Days Research",
    description:
      "Run the local last30days engine to research what people have been saying about a topic in the last 30 days across Reddit, X, YouTube, TikTok, Hacker News, Polymarket, GitHub, Digg, and optional web/social sources. Output is truncated to pi's default tool-output budget; full reports are saved locally.",
    promptSnippet:
      "Research current discourse, recommendations, comparisons, and trend signals across last30days social/news sources.",
    promptGuidelines: [
      "Use last30days_research when the user wants current last-30-days discourse, sentiment, recommendations, trend signals, or comparisons.",
      "Prefer last30days_research depth=quick for a fast pulse check, balanced for normal use, and deep for exhaustive research.",
      "Use last30days_diagnose when last30days_research results seem sparse or the user asks about setup.",
    ],
    parameters: Type.Object({
      topic: Type.String({ description: "Topic to research" }),
      depth: Type.Optional(depthSchema),
      days: Type.Optional(Type.Number({ description: "Lookback window in days", minimum: 1, maximum: 365 })),
      emit: Type.Optional(emitSchema),
      search: Type.Optional(Type.String({ description: "Optional comma-separated source list, e.g. reddit,youtube,github" })),
      webBackend: Type.Optional(webBackendSchema),
      autoResolve: Type.Optional(Type.Boolean({ description: "Use last30days auto-resolve for platforms without native web search", default: true })),
      competitors: Type.Optional(Type.Number({ description: "Optional number of competitors to auto-discover for comparison mode, 1..6", minimum: 1, maximum: 6 })),
    }),
    async execute(_toolCallId, params, signal, onUpdate) {
      const depth = (params.depth ?? "balanced") as ResearchDepth;
      onUpdate?.({ content: [{ type: "text", text: `Running last30days (${depth}) for: ${params.topic}` }] });

      const result = await runResearch(
        pi,
        {
          topic: params.topic,
          depth,
          days: params.days,
          emit: params.emit as ResearchEmit | undefined,
          search: params.search,
          webBackend: params.webBackend as WebBackend | undefined,
          autoResolve: params.autoResolve,
          competitors: params.competitors,
        },
        signal,
      );
      const toolText = formatToolText(result.text);
      return {
        content: [{ type: "text", text: toolText.text }],
        details: { ...result.details, truncation: toolText.truncation },
      };
    },
  });

  pi.registerTool({
    name: "last30days_diagnose",
    label: "Last30Days Diagnose",
    description: "Check which last30days sources are currently available and explain what to configure next.",
    promptSnippet: "Inspect current last30days setup and source availability.",
    promptGuidelines: ["Use last30days_diagnose when the user asks about setup, missing sources, or why a last30days result seems sparse."],
    parameters: Type.Object({}),
    async execute(_toolCallId, _params, signal) {
      const result = await runDiagnose(pi, signal);
      return { content: [{ type: "text", text: result.text }], details: result.details };
    },
  });

  pi.registerCommand("last30days", {
    description: "Research a topic with the local last30days engine",
    handler: async (args, ctx) => {
      const parsed = parseCommandArgs(args);
      if (!parsed.topic) {
        ctx.ui.notify("Usage: /last30days <topic> [--quick|--balanced|--deep] [--days N] [--emit html]", "warning");
        return;
      }
      if (!ctx.isIdle()) {
        ctx.ui.notify("Agent is busy. Wait for the current turn to finish.", "warning");
        return;
      }

      ctx.ui.notify(`Researching ${parsed.topic}...`, "info");
      ctx.ui.setStatus("last30days", `researching ${parsed.topic}`);
      try {
        const result = await runResearch(pi, parsed);
        pi.sendMessage({ customType: "last30days-report", content: result.text, display: true, details: result.details });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.ui.notify(`last30days failed: ${message}`, "error");
      } finally {
        ctx.ui.setStatus("last30days", undefined);
      }
    },
  });

  pi.registerCommand("last30days-doctor", {
    description: "Explain which last30days sources are available and what to configure next",
    handler: async (_args, ctx) => {
      if (!ctx.isIdle()) {
        ctx.ui.notify("Agent is busy. Wait for the current turn to finish.", "warning");
        return;
      }

      ctx.ui.notify("Checking last30days setup...", "info");
      ctx.ui.setStatus("last30days", "checking setup");
      try {
        const result = await runDiagnose(pi);
        pi.sendMessage({ customType: "last30days-doctor", content: result.text, display: true, details: result.details });
      } catch (error) {
        const message = error instanceof Error ? error.message : String(error);
        ctx.ui.notify(`last30days doctor failed: ${message}`, "error");
      } finally {
        ctx.ui.setStatus("last30days", undefined);
      }
    },
  });

  pi.registerCommand("last30days-config", {
    description: "Open ~/.config/last30days/.env for manual setup",
    handler: async (_args, ctx) => {
      try {
        await ensureConfigFile();
        await openPath(pi, configPath);
        ctx.ui.notify(`Opened ${configPath}`, "info");
      } catch {
        ctx.ui.notify(`Could not open config automatically. Edit ${configPath}`, "warning");
      }
    },
  });

  pi.registerCommand("last30days-open", {
    description: "Open the local last30days report directory",
    handler: async (_args, ctx) => {
      try {
        await mkdir(saveDir, { recursive: true });
        await openPath(pi, saveDir);
        ctx.ui.notify(`Opened ${saveDir}`, "info");
      } catch {
        ctx.ui.notify(`Could not open report directory automatically. Open ${saveDir}`, "warning");
      }
    },
  });

  pi.registerCommand("last30days-skill", {
    description: "Run the pi last30days skill explicitly",
    handler: async (args, ctx) => {
      if (!ctx.isIdle()) {
        ctx.ui.notify("Agent is busy. Wait for the current turn to finish.", "warning");
        return;
      }
      pi.sendUserMessage(`/skill:last30days ${args.trim()}`.trim());
    },
  });
}
