package ledger

import (
	"math"
	"math/big"
	"reflect"
	"sync"
	"testing"
)

func newLedger(t *testing.T, users int, balance uint64) *Ledger {
	t.Helper()
	l, err := New(users, balance)
	if err != nil {
		t.Fatal(err)
	}
	return l
}

func requireConservation(t *testing.T, s Snapshot) {
	t.Helper()
	total := new(big.Int)
	for _, value := range []uint64{s.Principal, s.Protocol, s.Creator, s.Treasury} {
		total.Add(total, new(big.Int).SetUint64(value))
	}
	var accepted uint64
	for _, user := range s.Users {
		total.Add(total, new(big.Int).SetUint64(user.Balance))
		accepted += user.Accepted
	}
	if total.Cmp(new(big.Int).SetUint64(s.Initial)) != 0 {
		t.Fatalf("money changed: total=%s initial=%d snapshot=%+v", total, s.Initial, s)
	}
	if accepted != s.Accepted {
		t.Fatalf("per-user accepted=%d aggregate=%d", accepted, s.Accepted)
	}
}

// big.Int multiplication/division is independent of the implementation's
// quotient/remainder decomposition and checks the entire uint64 domain.
func expectedBPS(amount, rate uint64) uint64 {
	value := new(big.Int).SetUint64(amount)
	value.Mul(value, new(big.Int).SetUint64(rate))
	value.Quo(value, big.NewInt(10000))
	return value.Uint64()
}

func TestNewRejectsInvalidCountAndInitialOverflow(t *testing.T) {
	for _, tc := range []struct {
		users   int
		balance uint64
	}{
		{0, 1}, {-1, 1}, {2, math.MaxUint64}, {3, math.MaxUint64/3 + 1}, {math.MaxInt, 0},
	} {
		l, err := New(tc.users, tc.balance)
		if err == nil || l != nil {
			t.Fatalf("New(%d,%d) should fail, got ledger=%v error=%v", tc.users, tc.balance, l, err)
		}
	}
	l := newLedger(t, 1, math.MaxUint64)
	if s := l.Snapshot(); s.Initial != math.MaxUint64 || s.Users[0].Balance != math.MaxUint64 {
		t.Fatalf("maximum initial amount was changed: %+v", s)
	}
	requireConservation(t, newLedger(t, 40, 0).Snapshot())
}

func TestPaymentHasSelectedFeeSplit(t *testing.T) {
	l := newLedger(t, 2, 20000)
	result := l.Apply(Request{ID: 0, User: 1, Amount: Payment, MinCredit: NetCredit, Deadline: 10}, 10)
	if result != (Result{Accepted: true, Reason: "accepted"}) {
		t.Fatalf("inclusive deadline or zero ID rejected: %+v", result)
	}
	want := Snapshot{
		Users:   []Account{{Balance: 20000}, {Balance: 10000, Accepted: 1}},
		Initial: 40000, Principal: 9700, Protocol: 60, Creator: 30, Treasury: 210, Accepted: 1,
	}
	if got := l.Snapshot(); !reflect.DeepEqual(got, want) {
		t.Fatalf("got %+v, want %+v", got, want)
	}
	requireConservation(t, l.Snapshot())
}

func TestEveryRejectionRollsBackAndUnconsumedIDCanRetry(t *testing.T) {
	good := Request{ID: 17, User: 0, Amount: Payment, MinCredit: NetCredit, Deadline: 100}
	for _, tc := range []struct {
		name   string
		reason string
		edit   func(*Request)
		now    uint64
	}{
		{"expired", "expired", func(r *Request) { r.Deadline = 99 }, 100},
		{"minimum", "minimum", func(r *Request) { r.MinCredit = 9701 }, 100},
		{"insufficient", "insufficient", func(r *Request) { r.Amount = 30001 }, 100},
		{"negative user", "unknown-user", func(r *Request) { r.User = -1 }, 100},
		{"past last user", "unknown-user", func(r *Request) { r.User = 2 }, 100},
		{"zero amount", "invalid", func(r *Request) { r.Amount = 0 }, 100},
	} {
		t.Run(tc.name, func(t *testing.T) {
			l := newLedger(t, 2, 30000)
			before := l.Snapshot()
			bad := good
			tc.edit(&bad)
			if got := l.Apply(bad, tc.now); got != (Result{Reason: tc.reason}) {
				t.Fatalf("rejection=%+v, want %q", got, tc.reason)
			}
			if after := l.Snapshot(); !reflect.DeepEqual(after, before) {
				t.Fatalf("rejected request changed state: before=%+v after=%+v", before, after)
			}
			if got := l.Apply(good, 100); !got.Accepted || got.Reason != "accepted" {
				t.Fatalf("rejection consumed ID %d: %+v", good.ID, got)
			}
			requireConservation(t, l.Snapshot())
		})
	}
}

func TestDuplicateCannotBeRetargetedOrModifyState(t *testing.T) {
	l := newLedger(t, 2, 30000)
	r := Request{ID: 55, User: 0, Amount: Payment, Deadline: 10}
	if !l.Apply(r, 1).Accepted {
		t.Fatal("first request rejected")
	}
	before := l.Snapshot()
	for _, user := range []int{0, 1} {
		r.User = user
		if got := l.Apply(r, 1); got != (Result{Reason: "duplicate"}) {
			t.Fatalf("duplicate changed target and escaped defense: %+v", got)
		}
		if after := l.Snapshot(); !reflect.DeepEqual(after, before) {
			t.Fatalf("duplicate changed state: before=%+v after=%+v", before, after)
		}
	}
}

func TestFeeRoundingAndMaximumAmounts(t *testing.T) {
	for _, amount := range []uint64{1, 33, 34, 99, 100, 333, 334, 9999, 10000, 10001, math.MaxUint64 / 300, math.MaxUint64} {
		l := newLedger(t, 1, amount)
		fee := expectedBPS(amount, 300)
		protocol := expectedBPS(fee, 2000)
		creator := expectedBPS(fee, 1000)
		request := Request{ID: amount, Amount: amount, User: 0, MinCredit: amount - fee, Deadline: math.MaxUint64}
		if got := l.Apply(request, math.MaxUint64); !got.Accepted {
			t.Fatalf("amount %d rejected: %+v", amount, got)
		}
		s := l.Snapshot()
		if s.Users[0].Balance != 0 || s.Principal != amount-fee || s.Protocol != protocol || s.Creator != creator || s.Treasury != fee-protocol-creator {
			t.Fatalf("rounding for amount %d: %+v", amount, s)
		}
		requireConservation(t, s)
	}
}

func TestMaximumRejectedRequestCanBeCorrectedWithSameID(t *testing.T) {
	l := newLedger(t, 1, math.MaxUint64)
	credit := math.MaxUint64 - expectedBPS(math.MaxUint64, 300)
	r := Request{ID: math.MaxUint64, User: 0, Amount: math.MaxUint64, MinCredit: credit + 1, Deadline: math.MaxUint64}
	before := l.Snapshot()
	if got := l.Apply(r, math.MaxUint64); got != (Result{Reason: "minimum"}) {
		t.Fatalf("minimum boundary rejected incorrectly: %+v", got)
	}
	if !reflect.DeepEqual(l.Snapshot(), before) {
		t.Fatal("maximum rejected payment changed state")
	}
	r.MinCredit = credit
	if !l.Apply(r, math.MaxUint64).Accepted {
		t.Fatal("valid maximum payment rejected after correction")
	}
	requireConservation(t, l.Snapshot())
}

func TestConcurrentDuplicateHasExactlyOneWinner(t *testing.T) {
	l := newLedger(t, 40, 30000)
	const workers = 200
	results := make(chan Result, workers)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for i := range workers {
		wg.Go(func() {
			<-start
			results <- l.Apply(Request{ID: 99, User: i % 40, Amount: Payment, Deadline: 1}, 1)
		})
	}
	close(start)
	wg.Wait()
	close(results)
	var accepted int
	for result := range results {
		if result.Accepted {
			accepted++
		} else if result.Reason != "duplicate" {
			t.Fatalf("unexpected losing result: %+v", result)
		}
	}
	if accepted != 1 || l.Snapshot().Accepted != 1 {
		t.Fatalf("same ID produced %d winners, snapshot=%+v", accepted, l.Snapshot())
	}
	requireConservation(t, l.Snapshot())
}

func TestFortyConcurrentUsersMatchIndependentExpectedLedger(t *testing.T) {
	const users = 40
	const payments = 20
	l := newLedger(t, users, payments*Payment)
	start := make(chan struct{})
	results := make(chan Result, users*payments)
	var wg sync.WaitGroup
	for user := range users {
		for step := range payments {
			wg.Go(func() {
				<-start
				results <- l.Apply(Request{ID: uint64(user*payments + step), User: user, Amount: Payment, MinCredit: NetCredit, Deadline: 100}, 100)
			})
		}
	}
	close(start)
	wg.Wait()
	close(results)
	for result := range results {
		if !result.Accepted || result.Reason != "accepted" {
			t.Fatalf("independently funded request rejected: %+v", result)
		}
	}
	s := l.Snapshot()
	if s.Accepted != users*payments || s.Initial != 8000000 || s.Principal != 7760000 || s.Protocol != 48000 || s.Creator != 24000 || s.Treasury != 168000 {
		t.Fatalf("aggregate differs from independent totals: %+v", s)
	}
	for i, user := range s.Users {
		if user != (Account{Accepted: payments}) {
			t.Fatalf("user %d differs: %+v", i, user)
		}
	}
	requireConservation(t, s)
}

func TestConcurrentDistinctIDsCannotOverdrawOneUser(t *testing.T) {
	l := newLedger(t, 1, Payment)
	const workers = 100
	results := make(chan Result, workers)
	start := make(chan struct{})
	var wg sync.WaitGroup
	for id := range workers {
		wg.Go(func() {
			<-start
			results <- l.Apply(Request{ID: uint64(id), User: 0, Amount: Payment, Deadline: 10}, 10)
		})
	}
	close(start)
	wg.Wait()
	close(results)
	var accepted int
	for result := range results {
		if result.Accepted {
			accepted++
		} else if result.Reason != "insufficient" {
			t.Fatalf("unexpected overspending result: %+v", result)
		}
	}
	s := l.Snapshot()
	if accepted != 1 || s.Users[0] != (Account{Accepted: 1}) || s.Accepted != 1 {
		t.Fatalf("one funded payment produced %d winners: %+v", accepted, s)
	}
	requireConservation(t, s)
}

func TestSnapshotIsIsolatedFromCallerMutation(t *testing.T) {
	l := newLedger(t, 2, 30000)
	s := l.Snapshot()
	s.Users[0] = Account{Balance: math.MaxUint64, Accepted: 99}
	s.Users = append(s.Users, Account{Balance: 1})
	if got := l.Snapshot(); len(got.Users) != 2 || got.Users[0] != (Account{Balance: 30000}) {
		t.Fatalf("caller mutated live state: %+v", got)
	}
	requireConservation(t, l.Snapshot())
}

func FuzzAccountingMatchesIndependentWideIntegerFormula(f *testing.F) {
	for _, amount := range []uint64{1, 34, 334, Payment, math.MaxUint64} {
		f.Add(amount)
	}
	f.Fuzz(func(t *testing.T, amount uint64) {
		if amount == 0 {
			return
		}
		l := newLedger(t, 1, amount)
		if !l.Apply(Request{User: 0, Amount: amount, Deadline: 1}, 0).Accepted {
			t.Fatalf("funded amount %d rejected", amount)
		}
		fee := expectedBPS(amount, 300)
		protocol := expectedBPS(fee, 2000)
		creator := expectedBPS(fee, 1000)
		s := l.Snapshot()
		if s.Principal != amount-fee || s.Protocol != protocol || s.Creator != creator || s.Treasury != fee-protocol-creator {
			t.Fatalf("independent fee formula disagrees for %d: %+v", amount, s)
		}
		requireConservation(t, s)
	})
}
