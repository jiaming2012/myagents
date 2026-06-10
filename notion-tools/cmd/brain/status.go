package main

import (
	"fmt"
	"os"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
)

func runStatus(_ []string) {
	root := config.ProjectRoot()
	dataDir, err := pipeline.DataDir(root, handler)
	if err != nil {
		fatal("data dir: %v", err)
	}

	var pages DownloadManifest
	pagesFound, _ := pipeline.LoadJSON(pagesPath(dataDir), &pages)

	var insights InsightsManifest
	insightsFound, _ := pipeline.LoadJSON(insightsPath(dataDir), &insights)

	var applied AppliedManifest
	appliedFound, _ := pipeline.LoadJSON(appliedPath(dataDir), &applied)

	if !pagesFound && !insightsFound && !appliedFound {
		fmt.Println("brain: no state. Run `task notion:brain:download` first.")
		return
	}

	if pagesFound {
		totalTodos := 0
		for _, p := range pages.Pages {
			totalTodos += len(p.TodoBlocks)
		}
		fmt.Printf("=== download ===\n")
		fmt.Printf("  pages with todos: %d (%d unchecked todos)\n", len(pages.Pages), totalTodos)
		fmt.Printf("  updated:          %s\n", pages.UpdatedAt.Format("2006-01-02 15:04:05"))
		fmt.Println()
	}

	if insightsFound {
		fmt.Printf("=== analyze ===\n")
		fmt.Printf("  insights queued: %d\n", len(insights.Insights))
		fmt.Printf("  updated:         %s\n", insights.UpdatedAt.Format("2006-01-02 15:04:05"))
		fmt.Println()
	}

	if appliedFound {
		stamped, unstamped := 0, 0
		for _, it := range applied.Items {
			if it.StampedAt != nil {
				stamped++
			} else {
				unstamped++
			}
		}
		fmt.Printf("=== apply ===\n")
		fmt.Printf("  queued to todoist: %d  (stamped: %d, awaiting stamp: %d)\n",
			len(applied.Items), stamped, unstamped)
		fmt.Printf("  updated:           %s\n", applied.UpdatedAt.Format("2006-01-02 15:04:05"))
	}

	_ = os.Stderr
}
