// Command brain triages unsorted Notion to-do blocks: pages tagged
// triage:brain → unchecked to_do blocks → Todoist Inbox tasks, with the
// source block stamped to prevent re-queueing.
package main

import (
	"fmt"
	"os"
)

const handler = "brain"

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
	fmt.Fprintln(os.Stderr, `Usage: brain <command>

Commands:
  download   Fetch pages tagged triage:brain, extract unchecked to_do blocks
  apply      Push triaged items to Todoist + stamp Notion source blocks
  status     Show pipeline state`)
}
