package provider

import (
	"context"

	"github.com/jamal/email-manager/internal/email"
)

// EmailProvider abstracts email operations across Gmail and Zoho.
type EmailProvider interface {
	// FetchEmails returns emails matching the given query string.
	// Query format is provider-specific (Gmail search syntax or Zoho folder/filter).
	FetchEmails(ctx context.Context, query string, maxResults int) ([]email.Email, error)

	// DeleteEmails moves the given message IDs to trash.
	DeleteEmails(ctx context.Context, ids []string) error

	// AccountName returns a display name for this account.
	AccountName() string

	// AccountEmail returns the email address for this account.
	AccountEmail() string
}
