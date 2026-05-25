package main

import (
	"encoding/json"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/pipeline"
)

func runPrepareChunk(args []string) {
	fs := flag.NewFlagSet("prepare-chunk", flag.ExitOnError)
	chunkSize := fs.Int("chunk-size", 10, "emails per analysis chunk")
	fs.Parse(args)

	root := config.ProjectRoot()

	manifest, err := pipeline.LoadDownloadManifest(root)
	if err != nil || manifest == nil {
		fmt.Fprintf(os.Stderr, "No emails downloaded. Run 'download' first.\n")
		os.Exit(1)
	}

	insights, err := pipeline.LoadInsightsManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading insights: %v\n", err)
		os.Exit(1)
	}

	// Build set of already-analyzed IDs
	analyzed := make(map[string]bool)
	if insights != nil {
		for _, ins := range insights.Insights {
			analyzed[ins.EmailID] = true
		}
	}

	// Find unanalyzed emails
	var pending []pipeline.DownloadedEmail
	for _, e := range manifest.Emails {
		if !analyzed[e.ID] {
			pending = append(pending, e)
		}
	}

	if len(pending) == 0 {
		fmt.Println("All emails analyzed.")
		os.Exit(1) // non-zero signals "done" to the Taskfile loop
	}

	// Take next chunk
	chunk := pending
	if len(chunk) > *chunkSize {
		chunk = chunk[:*chunkSize]
	}

	// Build prompt
	var b strings.Builder
	b.WriteString(`You are an email triage assistant. Classify each email below.

For each email, return a JSON object with:
- "email_id": the ID provided
- "topic": one of ["Finance", "Security", "Work", "Social", "Shopping", "Travel", "Health", "Newsletter", "Other"]
- "urgency": one of ["urgent", "action_needed", "fyi"]
- "summary": a 1-2 sentence plain-text summary of what this email is about
- "why_important": a brief explanation of why this might need attention, or "Routine" if it doesn't

Return ONLY a JSON array of objects. No markdown fences, no explanation.

Emails:
`)
	for _, e := range chunk {
		fmt.Fprintf(&b, "---\nID: %s\nAccount: %s\nFrom: %s\nSubject: %s\nSnippet: %s\nDate: %s\nUnread: %v\n", e.ID, e.AccountName, e.From, e.Subject, e.Snippet, e.Date.Format(time.RFC3339), e.Unread)
	}

	promptPath := pipeline.ChunkPromptPath(root)
	if err := os.WriteFile(promptPath, []byte(b.String()), 0644); err != nil {
		fmt.Fprintf(os.Stderr, "Error writing prompt: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Prepared chunk of %d emails → %s\n", len(chunk), promptPath)
	fmt.Printf("Remaining: %d unanalyzed\n", len(pending)-len(chunk))
}

func runIngestChunk(args []string) {
	root := config.ProjectRoot()

	responsePath := pipeline.ChunkResponsePath(root)
	data, err := os.ReadFile(responsePath)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error reading response: %v\n", err)
		os.Exit(1)
	}

	// Parse Claude's JSON response — strip markdown fences if present
	responseText := strings.TrimSpace(string(data))
	responseText = strings.TrimPrefix(responseText, "```json")
	responseText = strings.TrimPrefix(responseText, "```")
	responseText = strings.TrimSuffix(responseText, "```")
	responseText = strings.TrimSpace(responseText)

	var parsed []struct {
		EmailID      string `json:"email_id"`
		Topic        string `json:"topic"`
		Urgency      string `json:"urgency"`
		Summary      string `json:"summary"`
		WhyImportant string `json:"why_important"`
	}
	if err := json.Unmarshal([]byte(responseText), &parsed); err != nil {
		fmt.Fprintf(os.Stderr, "Error parsing Claude response: %v\n", err)
		fmt.Fprintf(os.Stderr, "Response was:\n%s\n", responseText[:min(500, len(responseText))])
		os.Exit(1)
	}

	// Load download manifest to get original email data
	manifest, err := pipeline.LoadDownloadManifest(root)
	if err != nil || manifest == nil {
		fmt.Fprintf(os.Stderr, "Error loading emails: %v\n", err)
		os.Exit(1)
	}
	emailByID := make(map[string]pipeline.DownloadedEmail)
	for _, e := range manifest.Emails {
		emailByID[e.ID] = e
	}

	// Load or create insights manifest
	insights, err := pipeline.LoadInsightsManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading insights: %v\n", err)
		os.Exit(1)
	}
	if insights == nil {
		insights = &pipeline.InsightsManifest{
			Version:   1,
			CreatedAt: time.Now(),
			ModelUsed: "claude-code",
		}
	}

	// Merge new insights
	now := time.Now()
	for _, p := range parsed {
		orig, ok := emailByID[p.EmailID]
		if !ok {
			fmt.Fprintf(os.Stderr, "Warning: unknown email_id %q in response, skipping\n", p.EmailID)
			continue
		}
		insights.Insights = append(insights.Insights, pipeline.EmailInsight{
			EmailID:      p.EmailID,
			AccountName:  orig.AccountName,
			Subject:      orig.Subject,
			From:         orig.From,
			Snippet:      orig.Snippet,
			Date:         orig.Date,
			Unread:       orig.Unread,
			AnalyzedAt:   now,
			Topic:        p.Topic,
			Urgency:      p.Urgency,
			Summary:      p.Summary,
			WhyImportant: p.WhyImportant,
		})
	}

	insights.TotalEmails = len(manifest.Emails)
	insights.Analyzed = len(insights.Insights)

	if err := pipeline.SaveInsightsManifest(root, insights); err != nil {
		fmt.Fprintf(os.Stderr, "Error saving insights: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Ingested %d insights (%d/%d total)\n", len(parsed), insights.Analyzed, insights.TotalEmails)
}
