package provider

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"sync"
	"time"

	"github.com/jamal/email-tools/internal/email"
)

// ZohoProvider implements EmailProvider for Zoho Mail accounts.
type ZohoProvider struct {
	name         string
	addr         string
	accountID    string
	clientID     string
	clientSecret string
	refreshToken string

	mu          sync.Mutex
	accessToken string
	tokenExpiry time.Time
}

func NewZohoProvider(name, addr, accountID, clientID, clientSecret, refreshToken string) *ZohoProvider {
	return &ZohoProvider{
		name:         name,
		addr:         addr,
		accountID:    accountID,
		clientID:     clientID,
		clientSecret: clientSecret,
		refreshToken: refreshToken,
	}
}

func (z *ZohoProvider) AccountName() string  { return z.name }
func (z *ZohoProvider) AccountEmail() string { return z.addr }

func (z *ZohoProvider) getAccessToken(ctx context.Context) (string, error) {
	z.mu.Lock()
	defer z.mu.Unlock()

	if z.accessToken != "" && time.Now().Before(z.tokenExpiry) {
		return z.accessToken, nil
	}

	data := url.Values{
		"grant_type":    {"refresh_token"},
		"client_id":     {z.clientID},
		"client_secret": {z.clientSecret},
		"refresh_token": {z.refreshToken},
	}

	req, err := http.NewRequestWithContext(ctx, "POST", "https://accounts.zoho.com/oauth/v2/token", strings.NewReader(data.Encode()))
	if err != nil {
		return "", err
	}
	req.Header.Set("Content-Type", "application/x-www-form-urlencoded")

	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return "", fmt.Errorf("zoho token refresh: %w", err)
	}
	defer resp.Body.Close()

	var result struct {
		AccessToken string `json:"access_token"`
		ExpiresIn   int    `json:"expires_in"`
		Error       string `json:"error"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return "", fmt.Errorf("zoho token decode: %w", err)
	}
	if result.Error != "" {
		return "", fmt.Errorf("zoho token error: %s", result.Error)
	}

	z.accessToken = result.AccessToken
	// Refresh 5 minutes early (same pattern as yumyums/hq)
	z.tokenExpiry = time.Now().Add(time.Duration(result.ExpiresIn-300) * time.Second)

	return z.accessToken, nil
}

func (z *ZohoProvider) doRequest(ctx context.Context, method, endpoint string, body io.Reader) (*http.Response, error) {
	token, err := z.getAccessToken(ctx)
	if err != nil {
		return nil, err
	}

	req, err := http.NewRequestWithContext(ctx, method, endpoint, body)
	if err != nil {
		return nil, err
	}
	req.Header.Set("Authorization", "Zoho-oauthtoken "+token)
	req.Header.Set("Content-Type", "application/json")

	return http.DefaultClient.Do(req)
}

func (z *ZohoProvider) FetchEmails(ctx context.Context, query string, maxResults int) ([]email.Email, error) {
	// First get the inbox folder ID
	folderID, err := z.getInboxFolderID(ctx)
	if err != nil {
		return nil, err
	}

	// Use folder-based message listing with status filter
	endpoint := fmt.Sprintf("https://mail.zoho.com/api/accounts/%s/messages/view?folderId=%s&limit=%d&status=unread",
		z.accountID, folderID, maxResults)

	resp, err := z.doRequest(ctx, "GET", endpoint, nil)
	if err != nil {
		return nil, fmt.Errorf("zoho fetch: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		body, _ := io.ReadAll(resp.Body)
		return nil, fmt.Errorf("zoho fetch status %d: %s", resp.StatusCode, string(body))
	}

	var result struct {
		Data []struct {
			MessageID string      `json:"messageId"`
			Subject   string      `json:"subject"`
			Sender    string      `json:"sender"`
			Summary   string      `json:"summary"`
			RecvDate  json.Number `json:"receivedTime"`
			Status2   string      `json:"status2"`
			FolderID  string      `json:"folderId"`
		} `json:"data"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&result); err != nil {
		return nil, fmt.Errorf("zoho decode: %w", err)
	}

	var emails []email.Email
	for _, m := range result.Data {
		recvMs, _ := m.RecvDate.Int64()
		emails = append(emails, email.Email{
			ID:      m.MessageID,
			Subject: m.Subject,
			From:    m.Sender,
			Snippet: m.Summary,
			Date:    time.UnixMilli(recvMs),
			Unread:  m.Status2 == "1",
		})
	}

	return emails, nil
}

func (z *ZohoProvider) getInboxFolderID(ctx context.Context) (string, error) {
	endpoint := fmt.Sprintf("https://mail.zoho.com/api/accounts/%s/folders", z.accountID)

	resp, err := z.doRequest(ctx, "GET", endpoint, nil)
	if err != nil {
		return "", fmt.Errorf("zoho folders: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", fmt.Errorf("zoho folders read: %w", err)
	}

	// Zoho may return {"data": [...]} or a raw array
	var raw json.RawMessage
	var wrapper struct {
		Data json.RawMessage `json:"data"`
	}
	if err := json.Unmarshal(body, &wrapper); err == nil && wrapper.Data != nil {
		raw = wrapper.Data
	} else {
		raw = body
	}

	var folders []map[string]interface{}
	if err := json.Unmarshal(raw, &folders); err != nil {
		return "", fmt.Errorf("zoho folders decode: %w (body: %s)", err, string(body))
	}

	for _, f := range folders {
		name, _ := f["folderName"].(string)
		if strings.EqualFold(name, "Inbox") {
			switch v := f["folderId"].(type) {
			case string:
				return v, nil
			case float64:
				return fmt.Sprintf("%d", int64(v)), nil
			}
		}
	}

	return "", fmt.Errorf("inbox folder not found")
}

func (z *ZohoProvider) DeleteEmails(ctx context.Context, ids []string) error {
	if len(ids) == 0 {
		return nil
	}

	// Zoho Mail API: move messages to trash
	endpoint := fmt.Sprintf("https://mail.zoho.com/api/accounts/%s/messages", z.accountID)

	payload := struct {
		MsgIDs []string `json:"messageId"`
		Mode   string   `json:"mode"`
	}{
		MsgIDs: ids,
		Mode:   "moveToTrash",
	}

	body, err := json.Marshal(payload)
	if err != nil {
		return err
	}

	resp, err := z.doRequest(ctx, "PUT", endpoint, strings.NewReader(string(body)))
	if err != nil {
		return fmt.Errorf("zoho delete: %w", err)
	}
	defer resp.Body.Close()

	if resp.StatusCode >= 300 {
		respBody, _ := io.ReadAll(resp.Body)
		return fmt.Errorf("zoho delete status %d: %s", resp.StatusCode, string(respBody))
	}

	return nil
}
