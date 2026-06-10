// Command registry lists every Notion object opted into a triage:<handler>
// tag. Read-only; intended as the first sanity check before running any
// handler pipeline.
package main

import (
	"context"
	"fmt"
	"os"
	"sort"

	"github.com/jamal/notion-tools/internal/config"
	"github.com/jamal/notion-tools/internal/provider"
	"github.com/jamal/notion-tools/internal/registry"
)

func main() {
	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "config: %v\n", err)
		os.Exit(1)
	}
	if cfg.NotionAPIToken == "" {
		fmt.Fprintln(os.Stderr, "ERROR: NOTION_API_TOKEN not set (see notion-tools/.env.example)")
		os.Exit(1)
	}
	n := provider.New(cfg.NotionAPIToken)

	tagged, err := registry.Discover(context.Background(), n, nil)
	if err != nil {
		fmt.Fprintf(os.Stderr, "discover: %v\n", err)
		os.Exit(1)
	}
	if len(tagged) == 0 {
		fmt.Println("No triage:* tags found.")
		fmt.Println()
		fmt.Println("To opt a Notion object in, add `triage:brain` or `triage:people`")
		fmt.Println("to its title, or to a Tags / multi_select property.")
		return
	}

	sort.Slice(tagged, func(i, j int) bool {
		if tagged[i].Handler != tagged[j].Handler {
			return tagged[i].Handler < tagged[j].Handler
		}
		return tagged[i].Title < tagged[j].Title
	})

	currentHandler := ""
	for _, t := range tagged {
		if t.Handler != currentHandler {
			if currentHandler != "" {
				fmt.Println()
			}
			fmt.Printf("=== %s ===\n", t.Handler)
			currentHandler = t.Handler
		}
		fmt.Printf("  %-9s %s\n", "["+t.ObjectType+"]", t.Title)
		fmt.Printf("            %s  (%s)\n", t.URL, t.Source)
	}
	fmt.Printf("\n%d object(s) discovered.\n", len(tagged))
}
