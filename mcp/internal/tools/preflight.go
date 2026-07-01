package tools

import (
	"context"
	"errors"
	"fmt"

	mcplib "github.com/mark3labs/mcp-go/mcp"
	"github.com/mark3labs/mcp-go/server"

	"github.com/mvanhorn/last30days-skill/mcp/internal/engine"
)

func registerPreflightTool(s *server.MCPServer, cfg Config) {
	s.AddTool(
		mcplib.NewTool("preflight",
			mcplib.WithDescription(
				"Safely summarize what last30days would read, write, execute, and contact "+
					"without running research, saving files, or reading browser cookies.",
			),
			mcplib.WithString("format", mcplib.Description("Output shape: 'text' (default) for a concise summary or 'json' for structured details.")),
			mcplib.WithReadOnlyHintAnnotation(true),
			mcplib.WithDestructiveHintAnnotation(false),
			mcplib.WithOpenWorldHintAnnotation(false),
		),
		makePreflightHandler(cfg),
	)
}

func makePreflightHandler(cfg Config) server.ToolHandlerFunc {
	return func(ctx context.Context, req mcplib.CallToolRequest) (*mcplib.CallToolResult, error) {
		format, err := preflightFormatArgument(req.GetArguments())
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

		res, runErr := engine.Run(ctx, engine.RunOptions{
			CacheDir: cacheDir,
			Args:     preflightRunArgs(format),
		})
		if runErr != nil {
			return mcplib.NewToolResultError(formatRunError(runErr, res)), nil
		}
		return mcplib.NewToolResultText(string(res.Stdout)), nil
	}
}

func preflightRunArgs(format string) []string {
	runArgs := []string{"--preflight", "--preflight-report-on-save-dir", mcpSaveDir()}
	if format == "json" {
		runArgs = append(runArgs, "--emit=json")
	}
	return runArgs
}

func preflightFormatArgument(args map[string]any) (string, error) {
	raw, ok := args["format"]
	if !ok {
		return "text", nil
	}
	value, ok := raw.(string)
	if !ok {
		return "", errors.New("format must be a string")
	}
	switch value {
	case "", "text":
		return "text", nil
	case "json":
		return "json", nil
	default:
		return "", fmt.Errorf("format must be 'text' or 'json', got %q", value)
	}
}
