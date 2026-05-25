package main

import (
	"fmt"
	"os"
	"time"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/provider"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}

	switch os.Args[1] {
	case "download":
		runDownload(os.Args[2:])
	case "prepare-chunk":
		runPrepareChunk(os.Args[2:])
	case "ingest-chunk":
		runIngestChunk(os.Args[2:])
	case "view":
		runView(os.Args[2:])
	case "status":
		runStatus(os.Args[2:])
	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintf(os.Stderr, `Usage: important <command> [flags]

Commands:
  download       Download emails from all accounts to emails.json
  prepare-chunk  Write next unanalyzed chunk as a prompt file
  ingest-chunk   Parse Claude response and merge into insights.json
  view           View analyzed emails in TUI
  status         Show download/analysis progress
`)
}

func buildProvider(acct config.Account, cfg *config.Config, root string) (provider.EmailProvider, error) {
	switch acct.Provider {
	case "gmail":
		tokenPath := acct.TokenFile
		if tokenPath != "" && tokenPath[0] != '/' {
			tokenPath = root + "/" + tokenPath
		}
		return provider.NewGmailProvider(acct.Name, acct.Email, cfg.GmailClientID, cfg.GmailClientSecret, tokenPath)
	case "zoho":
		return provider.NewZohoProvider(acct.Name, acct.Email, cfg.ZohoAccountID, cfg.ZohoClientID, cfg.ZohoClientSecret, cfg.ZohoRefreshToken), nil
	default:
		return nil, fmt.Errorf("unknown provider: %s", acct.Provider)
	}
}

func formatAge(d time.Duration) string {
	switch {
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
}
