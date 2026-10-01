package main

import (
	"context"
	"encoding/json"
	"errors"
	"flag"
	"fmt"
	"io"
	"os"
	"os/signal"
	"syscall"
	"time"

	"hedgefun.local/v2load/internal/runner"
)

func main() {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	code := run(ctx, os.Args[1:], os.Stdout, os.Stderr)
	stop()
	os.Exit(code)
}

func run(ctx context.Context, args []string, out, errOut io.Writer) int {
	if len(args) > 0 && args[0] == "version" {
		if len(args) != 1 {
			fmt.Fprintln(errOut, "version accepts no arguments")
			return 2
		}
		if _, err := fmt.Fprintln(out, "v2load 0.1.0 (offline model)"); err != nil {
			fmt.Fprintln(errOut, "write version:", err)
			return 1
		}
		return 0
	}
	if len(args) > 0 && args[0] == "run" {
		args = args[1:]
	}
	fs := flag.NewFlagSet("v2load", flag.ContinueOnError)
	fs.SetOutput(errOut)
	var c runner.Config
	fs.IntVar(&c.Users, "users", 40, "虚拟用户数（没有钱包地址）")
	fs.IntVar(&c.Workers, "workers", 40, "并发客户端数，最大256")
	fs.IntVar(&c.Rounds, "rounds", 25, "每用户请求轮数")
	timeout := fs.Duration("timeout", 30*time.Second, "总超时，1ms至5m")
	jsonOutput := fs.Bool("json", false, "输出JSON报告")
	fs.Usage = func() {
		fmt.Fprintln(errOut, "v2load run [flags] — 本地虚拟用户账本压力测试")
		fmt.Fprintln(errOut, "无网络/RPC、钱包、私钥、签名或交易广播；结果不是链上吞吐测量。")
		fs.PrintDefaults()
	}
	if err := fs.Parse(args); err != nil {
		if errors.Is(err, flag.ErrHelp) {
			return 0
		}
		return 2
	}
	if fs.NArg() != 0 {
		fmt.Fprintln(errOut, "unexpected positional arguments")
		return 2
	}
	if err := c.Validate(); err != nil {
		fmt.Fprintln(errOut, err)
		return 2
	}
	if *timeout < time.Millisecond || *timeout > 5*time.Minute {
		fmt.Fprintln(errOut, "timeout must be 1ms..5m")
		return 2
	}
	ctx, cancel := context.WithTimeout(ctx, *timeout)
	defer cancel()
	report, err := runner.Run(ctx, c)
	var writeErr error
	if *jsonOutput {
		encoder := json.NewEncoder(out)
		encoder.SetIndent("", "  ")
		writeErr = encoder.Encode(report)
	} else {
		_, writeErr = fmt.Fprintf(out,
			"本地模拟：%s\n虚拟用户 %d · 并发客户端 %d · 完成 %d/%d 请求\n接受 %d · 过期拒绝 %d · 最低到账拒绝 %d · 意外拒绝 %d\n耗时 %s（仅本地模型）\n资金守恒 %t · 分账 %t · 余额回滚 %t · 完整工作量 %t · 统一规则 %t\n",
			report.Status, c.Users, c.Workers, report.Completed, report.Planned,
			report.Counts.Accepted, report.Counts.Expired, report.Counts.Minimum, report.Counts.Unexpected,
			time.Duration(report.ElapsedNS), report.Checks.Conservation, report.Checks.FeeSplit,
			report.Checks.UserBalances, report.Checks.CompleteWorkload, report.Checks.UniformPolicy)
	}
	if writeErr != nil {
		fmt.Fprintln(errOut, "write report:", writeErr)
		return 1
	}
	if err != nil {
		fmt.Fprintln(errOut, err)
		if errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded) {
			return 124
		}
		return 1
	}
	return 0
}
