package todoist

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"net/http"
)

const apiURL = "https://api.todoist.com/rest/v2/tasks"

type Client struct {
	token string
}

func NewClient(token string) *Client {
	return &Client{token: token}
}

type Task struct {
	Content     string `json:"content"`
	Description string `json:"description,omitempty"`
}

func (c *Client) CreateTask(ctx context.Context, task Task) error {
	body, err := json.Marshal(task)
	if err != nil {
		return err
	}

	req, err := http.NewRequestWithContext(ctx, "POST", apiURL, bytes.NewReader(body))
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+c.token)
	req.Header.Set("Content-Type", "application/json")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		return fmt.Errorf("todoist API returned %d", resp.StatusCode)
	}
	return nil
}
