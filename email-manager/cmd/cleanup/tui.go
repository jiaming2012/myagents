package main

import (
	"context"
	"fmt"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/jamal/email-manager/internal/email"
	"github.com/jamal/email-manager/internal/provider"
)

// --- styles ---

var (
	headerStyle   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("12"))
	selectedStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("10"))
	dimStyle      = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	deleteStyle   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("9"))
	helpStyle     = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	statusStyle   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("11"))
	countStyle    = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("13"))
)

// --- data types ---

type accountData struct {
	name     string
	email    string
	provider provider.EmailProvider
	groups   []senderGroup
	total    int
}

// --- messages ---

type deleteResultMsg struct {
	count int
	err   error
}

// --- view modes ---

type viewMode int

const (
	viewGroups  viewMode = iota // browsing sender groups
	viewExpand                  // viewing individual emails in a group
	viewConfirm                 // confirming deletion
)

// --- model ---

type model struct {
	accounts     []accountData
	acctIdx      int
	cursor       int
	selected     map[int]bool // selected group indices (in group view) or email indices (in expand view)
	mode         viewMode
	expandIdx    int // which group is expanded
	expandSel    map[int]bool
	offset       int // scroll offset
	height       int // terminal height
	width        int // terminal width
	totalDeleted int
	status       string
	quitting     bool
	deleting     bool
	dryRun       bool
}

func newModel(accounts []accountData) model {
	return model{
		accounts: accounts,
		selected: make(map[int]bool),
		height:   24,
		width:    80,
	}
}

func (m model) Init() tea.Cmd {
	return nil
}

func (m model) currentAccount() accountData {
	return m.accounts[m.acctIdx]
}

func (m model) listLen() int {
	switch m.mode {
	case viewGroups:
		return len(m.currentAccount().groups)
	case viewExpand:
		return len(m.currentAccount().groups[m.expandIdx].emails)
	default:
		return 0
	}
}

// visible items that fit on screen (each item takes 2 lines)
func (m model) pageSize() int {
	// reserve lines for header (3), footer/help (3), status (1)
	available := m.height - 7
	// each item is 2 lines (name + subject/snippet)
	ps := available / 2
	if ps < 3 {
		ps = 3
	}
	return ps
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.height = msg.Height
		m.width = msg.Width
		return m, nil

	case deleteResultMsg:
		m.deleting = false
		if msg.err != nil {
			m.status = fmt.Sprintf("Error: %v", msg.err)
		} else {
			m.totalDeleted += msg.count
			m.status = fmt.Sprintf("Deleted %d emails.", msg.count)
			// Remove deleted groups/emails and advance
			m = m.removeSelected()
		}
		return m, nil

	case tea.KeyMsg:
		if m.deleting {
			return m, nil
		}

		switch msg.String() {
		case "q", "ctrl+c":
			m.quitting = true
			return m, tea.Quit

		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
				if m.cursor < m.offset {
					m.offset = m.cursor
				}
			}

		case "down", "j":
			if m.cursor < m.listLen()-1 {
				m.cursor++
				if m.cursor >= m.offset+m.pageSize() {
					m.offset = m.cursor - m.pageSize() + 1
				}
			}

		case " ":
			switch m.mode {
			case viewGroups:
				m.selected[m.cursor] = !m.selected[m.cursor]
				if !m.selected[m.cursor] {
					delete(m.selected, m.cursor)
				}
			case viewExpand:
				m.expandSel[m.cursor] = !m.expandSel[m.cursor]
				if !m.expandSel[m.cursor] {
					delete(m.expandSel, m.cursor)
				}
			}

		case "a":
			// select/deselect all
			switch m.mode {
			case viewGroups:
				allSelected := len(m.selected) == m.listLen()
				m.selected = make(map[int]bool)
				if !allSelected {
					for i := 0; i < m.listLen(); i++ {
						m.selected[i] = true
					}
				}
			case viewExpand:
				allSelected := len(m.expandSel) == m.listLen()
				m.expandSel = make(map[int]bool)
				if !allSelected {
					for i := 0; i < m.listLen(); i++ {
						m.expandSel[i] = true
					}
				}
			}

		case "enter", "e":
			if m.mode == viewGroups && m.listLen() > 0 {
				m.mode = viewExpand
				m.expandIdx = m.cursor
				m.expandSel = make(map[int]bool)
				m.cursor = 0
				m.offset = 0
			}

		case "esc", "backspace":
			if m.mode == viewExpand {
				m.mode = viewGroups
				m.cursor = m.expandIdx
				m.offset = 0
				if m.cursor >= m.pageSize() {
					m.offset = m.cursor - m.pageSize() + 1
				}
			}

		case "d":
			return m.handleDelete()

		case "tab":
			// next account
			if m.mode == viewGroups && len(m.accounts) > 1 {
				m.acctIdx = (m.acctIdx + 1) % len(m.accounts)
				m.cursor = 0
				m.offset = 0
				m.selected = make(map[int]bool)
				m.status = ""
			}

		case "shift+tab":
			// prev account
			if m.mode == viewGroups && len(m.accounts) > 1 {
				m.acctIdx = (m.acctIdx - 1 + len(m.accounts)) % len(m.accounts)
				m.cursor = 0
				m.offset = 0
				m.selected = make(map[int]bool)
				m.status = ""
			}
		}
	}

	return m, nil
}

func (m model) handleDelete() (model, tea.Cmd) {
	if m.dryRun {
		m.status = "dry run — delete disabled"
		return m, nil
	}

	acct := m.currentAccount()

	var toDelete []email.Email

	switch m.mode {
	case viewGroups:
		if len(m.selected) == 0 {
			// delete group under cursor
			if m.cursor < len(acct.groups) {
				toDelete = acct.groups[m.cursor].emails
				m.selected[m.cursor] = true
			}
		} else {
			for idx := range m.selected {
				if idx < len(acct.groups) {
					toDelete = append(toDelete, acct.groups[idx].emails...)
				}
			}
		}

	case viewExpand:
		g := acct.groups[m.expandIdx]
		if len(m.expandSel) == 0 {
			if m.cursor < len(g.emails) {
				toDelete = []email.Email{g.emails[m.cursor]}
				m.expandSel[m.cursor] = true
			}
		} else {
			for idx := range m.expandSel {
				if idx < len(g.emails) {
					toDelete = append(toDelete, g.emails[idx])
				}
			}
		}
	}

	if len(toDelete) == 0 {
		return m, nil
	}

	m.deleting = true
	m.status = fmt.Sprintf("Deleting %d emails...", len(toDelete))

	ids := extractIDs(toDelete)
	p := acct.provider

	return m, func() tea.Msg {
		err := p.DeleteEmails(context.Background(), ids)
		if err != nil {
			return deleteResultMsg{err: err}
		}
		return deleteResultMsg{count: len(ids)}
	}
}

func (m model) removeSelected() model {
	switch m.mode {
	case viewGroups:
		var remaining []senderGroup
		for i, g := range m.currentAccount().groups {
			if !m.selected[i] {
				remaining = append(remaining, g)
			}
		}
		m.accounts[m.acctIdx].groups = remaining
		m.accounts[m.acctIdx].total = 0
		for _, g := range remaining {
			m.accounts[m.acctIdx].total += len(g.emails)
		}
		m.selected = make(map[int]bool)
		if m.cursor >= len(remaining) && m.cursor > 0 {
			m.cursor = len(remaining) - 1
		}
		// Remove account if empty
		if len(remaining) == 0 && len(m.accounts) > 1 {
			m.accounts = append(m.accounts[:m.acctIdx], m.accounts[m.acctIdx+1:]...)
			if m.acctIdx >= len(m.accounts) {
				m.acctIdx = 0
			}
			m.cursor = 0
			m.offset = 0
		}

	case viewExpand:
		g := m.currentAccount().groups[m.expandIdx]
		var remaining []email.Email
		for i, e := range g.emails {
			if !m.expandSel[i] {
				remaining = append(remaining, e)
			}
		}
		m.expandSel = make(map[int]bool)
		if len(remaining) == 0 {
			// Remove the whole group
			groups := m.currentAccount().groups
			groups = append(groups[:m.expandIdx], groups[m.expandIdx+1:]...)
			m.accounts[m.acctIdx].groups = groups
			m.mode = viewGroups
			if m.expandIdx >= len(groups) && m.expandIdx > 0 {
				m.cursor = len(groups) - 1
			} else {
				m.cursor = m.expandIdx
			}
			m.offset = 0
		} else {
			m.accounts[m.acctIdx].groups[m.expandIdx].emails = remaining
			if m.cursor >= len(remaining) {
				m.cursor = len(remaining) - 1
			}
		}
		// Recalculate total
		total := 0
		for _, g := range m.currentAccount().groups {
			total += len(g.emails)
		}
		m.accounts[m.acctIdx].total = total
	}

	return m
}

func (m model) View() string {
	if m.quitting {
		return ""
	}

	if len(m.accounts) == 0 {
		return "Nothing to clean up.\n"
	}

	var b strings.Builder
	acct := m.currentAccount()

	// Header
	acctTabs := m.renderAccountTabs()
	b.WriteString(acctTabs + "\n")
	header := fmt.Sprintf("%s (%s) — %d emails, %d groups", acct.name, acct.email, acct.total, len(acct.groups))
	if m.dryRun {
		header += "  " + dimStyle.Render("(dry run)")
	}
	b.WriteString(headerStyle.Render(header))
	b.WriteString("\n\n")

	// List
	switch m.mode {
	case viewGroups:
		b.WriteString(m.renderGroups())
	case viewExpand:
		g := acct.groups[m.expandIdx]
		b.WriteString(dimStyle.Render(fmt.Sprintf("  Group: %s (%d emails)", g.sender, len(g.emails))))
		b.WriteString("\n\n")
		b.WriteString(m.renderEmails(g.emails, m.expandSel))
	}

	// Status
	if m.status != "" {
		b.WriteString("\n")
		b.WriteString(statusStyle.Render("  "+m.status))
	}

	// Help
	b.WriteString("\n\n")
	b.WriteString(m.renderHelp())

	return b.String()
}

func (m model) renderAccountTabs() string {
	if len(m.accounts) <= 1 {
		return ""
	}
	var tabs []string
	for i, a := range m.accounts {
		label := a.name
		if i == m.acctIdx {
			tabs = append(tabs, selectedStyle.Render("["+label+"]"))
		} else {
			tabs = append(tabs, dimStyle.Render(" "+label+" "))
		}
	}
	return strings.Join(tabs, "  ")
}

func (m model) renderGroups() string {
	var b strings.Builder
	groups := m.currentAccount().groups
	ps := m.pageSize()
	end := m.offset + ps
	if end > len(groups) {
		end = len(groups)
	}

	for i := m.offset; i < end; i++ {
		g := groups[i]
		cursor := "  "
		if i == m.cursor {
			cursor = "> "
		}
		check := "[ ]"
		if m.selected[i] {
			check = "[x]"
		}

		label := groupSubjectLabel(g)
		countStr := countStyle.Render(fmt.Sprintf("%d", len(g.emails)))
		ageStr := dimStyle.Render(formatRange(g.oldest, g.newest))

		line := fmt.Sprintf("%s%s %s — %s email(s), %s", cursor, check, g.sender, countStr, ageStr)
		if i == m.cursor {
			line = selectedStyle.Render(fmt.Sprintf("%s%s %s", cursor, check, g.sender)) +
				fmt.Sprintf(" — %s email(s), %s", countStr, ageStr)
		}
		b.WriteString(line + "\n")
		b.WriteString(fmt.Sprintf("       %s\n", dimStyle.Render(label)))
	}

	if len(groups) > ps {
		b.WriteString(dimStyle.Render(fmt.Sprintf("\n  showing %d-%d of %d groups", m.offset+1, end, len(groups))))
	}

	return b.String()
}

func (m model) renderEmails(emails []email.Email, sel map[int]bool) string {
	var b strings.Builder
	ps := m.pageSize()
	end := m.offset + ps
	if end > len(emails) {
		end = len(emails)
	}

	for i := m.offset; i < end; i++ {
		e := emails[i]
		cursor := "  "
		if i == m.cursor {
			cursor = "> "
		}
		check := "[ ]"
		if sel[i] {
			check = "[x]"
		}

		subj := e.Subject
		if len(subj) > 60 {
			subj = subj[:57] + "..."
		}
		snippet := e.Snippet
		if len(snippet) > 70 {
			snippet = snippet[:67] + "..."
		}

		age := formatAge(time.Since(e.Date))
		line := fmt.Sprintf("%s%s %q  %s", cursor, check, subj, dimStyle.Render(age+" ago"))
		if i == m.cursor {
			line = selectedStyle.Render(fmt.Sprintf("%s%s", cursor, check)) +
				fmt.Sprintf(" %q  %s", subj, dimStyle.Render(age+" ago"))
		}
		b.WriteString(line + "\n")
		b.WriteString(fmt.Sprintf("       %s\n", dimStyle.Render(snippet)))
	}

	if len(emails) > ps {
		b.WriteString(dimStyle.Render(fmt.Sprintf("\n  showing %d-%d of %d emails", m.offset+1, end, len(emails))))
	}

	return b.String()
}

func (m model) renderHelp() string {
	var parts []string
	switch m.mode {
	case viewGroups:
		parts = []string{
			"j/k navigate",
			"space select",
			"a select all",
			"enter expand",
			"d delete",
		}
		if len(m.accounts) > 1 {
			parts = append(parts, "tab next account")
		}
		parts = append(parts, "q quit")
	case viewExpand:
		parts = []string{
			"j/k navigate",
			"space select",
			"a select all",
			"d delete",
			"esc back",
			"q quit",
		}
	}
	return helpStyle.Render("  " + strings.Join(parts, "  |  "))
}
