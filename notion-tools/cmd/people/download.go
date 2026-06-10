package main

import (
	"context"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/pipeline"
	"github.com/jamal/notion-tools/internal/provider"
	"github.com/jamal/notion-tools/internal/registry"
	"github.com/jamal/notion-tools/internal/todoist"
)

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
	applied := loadApplied(dataDir)

	// --- Pass A: Todoist completion bridge ----------------------------
	if cfg.TodoistAPIToken != "" {
		bridged := runBridge(ctx, n, todoist.NewClient(cfg.TodoistAPIToken), applied)
		if bridged > 0 {
			fmt.Printf("people: bridged %d completion(s) → Notion\n", bridged)
		}
	} else {
		fmt.Fprintln(os.Stderr, "warn: TODOIST_API_TOKEN not set, skipping completion bridge")
	}

	// --- Pass B: pull current people DBs ------------------------------
	tagged, err := registry.Discover(ctx, n, []string{handler})
	if err != nil {
		fatal("registry: %v", err)
	}
	dbs := make([]registry.Tagged, 0, len(tagged))
	for _, t := range tagged {
		if t.ObjectType != "database" {
			fmt.Fprintf(os.Stderr, "  skip %s: people handler expects a database, got %s\n", t.Title, t.ObjectType)
			continue
		}
		dbs = append(dbs, t)
	}
	if len(dbs) == 0 {
		fmt.Println("people: no databases tagged triage:people.")
		_ = pipeline.SaveJSON(rowsPath(dataDir), &DownloadManifest{
			Envelope:     pipeline.NewEnvelope(handler),
			GeneratedFor: time.Now().Format("2006-01-02"),
		})
		return
	}

	today := time.Now()
	manifest := &DownloadManifest{
		Envelope:     pipeline.NewEnvelope(handler),
		GeneratedFor: today.Format("2006-01-02"),
	}

	// Set of rows already in the active queue — skip; they're awaiting completion.
	openQueue := map[string]bool{}
	for rowID, item := range applied.Items {
		if item.CompletedAt == nil {
			openQueue[rowID] = true
		}
	}

	for _, db := range dbs {
		if err := verifySchema(ctx, n, db); err != nil {
			fatal("schema check for %q: %v", db.Title, err)
		}
		rows, err := n.QueryDatabase(ctx, db.ObjectID)
		if err != nil {
			fmt.Fprintf(os.Stderr, "  warn querying %s: %v\n", db.Title, err)
			continue
		}
		for _, r := range rows {
			name, cadence, notes, last, ctxTags := readPersonProps(r.Properties)
			if name == "" {
				continue // skip empty rows
			}
			days, ok := CadenceDays[strings.ToLower(cadence)]
			if !ok {
				// Unknown cadence → skip (don't second-guess the user's data).
				continue
			}
			person := Person{
				RowID:       r.ID,
				DBID:        db.ObjectID,
				DBTitle:     db.Title,
				Name:        name,
				Cadence:     strings.ToLower(cadence),
				CadenceDays: days,
				Context:     ctxTags,
				Notes:       notes,
				PageURL:     r.URL,
			}
			if last != nil {
				ds := daysBetween(*last, today)
				person.LastContacted = last
				person.DaysSince = &ds
				person.OverdueBy = ds - days
			} else {
				// Never contacted → infinite overdue. Use a sentinel.
				person.OverdueBy = days // treat "never" as one full cadence overdue
			}
			isOverdue := person.OverdueBy > 0 || person.LastContacted == nil
			if !isOverdue {
				continue
			}
			if openQueue[r.ID] {
				continue
			}
			manifest.People = append(manifest.People, person)
		}
	}

	if err := pipeline.SaveJSON(rowsPath(dataDir), manifest); err != nil {
		fatal("save rows.json: %v", err)
	}
	fmt.Printf("people: %d overdue person(s) → %s\n", len(manifest.People), rowsPath(dataDir))
}

// verifySchema fails loudly if a tagged DB is missing Name / Last Contacted /
// Cadence, or has them at the wrong type. The user fixes the DB in Notion
// before continuing.
func verifySchema(ctx context.Context, n *provider.Notion, db registry.Tagged) error {
	schema, err := n.GetDatabase(ctx, db.ObjectID)
	if err != nil {
		return err
	}
	required := map[string]string{
		PropName:          "title",
		PropLastContacted: "date",
		PropCadence:       "select",
	}
	for prop, wantType := range required {
		raw, ok := schema.Properties[prop]
		if !ok {
			return fmt.Errorf("missing required property: %q", prop)
		}
		if got := provider.PropertyType(raw); got != wantType {
			return fmt.Errorf("property %q has type %q, expected %q", prop, got, wantType)
		}
	}
	return nil
}

// runBridge scans Todoist completions, finds tasks whose description carries
// our notion-people:{row_id} marker, and patches Notion Last Contacted on
// the matching row. Returns the number of rows bridged.
func runBridge(ctx context.Context, n *provider.Notion, td *todoist.Client, applied *AppliedManifest) int {
	// Build set of task IDs we're waiting on.
	openByTaskID := map[string]string{} // taskID -> rowID
	earliest := time.Now()
	for rowID, item := range applied.Items {
		if item.CompletedAt != nil {
			continue
		}
		openByTaskID[item.TodoistTaskID] = rowID
		if item.QueuedAt.Before(earliest) {
			earliest = item.QueuedAt
		}
	}
	if len(openByTaskID) == 0 {
		return 0
	}

	// One API call: get every completion since the earliest open queue time
	// minus a small buffer for clock skew.
	since := earliest.Add(-1 * time.Hour)
	completed, err := td.ListCompletedSince(ctx, since)
	if err != nil {
		fmt.Fprintf(os.Stderr, "warn: bridge could not list completions: %v\n", err)
		return 0
	}

	bridged := 0
	for _, c := range completed {
		rowID, watching := openByTaskID[c.TaskID]
		if !watching {
			continue
		}
		// Patch Notion Last Contacted = completion date.
		if err := n.PatchPageDate(ctx, rowID, PropLastContacted, c.CompletedAt); err != nil {
			fmt.Fprintf(os.Stderr, "warn: bridge failed to patch %s: %v\n", rowID, err)
			continue
		}
		item := applied.Items[rowID]
		t := c.CompletedAt
		item.CompletedAt = &t
		item.NotionStamped = true
		applied.Items[rowID] = item
		bridged++
	}
	if bridged > 0 {
		applied.Touch()
		// Best-effort save — the caller will save again later in download,
		// but persisting now means a crash between here and downstream
		// work still records the bridge result.
		root := config.ProjectRoot()
		dataDir, _ := pipeline.DataDir(root, handler)
		_ = pipeline.SaveJSON(appliedPath(dataDir), applied)
	}
	return bridged
}

func daysBetween(a, b time.Time) int {
	a = time.Date(a.Year(), a.Month(), a.Day(), 0, 0, 0, 0, time.UTC)
	b = time.Date(b.Year(), b.Month(), b.Day(), 0, 0, 0, 0, time.UTC)
	return int(b.Sub(a).Hours() / 24)
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
