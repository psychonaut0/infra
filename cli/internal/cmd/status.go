package cmd

import (
	"context"
	"encoding/json"
	"fmt"
	"os"
	"sort"
	"strings"
	"sync"
	"time"

	"github.com/jedib0t/go-pretty/v6/table"
	"github.com/psychonaut0/infra/cli/internal/discover"
	"github.com/psychonaut0/infra/cli/internal/repo"
	"github.com/psychonaut0/infra/cli/internal/ssh"
	"github.com/spf13/cobra"
)

// statusRow is one row in the fleet table.
type statusRow struct {
	CT      string `json:"ct"`
	Service string `json:"service"`
	State   string `json:"state"`
	Status  string `json:"status"`
}

func newStatusCmd() *cobra.Command {
	var asJSON bool
	var filterCT string
	c := &cobra.Command{
		Use:   "status",
		Short: "Fleet-wide Docker service overview",
		RunE: func(_ *cobra.Command, _ []string) error {
			idx, err := discover.Load(repo.Locate)
			if err != nil {
				return err
			}

			// Distinct CTs from the index.
			ctSet := map[string]struct{}{}
			for _, locs := range idx.Services {
				for _, l := range locs {
					ctSet[l.CT] = struct{}{}
				}
			}
			if filterCT != "" {
				if _, ok := ctSet[filterCT]; !ok {
					return fmt.Errorf("ct %q not found", filterCT)
				}
				ctSet = map[string]struct{}{filterCT: {}}
			}

			ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
			defer cancel()

			runner := ssh.New()
			rows := make([]statusRow, 0, 32)
			var mu sync.Mutex
			var wg sync.WaitGroup
			errs := make(chan error, len(ctSet))

			for ct := range ctSet {
				wg.Add(1)
				go func(ct string) {
					defer wg.Done()
					// docker ps with pipe-separated, parsable output
					out, err := runner.Output(ctx, ct, `docker ps -a --format "{{.Names}}|{{.State}}|{{.Status}}"`)
					if err != nil {
						errs <- fmt.Errorf("ssh %s: %w", ct, err)
						return
					}
					mu.Lock()
					defer mu.Unlock()
					for _, line := range strings.Split(strings.TrimSpace(string(out)), "\n") {
						if line == "" {
							continue
						}
						parts := strings.SplitN(line, "|", 3)
						if len(parts) != 3 {
							continue
						}
						rows = append(rows, statusRow{
							CT:      ct,
							Service: parts[0],
							State:   parts[1],
							Status:  parts[2],
						})
					}
				}(ct)
			}
			wg.Wait()
			close(errs)

			sort.Slice(rows, func(a, b int) bool {
				if rows[a].CT != rows[b].CT {
					return rows[a].CT < rows[b].CT
				}
				return rows[a].Service < rows[b].Service
			})

			if asJSON {
				enc := json.NewEncoder(os.Stdout)
				enc.SetIndent("", "  ")
				if err := enc.Encode(rows); err != nil {
					return err
				}
			} else {
				t := table.NewWriter()
				t.SetOutputMirror(os.Stdout)
				t.AppendHeader(table.Row{"CT", "SERVICE", "STATE", "STATUS"})
				for _, r := range rows {
					t.AppendRow(table.Row{r.CT, r.Service, r.State, r.Status})
				}
				t.Render()
			}

			// Report collected errors after the table — non-fatal so one
			// bad CT doesn't hide good data.
			for e := range errs {
				fmt.Fprintln(os.Stderr, "warning:", e)
			}
			return nil
		},
	}
	c.Flags().BoolVar(&asJSON, "json", false, "Emit JSON instead of a table")
	c.Flags().StringVar(&filterCT, "ct", "", "Filter to one CT")
	return c
}
