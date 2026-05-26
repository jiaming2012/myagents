package provider

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"

	"github.com/jamal/email-tools/internal/email"
)

// GmailProvider implements EmailProvider for Gmail accounts.
type GmailProvider struct {
	name    string
	addr    string
	service *gmail.Service
}

// NewGmailProvider creates a Gmail provider using stored OAuth2 tokens.
func NewGmailProvider(name, addr, clientID, clientSecret, tokenFile string) (*GmailProvider, error) {
	oauthCfg := &oauth2.Config{
		ClientID:     clientID,
		ClientSecret: clientSecret,
		Endpoint:     google.Endpoint,
		RedirectURL:  "http://localhost:8085/callback",
		Scopes:       []string{gmail.GmailModifyScope},
	}

	tok, err := loadToken(tokenFile)
	if err != nil {
		return nil, fmt.Errorf("loading token for %s: %w (run 'task auth:gmail -- --email %s' first)", name, err, addr)
	}

	client := oauthCfg.Client(context.Background(), tok)
	srv, err := gmail.NewService(context.Background(), option.WithHTTPClient(client))
	if err != nil {
		return nil, fmt.Errorf("creating gmail service for %s: %w", name, err)
	}

	return &GmailProvider{name: name, addr: addr, service: srv}, nil
}

func (g *GmailProvider) AccountName() string  { return g.name }
func (g *GmailProvider) AccountEmail() string { return g.addr }

func (g *GmailProvider) FetchEmails(ctx context.Context, query string, maxResults int) ([]email.Email, error) {
	var allMsgIDs []string
	pageSize := int64(500)
	if int64(maxResults) < pageSize {
		pageSize = int64(maxResults)
	}

	// Paginate message list (lightweight — only fetches IDs)
	pageToken := ""
	for {
		call := g.service.Users.Messages.List("me").Q(query).MaxResults(pageSize).Context(ctx)
		if pageToken != "" {
			call = call.PageToken(pageToken)
		}

		resp, err := call.Do()
		if err != nil {
			return nil, fmt.Errorf("listing messages: %w", err)
		}

		for _, msg := range resp.Messages {
			allMsgIDs = append(allMsgIDs, msg.Id)
			if len(allMsgIDs) >= maxResults {
				break
			}
		}

		fmt.Fprintf(os.Stderr, "\r  %s: found %d emails...", g.name, len(allMsgIDs))

		if len(allMsgIDs) >= maxResults || resp.NextPageToken == "" {
			break
		}
		pageToken = resp.NextPageToken
		time.Sleep(100 * time.Millisecond) // rate limit between pages
	}

	fmt.Fprintf(os.Stderr, "\r  %s: fetching %d emails...          \n", g.name, len(allMsgIDs))

	// Fetch full message details with rate limiting
	var emails []email.Email
	for i, msgID := range allMsgIDs {
		full, err := g.service.Users.Messages.Get("me", msgID).Format("full").
			Context(ctx).Do()
		if err != nil {
			continue
		}

		e := email.Email{
			ID:      msgID,
			Snippet: full.Snippet,
			Labels:  full.LabelIds,
			Unread:  containsLabel(full.LabelIds, "UNREAD"),
		}

		if full.InternalDate > 0 {
			e.Date = time.UnixMilli(full.InternalDate)
		}

		for _, h := range full.Payload.Headers {
			switch h.Name {
			case "Subject":
				e.Subject = h.Value
			case "From":
				e.From = h.Value
			}
		}

		// Extract plain text body
		e.Body = extractPlainText(full.Payload)
		if len(e.Body) > email.MaxBodyLen {
			e.Body = e.Body[:email.MaxBodyLen]
		}

		emails = append(emails, e)

		// Progress + rate limit
		if (i+1)%10 == 0 || i+1 == len(allMsgIDs) {
			fmt.Fprintf(os.Stderr, "\r  %s: %d/%d emails fetched...", g.name, i+1, len(allMsgIDs))
		}
		if (i+1)%50 == 0 {
			time.Sleep(200 * time.Millisecond)
		}
	}

	if len(allMsgIDs) > 0 {
		fmt.Fprintf(os.Stderr, "\r  %s: %d emails fetched.            \n", g.name, len(emails))
	}

	return emails, nil
}

func (g *GmailProvider) DeleteEmails(ctx context.Context, ids []string) error {
	if len(ids) == 0 {
		return nil
	}

	// Gmail batch modify limited to 1000 IDs per call
	const batchSize = 1000
	for i := 0; i < len(ids); i += batchSize {
		end := i + batchSize
		if end > len(ids) {
			end = len(ids)
		}
		req := &gmail.BatchModifyMessagesRequest{
			Ids:            ids[i:end],
			AddLabelIds:    []string{"TRASH"},
			RemoveLabelIds: []string{"INBOX"},
		}
		if err := g.service.Users.Messages.BatchModify("me", req).Context(ctx).Do(); err != nil {
			return fmt.Errorf("batch delete %d-%d: %w", i, end, err)
		}
	}
	return nil
}

// --- OAuth2 token helpers ---

func loadToken(path string) (*oauth2.Token, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()

	var tok oauth2.Token
	if err := json.NewDecoder(f).Decode(&tok); err != nil {
		return nil, err
	}
	return &tok, nil
}

// SaveToken writes an OAuth2 token to a JSON file, creating directories as needed.
func SaveToken(path string, token *oauth2.Token) error {
	if err := os.MkdirAll(filepath.Dir(path), 0700); err != nil {
		return err
	}
	f, err := os.Create(path)
	if err != nil {
		return err
	}
	defer f.Close()
	return json.NewEncoder(f).Encode(token)
}

// extractPlainText walks a Gmail message payload tree and returns the
// first text/plain part it finds, decoded from base64.
func extractPlainText(payload *gmail.MessagePart) string {
	if payload == nil {
		return ""
	}
	// Direct text/plain body
	if payload.MimeType == "text/plain" && payload.Body != nil && payload.Body.Data != "" {
		data, err := base64.URLEncoding.DecodeString(payload.Body.Data)
		if err == nil {
			return strings.TrimSpace(string(data))
		}
	}
	// Recurse into multipart parts
	for _, part := range payload.Parts {
		if text := extractPlainText(part); text != "" {
			return text
		}
	}
	return ""
}

func containsLabel(labels []string, target string) bool {
	for _, l := range labels {
		if l == target {
			return true
		}
	}
	return false
}


// StartAuthServer runs a temporary HTTP server to capture the OAuth2 callback.
// Returns the authorization code.
func StartAuthServer(ctx context.Context) (string, error) {
	codeCh := make(chan string, 1)
	errCh := make(chan error, 1)

	mux := http.NewServeMux()
	mux.HandleFunc("/callback", func(w http.ResponseWriter, r *http.Request) {
		code := r.URL.Query().Get("code")
		if code == "" {
			errCh <- fmt.Errorf("no code in callback")
			fmt.Fprint(w, "Error: no authorization code received.")
			return
		}
		codeCh <- code
		fmt.Fprint(w, "Authorization successful! You can close this tab.")
	})

	server := &http.Server{Addr: ":8085", Handler: mux}

	go func() {
		if err := server.ListenAndServe(); err != nil && err != http.ErrServerClosed {
			errCh <- err
		}
	}()

	defer server.Shutdown(ctx)

	select {
	case code := <-codeCh:
		return code, nil
	case err := <-errCh:
		return "", err
	case <-ctx.Done():
		return "", ctx.Err()
	}
}
