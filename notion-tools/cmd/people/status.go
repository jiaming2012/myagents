package main

import (
	"fmt"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
)

func runStatus(_ []string) {
	root := config.ProjectRoot()
	dataDir, err := pipeline.DataDir(root, handler)
	if err != nil {
		fatal("data dir: %v", err)
	}

	var rows DownloadManifest
	rowsFound, _ := pipeline.LoadJSON(rowsPath(dataDir), &rows)

	var followups FollowupsManifest
	followupsFound, _ := pipeline.LoadJSON(followupsPath(dataDir), &followups)

	var applied AppliedManifest
	appliedFound, _ := pipeline.LoadJSON(appliedPath(dataDir), &applied)

	if !rowsFound && !followupsFound && !appliedFound {
		fmt.Println("people: no state. Run `task notion:people:download` first.")
		return
	}

	if rowsFound {
		fmt.Printf("=== download ===\n")
		fmt.Printf("  overdue people: %d\n", len(rows.People))
		fmt.Printf("  generated for:  %s\n", rows.GeneratedFor)
		fmt.Printf("  updated:        %s\n", rows.UpdatedAt.Format("2006-01-02 15:04:05"))
		fmt.Println()
	}

	if followupsFound {
		fmt.Printf("=== analyze ===\n")
		fmt.Printf("  followups proposed: %d\n", len(followups.Followups))
		fmt.Printf("  updated:            %s\n", followups.UpdatedAt.Format("2006-01-02 15:04:05"))
		fmt.Println()
	}

	if appliedFound {
		open, done := 0, 0
		for _, it := range applied.Items {
			if it.CompletedAt == nil {
				open++
			} else {
				done++
			}
		}
		fmt.Printf("=== apply ===\n")
		fmt.Printf("  active queue: %d  (completed + bridged: %d)\n", open, done)
		fmt.Printf("  updated:      %s\n", applied.UpdatedAt.Format("2006-01-02 15:04:05"))
	}
}
