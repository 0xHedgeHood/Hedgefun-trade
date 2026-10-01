// Package runner exercises a synthetic fee ledger with bounded concurrent clients.
// It has no network, wallet, transaction signing, or blockchain execution capability.
package runner

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"time"

	"hedgefun.local/v2load/internal/ledger"
)

const (
	MaxUsers    = 10_000
	MaxWorkers  = 256
	MaxRounds   = 10_000
	MaxRequests = 1_000_000
)

type Config struct {
	Users   int `json:"virtual_users"`
	Workers int `json:"workers"`
	Rounds  int `json:"rounds_per_user"`
}

func (c Config) Validate() error {
	if c.Users < 1 || c.Users > MaxUsers {
		return fmt.Errorf("users must be 1..%d", MaxUsers)
	}
	if c.Workers < 1 || c.Workers > MaxWorkers {
		return fmt.Errorf("workers must be 1..%d", MaxWorkers)
	}
	if c.Rounds < 1 || c.Rounds > MaxRounds {
		return fmt.Errorf("rounds must be 1..%d", MaxRounds)
	}
	if c.Rounds > MaxRequests/c.Users {
		return fmt.Errorf("users * rounds must not exceed %d", MaxRequests)
	}
	return nil
}

type Counts struct {
	Accepted   uint64 `json:"accepted"`
	Expired    uint64 `json:"expired"`
	Minimum    uint64 `json:"minimum_credit_rejected"`
	Unexpected uint64 `json:"unexpected_rejections"`
}

func (c Counts) total() uint64 { return c.Accepted + c.Expired + c.Minimum + c.Unexpected }

type Accounting struct {
	Initial   uint64 `json:"initial_synthetic_units"`
	Remaining uint64 `json:"remaining_synthetic_units"`
	Principal uint64 `json:"principal_units"`
	Protocol  uint64 `json:"protocol_fee_units"`
	Creator   uint64 `json:"creator_fee_units"`
	Treasury  uint64 `json:"treasury_fee_units"`
}

type Checks struct {
	Conservation     bool `json:"conservation"`
	CountsMatch      bool `json:"counts_match_ledger"`
	FeeSplit         bool `json:"three_percent_twenty_ten_seventy"`
	UserBalances     bool `json:"accepted_only_changes_balances"`
	CompleteWorkload bool `json:"complete_workload"`
	UniformPolicy    bool `json:"uniform_policy_for_completed_workload"`
}

type Report struct {
	SchemaVersion int        `json:"schema_version"`
	Mode          string     `json:"mode"`
	Config        Config     `json:"config"`
	Planned       uint64     `json:"planned_requests"`
	Submitted     uint64     `json:"submitted_requests"`
	Completed     uint64     `json:"completed_requests"`
	Counts        Counts     `json:"results"`
	Accounting    Accounting `json:"accounting"`
	Checks        Checks     `json:"checks"`
	ElapsedNS     int64      `json:"elapsed_ns"`
	Status        string     `json:"status"`
}

type InvariantError struct{}

func (InvariantError) Error() string { return "offline ledger invariant failed" }

// Expected counts are independent of scheduling: each virtual user gets the same
// invalid rounds. Every 170th round overlaps both faults, with expiry taking precedence.
func expected(rounds int) Counts {
	expired := uint64(rounds / 10)
	minimum := uint64(rounds/17 - rounds/170)
	return Counts{Accepted: uint64(rounds) - expired - minimum, Expired: expired, Minimum: minimum}
}

func request(user, round, users int) ledger.Request {
	r := ledger.Request{ID: uint64(round*users + user + 1), User: user,
		Amount: ledger.Payment, MinCredit: ledger.NetCredit, Deadline: 1}
	if round%10 == 9 {
		r.Deadline = 0
	} else if round%17 == 16 {
		r.MinCredit = ledger.NetCredit + 1
	}
	return r
}

// Run starts at most Workers client goroutines; ledger commits remain atomic and serial.
// Cancellation can leave queued requests unprocessed. The report distinguishes them
// from completed rejections and never labels a partial workload as a passing run.
func Run(ctx context.Context, c Config) (Report, error) {
	if err := c.Validate(); err != nil {
		return Report{}, err
	}
	started := time.Now()
	initialBalance := uint64(c.Rounds) * ledger.Payment
	book, err := ledger.New(c.Users, initialBalance)
	if err != nil {
		return Report{}, err
	}
	jobs := make(chan ledger.Request, c.Workers)
	results := make([]Counts, c.Workers)
	var wg sync.WaitGroup
	for worker := 0; worker < c.Workers; worker++ {
		wg.Add(1)
		go func(index int) {
			defer wg.Done()
			for {
				select {
				case <-ctx.Done():
					return
				case r, ok := <-jobs:
					if !ok || ctx.Err() != nil {
						return
					}
					result := book.Apply(r, 1)
					switch {
					case result.Accepted && result.Reason == "accepted":
						results[index].Accepted++
					case !result.Accepted && result.Reason == "expired":
						results[index].Expired++
					case !result.Accepted && result.Reason == "minimum":
						results[index].Minimum++
					default:
						results[index].Unexpected++
					}
				}
			}
		}(worker)
	}
	var submitted uint64
produce:
	for round := 0; round < c.Rounds; round++ {
		for user := 0; user < c.Users; user++ {
			if ctx.Err() != nil {
				break produce
			}
			select {
			case <-ctx.Done():
				break produce
			case jobs <- request(user, round, c.Users):
				submitted++
			}
		}
	}
	close(jobs)
	wg.Wait()
	var counts Counts
	for _, n := range results {
		counts.Accepted += n.Accepted
		counts.Expired += n.Expired
		counts.Minimum += n.Minimum
		counts.Unexpected += n.Unexpected
	}
	s := book.Snapshot()
	a := Accounting{Initial: s.Initial, Principal: s.Principal, Protocol: s.Protocol,
		Creator: s.Creator, Treasury: s.Treasury}
	var perUserAccepted uint64
	userBalances, uniform := true, true
	e := expected(c.Rounds)
	for _, user := range s.Users {
		a.Remaining += user.Balance
		perUserAccepted += user.Accepted
		if user.Accepted > uint64(c.Rounds) || user.Balance != initialBalance-user.Accepted*ledger.Payment {
			userBalances = false
		}
		if user.Accepted != e.Accepted {
			uniform = false
		}
	}
	planned := uint64(c.Users * c.Rounds)
	complete := counts.total() == planned && submitted == planned
	checks := Checks{
		Conservation: a.Remaining+a.Principal+a.Protocol+a.Creator+a.Treasury == a.Initial,
		CountsMatch:  counts.Accepted == s.Accepted && perUserAccepted == counts.Accepted && counts.total() <= submitted && counts.Unexpected == 0,
		// These fixed expected units do not call the ledger's fee calculation.
		FeeSplit:         a.Principal == counts.Accepted*9700 && a.Protocol == counts.Accepted*60 && a.Creator == counts.Accepted*30 && a.Treasury == counts.Accepted*210,
		UserBalances:     userBalances,
		CompleteWorkload: complete,
		UniformPolicy:    complete && uniform && counts.Expired == uint64(c.Users)*e.Expired && counts.Minimum == uint64(c.Users)*e.Minimum,
	}
	report := Report{SchemaVersion: 1, Mode: "offline-synthetic-fee-ledger", Config: c,
		Planned: planned, Submitted: submitted, Completed: counts.total(), Counts: counts,
		Accounting: a, Checks: checks, ElapsedNS: time.Since(started).Nanoseconds(), Status: "passed"}
	return finish(report, ctx.Err())
}

// A cancellation also wins over a complete workload if it occurs during final
// aggregation. Accounting failures always take precedence over cancellation.
func finish(report Report, cause error) (Report, error) {
	checks := report.Checks
	if !checks.Conservation || !checks.CountsMatch || !checks.FeeSplit || !checks.UserBalances {
		report.Status = "invariant_failed"
		return report, InvariantError{}
	}
	if cause != nil {
		report.Status = "cancelled"
		if errors.Is(cause, context.DeadlineExceeded) {
			report.Status = "timed_out"
		}
		return report, cause
	}
	if !checks.CompleteWorkload || !checks.UniformPolicy {
		report.Status = "invariant_failed"
		return report, InvariantError{}
	}
	return report, nil
}
