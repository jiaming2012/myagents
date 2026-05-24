package provider

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/gmail/v1"
	"google.golang.org/api/option"

	"github.com/jamal/email-manager/internal/email"
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
	call := g.service.Users.Messages.List("me").Q(query).MaxResults(int64(maxResults)).Context(ctx)

	resp, err := call.Do()
	if err != nil {
		return nil, fmt.Errorf("listing messages: %w", err)
	}

	var emails []email.Email
	for _, msg := range resp.Messages {
		full, err := g.service.Users.Messages.Get("me", msg.Id).Format("metadata").
			MetadataHeaders("Subject", "From", "Date").Context(ctx).Do()
		if err != nil {
			continue
		}

		e := email.Email{
			ID:      msg.Id,
			Snippet: full.Snippet,
			Labels:  full.LabelIds,
			Unread:  containsLabel(full.LabelIds, "UNREAD"),
		}

		for _, h := range full.Payload.Headers {
			switch h.Name {
			case "Subject":
				e.Subject = h.Value
			case "From":
				e.From = h.Value
			case "Date":
				if t, err := parseEmailDate(h.Value); err == nil {
					e.Date = t
				}
			}
		}

		emails = append(emails, e)
	}

	return emails, nil
}

func (g *GmailProvider) DeleteEmails(ctx context.Context, ids []string) error {
	if len(ids) == 0 {
		return nil
	}

	// Gmail batch trash (moves to trash, recoverable for 30 days)
	req := &gmail.BatchModifyMessagesRequest{
		Ids:            ids,
		AddLabelIds:    []string{"TRASH"},
		RemoveLabelIds: []string{"INBOX"},
	}

	return g.service.Users.Messages.BatchModify("me", req).Context(ctx).Do()
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

func containsLabel(labels []string, target string) bool {
	for _, l := range labels {
		if l == target {
			return true
		}
	}
	return false
}

func parseEmailDate(s string) (time.Time, error) {
	formats := []string{
		time.RFC1123Z,
		time.RFC1123,
		"Mon, 2 Jan 2006 15:04:05 -0700",
		"2 Jan 2006 15:04:05 -0700",
		time.RFC3339,
	}
	for _, f := range formats {
		if t, err := time.Parse(f, s); err == nil {
			return t, nil
		}
	}
	return time.Time{}, fmt.Errorf("unparseable date: %s", s)
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
