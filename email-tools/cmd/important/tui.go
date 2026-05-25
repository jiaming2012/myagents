package main

import (
	"fmt"
	"sort"
	"strings"
	"time"

	tea "github.com/charmbracelet/bubbletea"
	"github.com/charmbracelet/lipgloss"

	"github.com/jamal/email-tools/internal/email"
)

var (
	headerStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("12"))
	activeTab   = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("10"))
	dimStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	fromStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("14"))
	acctStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("13"))
	helpStyle   = lipgloss.NewStyle().Foreground(lipgloss.Color("8"))
	countStyle  = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("13"))
	urgentStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("9"))
	actionStyle = lipgloss.NewStyle().Bold(true).Foreground(lipgloss.Color("11"))
	fyiStyle    = lipgloss.NewStyle().Foreground(lipgloss.Color("12"))
)

// --- urgency levels ---

type urgency int

const (
	urgentLevel urgency = iota
	actionLevel
	infoLevel
)

func (u urgency) String() string {
	switch u {
	case urgentLevel:
		return "URGENT"
	case actionLevel:
		return "ACTION NEEDED"
	default:
		return "FYI"
	}
}

func (u urgency) style() lipgloss.Style {
	switch u {
	case urgentLevel:
		return urgentStyle
	case actionLevel:
		return actionStyle
	default:
		return fyiStyle
	}
}

// --- topic categories ---

type topic int

const (
	topicFinance topic = iota
	topicSecurity
	topicWork
	topicSocial
	topicShopping
	topicTravel
	topicHealth
	topicNewsletter
	topicOther
)

var allTopics = []topic{
	topicFinance, topicSecurity, topicWork, topicSocial,
	topicShopping, topicTravel, topicHealth, topicNewsletter, topicOther,
}

func (t topic) String() string {
	switch t {
	case -1:
		return "All"
	case topicFinance:
		return "Finance"
	case topicSecurity:
		return "Security"
	case topicWork:
		return "Work"
	case topicSocial:
		return "Social"
	case topicShopping:
		return "Shopping"
	case topicTravel:
		return "Travel"
	case topicHealth:
		return "Health"
	case topicNewsletter:
		return "Newsletter"
	default:
		return "Other"
	}
}

// --- classification ---

type classified struct {
	email   email.Email
	account string
	urgency urgency
	topic   topic
}

type displayItem struct {
	isHeader bool
	label    string
	style    lipgloss.Style
	count    int
	item     classified
}

func classifyEmail(e email.Email, accountName string) classified {
	subj := strings.ToLower(e.Subject)
	from := strings.ToLower(e.From)

	c := classified{email: e, account: accountName, urgency: infoLevel, topic: topicOther}

	urgentKeywords := []string{"urgent", "suspended", "suspension", "immediately", "action required",
		"verify your", "confirm your identity", "unauthorized", "security alert"}
	actionKeywords := []string{"payment declined", "payment failed", "failed to process",
		"couldn't process", "balance due", "overdue", "reminder:", "requires",
		"update your", "finish setting up", "complete your", "charged your", "has been charged"}

	for _, kw := range urgentKeywords {
		if strings.Contains(subj, kw) || strings.Contains(from, kw) {
			c.urgency = urgentLevel
			break
		}
	}
	if c.urgency != urgentLevel {
		for _, kw := range actionKeywords {
			if strings.Contains(subj, kw) {
				c.urgency = actionLevel
				break
			}
		}
	}

	financeSenders := []string{"mercury", "bluevine", "wave", "xero", "onpay", "coinbase",
		"tradier", "digitalocean", "vultr", "stripe", "paypal", "venmo",
		"bank", "invoice", "receipt", "billing"}
	securitySenders := []string{"no-reply@accounts", "noreply@accounts",
		"security-noreply", "alert@"}
	workSenders := []string{"linear", "github", "slack", "notion", "jira", "gitlab",
		"bitbucket", "asana", "trello", "confluence", "archer"}
	socialSenders := []string{"facebook", "linkedin", "twitter", "instagram",
		"discord", "meetup", "pickleball", "dupr", "podplay"}
	shoppingSenders := []string{"amazon", "ebay", "walmart", "target", "etsy",
		"fiverr", "crexi", "restaurant depot", "restaurantstore"}
	travelSenders := []string{"airbnb", "booking.com", "expedia", "hotels",
		"airline", "united", "delta", "southwest"}
	healthSenders := []string{"health", "medical", "pharmacy", "doctor",
		"hospital", "dental", "insurance"}
	newsletterSenders := []string{"newsletter", "nytimes", "nytdirect", "substack",
		"beehiiv", "the-messenger", "editorpicks", "breakingnews", "whimsical",
		"tradingview", "digest"}

	fromLower := from + " " + subj
	match := func(keywords []string) bool {
		for _, kw := range keywords {
			if strings.Contains(fromLower, kw) {
				return true
			}
		}
		return false
	}

	switch {
	case match(securitySenders) || strings.Contains(subj, "security alert") || strings.Contains(subj, "sign-in"):
		c.topic = topicSecurity
	case match(financeSenders) || strings.Contains(subj, "payment") || strings.Contains(subj, "invoice"):
		c.topic = topicFinance
	case match(workSenders):
		c.topic = topicWork
	case match(travelSenders):
		c.topic = topicTravel
	case match(healthSenders):
		c.topic = topicHealth
	case match(shoppingSenders):
		c.topic = topicShopping
	case match(socialSenders):
		c.topic = topicSocial
	case match(newsletterSenders):
		c.topic = topicNewsletter
	}

	if c.topic == topicSecurity && c.urgency == infoLevel {
		c.urgency = actionLevel
	}

	return c
}

// --- data types ---

type acctData struct {
	name   string
	email  string
	emails []email.Email
}

type categoryTab struct {
	topic topic
	items []classified
	count int
}

// --- model ---

type model struct {
	accounts   []acctData
	all        []classified  // all classified emails
	acctIdx    int           // -1 = All, 0..n = specific account
	categories []categoryTab
	catIdx     int
	allGrouped bool // true = group by topic in All tab, false = flat timeline
	cursor     int
	offset     int
	height     int
	width      int
	quitting   bool
	items      []displayItem
}

func newModel(accounts []acctData) model {
	m := model{
		accounts: accounts,
		acctIdx:  -1, // "All" by default
		height:   24,
		width:    80,
	}

	for _, acct := range accounts {
		for _, e := range acct.emails {
			m.all = append(m.all, classifyEmail(e, acct.name))
		}
	}

	m.rebuildCategories()
	return m
}

func (m *model) rebuildCategories() {
	// Filter by account
	var filtered []classified
	for _, c := range m.all {
		if m.acctIdx == -1 || c.account == m.accounts[m.acctIdx].name {
			filtered = append(filtered, c)
		}
	}

	byTopic := make(map[topic][]classified)
	for _, c := range filtered {
		byTopic[c.topic] = append(byTopic[c.topic], c)
	}

	// "All" category first
	m.categories = []categoryTab{{
		topic: -1,
		items: filtered,
		count: len(filtered),
	}}
	for _, t := range allTopics {
		if items, ok := byTopic[t]; ok {
			m.categories = append(m.categories, categoryTab{
				topic: t,
				items: items,
				count: len(items),
			})
		}
	}

	// Reset category selection if out of bounds
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

	// "All" tab: grouped by topic or flat timeline
	if cat.topic == -1 && m.allGrouped {
		byTopic := make(map[topic][]classified)
		for _, c := range cat.items {
			byTopic[c.topic] = append(byTopic[c.topic], c)
		}

		// Sort each topic's emails by date desc
		for t := range byTopic {
			items := byTopic[t]
			sort.Slice(items, func(i, j int) bool {
				return items[i].email.Date.After(items[j].email.Date)
			})
		}

		// Use the same topic order as the category tabs (skip "All" at index 0)
		var display []displayItem
		for _, tab := range m.categories[1:] {
			items, ok := byTopic[tab.topic]
			if !ok {
				continue
			}
			display = append(display, displayItem{
				isHeader: true,
				label:    tab.topic.String(),
				count:    len(items),
			})
			for _, c := range items {
				display = append(display, displayItem{item: c})
			}
		}
		return display
	}

	// Specific topic tab: flat list sorted by timestamp
	items := make([]classified, len(cat.items))
	copy(items, cat.items)
	sort.Slice(items, func(i, j int) bool {
		return items[i].email.Date.After(items[j].email.Date)
	})

	var display []displayItem
	for _, c := range items {
		display = append(display, displayItem{item: c})
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

func (m model) Update(msg tea.Msg) (tea.Model, tea.Cmd) {
	switch msg := msg.(type) {
	case tea.WindowSizeMsg:
		m.height = msg.Height
		m.width = msg.Width

	case tea.KeyMsg:
		switch msg.String() {
		case "q", "ctrl+c", "esc":
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

		// Category tabs: tab / shift+tab
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

		// Toggle grouped/flat in All tab
		case "g":
			if len(m.categories) > 0 && m.categories[m.catIdx].topic == -1 {
				m.allGrouped = !m.allGrouped
				m.cursor = 0
				m.offset = 0
				m.items = m.buildItems()
			}

		// Account tabs: left/right or h/l
		case "right", "l":
			// cycle: -1 (All) -> 0 -> 1 -> ... -> n-1 -> -1
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

func (m *model) adjustScroll() {
	itemIdx := m.cursorToItemIdx(m.cursor)
	if itemIdx < m.offset {
		m.offset = itemIdx
		if m.offset > 0 && m.items[m.offset-1].isHeader {
			m.offset--
		}
	}
	lines := 0
	visible := m.height - 8
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

	var b strings.Builder

	// === Row 1: Account tabs ===
	var acctTabs []string
	// "All" tab
	allCount := len(m.all)
	if m.acctIdx == -1 {
		acctTabs = append(acctTabs, activeTab.Render(fmt.Sprintf("[All (%d)]", allCount)))
	} else {
		acctTabs = append(acctTabs, dimStyle.Render(fmt.Sprintf(" All (%d) ", allCount)))
	}
	for i, a := range m.accounts {
		label := fmt.Sprintf("%s (%d)", a.name, len(a.emails))
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
		label := fmt.Sprintf("%s (%d)", cat.topic.String(), cat.count)
		if i == m.catIdx {
			catTabs = append(catTabs, activeTab.Render("["+label+"]"))
		} else {
			catTabs = append(catTabs, dimStyle.Render(" "+label+" "))
		}
	}
	b.WriteString(strings.Join(catTabs, " ") + "\n\n")

	// === Email list ===
	lines := 0
	maxLines := m.height - 8
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
			b.WriteString(item.style.Render(fmt.Sprintf("  === %s (%d) ===", item.label, item.count)))
			b.WriteString("\n\n")
			lines += 2
		} else {
			if lines+3 > maxLines {
				break
			}

			e := item.item.email
			cursor := "  "
			if navIdx == m.cursor {
				cursor = "> "
			}

			unreadMark := " "
			if e.Unread {
				unreadMark = "●"
			}

			subj := e.Subject
			maxSubj := m.width - 25
			if maxSubj < 30 {
				maxSubj = 30
			}
			if len(subj) > maxSubj {
				subj = subj[:maxSubj-3] + "..."
			}

			age := formatAge(time.Since(e.Date))
			snippet := e.Snippet
			maxSnip := m.width - 10
			if maxSnip < 40 {
				maxSnip = 40
			}
			if len(snippet) > maxSnip {
				snippet = snippet[:maxSnip-3] + "..."
			}

			acctTag := acctStyle.Render(item.item.account)

			if navIdx == m.cursor {
				b.WriteString(activeTab.Render(cursor) + fmt.Sprintf("%s %q  %s\n", unreadMark, subj, dimStyle.Render(age+" ago")))
			} else {
				b.WriteString(fmt.Sprintf("%s%s %q  %s\n", cursor, unreadMark, subj, dimStyle.Render(age+" ago")))
			}
			b.WriteString(fmt.Sprintf("      %s — %s\n", fromStyle.Render(e.From), acctTag))
			b.WriteString(fmt.Sprintf("      %s\n", dimStyle.Render(snippet)))
			lines += 3
			navIdx++
		}
	}

	// Scroll indicator
	total := m.navigableLen()
	if total > 0 {
		b.WriteString(dimStyle.Render(fmt.Sprintf("\n  %d of %d", m.cursor+1, total)))
	}

	// Help
	b.WriteString("\n\n")
	b.WriteString(m.renderHelp())

	return b.String()
}

func (m model) renderHelp() string {
	parts := []string{
		"j/k navigate",
		"h/l account",
		"tab category",
		"g group/flat",
		"q quit",
	}
	return helpStyle.Render("  " + strings.Join(parts, "  |  "))
}
