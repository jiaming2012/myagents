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

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/pipeline"
)

func runDownload(args []string) {
	fs := flag.NewFlagSet("download", flag.ExitOnError)
	since := fs.Int("since", 7, "download emails from the last N days")
	maxEmails := fs.Int("max", 100, "maximum total emails to keep")
	account := fs.String("account", "", "filter to a specific account name")
	chunkSize := fs.Int("chunk-size", 25, "emails to fetch per provider call")
	fs.Parse(args)

	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading config: %v\n", err)
		os.Exit(1)
	}

	// Ensure data directory exists
	if _, err := pipeline.DataDir(root); err != nil {
		fmt.Fprintf(os.Stderr, "Error creating data dir: %v\n", err)
		os.Exit(1)
	}

	// Load existing manifest for dedup
	manifest, err := pipeline.LoadDownloadManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading existing emails: %v\n", err)
		os.Exit(1)
	}
	if manifest == nil {
		manifest = &pipeline.DownloadManifest{
			Version:   1,
			CreatedAt: time.Now(),
		}
	}

	existingIdx := make(map[string]int)
	for i, e := range manifest.Emails {
		existingIdx[e.ID] = i
	}

	// Filter accounts
	var accounts []config.Account
	for _, acct := range cfg.Accounts {
		if *account != "" && !strings.EqualFold(acct.Name, *account) {
			continue
		}
		accounts = append(accounts, acct)
	}

	// Fetch in parallel
	type result struct {
		status    pipeline.AccountFetchStatus
		newEmails []pipeline.DownloadedEmail
		updates   []pipeline.DownloadedEmail
	}
	results := make([]result, len(accounts))
	var wg sync.WaitGroup

	ctx := context.Background()
	for i, acct := range accounts {
		wg.Add(1)
		go func(i int, acct config.Account) {
			defer wg.Done()

			p, err := buildProvider(acct, cfg, root)
			if err != nil {
				results[i] = result{
					status: pipeline.AccountFetchStatus{
						Name:      acct.Name,
						Email:     acct.Email,
						Provider:  acct.Provider,
						FetchedAt: time.Now(),
						Error:     err.Error(),
					},
				}
				return
			}

			query := buildDownloadQuery(acct.Provider, *since)
			emails, err := p.FetchEmails(ctx, query, *chunkSize)
			if err != nil {
				results[i] = result{
					status: pipeline.AccountFetchStatus{
						Name:      acct.Name,
						Email:     acct.Email,
						Provider:  acct.Provider,
						FetchedAt: time.Now(),
						Error:     err.Error(),
					},
				}
				return
			}

			var newEmails []pipeline.DownloadedEmail
			var updates []pipeline.DownloadedEmail
			for _, e := range emails {
				de := pipeline.DownloadedEmail{
					ID:           e.ID,
					AccountName:  acct.Name,
					AccountEmail: acct.Email,
					Subject:      e.Subject,
					From:         e.From,
					Snippet:      e.Snippet,
					Body:         e.Body,
					Date:         e.Date,
					Labels:       e.Labels,
					Unread:       e.Unread,
				}
				if _, exists := existingIdx[e.ID]; exists {
					updates = append(updates, de)
				} else {
					newEmails = append(newEmails, de)
				}
			}

			results[i] = result{
				status: pipeline.AccountFetchStatus{
					Name:      acct.Name,
					Email:     acct.Email,
					Provider:  acct.Provider,
					FetchedAt: time.Now(),
					Count:     len(newEmails),
				},
				newEmails: newEmails,
				updates:   updates,
			}
		}(i, acct)
	}
	wg.Wait()

	// Merge results: update existing emails, append new ones
	newCount := 0
	updatedCount := 0
	manifest.Accounts = nil
	for _, r := range results {
		manifest.Accounts = append(manifest.Accounts, r.status)
		if r.status.Error != "" {
			fmt.Fprintf(os.Stderr, "  %s: %s\n", r.status.Name, r.status.Error)
			continue
		}
		// Update existing emails (e.g. unread status changed)
		for _, u := range r.updates {
			if idx, ok := existingIdx[u.ID]; ok {
				manifest.Emails[idx] = u
				updatedCount++
			}
		}
		// Append new emails
		manifest.Emails = append(manifest.Emails, r.newEmails...)
		newCount += len(r.newEmails)
	}

	// Enforce max limit: keep newest
	if len(manifest.Emails) > *maxEmails {
		sort.Slice(manifest.Emails, func(i, j int) bool {
			return manifest.Emails[i].Date.After(manifest.Emails[j].Date)
		})
		manifest.Emails = manifest.Emails[:*maxEmails]
	}

	if err := pipeline.SaveDownloadManifest(root, manifest); err != nil {
		fmt.Fprintf(os.Stderr, "Error saving emails: %v\n", err)
		os.Exit(1)
	}

	fmt.Printf("Downloaded %d new, %d updated (%d total, %d accounts)\n", newCount, updatedCount, len(manifest.Emails), len(accounts))
}

func buildDownloadQuery(providerType string, sinceDays int) string {
	switch providerType {
	case "gmail":
		return fmt.Sprintf("newer_than:%dd", sinceDays)
	case "zoho":
		cutoff := time.Now().AddDate(0, 0, -sinceDays)
		return fmt.Sprintf("after:%s", cutoff.Format("2006-01-02"))
	default:
		return ""
	}
}
