package main

import (
	"context"
	"fmt"
	"os"
	"time"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
	"github.com/jamal/notion-tools/internal/provider"
	"github.com/jamal/notion-tools/internal/registry"
)

const maxBlockDepth = 3

func runDownload(_ []string) {
	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fatal("config: %v", err)
	}
	if cfg.NotionAPIToken == "" {
		fatal("NOTION_API_TOKEN not set")
	}
	ctx := context.Background()
	n := provider.New(cfg.NotionAPIToken)

	dataDir, err := pipeline.DataDir(root, handler)
	if err != nil {
		fatal("data dir: %v", err)
	}

	// Load applied.json so we can drop blocks we've already queued.
	applied := loadApplied(dataDir)
	alreadyQueued := map[string]bool{}
	for blockID := range applied.Items {
		alreadyQueued[blockID] = true
	}

	tagged, err := registry.Discover(ctx, n, []string{handler})
	if err != nil {
		fatal("registry: %v", err)
	}

	cutoff := time.Now().AddDate(0, 0, -cfg.LookbackDays)
	manifest := &DownloadManifest{
		Envelope:     pipeline.NewEnvelope(handler),
		LookbackDays: cfg.LookbackDays,
	}

	for _, t := range tagged {
		if t.ObjectType != "page" {
			// brain handler only consumes pages; a DB tagged triage:brain
			// is a configuration error — surface it but don't fail.
			fmt.Fprintf(os.Stderr, "  skip %s [%s]: brain handler only processes pages, not databases\n", t.Title, t.ObjectID)
			continue
		}
		todos, err := collectTodos(ctx, n, t.ObjectID, 0, alreadyQueued)
		if err != nil {
			fmt.Fprintf(os.Stderr, "  warn %s: %v\n", t.Title, err)
			continue
		}
		if len(todos) == 0 {
			continue
		}
		// Best-effort last_edited_time: not in the registry hit; we leave
		// it at cutoff so the filter doesn't drop this page.
		manifest.Pages = append(manifest.Pages, DownloadedPage{
			ID:         t.ObjectID,
			Title:      t.Title,
			URL:        t.URL,
			LastEdited: cutoff, // not exposed by registry; placeholder
			TodoBlocks: todos,
		})
	}

	if err := pipeline.SaveJSON(pagesPath(dataDir), manifest); err != nil {
		fatal("save pages.json: %v", err)
	}
	totalTodos := 0
	for _, p := range manifest.Pages {
		totalTodos += len(p.TodoBlocks)
	}
	fmt.Printf("brain: %d page(s) with %d unchecked to-do(s) → %s\n",
		len(manifest.Pages), totalTodos, pagesPath(dataDir))
}

// collectTodos walks a page's blocks recursively up to maxBlockDepth and
// returns every unchecked to_do block that isn't already in alreadyQueued.
func collectTodos(ctx context.Context, n *provider.Notion, parentID string, depth int, alreadyQueued map[string]bool) ([]TodoBlock, error) {
	if depth >= maxBlockDepth {
		return nil, nil
	}
	blocks, err := n.BlockChildren(ctx, parentID)
	if err != nil {
		return nil, err
	}
	var out []TodoBlock
	for _, b := range blocks {
		if alreadyQueued[b.ID] {
			continue
		}
		if td, ok := provider.AsToDo(b); ok && !td.Checked && td.Text != "" {
			out = append(out, TodoBlock{
				ID:      b.ID,
				Text:    td.Text,
				Checked: td.Checked,
				Indent:  depth,
			})
		}
		if b.HasChildren {
			children, err := collectTodos(ctx, n, b.ID, depth+1, alreadyQueued)
			if err != nil {
				return nil, err
			}
			out = append(out, children...)
		}
	}
	return out, nil
}

func loadApplied(dataDir string) *AppliedManifest {
	m := &AppliedManifest{Envelope: pipeline.NewEnvelope(handler), Items: map[string]AppliedItem{}}
	_, err := pipeline.LoadJSON(appliedPath(dataDir), m)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warn: applied.json unreadable, treating as empty: %v\n", err)
		return &AppliedManifest{Envelope: pipeline.NewEnvelope(handler), Items: map[string]AppliedItem{}}
	}
	if m.Items == nil {
		m.Items = map[string]AppliedItem{}
	}
	return m
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "ERROR: "+format+"\n", args...)
	os.Exit(1)
}
