package main

import (
	"path/filepath"
	"time"

	"github.com/jamal/notion-tools/internal/pipeline"
)

// DownloadManifest is .cache/people/rows.json. Only includes overdue people.
type DownloadManifest struct {
	pipeline.Envelope
	GeneratedFor string   `json:"generated_for"` // YYYY-MM-DD
	People       []Person `json:"people"`
}

type Person struct {
	RowID         string     `json:"row_id"`
	DBID          string     `json:"db_id"`
	DBTitle       string     `json:"db_title"`
	Name          string     `json:"name"`
	LastContacted *time.Time `json:"last_contacted"` // nil = never
	Cadence       string     `json:"cadence"`
	CadenceDays   int        `json:"cadence_days"`
	Context       []string   `json:"context,omitempty"`
	Notes         string     `json:"notes,omitempty"`
	PageURL       string     `json:"page_url"`
	DaysSince     *int       `json:"days_since"`  // nil if never contacted
	OverdueBy     int        `json:"overdue_by"`  // days past next-contact-due
}

// FollowupsManifest is .cache/people/followups.json — Claude writes it.
type FollowupsManifest struct {
	pipeline.Envelope
	Followups []Followup `json:"followups"`
}

type Followup struct {
	RowID        string `json:"row_id"`
	Name         string `json:"name"`
	DBTitle      string `json:"db_title"`
	PageURL      string `json:"page_url"`
	Content      string `json:"content"`
	Reasoning    string `json:"reasoning,omitempty"`
	Priority     string `json:"priority"`              // "p1".."p4"
	SuggestedDue string `json:"suggested_due,omitempty"` // YYYY-MM-DD
	GrpLabel     string `json:"grp_label,omitempty"`
}

// AppliedManifest is .cache/people/applied.json. Index is row_id.
type AppliedManifest struct {
	pipeline.Envelope
	Items map[string]AppliedItem `json:"items"`
}

type AppliedItem struct {
	RowID         string     `json:"row_id"`
	Name          string     `json:"name"`
	TodoistTaskID string     `json:"todoist_task_id"`
	TodoistURL    string     `json:"todoist_url"`
	QueuedAt      time.Time  `json:"queued_at"`
	CompletedAt   *time.Time `json:"completed_at,omitempty"`
	NotionStamped bool       `json:"notion_stamped"` // Last Contacted patched?
}

func rowsPath(dataDir string) string      { return filepath.Join(dataDir, "rows.json") }
func followupsPath(dataDir string) string { return filepath.Join(dataDir, "followups.json") }
func appliedPath(dataDir string) string   { return filepath.Join(dataDir, "applied.json") }
