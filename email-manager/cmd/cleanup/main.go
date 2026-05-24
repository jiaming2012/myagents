package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/jamal/email-manager/internal/config"
	"github.com/jamal/email-manager/internal/email"
	"github.com/jamal/email-manager/internal/provider"
)

type senderGroup struct {
	sender  string
	subject string
	emails  []email.Email
	oldest  time.Time
	newest  time.Time
}

type accountResult struct {
	name     string
	email    string
	provider provider.EmailProvider
	groups   []senderGroup
	total    int
	err      error
}

func main() {
	dryRun := flag.Bool("dry-run", false, "preview deletions without prompting")
	olderThan := flag.Int("older-than", 0, "delete emails older than N days (overrides accounts.yaml)")
	account := flag.String("account", "", "filter to a specific account name")
	flag.Parse()

	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading config: %v\n", err)
		os.Exit(1)
	}

	maxAge := cfg.Cleanup.OlderThanDays
	if *olderThan > 0 {
		maxAge = *olderThan
	}
	if maxAge == 0 {
		maxAge = 30
	}

	ctx := context.Background()

	// Fetch all accounts in parallel
	results := fetchAllAccounts(ctx, cfg, root, *account, maxAge)

	// Build TUI model from results
	var accounts []accountData
	for _, r := range results {
		if r.err != nil {
			fmt.Fprintf(os.Stderr, "Error with %s: %v\n", r.name, r.err)
			continue
		}
		if r.total == 0 {
			continue
		}
		accounts = append(accounts, accountData{
			name:     r.name,
			email:    r.email,
			provider: r.provider,
			groups:   r.groups,
			total:    r.total,
		})
	}

	if len(accounts) == 0 {
		fmt.Println("Nothing to clean up.")
		return
	}

	m := newModel(accounts)
	m.dryRun = *dryRun
	p := tea.NewProgram(m, tea.WithAltScreen())
	finalModel, err := p.Run()
	if err != nil {
		fmt.Fprintf(os.Stderr, "TUI error: %v\n", err)
		os.Exit(1)
	}

	fm := finalModel.(model)
	if fm.totalDeleted > 0 {
		fmt.Printf("Deleted %d emails total.\n", fm.totalDeleted)
	} else {
		fmt.Println("No emails deleted.")
	}
}

func fetchAllAccounts(ctx context.Context, cfg *config.Config, root, accountFilter string, maxAge int) []accountResult {
	var accounts []config.Account
	for _, acct := range cfg.Accounts {
		if accountFilter != "" && !strings.EqualFold(acct.Name, accountFilter) {
			continue
		}
		accounts = append(accounts, acct)
	}

	results := make([]accountResult, len(accounts))
	var wg sync.WaitGroup

	for i, acct := range accounts {
		wg.Add(1)
		go func(i int, acct config.Account) {
			defer wg.Done()
			results[i] = fetchAccount(ctx, acct, cfg, root, maxAge)
		}(i, acct)
	}

	wg.Wait()
	return results
}

func fetchAccount(ctx context.Context, acct config.Account, cfg *config.Config, root string, maxAge int) accountResult {
	p, err := buildProvider(acct, cfg, root)
	if err != nil {
		return accountResult{name: acct.Name, email: acct.Email, err: err}
	}

	var allEmails []email.Email

	if maxAge > 0 {
		query := buildOlderThanQuery(acct.Provider, maxAge)
		emails, err := p.FetchEmails(ctx, query, 100)
		if err != nil {
			return accountResult{name: acct.Name, email: acct.Email, err: err}
		}
		allEmails = append(allEmails, emails...)
	}

	for _, cat := range cfg.Cleanup.Categories {
		query := buildCategoryQuery(acct.Provider, cat)
		emails, err := p.FetchEmails(ctx, query, 100)
		if err != nil {
			continue
		}
		allEmails = append(allEmails, emails...)
	}

	allEmails = dedup(allEmails)
	groups := groupBySender(allEmails)

	return accountResult{
		name:     acct.Name,
		email:    acct.Email,
		provider: p,
		groups:   groups,
		total:    len(allEmails),
	}
}

// --- helpers ---

func groupBySender(emails []email.Email) []senderGroup {
	index := make(map[string]int)
	var groups []senderGroup

	for _, e := range emails {
		sender := normalizeSender(e.From)
		if idx, ok := index[sender]; ok {
			groups[idx].emails = append(groups[idx].emails, e)
			if e.Date.Before(groups[idx].oldest) {
				groups[idx].oldest = e.Date
			}
			if e.Date.After(groups[idx].newest) {
				groups[idx].newest = e.Date
			}
		} else {
			index[sender] = len(groups)
			groups = append(groups, senderGroup{
				sender:  sender,
				subject: e.Subject,
				emails:  []email.Email{e},
				oldest:  e.Date,
				newest:  e.Date,
			})
		}
	}

	sort.Slice(groups, func(i, j int) bool {
		return len(groups[i].emails) > len(groups[j].emails)
	})

	return groups
}

func normalizeSender(from string) string {
	if idx := strings.Index(from, "<"); idx >= 0 {
		end := strings.Index(from, ">")
		if end > idx {
			return from[idx+1 : end]
		}
	}
	return from
}

func allSameSubject(emails []email.Email) bool {
	if len(emails) == 0 {
		return true
	}
	subj := emails[0].Subject
	for _, e := range emails[1:] {
		if e.Subject != subj {
			return false
		}
	}
	return true
}

func groupSubjectLabel(g senderGroup) string {
	if len(g.emails) > 1 && allSameSubject(g.emails) {
		return fmt.Sprintf("all %q", g.subject)
	} else if len(g.emails) > 1 {
		return fmt.Sprintf("e.g. %q", g.subject)
	}
	return fmt.Sprintf("%q", g.subject)
}

func groupLetter(i int) rune {
	if i < 26 {
		return rune('A' + i)
	}
	return rune('a' + i - 26)
}

func formatRange(oldest, newest time.Time) string {
	o := formatAge(time.Since(oldest))
	n := formatAge(time.Since(newest))
	if o == n {
		return o + " ago"
	}
	return n + " - " + o + " ago"
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

func dedup(emails []email.Email) []email.Email {
	seen := make(map[string]bool)
	var result []email.Email
	for _, e := range emails {
		if !seen[e.ID] {
			seen[e.ID] = true
			result = append(result, e)
		}
	}
	return result
}

func extractIDs(emails []email.Email) []string {
	ids := make([]string, len(emails))
	for i, e := range emails {
		ids[i] = e.ID
	}
	return ids
}

func buildOlderThanQuery(providerType string, days int) string {
	switch providerType {
	case "gmail":
		return fmt.Sprintf("older_than:%dd", days)
	case "zoho":
		cutoff := time.Now().AddDate(0, 0, -days)
		return fmt.Sprintf("before:%s", cutoff.Format("2006-01-02"))
	default:
		return ""
	}
}

func buildCategoryQuery(providerType string, category string) string {
	switch providerType {
	case "gmail":
		return fmt.Sprintf("category:%s", category)
	case "zoho":
		return category
	default:
		return ""
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
