package execx

import (
	"bytes"
	"context"
	"fmt"
	"os"
	"os/exec"
	"runtime"
	"strings"
	"time"
)

type Result struct {
	Command         string   `json:"command"`
	Args            []string `json:"args,omitempty"`
	ExitCode        *int     `json:"exit_code"`
	TimedOut        bool     `json:"timed_out"`
	DurationSeconds float64  `json:"duration_seconds"`
	Stdout          string   `json:"stdout"`
	Stderr          string   `json:"stderr"`
	Shell           string   `json:"shell"`
	Error           string   `json:"error,omitempty"`
}

func RunShell(command, cwd string, env map[string]string, timeout time.Duration) Result {
	shellPrefix, shellName, err := detectShell()
	if err != nil {
		return Result{Command: command, DurationSeconds: 0, Shell: "", Error: err.Error()}
	}
	args := append(shellPrefix, command)
	return run(args, command, cwd, env, timeout, shellName)
}

func RunProcess(argv []string, cwd string, env map[string]string, timeout time.Duration) Result {
	resolved := append([]string{}, argv...)
	if len(resolved) > 0 {
		if path, err := exec.LookPath(resolved[0]); err == nil {
			resolved[0] = path
		}
	}
	return run(resolved, quoteArgs(argv), cwd, env, timeout, "direct")
}

func run(argv []string, command, cwd string, env map[string]string, timeout time.Duration, shell string) Result {
	start := time.Now()
	ctx := context.Background()
	var cancel context.CancelFunc
	if timeout > 0 {
		ctx, cancel = context.WithTimeout(ctx, timeout)
		defer cancel()
	}
	cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
	cmd.Dir = cwd
	cmd.Env = mergeEnv(env)
	var stdoutBuf, stderrBuf bytes.Buffer
	cmd.Stdout = &stdoutBuf
	cmd.Stderr = &stderrBuf
	err := cmd.Run()
	result := Result{
		Command:         command,
		Args:            append([]string{}, argv...),
		DurationSeconds: round3(time.Since(start).Seconds()),
		Stdout:          stdoutBuf.String(),
		Stderr:          stderrBuf.String(),
		Shell:           shell,
	}
	if ctx.Err() == context.DeadlineExceeded {
		result.TimedOut = true
		result.Error = "timeout"
		return result
	}
	if err == nil {
		code := 0
		result.ExitCode = &code
		return result
	}
	if exitErr, ok := err.(*exec.ExitError); ok {
		code := exitErr.ExitCode()
		result.ExitCode = &code
		return result
	}
	result.Error = err.Error()
	return result
}

func detectShell() ([]string, string, error) {
	if runtime.GOOS == "windows" {
		if path, err := exec.LookPath("pwsh"); err == nil {
			return []string{path, "-NoProfile", "-Command"}, "pwsh", nil
		}
		if path, err := exec.LookPath("powershell"); err == nil {
			return []string{path, "-NoProfile", "-Command"}, "powershell", nil
		}
		if comspec := os.Getenv("COMSPEC"); comspec != "" {
			return []string{comspec, "/d", "/s", "/c"}, "cmd", nil
		}
		return nil, "", fmt.Errorf("no Windows shell found")
	}
	if path, err := exec.LookPath("bash"); err == nil {
		return []string{path, "-lc"}, "bash", nil
	}
	if path, err := exec.LookPath("sh"); err == nil {
		return []string{path, "-lc"}, "sh", nil
	}
	return nil, "", fmt.Errorf("no supported shell found")
}

func mergeEnv(extra map[string]string) []string {
	base := os.Environ()
	if len(extra) == 0 {
		return base
	}
	index := map[string]int{}
	for i, item := range base {
		key := item
		if idx := strings.Index(item, "="); idx >= 0 {
			key = item[:idx]
		}
		if runtime.GOOS == "windows" {
			key = strings.ToUpper(key)
		}
		index[key] = i
	}
	for key, value := range extra {
		entry := fmt.Sprintf("%s=%s", key, value)
		lookup := key
		if runtime.GOOS == "windows" {
			lookup = strings.ToUpper(key)
		}
		if pos, ok := index[lookup]; ok {
			base[pos] = entry
		} else {
			base = append(base, entry)
		}
	}
	return base
}

func quoteArgs(argv []string) string {
	parts := make([]string, 0, len(argv))
	for _, arg := range argv {
		if strings.ContainsAny(arg, " \t\"") {
			arg = `"` + strings.ReplaceAll(arg, `"`, `\"`) + `"`
		}
		parts = append(parts, arg)
	}
	return strings.Join(parts, " ")
}

func round3(v float64) float64 {
	return float64(int(v*1000+0.5)) / 1000
}
