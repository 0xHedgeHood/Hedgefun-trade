// Package ledger provides a purely local integer ledger for atomic fee-accounting
// stress tests. Users are slice indexes, and all amounts are synthetic units.
package ledger

import (
	"errors"
	"math"
	"sync"
)

const (
	FeeBPS      uint64 = 300
	ProtocolBPS uint64 = 2000
	CreatorBPS  uint64 = 1000
	Payment     uint64 = 10000
	NetCredit   uint64 = 9700
	basisPoints uint64 = 10000
)

// Request describes a local debit. An ID is consumed only by an accepted debit.
// Deadline is inclusive; now is supplied by the caller rather than a wall clock.
type Request struct {
	ID        uint64
	User      int
	Amount    uint64
	MinCredit uint64
	Deadline  uint64
}

type Result struct {
	Accepted bool
	Reason   string
}

type Account struct {
	Balance  uint64
	Accepted uint64
}

type Snapshot struct {
	Users     []Account
	Initial   uint64
	Principal uint64
	Protocol  uint64
	Creator   uint64
	Treasury  uint64
	Accepted  uint64
}

// Ledger serializes validation and commit under one mutex. It has no external
// dependencies, side effects, identifiers beyond integers, or pricing state.
type Ledger struct {
	mu        sync.Mutex
	users     []Account
	usedIDs   map[uint64]struct{}
	initial   uint64
	principal uint64
	protocol  uint64
	creator   uint64
	treasury  uint64
	accepted  uint64
}

func New(users int, initialBalance uint64) (*Ledger, error) {
	if users <= 0 {
		return nil, errors.New("users must be positive")
	}
	if initialBalance != 0 && uint64(users) > math.MaxUint64/initialBalance {
		return nil, errors.New("initial total exceeds uint64")
	}
	// Each account has two uint64 fields; reject an impossible slice length
	// before make would overflow the platform's allocation-length arithmetic.
	if users > math.MaxInt/16 {
		return nil, errors.New("account allocation exceeds platform limit")
	}
	l := &Ledger{
		users:   make([]Account, users),
		usedIDs: make(map[uint64]struct{}),
		initial: uint64(users) * initialBalance,
	}
	for i := range l.users {
		l.users[i].Balance = initialBalance
	}
	return l, nil
}

// bps computes floor(amount*rate/10000) without an overflowing multiplication.
// The rates in this package are at most 10000, so both terms and their sum fit.
func bps(amount, rate uint64) uint64 {
	return amount/basisPoints*rate + amount%basisPoints*rate/basisPoints
}

func (l *Ledger) Apply(req Request, now uint64) Result {
	l.mu.Lock()
	defer l.mu.Unlock()

	if req.User < 0 || req.User >= len(l.users) {
		return Result{Reason: "unknown-user"}
	}
	if req.Amount == 0 {
		return Result{Reason: "invalid"}
	}
	if _, exists := l.usedIDs[req.ID]; exists {
		return Result{Reason: "duplicate"}
	}
	if now > req.Deadline {
		return Result{Reason: "expired"}
	}
	fee := bps(req.Amount, FeeBPS)
	credit := req.Amount - fee
	if credit < req.MinCredit {
		return Result{Reason: "minimum"}
	}
	user := &l.users[req.User]
	if user.Balance < req.Amount {
		return Result{Reason: "insufficient"}
	}
	protocolFee := bps(fee, ProtocolBPS)
	creatorFee := bps(fee, CreatorBPS)
	treasuryFee := fee - protocolFee - creatorFee

	// No bucket can overflow: every positive debit removes existing money from
	// users, and users plus these buckets always sum to the representable Initial.
	// Positive debits also bound both accepted counters by Initial.
	user.Balance -= req.Amount
	user.Accepted++
	l.principal += credit
	l.protocol += protocolFee
	l.creator += creatorFee
	l.treasury += treasuryFee
	l.accepted++
	l.usedIDs[req.ID] = struct{}{}
	return Result{Accepted: true, Reason: "accepted"}
}

func (l *Ledger) Snapshot() Snapshot {
	l.mu.Lock()
	defer l.mu.Unlock()
	return Snapshot{
		Users:     append([]Account(nil), l.users...),
		Initial:   l.initial,
		Principal: l.principal,
		Protocol:  l.protocol,
		Creator:   l.creator,
		Treasury:  l.treasury,
		Accepted:  l.accepted,
	}
}
