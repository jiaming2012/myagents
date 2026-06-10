// Package provider is a thin Notion REST client. No SDK; just net/http and
// the bits of the v1 API the handlers actually need.
package provider

import (
	"bytes"
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"time"
)

const (
	baseURL       = "https://api.notion.com/v1"
	notionVersion = "2022-06-28"
)

type Notion struct {
	token  string
	client *http.Client
}

func New(token string) *Notion {
	return &Notion{
		token:  token,
		client: &http.Client{Timeout: 30 * time.Second},
	}
}

func (n *Notion) do(ctx context.Context, method, path string, body any, out any) error {
	var bodyReader io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return err
		}
		bodyReader = bytes.NewReader(buf)
	}
	req, err := http.NewRequestWithContext(ctx, method, baseURL+path, bodyReader)
	if err != nil {
		return err
	}
	req.Header.Set("Authorization", "Bearer "+n.token)
	req.Header.Set("Notion-Version", notionVersion)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := n.client.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return err
	}
	if resp.StatusCode >= 300 {
		return fmt.Errorf("notion %s %s: %d %s", method, path, resp.StatusCode, string(respBody))
	}
	if out != nil {
		return json.Unmarshal(respBody, out)
	}
	return nil
}

// --- Search ---------------------------------------------------------------

// SearchHit is one entry from /v1/search.
type SearchHit struct {
	Object         string          `json:"object"`           // "page" | "database"
	ID             string          `json:"id"`
	URL            string          `json:"url"`
	LastEditedTime time.Time       `json:"last_edited_time"`
	Properties     json.RawMessage `json:"properties"`        // page properties
	Title          []RichText      `json:"title"`             // database title (top-level)
	Parent         Parent          `json:"parent"`
}

type Parent struct {
	Type       string `json:"type"`                  // "workspace" | "database_id" | "page_id"
	DatabaseID string `json:"database_id,omitempty"`
	PageID     string `json:"page_id,omitempty"`
	Workspace  bool   `json:"workspace,omitempty"`
}

type RichText struct {
	PlainText string `json:"plain_text"`
}

type searchResp struct {
	Object     string      `json:"object"`
	Results    []SearchHit `json:"results"`
	NextCursor string      `json:"next_cursor"`
	HasMore    bool        `json:"has_more"`
}

// SearchAll walks every page of /v1/search, returning every object the
// integration can see, optionally filtered to "page" or "database".
func (n *Notion) SearchAll(ctx context.Context, filterObject string) ([]SearchHit, error) {
	var all []SearchHit
	cursor := ""
	for {
		body := map[string]any{
			"page_size": 100,
			"sort": map[string]any{
				"direction": "descending",
				"timestamp": "last_edited_time",
			},
		}
		if filterObject != "" {
			body["filter"] = map[string]any{"value": filterObject, "property": "object"}
		}
		if cursor != "" {
			body["start_cursor"] = cursor
		}
		var resp searchResp
		if err := n.do(ctx, "POST", "/search", body, &resp); err != nil {
			return nil, err
		}
		all = append(all, resp.Results...)
		if !resp.HasMore || resp.NextCursor == "" {
			return all, nil
		}
		cursor = resp.NextCursor
	}
}

// --- Page metadata --------------------------------------------------------

// PageTitle extracts the page title from a SearchHit. Notion pages store the
// title under a "title" property whose key varies by DB schema — we walk all
// page properties looking for the one of type "title".
func PageTitle(h SearchHit) string {
	if h.Object == "database" {
		var sb string
		for _, t := range h.Title {
			sb += t.PlainText
		}
		return sb
	}
	// Page: properties is a map of {name: {type, ...}}
	var props map[string]json.RawMessage
	if err := json.Unmarshal(h.Properties, &props); err != nil {
		return ""
	}
	for _, raw := range props {
		var head struct {
			Type  string     `json:"type"`
			Title []RichText `json:"title"`
		}
		if err := json.Unmarshal(raw, &head); err != nil {
			continue
		}
		if head.Type == "title" {
			s := ""
			for _, t := range head.Title {
				s += t.PlainText
			}
			return s
		}
	}
	return ""
}

// --- Blocks ---------------------------------------------------------------

// Block is the minimal block shape the handlers need. We keep Raw for any
// type-specific decoding the caller wants to do.
type Block struct {
	ID          string          `json:"id"`
	Type        string          `json:"type"`
	HasChildren bool            `json:"has_children"`
	Raw         json.RawMessage `json:"-"` // the original block JSON
}

type blockChildrenResp struct {
	Results    []json.RawMessage `json:"results"`
	NextCursor string            `json:"next_cursor"`
	HasMore    bool              `json:"has_more"`
}

// BlockChildren returns all immediate children of parentID.
func (n *Notion) BlockChildren(ctx context.Context, parentID string) ([]Block, error) {
	var all []Block
	cursor := ""
	for {
		path := "/blocks/" + parentID + "/children?page_size=100"
		if cursor != "" {
			path += "&start_cursor=" + cursor
		}
		var resp blockChildrenResp
		if err := n.do(ctx, "GET", path, nil, &resp); err != nil {
			return nil, err
		}
		for _, raw := range resp.Results {
			var head struct {
				ID          string `json:"id"`
				Type        string `json:"type"`
				HasChildren bool   `json:"has_children"`
			}
			if err := json.Unmarshal(raw, &head); err != nil {
				continue
			}
			all = append(all, Block{ID: head.ID, Type: head.Type, HasChildren: head.HasChildren, Raw: raw})
		}
		if !resp.HasMore || resp.NextCursor == "" {
			return all, nil
		}
		cursor = resp.NextCursor
	}
}

// ToDoBlock unpacks the type-specific payload of a "to_do" block.
type ToDoBlock struct {
	Text    string
	Checked bool
}

// AsToDo returns the to_do payload if b is a to_do block.
func AsToDo(b Block) (ToDoBlock, bool) {
	if b.Type != "to_do" {
		return ToDoBlock{}, false
	}
	var wrap struct {
		ToDo struct {
			RichText []RichText `json:"rich_text"`
			Checked  bool       `json:"checked"`
		} `json:"to_do"`
	}
	if err := json.Unmarshal(b.Raw, &wrap); err != nil {
		return ToDoBlock{}, false
	}
	text := ""
	for _, t := range wrap.ToDo.RichText {
		text += t.PlainText
	}
	return ToDoBlock{Text: text, Checked: wrap.ToDo.Checked}, true
}

// PatchToDoText replaces the rich_text of an existing to_do block. The caller
// passes the full new plain-text content; we wrap it as a single text run.
func (n *Notion) PatchToDoText(ctx context.Context, blockID, newText string, checked bool) error {
	body := map[string]any{
		"to_do": map[string]any{
			"rich_text": []map[string]any{
				{"type": "text", "text": map[string]any{"content": newText}},
			},
			"checked": checked,
		},
	}
	return n.do(ctx, "PATCH", "/blocks/"+blockID, body, nil)
}

// --- Databases ------------------------------------------------------------

type dbQueryResp struct {
	Results    []json.RawMessage `json:"results"`
	NextCursor string            `json:"next_cursor"`
	HasMore    bool              `json:"has_more"`
}

// DatabaseRow is one row of a database query.
type DatabaseRow struct {
	ID         string                     `json:"id"`
	URL        string                     `json:"url"`
	Properties map[string]json.RawMessage `json:"properties"`
}

// QueryDatabase returns every row in dbID. No filter — caller does any
// shaping after.
func (n *Notion) QueryDatabase(ctx context.Context, dbID string) ([]DatabaseRow, error) {
	var all []DatabaseRow
	cursor := ""
	for {
		body := map[string]any{"page_size": 100}
		if cursor != "" {
			body["start_cursor"] = cursor
		}
		var resp dbQueryResp
		if err := n.do(ctx, "POST", "/databases/"+dbID+"/query", body, &resp); err != nil {
			return nil, err
		}
		for _, raw := range resp.Results {
			var r DatabaseRow
			if err := json.Unmarshal(raw, &r); err != nil {
				return nil, err
			}
			all = append(all, r)
		}
		if !resp.HasMore || resp.NextCursor == "" {
			return all, nil
		}
		cursor = resp.NextCursor
	}
}

// DatabaseSchema is the subset of a database we need for schema validation.
type DatabaseSchema struct {
	Title      []RichText                 `json:"title"`
	Properties map[string]json.RawMessage `json:"properties"`
}

// GetDatabase fetches /v1/databases/{id} — used for schema-precondition checks.
func (n *Notion) GetDatabase(ctx context.Context, dbID string) (*DatabaseSchema, error) {
	var s DatabaseSchema
	if err := n.do(ctx, "GET", "/databases/"+dbID, nil, &s); err != nil {
		return nil, err
	}
	return &s, nil
}

// PropertyType returns the "type" string from a Notion property schema entry.
func PropertyType(raw json.RawMessage) string {
	var head struct {
		Type string `json:"type"`
	}
	_ = json.Unmarshal(raw, &head)
	return head.Type
}

// --- Page property patches ------------------------------------------------

// PatchPageDate sets a date-type page property to date (YYYY-MM-DD, no time).
func (n *Notion) PatchPageDate(ctx context.Context, pageID, propName string, date time.Time) error {
	body := map[string]any{
		"properties": map[string]any{
			propName: map[string]any{
				"date": map[string]any{"start": date.Format("2006-01-02")},
			},
		},
	}
	return n.do(ctx, "PATCH", "/pages/"+pageID, body, nil)
}
