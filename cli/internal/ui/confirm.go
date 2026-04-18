// Package ui has small terminal helpers shared across commands.
package ui

import (
	"bufio"
	"fmt"
	"os"
	"strings"
)

// Confirm prints "<prompt> [y/N] " and returns true on y/yes. Empty = false.
func Confirm(prompt string) (bool, error) {
	fmt.Fprintf(os.Stderr, "%s [y/N] ", prompt)
	r := bufio.NewReader(os.Stdin)
	line, err := r.ReadString('\n')
	if err != nil {
		return false, err
	}
	line = strings.TrimSpace(strings.ToLower(line))
	return line == "y" || line == "yes", nil
}
