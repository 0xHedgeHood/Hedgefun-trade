package main

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"

	"hedgefun.local/v2load/internal/runner"
)

func TestJSONAndHumanOutputAgreeWithTheFortyUserRun(t *testing.T) {
	var out, errOut bytes.Buffer
	if code := run(context.Background(), []string{"run", "--json"}, &out, &errOut); code != 0 {
		t.Fatalf("%d: %s", code, errOut.String())
	}
	var r runner.Report
	decoder := json.NewDecoder(&out)
	if err := decoder.Decode(&r); err != nil {
		t.Fatal(err)
	}
	if r.Status != "passed" || r.Completed != 1000 || r.Counts.Accepted != 880 || r.Counts.Expired != 80 || r.Counts.Minimum != 40 || !r.Checks.Conservation {
		t.Fatalf("bad report: %+v", r)
	}
	if errOut.Len() != 0 {
		t.Fatal(errOut.String())
	}
	if code := run(context.Background(), nil, &out, &errOut); code != 0 || !strings.Contains(out.String(), "完成 1000/1000") {
		t.Fatalf("%d %s", code, out.String())
	}
}

func TestBadFlagsAndResourcesNeverRunTheModel(t *testing.T) {
	cases := [][]string{{"--users=0"}, {"--workers=257"}, {"--rounds=0"}, {"--users=10000", "--rounds=101"},
		{"--timeout=0s"}, {"--timeout=1h"}, {"--rpc=http://localhost"}, {"--private-key=example"},
		{"--token=0x1"}, {"run", "extra"}, {"version", "extra"}}
	for _, args := range cases {
		var out, errOut bytes.Buffer
		if code := run(context.Background(), args, &out, &errOut); code != 2 || out.Len() != 0 || errOut.Len() == 0 {
			t.Fatalf("%v: code=%d out=%q error=%q", args, code, out.String(), errOut.String())
		}
	}
}

func TestHelpVersionAndCancelledExitCodes(t *testing.T) {
	var out, errOut bytes.Buffer
	if code := run(context.Background(), []string{"--help"}, &out, &errOut); code != 0 || !strings.Contains(errOut.String(), "本地虚拟用户") {
		t.Fatal("help")
	}
	if code := run(context.Background(), []string{"version"}, &out, &errOut); code != 0 || !strings.Contains(out.String(), "offline model") {
		t.Fatal("version")
	}
	out.Reset()
	errOut.Reset()
	ctx, cancel := context.WithCancel(context.Background())
	cancel()
	if code := run(ctx, []string{"--json"}, &out, &errOut); code != 124 {
		t.Fatalf("code=%d %s", code, errOut.String())
	}
	var r runner.Report
	if err := json.Unmarshal(out.Bytes(), &r); err != nil || r.Status != "cancelled" || r.Completed != 0 {
		t.Fatalf("%v %s", err, out.String())
	}
}

type failingWriter struct{}

func (failingWriter) Write([]byte) (int, error) { return 0, errors.New("destination unavailable") }

func TestReportWriteFailureDoesNotExitSuccessfully(t *testing.T) {
	var errOut bytes.Buffer
	if code := run(context.Background(), []string{"--users=1", "--workers=1", "--rounds=1", "--json"}, failingWriter{}, &errOut); code != 1 || !strings.Contains(errOut.String(), "write report") {
		t.Fatalf("%d: %s", code, errOut.String())
	}
}

func TestVersionWriteFailureDoesNotExitSuccessfully(t *testing.T) {
	var errOut bytes.Buffer
	if code := run(context.Background(), []string{"version"}, failingWriter{}, &errOut); code != 1 || !strings.Contains(errOut.String(), "write version") {
		t.Fatalf("%d: %s", code, errOut.String())
	}
}
