// Package main is the entry point for the nfl30 MCP server bundled
// as a .mcpb for Claude Desktop. The server registers a single research
// tool (see internal/tools) and serves it over stdio. See mcp/README.md
// for build and packaging instructions.
package main

import (
	"fmt"
	"os"

	"github.com/mark3labs/mcp-go/server"

	"github.com/cmdashc/last30days-nfl/mcp/internal/tools"
)

// Version is stamped at build time via -ldflags "-X main.Version=<tag>".
// It namespaces the per-user cache directory in internal/engine so multiple
// installed versions can coexist without clobbering each other.
var Version = "dev"

const (
	serverName    = "nfl30"
	serverVersion = "1"
)

func main() {
	s := server.NewMCPServer(
		serverName,
		serverVersion,
		server.WithToolCapabilities(false),
	)

	tools.Register(s, tools.Config{Version: Version})

	if err := server.ServeStdio(s); err != nil {
		fmt.Fprintf(os.Stderr, "nfl30-pp-mcp: %v\n", err)
		os.Exit(1)
	}
}
