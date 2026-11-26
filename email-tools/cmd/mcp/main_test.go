package main

import (
	"testing"
	"time"

	"github.com/jamal/email-tools/internal/pipeline"
)

func TestFiguresFromBothCaches(t *testing.T) {
	now := time.Date(2026, 11, 26, 12, 0, 0, 0, time.UTC)
	dl := &pipeline.DownloadManifest{UpdatedAt: now.Add(-90 * time.Minute)}
	ins := &pipeline.InsightsManifest{
		UpdatedAt: pipeline.FlexTime{Time: now.Add(-time.Hour)},
		Insights: []pipeline.EmailInsight{
			{Urgency: "urgent"}, {Urgency: "action_needed"}, {Urgency: "fyi"}, {Urgency: "action_needed"},
		},
	}
	got := figures(dl, ins, now)
	if len(got) != 2 {
		t.Fatalf("want 2 figures, got %d: %+v", len(got), got)
	}
	if got[0].Name != "action_needed" || got[0].Value != 3 || got[0].Unit != "emails" {
		t.Errorf("action_needed: %+v", got[0])
	}
	if got[0].AsOf != ins.UpdatedAt.Format(time.RFC3339) {
		t.Errorf("action_needed as_of = %q, want the insights' update time", got[0].AsOf)
	}
	if got[1].Name != "hours_since_download" || got[1].Value != 1.5 || got[1].Unit != "hours" {
		t.Errorf("hours_since_download: %+v", got[1])
	}
	if got[1].AsOf != now.Format(time.RFC3339) {
		t.Errorf("hours_since_download as_of = %q, want now", got[1].AsOf)
	}
}

func TestFiguresOmitWhatIsNotCached(t *testing.T) {
	now := time.Now()
	if got := figures(nil, nil, now); len(got) != 0 {
		t.Errorf("no caches: want no figures, got %+v", got)
	}
	got := figures(&pipeline.DownloadManifest{UpdatedAt: now}, nil, now)
	if len(got) != 1 || got[0].Name != "hours_since_download" || got[0].Value != 0 {
		t.Errorf("download only: %+v", got)
	}
}

func TestStatusReport(t *testing.T) {
	now := time.Date(2026, 11, 26, 12, 0, 0, 0, time.UTC)
	dl := &pipeline.DownloadManifest{
		UpdatedAt: now,
		Accounts:  []pipeline.AccountFetchStatus{{Name: "work", Count: 3, FetchedAt: now}, {Name: "home", Error: "token expired"}},
		Emails:    make([]pipeline.DownloadedEmail, 3),
	}
	ins := &pipeline.InsightsManifest{Analyzed: 2, ModelUsed: "claude", UpdatedAt: pipeline.FlexTime{Time: now}}
	s := status(dl, ins)
	if s.Download == nil || s.Download.TotalEmails != 3 || len(s.Download.Accounts) != 2 || s.Download.Accounts[1].Error != "token expired" {
		t.Errorf("download: %+v", s.Download)
	}
	if s.Analysis == nil || s.Analysis.Analyzed != 2 || s.Analysis.Total != 3 || s.Analysis.Model != "claude" {
		t.Errorf("analysis: %+v", s.Analysis)
	}
	if empty := status(nil, nil); empty.Download != nil || empty.Analysis != nil {
		t.Errorf("no caches: %+v", empty)
	}
}

func TestSelectInsights(t *testing.T) {
	all := []pipeline.EmailInsight{
		{Subject: "a", Urgency: "urgent"}, {Subject: "b", Urgency: "fyi"}, {Subject: "c", Urgency: "action_needed"}, {Subject: "d", Urgency: "urgent"},
	}
	if got := selectInsights(all, "urgent", 0); len(got) != 2 || got[0].Subject != "a" || got[1].Subject != "d" {
		t.Errorf("urgent: %+v", got)
	}
	if got := selectInsights(all, "", 3); len(got) != 3 {
		t.Errorf("limit 3: %+v", got)
	}
	if got := selectInsights(all, "fyi", 10); len(got) != 1 || got[0].Subject != "b" {
		t.Errorf("fyi: %+v", got)
	}
}
