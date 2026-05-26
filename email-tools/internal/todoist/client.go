package todoist

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"

	"github.com/google/uuid"
)

const syncURL = "https://api.todoist.com/api/v1/sync"

type Client struct {
	token string
}

func NewClient(token string) *Client {
	return &Client{token: token}
}

type Task struct {
	Content     string
	Description string
}

type syncCommand struct {
	Type   string      `json:"type"`
	TempID string      `json:"temp_id"`
	UUID   string      `json:"uuid"`
	Args   syncTaskArgs `json:"args"`
}

type syncTaskArgs struct {
	Content     string `json:"content"`
	Description string `json:"description,omitempty"`
}

type syncResponse struct {
	SyncStatus map[string]interface{} `json:"sync_status"`
}

func (c *Client) CreateTask(ctx context.Context, task Task) error {
	cmd := syncCommand{
		Type:   "item_add",
		TempID: uuid.NewString(),
		UUID:   uuid.NewString(),
		Args: syncTaskArgs{
			Content:     task.Content,
			Description: task.Description,
		},
	}

	cmds, err := json.Marshal([]syncCommand{cmd})
	if err != nil {
		return err
	}

	form := url.Values{}
	form.Set("commands", string(cmds))

	req, err := http.NewRequestWithContext(ctx, "POST", syncURL, strings.NewReader(form.Encode()))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("todoist API returned %d: %s", resp.StatusCode, string(body))
	}

	// Check sync status
	var sr syncResponse
	if err := json.NewDecoder(resp.Body).Decode(&sr); err != nil {
		return nil // request succeeded even if we can't parse response
	}

	for _, status := range sr.SyncStatus {
		if s, ok := status.(string); ok && s != "ok" {
			return fmt.Errorf("todoist sync error: %s", s)
		}
	}

	return nil
}
