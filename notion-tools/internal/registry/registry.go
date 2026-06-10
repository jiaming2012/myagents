// Package registry discovers Notion objects opted into a triage:<handler>
// tag. The tag can live in the page/DB title or in any multi-select property
// (e.g. Tags). Discovery is the single bottleneck between "what's in Notion"
// and "what the tool will touch."
package registry

import (
	"context"
	"encoding/json"
	"strings"

	"github.com/jamal/notion-tools/internal/provider"
)

const TagPrefix = "triage:"

// Tagged is one opted-in Notion object.
type Tagged struct {
	ObjectID   string `json:"object_id"`
	ObjectType string `json:"object_type"` // "page" | "database"
	Handler    string `json:"handler"`     // "brain", "people", ...
	Title      string `json:"title"`
	URL        string `json:"url"`
	Source     string `json:"source"`      // "title" | "multi_select:<prop>"
}

// Discover walks the Notion workspace and returns every page/database
// carrying a `triage:<handler>` tag where handler is in want. If want is
// empty, returns objects for all handler tags found.
func Discover(ctx context.Context, n *provider.Notion, want []string) ([]Tagged, error) {
	hits, err := n.SearchAll(ctx, "")
	if err != nil {
		return nil, err
	}
	wantSet := map[string]struct{}{}
	for _, w := range want {
		wantSet[w] = struct{}{}
	}
	keep := func(handler string) bool {
		if len(wantSet) == 0 {
			return true
		}
		_, ok := wantSet[handler]
		return ok
	}

	var out []Tagged
	for _, h := range hits {
		title := provider.PageTitle(h)
		if handler := extractHandler(title); handler != "" && keep(handler) {
			out = append(out, Tagged{
				ObjectID:   h.ID,
				ObjectType: h.Object,
				Handler:    handler,
				Title:      title,
				URL:        h.URL,
				Source:     "title",
			})
			continue
		}
		// Walk page properties for a multi_select containing a triage:* tag.
		if h.Object == "page" && len(h.Properties) > 0 {
			if handler, propName := extractFromMultiSelect(h.Properties); handler != "" && keep(handler) {
				out = append(out, Tagged{
					ObjectID:   h.ID,
					ObjectType: h.Object,
					Handler:    handler,
					Title:      title,
					URL:        h.URL,
					Source:     "multi_select:" + propName,
				})
			}
		}
	}
	return out, nil
}

// extractHandler returns the handler name from a title like
// "Friends DB [triage:people]" or "triage:brain — Yumyum menu". Match is
// case-insensitive on the prefix.
func extractHandler(title string) string {
	_, rest, ok := strings.Cut(strings.ToLower(title), TagPrefix)
	if !ok {
		return ""
	}
	end := len(rest)
	for i, r := range rest {
		if !isHandlerChar(r) {
			end = i
			break
		}
	}
	if end == 0 {
		return ""
	}
	return rest[:end]
}

func isHandlerChar(r rune) bool {
	return (r >= 'a' && r <= 'z') || (r >= '0' && r <= '9') || r == '_'
}

// extractFromMultiSelect scans a page's properties for any multi_select
// property whose options contain a triage:* tag. Returns the handler name
// plus the property it was found in.
func extractFromMultiSelect(props json.RawMessage) (string, string) {
	var m map[string]json.RawMessage
	if err := json.Unmarshal(props, &m); err != nil {
		return "", ""
	}
	for name, raw := range m {
		var head struct {
			Type        string `json:"type"`
			MultiSelect []struct {
				Name string `json:"name"`
			} `json:"multi_select"`
			Select struct {
				Name string `json:"name"`
			} `json:"select"`
		}
		if err := json.Unmarshal(raw, &head); err != nil {
			continue
		}
		switch head.Type {
		case "multi_select":
			for _, opt := range head.MultiSelect {
				if h := extractHandler(opt.Name); h != "" {
					return h, name
				}
			}
		case "select":
			if h := extractHandler(head.Select.Name); h != "" {
				return h, name
			}
		}
	}
	return "", ""
}
