// Package tools owns the MCP tool surface for last30days.
package tools

import (
	"context"
	"errors"
	"fmt"
	"os"
	"strings"

	mcplib "github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	"github.com/mvanhorn/last30days-skill/mcp/internal/engine"
)

// Config carries the version string used to namespace the per-user cache.
// main passes its ldflags-stamped Version here.
type Config struct {
	Version string
}

// Register adds every tool this server exposes to s. The caller supplies a
// Config so test harnesses can pin a version without touching globals.
func Register(s *server.MCPServer, cfg Config) {
	registerPreflightTool(s, cfg)
	s.AddTool(
		mcplib.NewTool("research",
			mcplib.WithDescription(
				"Research what people are actually saying about any topic in the last 30 days. "+
					"Aggregates Reddit, X, YouTube, Hacker News, Polymarket, GitHub, and the web, "+
					"scored by upvotes, likes, transcripts, and real-money prediction-market odds. "+
					"Returns the engine's compact output for the model to synthesize.",
			),
			mcplib.WithString("topic", mcplib.Required(), mcplib.Description("The subject to research (a person, company, product, event, or general topic).")),
			mcplib.WithString("emit", mcplib.Description("Output shape: 'compact' (default) for inline synthesis or 'html' to save a shareable brief alongside the response.")),
			mcplib.WithBoolean("save", mcplib.Description("Persist the synthesis as a markdown report under ~/Documents/Last30Days/ (or LAST30DAYS_MEMORY_DIR if set).")),
			mcplib.WithReadOnlyHintAnnotation(false),
			mcplib.WithDestructiveHintAnnotation(false),
			mcplib.WithOpenWorldHintAnnotation(true),
		),
		makeResearchHandler(cfg),
	)
}

func makeResearchHandler(cfg Config) server.ToolHandlerFunc {
	return func(ctx context.Context, req mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
		args := req.GetArguments()
		topic, err := requireString(args, "topic")
		if err != nil {
			return mcplib.NewToolResultError(err.Error()), nil
		}

		emit, err := emitArgument(args)
		if err != nil {
			return mcplib.NewToolResultError(err.Error()), nil
		}

		save, err := boolArgument(args, "save")
		if err != nil {
			return mcplib.NewToolResultError(err.Error()), nil
		}

		src, err := engine.EngineFS()
		if err != nil {
			return mcplib.NewToolResultError(fmt.Sprintf("engine source unavailable: %v", err)), nil
		}
		cacheDir, err := engine.EnsureUserCache(src, cfg.Version)
		if err != nil {
			return mcplib.NewToolResultError(fmt.Sprintf(
				"engine extract failed: %v\nhint: set %s to a writable directory if the default cache location is locked down",
				err, engine.CacheEnvOverride,
			)), nil
		}

		runArgs := researchRunArgs(topic, emit, save)

		res, runErr := engine.Run(ctx, engine.RunOptions{
			CacheDir: cacheDir,
			Args:     runArgs,
		})
		if runErr != nil {
			return mcplib.NewToolResultError(formatRunError(runErr, res)), nil
		}
		return mcplib.NewToolResultText(string(res.Stdout)), nil
	}
}

func researchRunArgs(topic, emit string, save bool) []string {
	runArgs := []string{topic, "--emit=" + emit, "--no-browser-cookies"}
	if save {
		runArgs = append(runArgs, "--save-dir", mcpSaveDir())
	}
	return runArgs
}

func mcpSaveDir() string {
	saveDir := os.Getenv("LAST30DAYS_MEMORY_DIR")
	if saveDir == "" {
		return "~/Documents/Last30Days"
	}
	return saveDir
}

func requireString(args map[string]any, name string) (string, error) {
	raw, ok := args[name]
	if !ok {
		return "", fmt.Errorf("%s is required", name)
	}
	value, ok := raw.(string)
	if !ok || strings.TrimSpace(value) == "" {
		return "", fmt.Errorf("%s must be a non-empty string", name)
	}
	return value, nil
}

func emitArgument(args map[string]any) (string, error) {
	raw, ok := args["emit"]
	if !ok {
		return "compact", nil
	}
	value, ok := raw.(string)
	if !ok {
		return "", errors.New("emit must be a string")
	}
	switch value {
	case "":
		return "compact", nil
	case "compact", "html":
		return value, nil
	default:
		return "", fmt.Errorf("emit must be 'compact' or 'html', got %q", value)
	}
}

func boolArgument(args map[string]any, name string) (bool, error) {
	raw, ok := args[name]
	if !ok {
		return false, nil
	}
	value, ok := raw.(bool)
	if !ok {
		return false, fmt.Errorf("%s must be a boolean", name)
	}
	return value, nil
}

// formatRunError flattens engine.Run's distinct error shapes into a single
// user-facing message that includes the relevant stderr context.
func formatRunError(runErr error, res *engine.RunResult) string {
	var msg strings.Builder
	msg.WriteString(runErr.Error())
	if res != nil && len(res.Stderr) > 0 {
		msg.WriteString("\nengine stderr:\n")
		msg.Write(res.Stderr)
	}
	return msg.String()
}
