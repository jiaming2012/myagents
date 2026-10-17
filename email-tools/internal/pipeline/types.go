package pipeline

import (
	"encoding/json"
	"fmt"
	"time"
)

// FlexTime tolerates timestamps without a timezone offset. insights.json is
// written by Claude during the analyze step, which may emit bare ISO-8601
// (e.g. "2026-08-26T16:32:30.667293") instead of strict RFC3339.
type FlexTime struct {
	time.Time
}

func (t *FlexTime) UnmarshalJSON(data []byte) error {
	var s string
	if err := json.Unmarshal(data, &s); err != nil {
		return err
	}
	if s == "" {
		return nil
	}
	for _, layout := range []string{
		time.RFC3339Nano,
		"2006-01-02T15:04:05.999999999",
		"2006-01-02 15:04:05",
	} {
		if parsed, err := time.ParseInLocation(layout, s, time.Local); err == nil {
			t.Time = parsed
			return nil
		}
	}
	return fmt.Errorf("unrecognized timestamp %q", s)
}

// DownloadManifest is the top-level structure persisted to emails.json.
type DownloadManifest struct {
	Version   int                  `json:"version"`
	CreatedAt time.Time            `json:"created_at"`
	UpdatedAt time.Time            `json:"updated_at"`
	Accounts  []AccountFetchStatus `json:"accounts"`
	Emails    []DownloadedEmail    `json:"emails"`
}

type AccountFetchStatus struct {
	Name      string    `json:"name"`
	Email     string    `json:"email"`
	Provider  string    `json:"provider"`
	FetchedAt time.Time `json:"fetched_at"`
	Count     int       `json:"count"`
	Error     string    `json:"error,omitempty"`
}

type DownloadedEmail struct {
	ID           string    `json:"id"`
	AccountName  string    `json:"account_name"`
	AccountEmail string    `json:"account_email"`
	Subject      string    `json:"subject"`
	From         string    `json:"from"`
	Snippet      string    `json:"snippet"`
	Body         string    `json:"body,omitempty"`
	Date         time.Time `json:"date"`
	Labels       []string  `json:"labels,omitempty"`
	Unread       bool      `json:"unread"`
}

// InsightsManifest is the top-level structure persisted to insights.json.
type InsightsManifest struct {
	Version     int            `json:"version"`
	CreatedAt   FlexTime       `json:"created_at"`
	UpdatedAt   FlexTime       `json:"updated_at"`
	ModelUsed   string         `json:"model_used"`
	TotalEmails int            `json:"total_emails"`
	Analyzed    int            `json:"analyzed"`
	Insights    []EmailInsight `json:"insights"`
}

type EmailInsight struct {
	EmailID      string   `json:"email_id"`
	AccountName  string   `json:"account_name"`
	Subject      string   `json:"subject"`
	From         string   `json:"from"`
	Snippet      string   `json:"snippet"`
	Date         FlexTime `json:"date"`
	Unread       bool     `json:"unread"`
	AnalyzedAt   FlexTime `json:"analyzed_at"`
	Topic        string    `json:"topic"`
	Urgency      string    `json:"urgency"`
	Summary      string    `json:"summary"`
	WhyImportant string    `json:"why_important"`
	ActionItems  []string  `json:"action_items,omitempty"`
}
