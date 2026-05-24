package email

import "time"

// Email represents a single email message across any provider.
type Email struct {
	ID      string
	Subject string
	From    string
	Snippet string
	Date    time.Time
	Labels  []string
	Unread  bool
}
