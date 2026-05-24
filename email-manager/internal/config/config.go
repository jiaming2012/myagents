package config

import (
	"fmt"
	"os"
	"path/filepath"

	"github.com/joho/godotenv"
	"gopkg.in/yaml.v3"
)

type Account struct {
	Name      string `yaml:"name"`
	Email     string `yaml:"email"`
	Provider  string `yaml:"provider"`
	TokenFile string `yaml:"token_file"`
}

type CleanupConfig struct {
	OlderThanDays int      `yaml:"older_than_days"`
	Categories    []string `yaml:"categories"`
}

type Config struct {
	Accounts []Account     `yaml:"accounts"`
	Cleanup  CleanupConfig `yaml:"cleanup"`

	// From .env
	GmailClientID     string
	GmailClientSecret string
	ZohoClientID      string
	ZohoClientSecret  string
	ZohoRefreshToken  string
	ZohoAccountID     string
}

// Load reads accounts.yaml and .env from the project root directory.
func Load(rootDir string) (*Config, error) {
	// Load .env (ignore error if missing)
	_ = godotenv.Load(filepath.Join(rootDir, ".env"))

	// Read accounts.yaml
	data, err := os.ReadFile(filepath.Join(rootDir, "accounts.yaml"))
	if err != nil {
		return nil, fmt.Errorf("reading accounts.yaml: %w", err)
	}

	var cfg Config
	if err := yaml.Unmarshal(data, &cfg); err != nil {
		return nil, fmt.Errorf("parsing accounts.yaml: %w", err)
	}

	// Populate env vars
	cfg.GmailClientID = os.Getenv("GMAIL_CLIENT_ID")
	cfg.GmailClientSecret = os.Getenv("GMAIL_CLIENT_SECRET")
	cfg.ZohoClientID = os.Getenv("ZOHO_CLIENT_ID")
	cfg.ZohoClientSecret = os.Getenv("ZOHO_CLIENT_SECRET")
	cfg.ZohoRefreshToken = os.Getenv("ZOHO_REFRESH_TOKEN")
	cfg.ZohoAccountID = os.Getenv("ZOHO_ACCOUNT_ID")

	return &cfg, nil
}

// ProjectRoot walks up from the executable to find accounts.yaml.
// Falls back to current working directory.
func ProjectRoot() string {
	// Try CWD first
	if _, err := os.Stat("accounts.yaml"); err == nil {
		cwd, _ := os.Getwd()
		return cwd
	}

	// Try executable directory
	exe, err := os.Executable()
	if err == nil {
		dir := filepath.Dir(exe)
		if _, err := os.Stat(filepath.Join(dir, "accounts.yaml")); err == nil {
			return dir
		}
	}

	cwd, _ := os.Getwd()
	return cwd
}
