package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/email"
	"github.com/jamal/email-tools/internal/provider"
)

type accountEmails struct {
	name   string
	email  string
	emails []email.Email
	err    error
}

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
	hasErrors := false

	// Fetch all accounts in parallel
	results := fetchAll(ctx, cfg, root, *account, *since, *maxResults)

	var accounts []acctData
	for _, r := range results {
		if r.err != nil {
			fmt.Fprintf(os.Stderr, "Error with %s: %v\n", r.name, r.err)
			hasErrors = true
			continue
		}
		if len(r.emails) == 0 {
			continue
		}
		accounts = append(accounts, acctData{
			name:   r.name,
			email:  r.email,
			emails: r.emails,
		})
	}

	if len(accounts) == 0 {
		fmt.Println("No important unread emails found.")
		if hasErrors {
			os.Exit(1)
		}
		return
	}

	m := newModel(accounts)
	p := tea.NewProgram(m, tea.WithAltScreen())
	finalModel, err := p.Run()
	if err != nil {
		fmt.Fprintf(os.Stderr, "TUI error: %v\n", err)
		os.Exit(1)
	}

	fm := finalModel.(model)
	total := 0
	for _, a := range fm.accounts {
		total += len(a.emails)
	}
	fmt.Printf("%d important email(s) across %d accounts.\n", total, len(fm.accounts))

	if hasErrors {
		os.Exit(1)
	}
}

func fetchAll(ctx context.Context, cfg *config.Config, root, accountFilter string, sinceDays, maxResults int) []accountEmails {
	var accounts []config.Account
	for _, acct := range cfg.Accounts {
		if accountFilter != "" && !strings.EqualFold(acct.Name, accountFilter) {
			continue
		}
		accounts = append(accounts, acct)
	}

	results := make([]accountEmails, len(accounts))
	var wg sync.WaitGroup

	for i, acct := range accounts {
		wg.Add(1)
		go func(i int, acct config.Account) {
			defer wg.Done()
			results[i] = fetchAccount(ctx, acct, cfg, root, sinceDays, maxResults)
		}(i, acct)
	}

	wg.Wait()
	return results
}

func fetchAccount(ctx context.Context, acct config.Account, cfg *config.Config, root string, sinceDays, maxResults int) accountEmails {
	p, err := buildProvider(acct, cfg, root)
	if err != nil {
		return accountEmails{name: acct.Name, email: acct.Email, err: err}
	}

	query := buildImportantQuery(acct.Provider, sinceDays)
	emails, err := p.FetchEmails(ctx, query, maxResults)
	if err != nil {
		return accountEmails{name: acct.Name, email: acct.Email, err: err}
	}

	return accountEmails{name: acct.Name, email: acct.Email, emails: emails}
}

func buildImportantQuery(providerType string, sinceDays int) string {
	switch providerType {
	case "gmail":
		return fmt.Sprintf("newer_than:%dd", sinceDays)
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
