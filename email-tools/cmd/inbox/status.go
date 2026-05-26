package main

import (
	"fmt"
	"os"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/pipeline"
)

func runStatus(args []string) {
	root := config.ProjectRoot()

	manifest, err := pipeline.LoadDownloadManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading emails: %v\n", err)
		os.Exit(1)
	}

	insights, err := pipeline.LoadInsightsManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading insights: %v\n", err)
		os.Exit(1)
	}

	if manifest == nil && insights == nil {
		fmt.Println("No data. Run 'task email:important:download' first.")
		return
	}

	// Download status
	if manifest != nil {
		fmt.Printf("=== Download ===\n")
		fmt.Printf("  Total emails: %d\n", len(manifest.Emails))
		fmt.Printf("  Last updated: %s\n", manifest.UpdatedAt.Format("2006-01-02 15:04:05"))
		fmt.Printf("\n  Per account:\n")
		for _, a := range manifest.Accounts {
			if a.Error != "" {
				fmt.Printf("    %s: ERROR - %s\n", a.Name, a.Error)
			} else {
				fmt.Printf("    %s: %d emails (fetched %s)\n", a.Name, a.Count, a.FetchedAt.Format("15:04:05"))
			}
		}
		fmt.Println()
	}

	// Analysis status
	if manifest != nil {
		total := len(manifest.Emails)
		analyzed := 0
		if insights != nil {
			analyzed = insights.Analyzed
		}

		pct := 0
		if total > 0 {
			pct = analyzed * 100 / total
		}

		fmt.Printf("=== Analysis ===\n")
		fmt.Printf("  Progress: %d/%d (%d%%)\n", analyzed, total, pct)
		if insights != nil {
			fmt.Printf("  Model: %s\n", insights.ModelUsed)
			fmt.Printf("  Last updated: %s\n", insights.UpdatedAt.Format("2006-01-02 15:04:05"))
		}
	}
}
