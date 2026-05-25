package email

import "time"

// Email represents a single email message across any provider.
type Email struct {
	ID      string
	Subject string
	From    string
	Snippet string
	Body    string
	Date    time.Time
	Labels  []string
	Unread  bool
}

// MaxBodyLen is the max characters of email body to retain.
const MaxBodyLen = 2000
