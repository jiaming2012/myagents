package main

import (
	"context"
	"flag"
	"fmt"
	"os"
	"time"

	"bufio"
	"encoding/json"
	"io"
	"net/http"
	"net/url"
	"path/filepath"
	"strings"

	"golang.org/x/oauth2"
	"golang.org/x/oauth2/google"
	"google.golang.org/api/gmail/v1"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/provider"
	"github.com/joho/godotenv"
)

func main() {
	providerFlag := flag.String("provider", "", "provider to auth (gmail or zoho)")
	emailFlag := flag.String("email", "", "email account to auth (for gmail)")
	flag.Parse()

	if *providerFlag == "" {
		fmt.Fprintln(os.Stderr, "Usage: go run ./cmd/auth --provider gmail --email user@gmail.com")
		os.Exit(1)
	}

	root := config.ProjectRoot()
	cfg, err := config.Load(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading config: %v\n", err)
		os.Exit(1)
	}

	switch *providerFlag {
	case "gmail":
		if err := authGmail(cfg, root, *emailFlag); err != nil {
			fmt.Fprintf(os.Stderr, "Gmail auth error: %v\n", err)
			os.Exit(1)
		}
	case "zoho":
		if err := authZoho(cfg, root); err != nil {
			fmt.Fprintf(os.Stderr, "Zoho auth error: %v\n", err)
			os.Exit(1)
		}
	default:
		fmt.Fprintf(os.Stderr, "Unknown provider: %s\n", *providerFlag)
		os.Exit(1)
	}
}

func authZoho(cfg *config.Config, root string) error {
	if cfg.ZohoClientID == "" || cfg.ZohoClientSecret == "" {
		return fmt.Errorf("ZOHO_CLIENT_ID and ZOHO_CLIENT_SECRET must be set in .env")
	}

	fmt.Println("1. Go to https://api-console.zoho.com/")
	fmt.Println("2. Select your Self Client")
	fmt.Println("3. Generate a code with scope: ZohoMail.messages.ALL,ZohoMail.folders.READ,ZohoMail.accounts.READ")
	fmt.Print("\nPaste the generated code: ")

	scanner := bufio.NewScanner(os.Stdin)
	if !scanner.Scan() {
		return fmt.Errorf("no input received")
	}
	code := strings.TrimSpace(scanner.Text())
	if code == "" {
		return fmt.Errorf("empty code")
	}

	// Exchange code for tokens
	data := url.Values{
		"code":          {code},
		"client_id":     {cfg.ZohoClientID},
		"client_secret": {cfg.ZohoClientSecret},
		"grant_type":    {"authorization_code"},
	}

	resp, err := http.Post("https://accounts.zoho.com/oauth/v2/token", "application/x-www-form-urlencoded", strings.NewReader(data.Encode()))
	if err != nil {
		return fmt.Errorf("token exchange request: %w", err)
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return fmt.Errorf("reading response: %w", err)
	}

	var result struct {
		AccessToken  string `json:"access_token"`
		RefreshToken string `json:"refresh_token"`
		Error        string `json:"error"`
	}
	if err := json.Unmarshal(body, &result); err != nil {
		return fmt.Errorf("decoding response: %w", err)
	}
	if result.Error != "" {
		return fmt.Errorf("zoho error: %s (raw: %s)", result.Error, string(body))
	}
	if result.RefreshToken == "" {
		return fmt.Errorf("no refresh token in response (raw: %s)", string(body))
	}

	// Fetch account ID using the access token
	accountID := cfg.ZohoAccountID
	if accountID == "" {
		req, err := http.NewRequest("GET", "https://mail.zoho.com/api/accounts", nil)
		if err != nil {
			return fmt.Errorf("accounts request: %w", err)
		}
		req.Header.Set("Authorization", "Zoho-oauthtoken "+result.AccessToken)

		acctResp, err := http.DefaultClient.Do(req)
		if err != nil {
			return fmt.Errorf("fetching account ID: %w", err)
		}
		defer acctResp.Body.Close()

		var acctResult struct {
			Data []struct {
				AccountID string `json:"accountId"`
			} `json:"data"`
		}
		if err := json.NewDecoder(acctResp.Body).Decode(&acctResult); err != nil {
			return fmt.Errorf("decoding accounts: %w", err)
		}
		if len(acctResult.Data) == 0 {
			return fmt.Errorf("no Zoho Mail accounts found")
		}
		accountID = acctResult.Data[0].AccountID
		fmt.Printf("Found Zoho account ID: %s\n", accountID)
	}

	// Update .env file
	envPath := filepath.Join(root, ".env")
	envMap, err := godotenv.Read(envPath)
	if err != nil {
		envMap = make(map[string]string)
	}
	envMap["ZOHO_REFRESH_TOKEN"] = result.RefreshToken
	envMap["ZOHO_ACCOUNT_ID"] = accountID

	if err := godotenv.Write(envMap, envPath); err != nil {
		return fmt.Errorf("writing .env: %w", err)
	}

	fmt.Printf("\nRefresh token saved to %s\n", envPath)
	fmt.Println("You can now use Zoho Mail with email-manager.")
	return nil
}

func authGmail(cfg *config.Config, root, emailAddr string) error {
	if cfg.GmailClientID == "" || cfg.GmailClientSecret == "" {
		return fmt.Errorf("GMAIL_CLIENT_ID and GMAIL_CLIENT_SECRET must be set in .env")
	}

	if emailAddr == "" {
		return fmt.Errorf("--email is required for Gmail auth")
	}

	// Find the account in config
	var acct *config.Account
	for i := range cfg.Accounts {
		if cfg.Accounts[i].Email == emailAddr {
			acct = &cfg.Accounts[i]
			break
		}
	}
	if acct == nil {
		return fmt.Errorf("account %s not found in accounts.yaml", emailAddr)
	}

	oauthCfg := &oauth2.Config{
		ClientID:     cfg.GmailClientID,
		ClientSecret: cfg.GmailClientSecret,
		Endpoint:     google.Endpoint,
		RedirectURL:  "http://localhost:8085/callback",
		Scopes:       []string{gmail.GmailModifyScope},
	}

	// Generate auth URL
	authURL := oauthCfg.AuthCodeURL("state-token", oauth2.AccessTypeOffline, oauth2.ApprovalForce)
	fmt.Printf("\n1. Open this URL in your browser:\n\n   %s\n\n", authURL)
	fmt.Println("2. Sign in with:", emailAddr)
	fmt.Println("3. Authorize the app")
	fmt.Println("\nWaiting for callback on http://localhost:8085/callback ...")

	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Minute)
	defer cancel()

	code, err := provider.StartAuthServer(ctx)
	if err != nil {
		return fmt.Errorf("auth server: %w", err)
	}

	tok, err := oauthCfg.Exchange(ctx, code)
	if err != nil {
		return fmt.Errorf("token exchange: %w", err)
	}

	tokenPath := acct.TokenFile
	if tokenPath != "" && tokenPath[0] != '/' {
		tokenPath = root + "/" + tokenPath
	}

	if err := provider.SaveToken(tokenPath, tok); err != nil {
		return fmt.Errorf("saving token: %w", err)
	}

	fmt.Printf("\nToken saved to %s\n", tokenPath)
	fmt.Println("You can now use 'task cleanup' and 'task important'.")
	return nil
}
