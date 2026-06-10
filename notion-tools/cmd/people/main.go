// Command people manages the keep-in-touch workflow: databases tagged
// triage:people get scanned, overdue rows become Todoist follow-up tasks,
// and Todoist completions are bridged back to Notion's Last Contacted field.
package main

import (
	"fmt"
	"os"
)

const handler = "people"

// CadenceDays maps the Notion Cadence select values to a day count.
var CadenceDays = map[string]int{
	"weekly":    7,
	"monthly":   30,
	"quarterly": 90,
	"yearly":    365,
}

// MarkerPrefix is what we embed in each Todoist task description so the
// bridge can find rows on completion. Format: `notion-people:<row_id>`.
const MarkerPrefix = "notion-people:"

// Required property names on every triage:people DB.
const (
	PropName          = "Name"
	PropLastContacted = "Last Contacted"
	PropCadence       = "Cadence"
)

func main() {
	if len(os.Args) < 2 {
		printUsage()
		os.Exit(1)
	}
	switch os.Args[1] {
	case "download":
		runDownload(os.Args[2:])
	case "apply":
		runApply(os.Args[2:])
	case "status":
		runStatus(os.Args[2:])
	default:
		printUsage()
		os.Exit(1)
	}
}

func printUsage() {
	fmt.Fprintln(os.Stderr, `Usage: people <command>

Commands:
  download   Bridge Todoist completions → Notion, then list overdue people
  apply      Push follow-up tasks to Todoist with row markers
  status     Show pipeline state`)
}

func fatal(format string, args ...any) {
	fmt.Fprintf(os.Stderr, "ERROR: "+format+"\n", args...)
	os.Exit(1)
}
