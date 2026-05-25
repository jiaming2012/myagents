package main

import (
	"fmt"
	"os"

	tea "github.com/charmbracelet/bubbletea"

	"github.com/jamal/email-tools/internal/config"
	"github.com/jamal/email-tools/internal/pipeline"
)

func runView(args []string) {
	root := config.ProjectRoot()

	insights, err := pipeline.LoadInsightsManifest(root)
	if err != nil {
		fmt.Fprintf(os.Stderr, "Error loading insights: %v\n", err)
		os.Exit(1)
	}
	if insights == nil || len(insights.Insights) == 0 {
		fmt.Println("No insights available. Run 'task email:important:analyze' first.")
		return
	}

	// Group by account
	acctMap := make(map[string][]pipeline.EmailInsight)
	var acctOrder []string
	seen := make(map[string]bool)
	for _, ins := range insights.Insights {
		if !seen[ins.AccountName] {
			seen[ins.AccountName] = true
			acctOrder = append(acctOrder, ins.AccountName)
		}
		acctMap[ins.AccountName] = append(acctMap[ins.AccountName], ins)
	}

	var accounts []acctData
	for _, name := range acctOrder {
		accounts = append(accounts, acctData{
			name:    name,
			insights: acctMap[name],
		})
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
		total += len(a.insights)
	}
	fmt.Printf("%d email(s) across %d accounts.\n", total, len(fm.accounts))
}
