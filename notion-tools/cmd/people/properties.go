package main

import (
	"encoding/json"
	"time"

	"github.com/jamal/notion-tools/internal/provider"
)

// readPersonProps pulls Name, Last Contacted, Cadence, Context, Notes from
// a Notion row's properties map. The caller has already verified the schema
// against the DB via verifySchema, so missing required props here are a hard
// error.
func readPersonProps(props map[string]json.RawMessage) (name, cadence, notes string, lastContacted *time.Time, context []string) {
	name = readTitle(props[PropName])
	cadence = readSelect(props[PropCadence])
	if raw, ok := props["Notes"]; ok {
		notes = readRichText(raw)
	}
	if raw, ok := props["Context"]; ok {
		context = readMultiSelect(raw)
	}
	if raw, ok := props[PropLastContacted]; ok {
		lastContacted = readDate(raw)
	}
	return
}

func readTitle(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var p struct {
		Title []provider.RichText `json:"title"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return ""
	}
	out := ""
	for _, t := range p.Title {
		out += t.PlainText
	}
	return out
}

func readRichText(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var p struct {
		RichText []provider.RichText `json:"rich_text"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return ""
	}
	out := ""
	for _, t := range p.RichText {
		out += t.PlainText
	}
	return out
}

func readSelect(raw json.RawMessage) string {
	if len(raw) == 0 {
		return ""
	}
	var p struct {
		Select *struct {
			Name string `json:"name"`
		} `json:"select"`
	}
	if err := json.Unmarshal(raw, &p); err != nil || p.Select == nil {
		return ""
	}
	return p.Select.Name
}

func readMultiSelect(raw json.RawMessage) []string {
	if len(raw) == 0 {
		return nil
	}
	var p struct {
		MultiSelect []struct {
			Name string `json:"name"`
		} `json:"multi_select"`
	}
	if err := json.Unmarshal(raw, &p); err != nil {
		return nil
	}
	out := make([]string, 0, len(p.MultiSelect))
	for _, m := range p.MultiSelect {
		out = append(out, m.Name)
	}
	return out
}

func readDate(raw json.RawMessage) *time.Time {
	if len(raw) == 0 {
		return nil
	}
	var p struct {
		Date *struct {
			Start string `json:"start"`
		} `json:"date"`
	}
	if err := json.Unmarshal(raw, &p); err != nil || p.Date == nil || p.Date.Start == "" {
		return nil
	}
	// Notion date "start" may be YYYY-MM-DD or full RFC3339.
	if t, err := time.Parse("2006-01-02", p.Date.Start); err == nil {
		return &t
	}
	if t, err := time.Parse(time.RFC3339, p.Date.Start); err == nil {
		return &t
	}
	return nil
}
