package cmd

import (
	"encoding/json"
	"os"
	"sort"
	"strings"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/spf13/cobra"
)

func newLsCmd() *cobra.Command {
	var asJSON bool
	c := &cobra.Command{
		Use:   "ls",
		Short: "List all services and the CT each runs on",
		RunE: func(_ *cobra.Command, _ []string) error {
			idx, err := discover.Load(repo.Locate)
			if err != nil {
				return err
			}

			// Build ct → []service (inverse of the index).
			byCT := map[string][]string{}
			for svc, locs := range idx.Services {
				for _, l := range locs {
					byCT[l.CT] = append(byCT[l.CT], svc)
				}
			}
			cts := make([]string, 0, len(byCT))
			for k := range byCT {
				cts = append(cts, k)
			}
			sort.Strings(cts)
			for _, ct := range cts {
				sort.Strings(byCT[ct])
			}

			if asJSON {
				enc := json.NewEncoder(os.Stdout)
				enc.SetIndent("", "  ")
				return enc.Encode(byCT)
			}

			t := table.NewWriter()
			t.SetOutputMirror(os.Stdout)
			t.AppendHeader(table.Row{"CT", "SERVICES"})
			for _, ct := range cts {
				t.AppendRow(table.Row{ct, strings.Join(byCT[ct], ", ")})
			}
			t.Render()
			return nil
		},
	}
	c.Flags().BoolVar(&asJSON, "json", false, "Emit JSON instead of a table")
	return c
}
