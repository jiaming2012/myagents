// Command mcp is email-tools as a Clearspan Source: a Model Context Protocol server
// over stdio, started by Clearspan as a child process (clearspan ADR 0005).
//
// It offers `key_indicators`, the figures Clearspan watches, and wraps the inbox
// pipeline's commands as tools. It reads what the pipeline has cached under
// .cache/inbox/ and never touches the mailboxes itself; `inbox_download` runs the
// existing download command, which is why it is not marked read-only.
//
// Run it from the project root (where accounts.yaml is): `go run ./cmd/mcp`.
// Register it in Clearspan with command `go`, arguments `run ./cmd/mcp`, and this
// directory.
package main

import (
	"context"
	"encoding/json"
	"fmt"
	"log"
	"math"
	"os"
	"os/exec"
	"time"

	"github.com/modelcontextprotocol/go-sdk/mcp"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/pipeline"
)

// figure is one item of the key_indicators answer, in the shape Clearspan reads.
type figure struct {
	Name  string  `json:"name"`
	Value float64 `json:"value"`
	Unit  string  `json:"unit,omitempty"`
	AsOf  string  `json:"as_of,omitempty"`
}

// figures are the Key Indicators: how many analysed emails need action, and how long
// ago the last download finished. A figure whose cache does not exist is left out
// rather than invented; Clearspan learns the names on connect, so the first download
// and analysis should happen before the Source is connected.
func figures(dl *pipeline.DownloadManifest, ins *pipeline.InsightsManifest, now time.Time) []figure {
	var out []figure
	if ins != nil {
		n := 0
		for _, i := range ins.Insights {
			if i.Urgency == "urgent" || i.Urgency == "action_needed" {
				n++
			}
		}
		out = append(out, figure{"action_needed", float64(n), "emails", ins.UpdatedAt.Format(time.RFC3339)})
	}
	if dl != nil {
		hours := math.Round(now.Sub(dl.UpdatedAt).Hours()*10) / 10
		out = append(out, figure{"hours_since_download", hours, "hours", now.Format(time.RFC3339)})
	}
	return out
}

// statusReport is what `inbox status` prints, as data.
type statusReport struct {
	Download *downloadStatus `json:"download,omitempty"`
	Analysis *analysisStatus `json:"analysis,omitempty"`
}

type downloadStatus struct {
	TotalEmails int                           `json:"total_emails"`
	UpdatedAt   time.Time                     `json:"updated_at"`
	Accounts    []pipeline.AccountFetchStatus `json:"accounts"`
}

type analysisStatus struct {
	Total     int       `json:"total"`
	Analyzed  int       `json:"analyzed"`
	Model     string    `json:"model,omitempty"`
	UpdatedAt time.Time `json:"updated_at"`
}

func status(dl *pipeline.DownloadManifest, ins *pipeline.InsightsManifest) statusReport {
	var s statusReport
	if dl != nil {
		s.Download = &downloadStatus{TotalEmails: len(dl.Emails), UpdatedAt: dl.UpdatedAt, Accounts: dl.Accounts}
		s.Analysis = &analysisStatus{Total: len(dl.Emails)}
	}
	if ins != nil {
		if s.Analysis == nil {
			s.Analysis = &analysisStatus{}
		}
		s.Analysis.Analyzed, s.Analysis.Model, s.Analysis.UpdatedAt = ins.Analyzed, ins.ModelUsed, ins.UpdatedAt.Time
	}
	return s
}

// selectInsights keeps the insights of one urgency (all when urgency is empty), at most
// limit of them (all when limit is 0), in the order they were analysed.
func selectInsights(all []pipeline.EmailInsight, urgency string, limit int) []pipeline.EmailInsight {
	out := []pipeline.EmailInsight{}
	for _, i := range all {
		if urgency != "" && i.Urgency != urgency {
			continue
		}
		out = append(out, i)
		if limit > 0 && len(out) == limit {
			break
		}
	}
	return out
}

func main() {
	log.SetOutput(os.Stderr) // stdout is the protocol
	root := config.ProjectRoot()
	if _, err := os.Stat(root + "/accounts.yaml"); err != nil {
		log.Fatalf("run from the email-tools directory (accounts.yaml not found in %s)", root)
	}

	load := func() (*pipeline.DownloadManifest, *pipeline.InsightsManifest, error) {
		dl, err := pipeline.LoadDownloadManifest(root)
		if err != nil {
			return nil, nil, fmt.Errorf("reading emails.json: %w", err)
		}
		ins, err := pipeline.LoadInsightsManifest(root)
		if err != nil {
			return nil, nil, fmt.Errorf("reading insights.json: %w", err)
		}
		return dl, ins, nil
	}

	srv := mcp.NewServer(&mcp.Implementation{Name: "email-tools", Version: "0.1"}, nil)
	noArgs := map[string]any{"type": "object"}

	srv.AddTool(&mcp.Tool{
		Name:        "key_indicators",
		Description: "The figures this Source reports: action_needed (analysed emails marked urgent or action_needed) and hours_since_download (time since the last inbox download finished). A figure is left out while its cache does not exist yet.",
		InputSchema: noArgs,
	}, func(context.Context, *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		dl, ins, err := load()
		if err != nil {
			return failed(err), nil
		}
		return asJSON(figures(dl, ins, time.Now()))
	})

	srv.AddTool(&mcp.Tool{
		Name:        "inbox_status",
		Description: "Progress of the inbox pipeline: how many emails are downloaded, per account, and how many are analysed. The same as `inbox status`.",
		InputSchema: noArgs,
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
	}, func(context.Context, *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		dl, ins, err := load()
		if err != nil {
			return failed(err), nil
		}
		return asJSON(status(dl, ins))
	})

	srv.AddTool(&mcp.Tool{
		Name:        "list_insights",
		Description: "The analysed emails: topic, urgency, summary, why it matters and action items, from insights.json. Filter by urgency (urgent, action_needed, fyi) and cap the count with limit.",
		InputSchema: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"urgency": map[string]any{"type": "string", "enum": []string{"urgent", "action_needed", "fyi"}, "description": "keep only this urgency; all when omitted"},
				"limit":   map[string]any{"type": "integer", "minimum": 0, "description": "at most this many; all when omitted or 0"},
			},
		},
		Annotations: &mcp.ToolAnnotations{ReadOnlyHint: true},
	}, func(_ context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		var args struct {
			Urgency string `json:"urgency"`
			Limit   int    `json:"limit"`
		}
		if len(req.Params.Arguments) > 0 {
			if err := json.Unmarshal(req.Params.Arguments, &args); err != nil {
				return failed(fmt.Errorf("arguments: %w", err)), nil
			}
		}
		_, ins, err := load()
		if err != nil {
			return failed(err), nil
		}
		if ins == nil {
			return asJSON([]pipeline.EmailInsight{})
		}
		return asJSON(selectInsights(ins.Insights, args.Urgency, args.Limit))
	})

	srv.AddTool(&mcp.Tool{
		Name:        "inbox_download",
		Description: "Downloads new email from every configured account into emails.json, the same as `inbox download`. Reaches the mailboxes and writes the cache, so it is not read-only.",
		InputSchema: map[string]any{
			"type": "object",
			"properties": map[string]any{
				"since_days": map[string]any{"type": "integer", "minimum": 1, "description": "download emails from the last N days (default 7)"},
			},
		},
	}, func(ctx context.Context, req *mcp.CallToolRequest) (*mcp.CallToolResult, error) {
		var args struct {
			SinceDays int `json:"since_days"`
		}
		if len(req.Params.Arguments) > 0 {
			if err := json.Unmarshal(req.Params.Arguments, &args); err != nil {
				return failed(fmt.Errorf("arguments: %w", err)), nil
			}
		}
		cmdArgs := []string{"run", "./cmd/inbox", "download"}
		if args.SinceDays > 0 {
			cmdArgs = append(cmdArgs, "--since", fmt.Sprint(args.SinceDays))
		}
		cmd := exec.CommandContext(ctx, "go", cmdArgs...)
		cmd.Dir = root
		out, err := cmd.CombinedOutput()
		if err != nil {
			return failed(fmt.Errorf("inbox download: %w\n%s", err, out)), nil
		}
		return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(out)}}}, nil
	})

	if err := srv.Run(context.Background(), &mcp.StdioTransport{}); err != nil {
		log.Fatal(err)
	}
}

func asJSON(v any) (*mcp.CallToolResult, error) {
	b, err := json.Marshal(v)
	if err != nil {
		return failed(err), nil
	}
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: string(b)}}}, nil
}

func failed(err error) *mcp.CallToolResult {
	return &mcp.CallToolResult{Content: []mcp.Content{&mcp.TextContent{Text: err.Error()}}, IsError: true}
}
