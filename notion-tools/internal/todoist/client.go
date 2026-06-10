// Package todoist is a thin wrapper over the bits of the Todoist API the
// notion-tools handlers need. Adapted from email-tools/internal/todoist/client.go
// with priority/due/labels on create plus a ListCompletedSince call for the
// completion bridge.
package todoist

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"

	"github.com/google/uuid"
)

const (
	syncURL = "https://api.todoist.com/api/v1/sync"
	apiURL  = "https://api.todoist.com/api/v1"
)

type Client struct {
	token  string
	client *http.Client
}

func NewClient(token string) *Client {
	return &Client{token: token, client: &http.Client{Timeout: 30 * time.Second}}
}

// Task is the create-task input. Empty fields are omitted.
type Task struct {
	Content     string
	Description string
	// Priority is Todoist API priority: 1=p4 (default), 2=p3, 3=p2, 4=p1.
	Priority int
	// DueDate is YYYY-MM-DD or empty.
	DueDate string
	Labels  []string
}

// CreatedTask is what the API returns on a successful create.
type CreatedTask struct {
	ID  string `json:"id"`
	URL string `json:"url"`
}

type syncCommand struct {
	Type   string         `json:"type"`
	TempID string         `json:"temp_id"`
	UUID   string         `json:"uuid"`
	Args   syncTaskArgs   `json:"args"`
}

type syncTaskArgs struct {
	Content     string         `json:"content"`
	Description string         `json:"description,omitempty"`
	Priority    int            `json:"priority,omitempty"`
	Due         *syncDueArgs   `json:"due,omitempty"`
	Labels      []string       `json:"labels,omitempty"`
}

type syncDueArgs struct {
	Date string `json:"date"`
}

type syncResponse struct {
	SyncStatus map[string]any    `json:"sync_status"`
	TempIDMap  map[string]string `json:"temp_id_mapping"`
}

// CreateTask creates a Todoist task and returns its ID + web URL.
func (c *Client) CreateTask(ctx context.Context, task Task) (*CreatedTask, error) {
	tempID := uuid.NewString()
	args := syncTaskArgs{
		Content:     task.Content,
		Description: task.Description,
		Priority:    task.Priority,
		Labels:      task.Labels,
	}
	if task.DueDate != "" {
		args.Due = &syncDueArgs{Date: task.DueDate}
	}
	cmd := syncCommand{Type: "item_add", TempID: tempID, UUID: uuid.NewString(), Args: args}

	cmds, err := json.Marshal([]syncCommand{cmd})
	if err != nil {
		return nil, err
	}
	form := url.Values{}
	form.Set("commands", string(cmds))

	req, err := http.NewRequestWithContext(ctx, "POST", syncURL, strings.NewReader(form.Encode()))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("todoist sync returned %d: %s", resp.StatusCode, string(body))
	}

	var sr syncResponse
	if err := json.Unmarshal(body, &sr); err != nil {
		return nil, err
	}
	if status, ok := sr.SyncStatus[cmd.UUID]; ok {
		if s, isStr := status.(string); isStr && s != "ok" {
			return nil, fmt.Errorf("todoist sync error: %s", s)
		}
		if m, isMap := status.(map[string]any); isMap {
			if errCode, ok := m["error"]; ok {
				return nil, fmt.Errorf("todoist sync error: %v", errCode)
			}
		}
	}
	realID, ok := sr.TempIDMap[tempID]
	if !ok {
		return nil, fmt.Errorf("todoist sync: no real ID for temp_id %s", tempID)
	}
	return &CreatedTask{
		ID:  realID,
		URL: "https://todoist.com/showTask?id=" + realID,
	}, nil
}

// CompletedTask is one entry from the completed-tasks feed.
type CompletedTask struct {
	TaskID      string    `json:"task_id"`
	Content     string    `json:"content"`
	CompletedAt time.Time `json:"completed_at"`
	// Description is fetched separately via GetTaskDescription — the
	// completed-tasks feed doesn't include description.
}

type completedResp struct {
	Items []struct {
		TaskID      string `json:"task_id"`
		Content     string `json:"content"`
		CompletedAt string `json:"completed_at"`
	} `json:"items"`
	NextCursor string `json:"next_cursor,omitempty"`
}

// ListCompletedSince returns tasks completed at-or-after `since`. The Todoist
// completed-tasks endpoint expects an ISO timestamp window.
func (c *Client) ListCompletedSince(ctx context.Context, since time.Time) ([]CompletedTask, error) {
	var out []CompletedTask
	q := url.Values{}
	q.Set("since", since.UTC().Format("2006-01-02T15:04:05"))
	q.Set("until", time.Now().UTC().Format("2006-01-02T15:04:05"))

	req, err := http.NewRequestWithContext(ctx, "GET",
		apiURL+"/tasks/completed/by_completion_date?"+q.Encode(), nil)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode >= 300 {
		return nil, fmt.Errorf("todoist completed returned %d: %s", resp.StatusCode, string(body))
	}
	var cr completedResp
	if err := json.Unmarshal(body, &cr); err != nil {
		return nil, err
	}
	for _, it := range cr.Items {
		t, _ := time.Parse(time.RFC3339, it.CompletedAt)
		out = append(out, CompletedTask{TaskID: it.TaskID, Content: it.Content, CompletedAt: t})
	}
	return out, nil
}

// GetTaskDescription fetches the description for a single task (the
// completed feed doesn't include it, but the bridge needs it to read the
// notion-people:{row_id} marker).
func (c *Client) GetTaskDescription(ctx context.Context, taskID string) (string, error) {
	req, err := http.NewRequestWithContext(ctx, "GET", apiURL+"/tasks/"+taskID, nil)
	if err != nil {
		return "", err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	resp, err := c.client.Do(req)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(resp.Body)
	if resp.StatusCode == 404 {
		return "", nil // task deleted; treat as no description
	}
	if resp.StatusCode >= 300 {
		return "", fmt.Errorf("todoist task get returned %d: %s", resp.StatusCode, string(body))
	}
	var t struct {
		Description string `json:"description"`
	}
	if err := json.Unmarshal(body, &t); err != nil {
		return "", err
	}
	return t.Description, nil
}
