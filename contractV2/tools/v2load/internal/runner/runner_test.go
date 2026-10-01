package runner

import (
	"context"
	"errors"
	"reflect"
	"testing"
)

func TestDefaultFortyUsersProduceIndependentlyExpectedReport(t *testing.T) {
	r, err := Run(context.Background(), Config{Users: 40, Workers: 40, Rounds: 25})
	if err != nil {
		t.Fatal(err)
	}
	if r.Planned != 1000 || r.Submitted != 1000 || r.Completed != 1000 || r.Status != "passed" {
		t.Fatalf("incomplete: %+v", r)
	}
	if r.Counts != (Counts{Accepted: 880, Expired: 80, Minimum: 40}) {
		t.Fatalf("counts: %+v", r.Counts)
	}
	want := Accounting{Initial: 10_000_000, Remaining: 1_200_000, Principal: 8_536_000,
		Protocol: 52_800, Creator: 26_400, Treasury: 184_800}
	if r.Accounting != want {
		t.Fatalf("accounting: %+v, want %+v", r.Accounting, want)
	}
	if r.Checks != (Checks{true, true, true, true, true, true}) {
		t.Fatalf("checks: %+v", r.Checks)
	}
}

func TestWorkerCountsDoNotChangeAccountingOrRejectionPriority(t *testing.T) {
	for _, rounds := range []int{1, 9, 10, 16, 17, 169, 170, 171, 340} {
		var want Counts
		// Enumerate independently rather than copying the runner's closed-form oracle.
		for n := 1; n <= rounds; n++ {
			switch {
			case n%10 == 0:
				want.Expired += 40
			case n%17 == 0:
				want.Minimum += 40
			default:
				want.Accepted += 40
			}
		}
		var accounting Accounting
		for i, workers := range []int{1, 40, 256} {
			r, err := Run(context.Background(), Config{40, workers, rounds})
			if err != nil {
				t.Fatal(err)
			}
			if r.Counts != want {
				t.Fatalf("rounds=%d workers=%d: %+v want %+v", rounds, workers, r.Counts, want)
			}
			if i == 0 {
				accounting = r.Accounting
			} else if accounting != r.Accounting {
				t.Fatal("scheduling changes money")
			}
		}
	}
}

func TestInvalidLimitsFailBeforeRunning(t *testing.T) {
	for _, c := range []Config{{0, 40, 25}, {10001, 40, 25}, {40, 0, 25}, {40, 257, 25}, {40, 40, 0}, {40, 40, 10001}, {10000, 40, 101}, {int(^uint(0) >> 1), 40, 25}} {
		r, err := Run(context.Background(), c)
		if err == nil || !reflect.DeepEqual(r, Report{}) {
			t.Fatalf("bad config ran: %+v, %+v", c, r)
		}
	}
}

func TestAlreadyCancelledRunIsPartialAndConservesAllFunds(t *testing.T) {
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	r, err := Run(ctx, Config{40, 40, 25})
	if !errors.Is(err, context.Canceled) || r.Status != "cancelled" {
		t.Fatalf("error=%v report=%+v", err, r)
	}
	if r.Completed != 0 || r.Submitted != 0 || r.Checks.CompleteWorkload || r.Checks.UniformPolicy {
		t.Fatalf("partial run passed: %+v", r)
	}
	if r.Accounting.Initial != r.Accounting.Remaining || !r.Checks.Conservation || !r.Checks.FeeSplit {
		t.Fatal("cancelled run changed money")
	}
}

func TestFinalCancellationNeverReportsPassedEvenAfterCompleteWorkload(t *testing.T) {
	good := Report{Status: "passed", Checks: Checks{true, true, true, true, true, true}}
	for _, cause := range []error{context.Canceled, context.DeadlineExceeded} {
		r, err := finish(good, cause)
		if !errors.Is(err, cause) || r.Status == "passed" || !r.Checks.CompleteWorkload {
			t.Fatalf("%v: %+v %v", cause, r, err)
		}
	}
	bad := good
	bad.Checks.Conservation = false
	r, err := finish(bad, context.Canceled)
	var invariant InvariantError
	if !errors.As(err, &invariant) || r.Status != "invariant_failed" {
		t.Fatal("cancellation hid corrupt accounting")
	}
}
