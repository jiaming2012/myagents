package main

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/jamal/email-tools/internal/pipeline"
	"github.com/jamal/email-tools/internal/todoist"
)

var (
	activeTab = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("10"))
	dimStyle  = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	fromStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("14"))
	acctStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("13"))
	helpStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))

	urgentStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("9"))
	actionStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("11"))
	fyiStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("12"))
	topicStyle  = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("5"))
	summaryStyle = lipgloss.NewStyle().Foreground(lipgloss.Color("7"))
)

// --- data types ---

type acctData struct {
	name     string
	insights []pipeline.EmailInsight
}

type categoryTab struct {
	topic string
	items []pipeline.EmailInsight
	count int
}

type displayItem struct {
	isHeader bool
	label    string
	count    int
	item     pipeline.EmailInsight
}

type actionItem struct {
	text     string
	subject  string
	from     string
	topic    string
	selected bool
	uploaded bool
}

// --- model ---

type model struct {
	accounts   []acctData
	all        []pipeline.EmailInsight // all insights
	acctIdx    int                     // -1 = All, 0..n = specific account
	categories []categoryTab
	catIdx     int
	allGrouped bool // true = group by topic in All tab
	hideFYI    bool // true = hide FYI urgency emails
	cursor     int
	offset     int
	height     int
	width      int
	quitting   bool
	items      []displayItem

	// Action items view
	actionView    bool
	actionItems   []actionItem
	actionCursor  int
	actionOffset  int
	confirming    bool // waiting for y/n to upload
	statusMsg     string
	todoistClient *todoist.Client
}

func newModel(accounts []acctData, tc *todoist.Client) model {
	m := model{
		accounts:      accounts,
		acctIdx:       -1,
		hideFYI:       true,
		height:        24,
		width:         80,
		todoistClient: tc,
	}

	for _, acct := range accounts {
		m.all = append(m.all, acct.insights...)
	}

	m.rebuildCategories()
	m.buildActionItems()
	return m
}

func (m *model) buildActionItems() {
	m.actionItems = nil
	for _, ins := range m.all {
		for _, item := range ins.ActionItems {
			m.actionItems = append(m.actionItems, actionItem{
				text:     item,
				subject:  ins.Subject,
				from:     ins.From,
				topic:    ins.Topic,
				selected: true,
			})
		}
	}
}

func (m *model) rebuildCategories() {
	var filtered []pipeline.EmailInsight
	for _, ins := range m.all {
		if m.acctIdx != -1 && ins.AccountName != m.accounts[m.acctIdx].name {
			continue
		}
		if m.hideFYI && ins.Urgency == "fyi" {
			continue
		}
		filtered = append(filtered, ins)
	}

	byTopic := make(map[string][]pipeline.EmailInsight)
	for _, ins := range filtered {
		byTopic[ins.Topic] = append(byTopic[ins.Topic], ins)
	}

	// "All" category first
	m.categories = []categoryTab{{
		topic: "All",
		items: filtered,
		count: len(filtered),
	}}

	// Sort topics by count descending
	type topicCount struct {
		topic string
		count int
	}
	var topics []topicCount
	for t, items := range byTopic {
		topics = append(topics, topicCount{t, len(items)})
	}
	sort.Slice(topics, func(i, j int) bool {
		return topics[i].count > topics[j].count
	})

	for _, tc := range topics {
		m.categories = append(m.categories, categoryTab{
			topic: tc.topic,
			items: byTopic[tc.topic],
			count: tc.count,
		})
	}

	if m.catIdx >= len(m.categories) {
		m.catIdx = 0
	}
	m.cursor = 0
	m.offset = 0
	m.items = m.buildItems()
}

func (m model) Init() tea.Cmd { return nil }

func (m model) buildItems() []displayItem {
	if len(m.categories) == 0 {
		return nil
	}

	cat := m.categories[m.catIdx]

	// "All" tab with grouping
	if cat.topic == "All" && m.allGrouped {
		byTopic := make(map[string][]pipeline.EmailInsight)
		for _, ins := range cat.items {
			byTopic[ins.Topic] = append(byTopic[ins.Topic], ins)
		}

		var display []displayItem
		for _, tab := range m.categories[1:] {
			items, ok := byTopic[tab.topic]
			if !ok {
				continue
			}
			sort.Slice(items, func(i, j int) bool {
				return items[i].Date.After(items[j].Date)
			})
			display = append(display, displayItem{
				isHeader: true,
				label:    tab.topic,
				count:    len(items),
			})
			for _, ins := range items {
				display = append(display, displayItem{item: ins})
			}
		}
		return display
	}

	// Flat list sorted by timestamp
	items := make([]pipeline.EmailInsight, len(cat.items))
	copy(items, cat.items)
	sort.Slice(items, func(i, j int) bool {
		return items[i].Date.After(items[j].Date)
	})

	var display []displayItem
	for _, ins := range items {
		display = append(display, displayItem{item: ins})
	}
	return display
}

func (m model) navigableLen() int {
	n := 0
	for _, item := range m.items {
		if !item.isHeader {
			n++
		}
	}
	return n
}

func (m model) cursorToItemIdx(cursor int) int {
	n := 0
	for i, item := range m.items {
		if !item.isHeader {
			if n == cursor {
				return i
			}
			n++
		}
	}
	return len(m.items) - 1
}

// uploadResultMsg is sent after the Todoist upload goroutine completes.
type uploadResultMsg struct {
	uploaded int
	err      error
}

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case uploadResultMsg:
		if msg.err != nil {
			m.statusMsg = fmt.Sprintf("Upload failed: %v", msg.err)
		} else {
			m.statusMsg = fmt.Sprintf("Uploaded %d tasks to Todoist", msg.uploaded)
			// Mark uploaded items
			count := 0
			for i := range m.actionItems {
				if m.actionItems[i].selected && !m.actionItems[i].uploaded {
					m.actionItems[i].uploaded = true
					m.actionItems[i].selected = false
					count++
					if count >= msg.uploaded {
						break
					}
				}
			}
		}
		m.confirming = false
		return m, nil

	case tea.WindowSizeMsg:
		m.height = msg.Height
		m.width = msg.Width

	case tea.KeyMsg:
		// Confirmation dialog
		if m.confirming {
			switch msg.String() {
			case "y":
				m.statusMsg = "Uploading..."
				return m, m.uploadToTodoist()
			case "n", "esc":
				m.confirming = false
				m.statusMsg = ""
			}
			return m, nil
		}

		// Action items view
		if m.actionView {
			return m.updateActionView(msg)
		}

		// Email view
		switch msg.String() {
		case "q", "ctrl+c":
			m.quitting = true
			return m, tea.Quit

		case "esc":
			m.quitting = true
			return m, tea.Quit

		case "up", "k":
			if m.cursor > 0 {
				m.cursor--
				m.adjustScroll()
			}

		case "down", "j":
			if m.cursor < m.navigableLen()-1 {
				m.cursor++
				m.adjustScroll()
			}

		case "tab":
			if len(m.categories) > 1 {
				m.catIdx = (m.catIdx + 1) % len(m.categories)
				m.cursor = 0
				m.offset = 0
				m.items = m.buildItems()
			}

		case "shift+tab":
			if len(m.categories) > 1 {
				m.catIdx = (m.catIdx - 1 + len(m.categories)) % len(m.categories)
				m.cursor = 0
				m.offset = 0
				m.items = m.buildItems()
			}

		case "g":
			if len(m.categories) > 0 && m.categories[m.catIdx].topic == "All" {
				m.allGrouped = !m.allGrouped
				m.cursor = 0
				m.offset = 0
				m.items = m.buildItems()
			}

		case "f":
			m.hideFYI = !m.hideFYI
			m.rebuildCategories()

		case "t":
			if len(m.actionItems) > 0 {
				m.actionView = true
				m.actionCursor = 0
				m.actionOffset = 0
				m.statusMsg = ""
			}

		case "right", "l":
			m.acctIdx++
			if m.acctIdx >= len(m.accounts) {
				m.acctIdx = -1
			}
			m.rebuildCategories()

		case "left", "h":
			m.acctIdx--
			if m.acctIdx < -1 {
				m.acctIdx = len(m.accounts) - 1
			}
			m.rebuildCategories()
		}
	}
	return m, nil
}

func (m model) updateActionView(msg tea.KeyMsg) (tea.Model, tea.Cmd) {
	switch msg.String() {
	case "q", "ctrl+c":
		m.quitting = true
		return m, tea.Quit

	case "esc":
		m.actionView = false
		m.statusMsg = ""

	case "up", "k":
		if m.actionCursor > 0 {
			m.actionCursor--
		}

	case "down", "j":
		if m.actionCursor < len(m.actionItems)-1 {
			m.actionCursor++
		}

	case " ":
		if m.actionCursor < len(m.actionItems) {
			m.actionItems[m.actionCursor].selected = !m.actionItems[m.actionCursor].selected
		}

	case "a":
		// Toggle all: if all selected, deselect all; otherwise select all
		allSelected := true
		for _, ai := range m.actionItems {
			if !ai.selected && !ai.uploaded {
				allSelected = false
				break
			}
		}
		for i := range m.actionItems {
			if !m.actionItems[i].uploaded {
				m.actionItems[i].selected = !allSelected
			}
		}

	case "u":
		if m.todoistClient == nil {
			m.statusMsg = "No TODOIST_API_TOKEN configured"
			break
		}
		selected := 0
		for _, ai := range m.actionItems {
			if ai.selected && !ai.uploaded {
				selected++
			}
		}
		if selected == 0 {
			m.statusMsg = "No items selected"
			break
		}
		m.confirming = true
		m.statusMsg = fmt.Sprintf("Upload %d items to Todoist? (y/n)", selected)
	}
	return m, nil
}

func (m *model) uploadToTodoist() tea.Cmd {
	// Collect items to upload
	type uploadItem struct {
		idx  int
		text string
		desc string
	}
	var items []uploadItem
	for i, ai := range m.actionItems {
		if ai.selected && !ai.uploaded {
			items = append(items, uploadItem{
				idx:  i,
				text: ai.text,
				desc: fmt.Sprintf("From: %s\nRe: %s", ai.from, ai.subject),
			})
		}
	}

	client := m.todoistClient
	return func() tea.Msg {
		ctx := context.Background()
		uploaded := 0
		for _, item := range items {
			err := client.CreateTask(ctx, todoist.Task{
				Content:     item.text,
				Description: item.desc,
			})
			if err != nil {
				return uploadResultMsg{uploaded: uploaded, err: err}
			}
			uploaded++
		}
		return uploadResultMsg{uploaded: uploaded}
	}
}

func (m *model) adjustScroll() {
	itemIdx := m.cursorToItemIdx(m.cursor)
	if itemIdx < m.offset {
		m.offset = itemIdx
		if m.offset > 0 && m.items[m.offset-1].isHeader {
			m.offset--
		}
	}
	lines := 0
	visible := m.height - 10
	for i := m.offset; i < len(m.items); i++ {
		if m.items[i].isHeader {
			lines += 2
		} else {
			lines += 3
		}
		if i > itemIdx && lines > visible {
			break
		}
	}
	if lines > visible {
		m.offset = itemIdx
		if m.offset > 0 && m.items[m.offset-1].isHeader {
			m.offset--
		}
	}
}

func (m model) View() string {
	if m.quitting {
		return ""
	}

	if m.actionView {
		return m.viewActionItems()
	}

	var b strings.Builder

	// === Row 1: Account tabs ===
	var acctTabs []string
	allCount := len(m.all)
	if m.acctIdx == -1 {
		acctTabs = append(acctTabs, activeTab.Render(fmt.Sprintf("[All (%d)]", allCount)))
	} else {
		acctTabs = append(acctTabs, dimStyle.Render(fmt.Sprintf(" All (%d) ", allCount)))
	}
	for i, a := range m.accounts {
		label := fmt.Sprintf("%s (%d)", a.name, len(a.insights))
		if i == m.acctIdx {
			acctTabs = append(acctTabs, activeTab.Render("["+label+"]"))
		} else {
			acctTabs = append(acctTabs, dimStyle.Render(" "+label+" "))
		}
	}
	b.WriteString(strings.Join(acctTabs, " ") + "\n")

	// === Row 2: Category tabs ===
	if len(m.categories) == 0 {
		b.WriteString("\n  No emails for this filter.\n")
		b.WriteString("\n\n")
		b.WriteString(m.renderHelp())
		return b.String()
	}

	var catTabs []string
	for i, cat := range m.categories {
		label := fmt.Sprintf("%s (%d)", cat.topic, cat.count)
		if i == m.catIdx {
			catTabs = append(catTabs, activeTab.Render("["+label+"]"))
		} else {
			catTabs = append(catTabs, dimStyle.Render(" "+label+" "))
		}
	}
	b.WriteString(strings.Join(catTabs, " ") + "\n\n")

	// === Email list ===
	lines := 0
	maxLines := m.height - 10
	navIdx := 0

	for i, item := range m.items {
		if i < m.offset {
			if !item.isHeader {
				navIdx++
			}
			continue
		}

		if item.isHeader {
			if lines+2 > maxLines {
				break
			}
			b.WriteString(topicStyle.Render(fmt.Sprintf("  === %s (%d) ===", item.label, item.count)))
			b.WriteString("\n\n")
			lines += 2
		} else {
			if lines+4 > maxLines {
				break
			}

			ins := item.item
			cursor := "  "
			if navIdx == m.cursor {
				cursor = "> "
			}

			unreadMark := " "
			if ins.Unread {
				unreadMark = "●"
			}

			urgTag := urgencyTag(ins.Urgency)

			subj := ins.Subject
			maxSubj := m.width - 30
			if maxSubj < 30 {
				maxSubj = 30
			}
			if len(subj) > maxSubj {
				subj = subj[:maxSubj-3] + "..."
			}

			age := formatAge(time.Since(ins.Date))

			if navIdx == m.cursor {
				b.WriteString(activeTab.Render(cursor) + fmt.Sprintf("%s %s %q  %s\n", unreadMark, urgTag, subj, dimStyle.Render(age+" ago")))
			} else {
				b.WriteString(fmt.Sprintf("%s%s %s %q  %s\n", cursor, unreadMark, urgTag, subj, dimStyle.Render(age+" ago")))
			}
			b.WriteString(fmt.Sprintf("      %s — %s\n", fromStyle.Render(ins.From), acctStyle.Render(ins.AccountName)))

			// Show AI summary instead of snippet
			summary := ins.Summary
			if summary == "" {
				summary = ins.Snippet
			}
			maxSnip := m.width - 10
			if maxSnip < 40 {
				maxSnip = 40
			}
			if len(summary) > maxSnip {
				summary = summary[:maxSnip-3] + "..."
			}
			b.WriteString(fmt.Sprintf("      %s\n", summaryStyle.Render(summary)))

			lines += 3
			navIdx++
		}
	}

	// Detail pane for selected email
	if m.navigableLen() > 0 {
		idx := m.cursorToItemIdx(m.cursor)
		if idx < len(m.items) && !m.items[idx].isHeader {
			ins := m.items[idx].item
			if ins.WhyImportant != "" {
				b.WriteString("\n")
				b.WriteString(dimStyle.Render("  Why: ") + ins.WhyImportant + "\n")
			}
			if len(ins.ActionItems) > 0 {
				b.WriteString(actionStyle.Render("  Action items:") + "\n")
				for _, item := range ins.ActionItems {
					b.WriteString("    → " + item + "\n")
				}
			}
		}
	}

	// Scroll indicator
	total := m.navigableLen()
	if total > 0 {
		b.WriteString(dimStyle.Render(fmt.Sprintf("\n  %d of %d", m.cursor+1, total)))
	}

	b.WriteString("\n\n")
	b.WriteString(m.renderHelp())

	return b.String()
}

func urgencyTag(urgency string) string {
	switch urgency {
	case "urgent":
		return urgentStyle.Render("[URGENT]")
	case "action_needed":
		return actionStyle.Render("[ACTION]")
	default:
		return fyiStyle.Render("[FYI]")
	}
}

func (m model) viewActionItems() string {
	var b strings.Builder

	// Header with counts
	selected := 0
	for _, ai := range m.actionItems {
		if ai.selected && !ai.uploaded {
			selected++
		}
	}
	b.WriteString(activeTab.Render(fmt.Sprintf("=== Action Items (%d selected / %d total) ===", selected, len(m.actionItems))))
	b.WriteString("\n\n")

	// List
	maxLines := m.height - 8
	lines := 0
	for i, ai := range m.actionItems {
		if lines+3 > maxLines {
			break
		}

		cursor := "  "
		if i == m.actionCursor {
			cursor = "> "
		}

		check := "[ ]"
		if ai.uploaded {
			check = dimStyle.Render("[✓]")
		} else if ai.selected {
			check = activeTab.Render("[x]")
		}

		text := ai.text
		maxText := m.width - 10
		if maxText < 40 {
			maxText = 40
		}
		if len(text) > maxText {
			text = text[:maxText-3] + "..."
		}

		if ai.uploaded {
			b.WriteString(fmt.Sprintf("%s%s %s\n", cursor, check, dimStyle.Render(text)))
		} else if i == m.actionCursor {
			b.WriteString(activeTab.Render(cursor) + fmt.Sprintf("%s %s\n", check, text))
		} else {
			b.WriteString(fmt.Sprintf("%s%s %s\n", cursor, check, text))
		}
		b.WriteString(fmt.Sprintf("      %s — %s\n", dimStyle.Render(ai.topic), fromStyle.Render(ai.from)))
		lines += 2
	}

	// Status / confirmation
	if m.statusMsg != "" {
		b.WriteString("\n")
		if m.confirming {
			b.WriteString(actionStyle.Render("  "+m.statusMsg) + "\n")
		} else {
			b.WriteString(activeTab.Render("  "+m.statusMsg) + "\n")
		}
	}

	// Scroll indicator
	if len(m.actionItems) > 0 {
		b.WriteString(dimStyle.Render(fmt.Sprintf("\n  %d of %d", m.actionCursor+1, len(m.actionItems))))
	}

	b.WriteString("\n\n")
	b.WriteString(m.renderActionHelp())

	return b.String()
}

func (m model) renderHelp() string {
	parts := []string{
		"j/k navigate",
		"h/l account",
		"tab category",
		"g group/flat",
		"f toggle FYI",
		"t action items",
		"q quit",
	}
	return helpStyle.Render("  " + strings.Join(parts, "  |  "))
}

func (m model) renderActionHelp() string {
	parts := []string{
		"j/k navigate",
		"space select",
		"a select all",
		"u upload to Todoist",
		"esc back",
	}
	return helpStyle.Render("  " + strings.Join(parts, "  |  "))
}
