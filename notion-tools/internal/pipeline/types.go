package pipeline

import "time"

// Envelope is the common header for every manifest written under .cache/<handler>/.
// Handler-specific payload types embed this.
type Envelope struct {
	Version   int       `json:"version"`
	Handler   string    `json:"handler"`
	CreatedAt time.Time `json:"created_at"`
	UpdatedAt time.Time `json:"updated_at"`
}

// NewEnvelope returns an Envelope with CreatedAt=now and UpdatedAt=now.
func NewEnvelope(handler string) Envelope {
	now := time.Now()
	return Envelope{Version: 1, Handler: handler, CreatedAt: now, UpdatedAt: now}
}

// Touch sets UpdatedAt to now.
func (e *Envelope) Touch() { e.UpdatedAt = time.Now() }
