package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"time"

	"github.com/jamal/email-manager/internal/config"
	"github.com/jamal/email-manager/internal/email"
	"github.com/jamal/email-manager/internal/provider"
)

func main() {
	since := flag.Int("since", 7, "show emails from the last N days")
	account := flag.String("account", "", "filter to a specific account name")
	maxResults := flag.Int("max", 20, "max emails per account")
	flag.Parse()

	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading config: %v\n", err)
		os.Exit(1)
	}

	ctx := context.Background()
	totalCount := 0
	hasErrors := false

	for _, acct := range cfg.Accounts {
		if *account != "" && !strings.EqualFold(acct.Name, *account) {
			continue
		}

		p, err := buildProvider(acct, cfg, root)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Skipping %s: %v\n", acct.Name, err)
			hasErrors = true
			continue
		}

		count, err := showImportant(ctx, p, acct, *since, *maxResults)
		if err != nil {
			fmt.Fprintf(os.Stderr, "Error with %s: %v\n", acct.Name, err)
			hasErrors = true
			continue
		}
		totalCount += count
	}

	if totalCount == 0 {
		fmt.Println("\nNo important unread emails found.")
	} else {
		fmt.Printf("\n%d important email(s) across all accounts.\n", totalCount)
	}

	if hasErrors {
		os.Exit(1)
	}
}

func showImportant(ctx context.Context, p provider.EmailProvider, acct config.Account, sinceDays, maxResults int) (int, error) {
	query := buildImportantQuery(acct.Provider, sinceDays)
	emails, err := p.FetchEmails(ctx, query, maxResults)
	if err != nil {
		return 0, err
	}

	// Filter to unread only
	var unread []email.Email
	for _, e := range emails {
		if e.Unread {
			unread = append(unread, e)
		}
	}

	if len(unread) == 0 {
		return 0, nil
	}

	fmt.Printf("\n=== %s (%s) — %d unread important ===\n", acct.Name, acct.Email, len(unread))

	for i, e := range unread {
		age := time.Since(e.Date)
		snippet := e.Snippet
		if len(snippet) > 100 {
			snippet = snippet[:100] + "..."
		}
		fmt.Printf("  [%d] %q\n      from: %s (%s ago)\n      %s\n\n",
			i+1, e.Subject, e.From, formatAge(age), snippet)
	}

	return len(unread), nil
}

func buildImportantQuery(providerType string, sinceDays int) string {
	switch providerType {
	case "gmail":
		return fmt.Sprintf("is:important is:unread newer_than:%dd", sinceDays)
	case "zoho":
		cutoff := time.Now().AddDate(0, 0, -sinceDays)
		return fmt.Sprintf("after:%s is:unread", cutoff.Format("2006-01-02"))
	default:
		return ""
	}
}

func formatAge(d time.Duration) string {
	switch {
	case d < time.Hour:
		return fmt.Sprintf("%dm", int(d.Minutes()))
	case d < 24*time.Hour:
		return fmt.Sprintf("%dh", int(d.Hours()))
	default:
		return fmt.Sprintf("%dd", int(d.Hours()/24))
	}
}

func buildProvider(acct config.Account, cfg *config.Config, root string) (provider.EmailProvider, error) {
	switch acct.Provider {
	case "gmail":
		tokenPath := acct.TokenFile
		if tokenPath != "" && tokenPath[0] != '/' {
			tokenPath = root + "/" + tokenPath
		}
		return provider.NewGmailProvider(acct.Name, acct.Email, cfg.GmailClientID, cfg.GmailClientSecret, tokenPath)
	case "zoho":
		return provider.NewZohoProvider(acct.Name, acct.Email, cfg.ZohoAccountID, cfg.ZohoClientID, cfg.ZohoClientSecret, cfg.ZohoRefreshToken), nil
	default:
		return nil, fmt.Errorf("unknown provider: %s", acct.Provider)
	}
}
