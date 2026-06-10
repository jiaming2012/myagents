package config

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"

	"github.com/joho/godotenv"
)

type Config struct {
	NotionAPIToken   string
	TodoistAPIToken  string
	LookbackDays     int
}

// Load reads .env from the repo root (and tool-local .env if present, which
// takes precedence per godotenv's load-if-unset semantics: tool-local first,
// then repo root fills in anything missing).
func Load(rootDir string) (*Config, error) {
	parent := filepath.Dir(rootDir)
	_ = godotenv.Load(filepath.Join(rootDir, ".env"))
	_ = godotenv.Load(filepath.Join(parent, ".env"))

	cfg := &Config{
		NotionAPIToken:  os.Getenv("NOTION_API_TOKEN"),
		TodoistAPIToken: os.Getenv("TODOIST_API_TOKEN"),
		LookbackDays:    14,
	}
	if s := os.Getenv("NOTION_LOOKBACK_DAYS"); s != "" {
		n, err := strconv.Atoi(s)
		if err != nil {
			return nil, fmt.Errorf("NOTION_LOOKBACK_DAYS=%q: %w", s, err)
		}
		cfg.LookbackDays = n
	}
	return cfg, nil
}

// ProjectRoot returns the notion-tools/ directory. Walks up from cwd looking
// for go.mod with `module github.com/jamal/notion-tools`.
func ProjectRoot() string {
	if dir, err := os.Getwd(); err == nil {
		for d := dir; ; {
			if matchesNotionModule(filepath.Join(d, "go.mod")) {
				return d
			}
			parent := filepath.Dir(d)
			if parent == d {
				break
			}
			d = parent
		}
	}
	cwd, _ := os.Getwd()
	return cwd
}

func matchesNotionModule(goModPath string) bool {
	data, err := os.ReadFile(goModPath)
	if err != nil {
		return false
	}
	return len(data) > 0 && containsLine(data, "module github.com/jamal/notion-tools")
}

func containsLine(data []byte, needle string) bool {
	s := string(data)
	for i := 0; i+len(needle) <= len(s); i++ {
		if s[i:i+len(needle)] == needle {
			return true
		}
	}
	return false
}
