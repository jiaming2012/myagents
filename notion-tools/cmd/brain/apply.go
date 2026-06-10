package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
	"github.com/jamal/notion-tools/internal/provider"
	"github.com/jamal/notion-tools/internal/todoist"
)

// Todoist API priority codes for "p1".."p4" labels (Todoist's quirk: 4=p1 highest).
var todoistPriority = map[string]int{"p1": 4, "p2": 3, "p3": 2, "p4": 1}

func runApply(_ []string) {
	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fatal("config: %v", err)
	}
	if cfg.NotionAPIToken == "" || cfg.TodoistAPIToken == "" {
		fatal("NOTION_API_TOKEN and TODOIST_API_TOKEN must both be set")
	}
	ctx := context.Background()
	n := provider.New(cfg.NotionAPIToken)
	td := todoist.NewClient(cfg.TodoistAPIToken)

	dataDir, err := pipeline.DataDir(root, handler)
	if err != nil {
		fatal("data dir: %v", err)
	}

	var insights InsightsManifest
	found, err := pipeline.LoadJSON(insightsPath(dataDir), &insights)
	if err != nil {
		fatal("read insights.json: %v", err)
	}
	if !found || len(insights.Insights) == 0 {
		fmt.Println("brain: no insights to apply.")
		return
	}

	applied := loadApplied(dataDir)
	queued, stamped, fails := 0, 0, 0

	for _, ins := range insights.Insights {
		if _, already := applied.Items[ins.SourceBlockID]; already {
			continue
		}
		// 1. Create Todoist task
		priority := todoistPriority[ins.Priority]
		if priority == 0 {
			priority = 1 // p4 default
		}
		desc := fmt.Sprintf("Notion · %s\n%s", ins.SourcePageTitle, ins.SourcePageURL)
		labels := []string{"grp_brain"}
		if ins.GrpLabel != "" && ins.GrpLabel != "grp_brain" {
			labels = append(labels, ins.GrpLabel)
		}
		created, err := td.CreateTask(ctx, todoist.Task{
			Content:     ins.Content,
			Description: desc,
			Priority:    priority,
			DueDate:     ins.SuggestedDue,
			Labels:      labels,
		})
		if err != nil {
			fmt.Fprintf(os.Stderr, "  FAIL todoist [%s]: %v\n", short(ins.Content, 40), err)
			fails++
			continue
		}
		queued++

		item := AppliedItem{
			SourceBlockID: ins.SourceBlockID,
			SourcePageID:  ins.SourcePageID,
			TodoistTaskID: created.ID,
			TodoistURL:    created.URL,
			QueuedAt:      time.Now(),
		}

		// 2. Stamp the Notion source block. Failure here doesn't roll back
		//    the Todoist task — we just record stamp_error and the next
		//    apply run will retry only the stamp.
		stampedText := fmt.Sprintf("[queued: %s] %s", created.URL, originalText(ins))
		if err := n.PatchToDoText(ctx, ins.SourceBlockID, stampedText, false); err != nil {
			item.StampError = err.Error()
			fmt.Fprintf(os.Stderr, "  warn stamp [%s]: %v\n", short(ins.Content, 40), err)
		} else {
			now := time.Now()
			item.StampedAt = &now
			item.OriginalText = stampedText
			stamped++
		}
		applied.Items[ins.SourceBlockID] = item
		fmt.Printf("  ok [%s] → %s\n", short(ins.Content, 60), created.URL)
	}

	// Retry stamps for items previously queued with stamp_error.
	for blockID, item := range applied.Items {
		if item.StampError == "" || item.StampedAt != nil {
			continue
		}
		// We don't know the original text; we re-stamp with just the marker.
		stampedText := fmt.Sprintf("[queued: %s] (text recovered after stamp retry)", item.TodoistURL)
		if err := n.PatchToDoText(ctx, blockID, stampedText, false); err != nil {
			continue
		}
		now := time.Now()
		item.StampedAt = &now
		item.StampError = ""
		item.OriginalText = stampedText
		applied.Items[blockID] = item
		stamped++
		fmt.Printf("  retry-stamp ok → %s\n", item.TodoistURL)
	}

	applied.Touch()
	if err := pipeline.SaveJSON(appliedPath(dataDir), applied); err != nil {
		fatal("save applied.json: %v", err)
	}
	fmt.Printf("\nbrain: queued %d, stamped %d, failed %d\n", queued, stamped, fails)
}

// originalText would ideally come from pages.json (the unstamped to-do text).
// For v1 we use the Claude-rewritten content — preserves the imperative form
// and keeps the stamp readable. If the user wants the verbatim Notion text
// preserved in-place, that's a small v2 improvement: keep the original
// rich_text array and re-emit it with the queued prefix prepended.
func originalText(ins Insight) string {
	return ins.Content
}

func short(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "…"
}
