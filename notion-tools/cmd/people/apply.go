package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
	"github.com/jamal/notion-tools/internal/todoist"
)

var todoistPriority = map[string]int{"p1": 4, "p2": 3, "p3": 2, "p4": 1}

func runApply(_ []string) {
	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fatal("config: %v", err)
	}
	if cfg.TodoistAPIToken == "" {
		fatal("TODOIST_API_TOKEN not set")
	}
	ctx := context.Background()
	td := todoist.NewClient(cfg.TodoistAPIToken)

	dataDir, err := pipeline.DataDir(root, handler)
	if err != nil {
		fatal("data dir: %v", err)
	}

	var followups FollowupsManifest
	found, err := pipeline.LoadJSON(followupsPath(dataDir), &followups)
	if err != nil {
		fatal("read followups.json: %v", err)
	}
	if !found || len(followups.Followups) == 0 {
		fmt.Println("people: no followups to apply.")
		return
	}

	applied := loadApplied(dataDir)
	queued, fails := 0, 0

	for _, f := range followups.Followups {
		if item, exists := applied.Items[f.RowID]; exists && item.CompletedAt == nil {
			// Already in the active queue — don't double-queue.
			continue
		}
		priority := todoistPriority[f.Priority]
		if priority == 0 {
			priority = 2 // p3 default for follow-ups
		}
		desc := fmt.Sprintf("Notion · %s → %s\n%s\n\n%sno-edit-below\n%s%s",
			f.DBTitle, f.Name, f.PageURL, "", MarkerPrefix, f.RowID)
		if f.Reasoning != "" {
			desc = f.Reasoning + "\n\n" + desc
		}
		labels := []string{"grp_people"}
		if f.GrpLabel != "" && f.GrpLabel != "grp_people" {
			labels = append(labels, f.GrpLabel)
		}
		due := f.SuggestedDue
		if due == "" {
			due = time.Now().Format("2006-01-02")
		}
		created, err := td.CreateTask(ctx, todoist.Task{
			Content:     f.Content,
			Description: desc,
			Priority:    priority,
			DueDate:     due,
			Labels:      labels,
		})
		if err != nil {
			fmt.Fprintf(os.Stderr, "  FAIL todoist [%s]: %v\n", f.Name, err)
			fails++
			continue
		}
		applied.Items[f.RowID] = AppliedItem{
			RowID:         f.RowID,
			Name:          f.Name,
			TodoistTaskID: created.ID,
			TodoistURL:    created.URL,
			QueuedAt:      time.Now(),
		}
		queued++
		fmt.Printf("  ok %s → %s\n", f.Name, created.URL)
	}
	applied.Touch()
	if err := pipeline.SaveJSON(appliedPath(dataDir), applied); err != nil {
		fatal("save applied.json: %v", err)
	}
	fmt.Printf("\npeople: queued %d, failed %d\n", queued, fails)
}
