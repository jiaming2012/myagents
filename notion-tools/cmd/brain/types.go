package main

import (
	"path/filepath"
	"time"

	"github.com/jamal/notion-tools/internal/pipeline"
)

// DownloadManifest is .cache/brain/pages.json.
type DownloadManifest struct {
	pipeline.Envelope
	LookbackDays int             `json:"lookback_days"`
	Pages        []DownloadedPage `json:"pages"`
}

type DownloadedPage struct {
	ID         string      `json:"id"`
	Title      string      `json:"title"`
	URL        string      `json:"url"`
	LastEdited time.Time   `json:"last_edited_time"`
	TodoBlocks []TodoBlock `json:"todo_blocks"`
}

type TodoBlock struct {
	ID      string `json:"id"`
	Text    string `json:"text"`
	Checked bool   `json:"checked"`
	Indent  int    `json:"indent"`
}

// InsightsManifest is .cache/brain/insights.json — Claude writes it.
type InsightsManifest struct {
	pipeline.Envelope
	Insights []Insight `json:"insights"`
}

type Insight struct {
	SourcePageID    string `json:"source_page_id"`
	SourcePageTitle string `json:"source_page_title"`
	SourcePageURL   string `json:"source_page_url"`
	SourceBlockID   string `json:"source_block_id"`
	Content         string `json:"content"`
	Priority        string `json:"priority"`              // "p1" .. "p4"
	SuggestedDue    string `json:"suggested_due,omitempty"` // YYYY-MM-DD
	GrpLabel        string `json:"grp_label,omitempty"`
}

// AppliedManifest is .cache/brain/applied.json — tracks what's been queued
// to Todoist and the stamp state of the source Notion block. Index is the
// source block ID.
type AppliedManifest struct {
	pipeline.Envelope
	Items map[string]AppliedItem `json:"items"`
}

type AppliedItem struct {
	SourceBlockID  string    `json:"source_block_id"`
	SourcePageID   string    `json:"source_page_id"`
	TodoistTaskID  string    `json:"todoist_task_id"`
	TodoistURL     string    `json:"todoist_url"`
	QueuedAt       time.Time `json:"queued_at"`
	StampedAt      *time.Time `json:"stamped_at,omitempty"`
	StampError     string    `json:"stamp_error,omitempty"`
	OriginalText   string    `json:"original_text"`
}

func pagesPath(dataDir string) string    { return filepath.Join(dataDir, "pages.json") }
func insightsPath(dataDir string) string { return filepath.Join(dataDir, "insights.json") }
func appliedPath(dataDir string) string  { return filepath.Join(dataDir, "applied.json") }
