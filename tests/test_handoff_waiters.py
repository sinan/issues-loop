"""Waiting and delivery tests for plugins/handoff/bin/handoff (the agent-to-agent mailbox).

Written from the spec, in this order: the module docstring of plugins/handoff/bin/handoff, the
"Agent-to-agent handoffs: the mailbox" section of docs/handoff/readme.md, and the "Agent
Handoffs" section of CLAUDE.md. Where the tool and the spec disagree, the test follows the spec
and is marked @unittest.expectedFailure with a "BUG:" comment, so the suite stays green while
the bug stays on record.

The tool is driven as a subprocess, the way agents and hooks run it. Every test gets:
  - its own HANDOFF_HOME in a fresh temp dir (the real ~/.claude/handoff is never touched) and
    a fixed HANDOFF_BUS ("test-bus"), so every command lands in HANDOFF_HOME/buses/test-bus,
  - an environment with every CLAUDE* and HANDOFF* variable removed before the test's own
    HANDOFF_HOME/HANDOFF_BUS are set; a session identity (CLAUDE_CODE_SESSION_ID /
    CLAUDE_CODE_HOST_SESSION_ID / CLAUDE_PID) is set only where a test needs one,
  - a fake `claude` first on PATH that prints canned JSON, so `claude agents --json` never
    reaches a real Claude Code.
Every background process is killed in cleanup.

Run: python3 -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "plugins" / "handoff" / "bin" / "handoff"
PY = sys.executable

FAST = ("--interval", "0.1")  # waiters in these tests poll every 0.1 s
HOOK_POLL = 3.0  # `hook idle` lists the mailbox every 3 s and takes no --interval

# session identities; CLAUDE_PID is this test process, which outlives every waiter
CODER = {"sid": "sess-coder-0001", "host": "local_coder_desktop", "claude_pid": os.getpid()}
DESIGNER = {"sid": "sess-designer-0002", "host": "local_designer_desktop", "claude_pid": os.getpid()}
REVIEWER = {"sid": "sess-reviewer-0003", "host": "local_reviewer_desktop", "claude_pid": os.getpid()}

FAKE_CLAUDE = """#!/bin/sh
echo "$*" >> '{log}'
cat <<'JSON'
[{{"sessionId": "sess-coder-0001", "name": "coder-by-name"}}]
JSON
"""

# The waiter's parent for the parent-death test: starts the waiter, records its pid, lingers.
PARENT = r"""
import os, subprocess, sys, time
pidfile = sys.argv[1]
child = subprocess.Popen(sys.argv[2:], stdin=subprocess.DEVNULL)
with open(pidfile + ".tmp", "w") as f:
    f.write(str(child.pid))
os.replace(pidfile + ".tmp", pidfile)
time.sleep(120)
"""

# Exit-code wrapper: runs handoff's __main__ in this very process (so this process IS the
# waiter and its parent is whoever started it) and writes the exit code to a file, which
# still works after the parent is gone and nobody is left to collect the status.
RUNNER = r"""
import os, runpy, sys
codefile, script = sys.argv[1], sys.argv[2]
sys.argv = [script] + sys.argv[3:]
try:
    runpy.run_path(script, run_name="__main__")
    code = 0
except SystemExit as e:
    code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
with open(codefile + ".tmp", "w") as f:
    f.write(str(code))
os.replace(codefile + ".tmp", codefile)
sys.exit(code)
"""


def pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def command_of(pid: int) -> str:
    return subprocess.run(["ps", "-p", str(pid), "-o", "command="],
                          capture_output=True, text=True).stdout


def big_body(tag: str) -> str:
    """A long markdown handoff with the shapes a naive parser trips on."""
    lines = [
        f"# Handoff {tag}",
        "",
        "Build the login screen exactly as specified below.",
        "",
        "---",
        "",
        "key: a line that looks like frontmatter",
        "",
        "```python",
        "def check(user):",
        "    return user.ok  # keep this",
        "```",
        "",
        "- unicode survives: é ü ✓ — “quoted”",
    ]
    lines += [f"{i:03d}. requirement {i} of {tag}" for i in range(1, 201)]
    lines += ["", f"END-OF-{tag}"]
    return "\n".join(lines) + "\n"


class Bg:
    """A background handoff process whose stdout/stderr go to files (like the Bash tool's
    output file for run_in_background)."""

    def __init__(self, popen: subprocess.Popen, out: Path, err: Path):
        self.popen, self.out, self.err = popen, out, err

    @property
    def pid(self) -> int:
        return self.popen.pid

    def running(self) -> bool:
        return self.popen.poll() is None

    def output(self) -> tuple[str, str]:
        return self.out.read_text(), self.err.read_text()


class MailboxTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="handoff-waiters-")
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "handoff-home"
        self.bus = "test-bus"
        self.box = self.home / "buses" / self.bus
        self.bin = self.tmp / "bin"
        self.bin.mkdir()
        self.claude_log = self.tmp / "claude-calls.log"
        stub = self.bin / "claude"
        stub.write_text(FAKE_CLAUDE.format(log=self.claude_log))
        stub.chmod(0o755)
        self.procs: list[subprocess.Popen] = []
        self.stray_pids: list[int] = []
        self.seq = 0
        # cleanups run LIFO: processes die before the temp dir goes
        self.addCleanup(self._kill_everything)

    def _kill_everything(self):
        for p in self.procs:
            if p.poll() is None:
                p.kill()
            try:
                p.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass
        for pid in self.stray_pids:
            # not our child (reparented); only kill it if it is still a handoff process
            if pid_alive(pid) and "handoff" in command_of(pid):
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    # environment and processes

    def env(self, sid=None, host=None, claude_pid=None) -> dict:
        e = {k: v for k, v in os.environ.items()
             if not k.startswith("CLAUDE") and not k.startswith("HANDOFF")}
        e["HANDOFF_HOME"] = str(self.home)
        e["HANDOFF_BUS"] = self.bus
        e["PATH"] = str(self.bin) + os.pathsep + e.get("PATH", "")
        e["PYTHONDONTWRITEBYTECODE"] = "1"
        if sid:
            e["CLAUDE_CODE_SESSION_ID"] = sid
        if host:
            e["CLAUDE_CODE_HOST_SESSION_ID"] = host
        if claude_pid:
            e["CLAUDE_PID"] = str(claude_pid)
        return e

    def run_tool(self, *args, env=None, stdin="", timeout=20) -> subprocess.CompletedProcess:
        return subprocess.run([PY, str(SCRIPT), *args], cwd=self.tmp,
                              env=self.env() if env is None else env, input=stdin,
                              capture_output=True, text=True, timeout=timeout)

    def start(self, *args, env=None, stdin=None, cwd=None) -> Bg:
        self.seq += 1
        out, err = self.tmp / f"bg{self.seq}.out", self.tmp / f"bg{self.seq}.err"
        with open(out, "w") as fo, open(err, "w") as fe:
            p = subprocess.Popen([PY, str(SCRIPT), *args], cwd=self.tmp if cwd is None else cwd,
                                 env=self.env() if env is None else env,
                                 stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                                 stdout=fo, stderr=fe, text=True)
        self.procs.append(p)
        if stdin is not None:
            p.stdin.write(stdin)
            p.stdin.close()
        return Bg(p, out, err)

    def spawn_sleep(self) -> subprocess.Popen:
        """A stand-in for a Claude Code process whose pid a session reports as CLAUDE_PID."""
        p = subprocess.Popen(["sleep", "60"])
        self.procs.append(p)
        return p

    def start_wait(self, role="coder", *extra, ident=CODER, env=None) -> Bg:
        return self.start("wait", "--as", role, *FAST, *extra,
                          env=self.env(**ident) if env is None else env)

    @staticmethod
    def hook_input(sid, stop_hook_active=False) -> str:
        return json.dumps({"session_id": sid, "hook_event_name": "Stop",
                           "stop_hook_active": stop_hook_active, "cwd": "/"})

    def start_hook_idle(self, sid, cwd=None) -> Bg:
        # hooks get their session (and, from the global index, their bus) from stdin alone;
        # their env carries no CLAUDE_* identity here, and their cwd need not be a git repo
        return self.start("hook", "idle", env=self.env(), stdin=self.hook_input(sid), cwd=cwd)

    def finish(self, bg: Bg, timeout: float) -> tuple[int, str, str]:
        try:
            code = bg.popen.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            out, err = bg.output()
            self.fail(f"pid {bg.pid} still running after {timeout}s\nstdout: {out}\nstderr: {err}")
        out, err = bg.output()
        return code, out, err

    def until(self, cond, timeout: float, what: str):
        end = time.monotonic() + timeout
        while True:
            value = cond()
            if value:
                return value
            if time.monotonic() >= end:
                self.fail(f"timed out after {timeout}s waiting for {what}")
            time.sleep(0.02)

    # mailbox operations and layout (layout per docs/handoff/readme.md)

    def join(self, role, ident, *flags):
        r = self.run_tool("join", "--as", role, *flags, env=self.env(**ident))
        self.assertEqual(r.returncode, 0, r.stderr)
        return r

    def post(self, sender, to, title, body, issue=None, env=None) -> tuple[str, str]:
        fd, name = tempfile.mkstemp(prefix="body-", suffix=".md", dir=self.tmp)  # thread-safe
        with os.fdopen(fd, "w") as f:
            f.write(body)
        body_file = Path(name)
        args = ["post", "--as", sender, "--to", to, "--title", title, "--body-file", str(body_file)]
        if issue is not None:
            args += ["--issue", str(issue)]
        r = self.run_tool(*args, env=env)
        if r.returncode != 0:
            raise AssertionError(f"post failed ({r.returncode}): {r.stdout}{r.stderr}")
        m = re.search(r"^posted (\S+) -> ", r.stdout, re.M)
        if not m:
            raise AssertionError(f"post printed no id: {r.stdout}")
        return m.group(1), r.stdout

    def unread(self, role) -> list[str]:
        box = self.box / role
        return sorted(p.stem for p in box.glob("*.md")) if box.is_dir() else []

    def delivered(self, role) -> list[str]:
        box = self.box / role / "read"
        return sorted(p.stem for p in box.glob("*.md")) if box.is_dir() else []

    def waiter_file(self, role) -> Path:
        return self.box / ".waiters" / f"{role}.json"

    def waiter_rec(self, role) -> dict | None:
        try:
            return json.loads(self.waiter_file(role).read_text())
        except (OSError, ValueError):
            return None

    def wait_armed(self, role, bg: Bg, kind=None, timeout=5.0):
        def armed():
            if not bg.running():
                out, err = bg.output()
                self.fail(f"waiter pid {bg.pid} exited {bg.popen.returncode} before arming\n"
                          f"stdout: {out}\nstderr: {err}")
            rec = self.waiter_rec(role)
            return rec and rec.get("pid") == bg.pid and (kind is None or rec.get("kind") == kind)
        self.until(armed, timeout, f"{role} waiter pid {bg.pid} ({kind or 'any'}) to arm")

    def status_line(self, role) -> str:
        r = self.run_tool("status")
        self.assertEqual(r.returncode, 0, r.stderr)
        m = re.search(rf"^\s+{re.escape(role)}\s.*$", r.stdout, re.M)
        self.assertIsNotNone(m, f"no status line for {role}:\n{r.stdout}")
        return m.group(0)


# ── wait ──────────────────────────────────────────────────────────────────────

class WaitTests(MailboxTest):

    def test_wait_exits_0_when_mail_lands_and_prints_the_full_body(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")
        time.sleep(0.4)  # several polls with an empty inbox
        self.assertTrue(w.running(), "wait must block while no handoff has landed")

        body = big_body("login")
        mid, _ = self.post("designer", "coder", "Login screen spec", body, issue=12)
        code, out, err = self.finish(w, 10)

        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn(body.strip(), out, "the output file must hold the full handoff text")
        self.assertIn(mid, out)
        self.assertIn("Login screen spec", out)
        self.assertEqual(self.unread("coder"), [], "the handoff is already marked read")
        self.assertEqual(self.delivered("coder"), [mid])
        self.assertIn("not waiting", self.status_line("coder"))

    # Regression (missing coverage): delivered handoffs carried no trust framing, so their text
    # read as if the owner had approved whatever they asked for. handoff_text() now prefixes
    # every delivery (wait, hook idle, hook stop, read --all) with that framing.
    def test_wait_output_includes_peer_trust_framing(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")
        mid, _ = self.post("designer", "coder", "Trust framing check", "TRUST-FRAMING-BODY\n")
        code, out, err = self.finish(w, 10)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn("TRUST-FRAMING-BODY", out)
        self.assertIn("From a peer agent on this machine", out)
        self.assertIn("not the owner's approval", out)

    def test_mail_already_waiting_is_delivered_at_once(self):
        self.join("coder", CODER)
        body1, body2 = big_body("first"), "second handoff\nwith two lines\n"
        m1, _ = self.post("designer", "coder", "First", body1)
        m2, _ = self.post("owner", "coder", "Second", body2)

        t0 = time.monotonic()
        # a 30 s interval: only the very first look at the mailbox can deliver within the budget
        w = self.start("wait", "--as", "coder", "--interval", "30", env=self.env(**CODER))
        code, out, err = self.finish(w, 10)
        elapsed = time.monotonic() - t0

        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertLess(elapsed, 5, "waiting mail must be delivered on the first look, not after an interval")
        self.assertIn(body1.strip(), out)
        self.assertIn(body2.strip(), out)
        self.assertEqual(self.unread("coder"), [])
        self.assertEqual(self.delivered("coder"), sorted([m1, m2]))

    def test_from_filter_leaves_other_senders_mail_unread(self):
        self.join("coder", CODER)
        w = self.start_wait("coder", "--from", "designer")
        self.wait_armed("coder", w)

        owner_body = "OWNER-BODY please also rename the button\n"
        owner_id, _ = self.post("owner", "coder", "From the owner", owner_body)
        time.sleep(0.6)  # ~6 polls
        self.assertTrue(w.running(), "mail from another sender must not wake a --from designer wait")
        self.assertEqual(self.unread("coder"), [owner_id])

        designer_body = "DESIGNER-BODY the spec is ready\n"
        designer_id, _ = self.post("designer", "coder", "From the designer", designer_body)
        code, out, err = self.finish(w, 10)

        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn("DESIGNER-BODY", out)
        self.assertNotIn("OWNER-BODY", out)
        self.assertEqual(self.unread("coder"), [owner_id], "non-matching mail stays unread")
        self.assertEqual(self.delivered("coder"), [designer_id])

    def test_issue_filter_leaves_other_issues_mail_unread(self):
        self.join("coder", CODER)
        w = self.start_wait("coder", "--issue", "12")
        self.wait_armed("coder", w)

        other_issue, _ = self.post("designer", "coder", "Issue seven", "ISSUE-7-BODY\n", issue=7)
        no_issue, _ = self.post("designer", "coder", "No issue", "NO-ISSUE-BODY\n")
        time.sleep(0.6)
        self.assertTrue(w.running(), "mail for another issue must not wake a --issue 12 wait")
        self.assertEqual(self.unread("coder"), sorted([other_issue, no_issue]))

        wanted, _ = self.post("designer", "coder", "Issue twelve", "ISSUE-12-BODY\n", issue=12)
        code, out, err = self.finish(w, 10)

        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn("ISSUE-12-BODY", out)
        self.assertNotIn("ISSUE-7-BODY", out)
        self.assertNotIn("NO-ISSUE-BODY", out)
        self.assertEqual(self.unread("coder"), sorted([other_issue, no_issue]))
        self.assertEqual(self.delivered("coder"), [wanted])

    def test_from_and_issue_filters_must_both_match(self):
        self.join("coder", CODER)
        match, _ = self.post("designer", "coder", "Match", "BOTH-MATCH\n", issue=12)
        wrong_issue, _ = self.post("designer", "coder", "Wrong issue", "WRONG-ISSUE\n", issue=7)
        wrong_sender, _ = self.post("owner", "coder", "Wrong sender", "WRONG-SENDER\n", issue=12)

        w = self.start("wait", "--as", "coder", "--from", "designer", "--issue", "12",
                       "--interval", "30", env=self.env(**CODER))
        code, out, err = self.finish(w, 10)

        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn("BOTH-MATCH", out)
        self.assertNotIn("WRONG-ISSUE", out)
        self.assertNotIn("WRONG-SENDER", out)
        self.assertEqual(self.delivered("coder"), [match])
        self.assertEqual(self.unread("coder"), sorted([wrong_issue, wrong_sender]))

    def test_timeout_exits_2_with_timeout_text(self):
        self.join("coder", CODER)
        t0 = time.monotonic()
        w = self.start_wait("coder", "--timeout", "0.5")
        code, out, err = self.finish(w, 10)
        elapsed = time.monotonic() - t0

        self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
        self.assertIn("TIMEOUT", out)
        self.assertGreaterEqual(elapsed, 0.45, "must not time out before --timeout")
        self.assertIn("not waiting", self.status_line("coder"))

    def test_timeout_with_only_non_matching_mail_leaves_it_unread(self):
        self.join("coder", CODER)
        owner_id, _ = self.post("owner", "coder", "From the owner", "OWNER-BODY\n")
        w = self.start_wait("coder", "--from", "designer", "--timeout", "0.5")
        code, out, err = self.finish(w, 10)

        self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
        self.assertIn("TIMEOUT", out)
        self.assertNotIn("OWNER-BODY", out)
        self.assertEqual(self.unread("coder"), [owner_id])
        self.assertEqual(self.delivered("coder"), [])

    # Regression (was a bug): post says a live waiter "wakes on its own" even when that waiter's --from/--issue
    # filter skips the new mail, so nothing wakes the recipient and no SendMessage ping is printed.
    def test_post_prints_the_ping_when_the_live_waiter_skips_this_mail(self):
        # spec: "if nothing waits for the recipient's mail, it prints the SendMessage ping"
        self.join("coder", CODER)
        w = self.start_wait("coder", "--from", "designer")
        self.wait_armed("coder", w)

        mid, out = self.post("owner", "coder", "Unrelated", "OWNER-BODY\n")
        time.sleep(0.5)
        self.assertTrue(w.running(), "the filtered waiter did not wake for this mail")
        self.assertEqual(self.unread("coder"), [mid])
        self.assertIn("SendMessage", out, f"nothing waits for this mail, yet post said:\n{out}")


# ── supersede rules ───────────────────────────────────────────────────────────

class SupersedeTests(MailboxTest):

    def test_second_wait_for_the_same_role_supersedes_the_first(self):
        self.join("coder", CODER)
        w1 = self.start_wait("coder")
        self.wait_armed("coder", w1, "bash")
        w2 = self.start_wait("coder")
        self.wait_armed("coder", w2, "bash")

        code1, out1, err1 = self.finish(w1, 5)
        self.assertEqual(code1, 3, f"stdout: {out1}\nstderr: {err1}")
        self.assertIn("SUPERSEDED", out1)
        self.assertTrue(w2.running(), "the newer wait stays armed")

        mid, _ = self.post("designer", "coder", "For the newer waiter", "NEWER-WAITER-BODY\n")
        code2, out2, err2 = self.finish(w2, 10)
        self.assertEqual(code2, 0, f"stdout: {out2}\nstderr: {err2}")
        self.assertIn("NEWER-WAITER-BODY", out2)
        self.assertNotIn("NEWER-WAITER-BODY", out1)
        self.assertEqual(self.delivered("coder"), [mid])

    def test_bash_wait_makes_a_live_idle_hook_exit_0_silently(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")

        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")
        code, out, err = self.finish(h, HOOK_POLL + 4)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertEqual(err, "", "a hook that steps aside must not wake the model")
        self.assertEqual(out, "")
        self.assertTrue(w.running())
        self.assertIn(f"waiting (bash, pid {w.pid})", self.status_line("coder"))

        mid, _ = self.post("designer", "coder", "After the hook stepped aside", "BASH-GETS-IT\n")
        codew, outw, errw = self.finish(w, 10)
        self.assertEqual(codew, 0, f"stdout: {outw}\nstderr: {errw}")
        self.assertIn("BASH-GETS-IT", outw)
        self.assertEqual(self.delivered("coder"), [mid])

    def test_idle_hook_started_while_a_bash_wait_is_live_exits_0_at_once(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")

        t0 = time.monotonic()
        r = self.run_tool("hook", "idle", stdin=self.hook_input(CODER["sid"]), timeout=15)
        elapsed = time.monotonic() - t0
        self.assertEqual(r.returncode, 0, f"stdout: {r.stdout}\nstderr: {r.stderr}")
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout, "")
        self.assertLess(elapsed, HOOK_POLL - 0.5, "the hook must step aside at once, not after a poll")

        self.assertTrue(w.running(), "the bash waiter stays armed")
        rec = self.waiter_rec("coder")
        self.assertEqual((rec or {}).get("pid"), w.pid)
        self.assertEqual((rec or {}).get("kind"), "bash")

        mid, _ = self.post("designer", "coder", "Still for bash", "BASH-STILL-ARMED\n")
        code, out, err = self.finish(w, 10)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertIn("BASH-STILL-ARMED", out)
        self.assertEqual(self.delivered("coder"), [mid])

    def test_idle_hook_does_not_step_aside_for_a_dead_bash_waiter(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")
        w.popen.kill()  # SIGKILL: the record stays behind, stale
        w.popen.wait()
        self.assertEqual((self.waiter_rec("coder") or {}).get("pid"), w.pid)

        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")  # it arms instead of deferring to a dead waiter
        self.assertTrue(h.running())

    def test_second_idle_hook_replaces_the_first_silently(self):
        self.join("coder", CODER)
        h1 = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h1, "hook")
        h2 = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h2, "hook")

        code1, out1, err1 = self.finish(h1, HOOK_POLL + 4)
        self.assertEqual(code1, 0, f"stdout: {out1}\nstderr: {err1}")
        self.assertEqual(err1, "", "the replaced hook must not wake the model")
        self.assertEqual(out1, "")
        self.assertTrue(h2.running(), "the newer hook stays armed")

        mid, _ = self.post("designer", "coder", "For the newer hook", "NEWER-HOOK-BODY\n")
        code2, out2, err2 = self.finish(h2, HOOK_POLL + 4)
        self.assertEqual(code2, 2, f"stdout: {out2}\nstderr: {err2}")
        self.assertIn("NEWER-HOOK-BODY", err2)
        self.assertEqual(self.delivered("coder"), [mid])

    # Regression (was a bug): join hands the role to the latest session, but a waiter the displaced session armed
    # before the takeover stays live and claims the role's mail, so the retired session gets it
    # (and post tells the sender that waiter "wakes on its own").
    def test_waiter_of_a_session_that_lost_the_role_does_not_take_its_mail(self):
        # spec: a live session's role is only taken with join --take-over
        old = {"sid": "sess-coder-old", "host": "local_old", "claude_pid": os.getpid()}
        self.join("coder", old)
        w = self.start_wait("coder", ident=old)
        self.wait_armed("coder", w, "bash")
        self.join("coder", CODER, "--take-over")  # a fresh thread takes the role; the old one is open

        mid, _ = self.post("designer", "coder", "For the new coder", "NEW-CODER-ONLY\n")
        code, out, _ = self.finish(w, timeout=5)
        self.assertEqual(code, 6, out)
        self.assertIn("TAKEN OVER", out, "the displaced session must learn it lost the role")
        self.assertNotIn("NEW-CODER-ONLY", out, "the displaced session took the new session's handoff")
        self.assertEqual(self.unread("coder"), [mid])

    # Regression (was a bug): `join --switch` left the old role's waiter armed, so it kept
    # claiming the next holder's mail after this session moved on to a different role. Now
    # --switch deletes this session's waiter record for the role it leaves, and its idle-hook
    # waiter exits SUPERSEDED silently (no wake, nothing printed).
    def test_switch_retires_the_old_roles_idle_hook_waiter_silently(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")

        self.join("designer", CODER, "--switch")
        self.assertIsNone(self.waiter_rec("coder"),
                         "switching roles must retire this session's waiter for the role it left")

        code, out, err = self.finish(h, HOOK_POLL + 4)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertEqual(out, "")
        self.assertEqual(err, "", "a retired idle waiter must exit silently, never wake the model")

        # its retired waiter must not steal the next coder's mail
        mid, _ = self.post("owner", "coder", "For whoever plays coder now", "NEXT-CODER-ONLY\n")
        self.assertEqual(self.unread("coder"), [mid])


# ── hook idle ─────────────────────────────────────────────────────────────────

class HookIdleTests(MailboxTest):

    def test_joined_session_blocks_until_mail_then_exits_2_with_the_handoff_on_stderr(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")
        time.sleep(0.5)
        self.assertTrue(h.running(), "the idle hook blocks while there is no mail")

        body = big_body("hooked")
        mid, _ = self.post("designer", "coder", "Hooked handoff", body)
        code, out, err = self.finish(h, HOOK_POLL + 5)

        self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
        self.assertIn(body.strip(), err, "exit 2 wakes the model with the full handoff on stderr")
        self.assertIn(mid, err)
        self.assertEqual(self.unread("coder"), [])
        self.assertEqual(self.delivered("coder"), [mid])
        self.assertIn("not waiting", self.status_line("coder"))

    # Bus resolution for `hook` comes only from the global session index (keyed by the stdin
    # session_id), never from cwd: a hook started from "/" (not inside any repo, so the old
    # cwd-derived repo bus would not even resolve to "test-bus") must still find the bus the
    # session joined and arm on it.
    def test_idle_hook_from_outside_any_repo_still_arms_on_the_joined_bus(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"], cwd="/")
        self.wait_armed("coder", h, "hook")
        time.sleep(0.5)
        self.assertTrue(h.running(), "the idle hook blocks while there is no mail")

        mid, _ = self.post("designer", "coder", "Hooked from outside any repo", "OUTSIDE-ANY-REPO\n")
        code, out, err = self.finish(h, HOOK_POLL + 5)

        self.assertEqual(code, 2, f"stdout: {out}\nstderr: {err}")
        self.assertIn("OUTSIDE-ANY-REPO", err, "exit 2 wakes the model with the handoff on stderr")
        self.assertIn(mid, err)
        self.assertEqual(self.unread("coder"), [])
        self.assertEqual(self.delivered("coder"), [mid])

    def test_silent_exit_0_for_sessions_that_never_joined(self):
        self.join("coder", CODER)
        mid, _ = self.post("designer", "coder", "Not yours", "NOT-FOR-STRANGERS\n")
        inputs = {
            "unknown session": self.hook_input("sess-never-joined"),
            "no session_id": json.dumps({"hook_event_name": "Stop"}),
            "empty stdin": "",
            "not json": "this is not json",
            "json list": "[]",
        }
        for event in ("idle", "stop"):
            for label, stdin in inputs.items():
                with self.subTest(event=event, stdin=label):
                    t0 = time.monotonic()
                    r = self.run_tool("hook", event, stdin=stdin, timeout=15)
                    self.assertEqual(r.returncode, 0, r.stderr)
                    self.assertEqual(r.stdout, "")
                    self.assertEqual(r.stderr, "")
                    self.assertLess(time.monotonic() - t0, HOOK_POLL - 0.5)
        self.assertEqual(self.unread("coder"), [mid], "a stranger's hook claims nothing")
        self.assertIsNone(self.waiter_rec("coder"), "a stranger's hook arms nothing")

    def test_silent_for_a_session_whose_role_was_taken_over(self):
        # one session per role: join --take-over hands the role to the new session
        old = {"sid": "sess-coder-old", "host": "local_old", "claude_pid": os.getpid()}
        self.join("coder", old)
        self.join("coder", CODER, "--take-over")
        mid, _ = self.post("designer", "coder", "For the new coder", "NEW-CODER-ONLY\n")

        r = self.run_tool("hook", "idle", stdin=self.hook_input(old["sid"]), timeout=15)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stderr, "")
        self.assertEqual(r.stdout, "")
        self.assertEqual(self.unread("coder"), [mid], "the old session must not get the new one's mail")
        self.assertIsNone(self.waiter_rec("coder"))


# ── hook busy ─────────────────────────────────────────────────────────────────

class HookBusyTests(MailboxTest):
    """Regression (was a bug): a stale idle-hook waiter could claim mail mid-turn. The
    UserPromptSubmit hook ("hook busy") now deletes the session's kind=hook waiter record so
    that idle hook exits 0 silently; it prints nothing itself; mail landing mid-turn stays
    unread until "hook stop" claims it at the end of the turn."""

    def busy(self, sid, timeout=15):
        return self.run_tool("hook", "busy", stdin=self.hook_input(sid), timeout=timeout)

    def test_hook_busy_prints_nothing_and_retires_the_idle_waiter(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")

        r = self.busy(CODER["sid"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(r.stdout, "", "UserPromptSubmit output would land in the prompt")
        self.assertEqual(r.stderr, "")
        self.assertIsNone(self.waiter_rec("coder"), "hook busy must retire the idle hook's waiter")

    def test_hook_busy_leaves_a_bash_waiter_armed(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")

        r = self.busy(CODER["sid"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((r.stdout, r.stderr), ("", ""))

        rec = self.waiter_rec("coder")
        self.assertEqual((rec or {}).get("pid"), w.pid, "hook busy must not touch a bash waiter")
        self.assertEqual((rec or {}).get("kind"), "bash")
        self.assertTrue(w.running())

    def test_hook_busy_makes_a_live_idle_hook_exit_0_silently_then_mail_waits_for_stop(self):
        self.join("coder", CODER)
        h = self.start_hook_idle(CODER["sid"])
        self.wait_armed("coder", h, "hook")

        r = self.busy(CODER["sid"])
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((r.stdout, r.stderr), ("", ""))

        code, out, err = self.finish(h, HOOK_POLL + 4)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        self.assertEqual(out, "")
        self.assertEqual(err, "", "the idle hook, once its waiter is retired, must exit silently")

        # mail that lands mid-turn stays unread until the Stop hook claims it at the turn's end
        mid, _ = self.post("designer", "coder", "Mid-turn handoff", "MID-TURN-BODY\n")
        self.assertEqual(self.unread("coder"), [mid], "mail posted mid-turn must stay unread")

        r2 = self.run_tool("hook", "stop", stdin=self.hook_input(CODER["sid"]), timeout=15)
        self.assertEqual(r2.returncode, 0, r2.stderr)
        self.assertIn("MID-TURN-BODY", json.loads(r2.stdout)["reason"])
        self.assertEqual(self.unread("coder"), [])
        self.assertEqual(self.delivered("coder"), [mid])

    def test_hook_busy_is_silent_for_a_session_that_never_joined(self):
        r = self.busy("sess-never-joined")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((r.stdout, r.stderr), ("", ""))


# ── exactly-once delivery ─────────────────────────────────────────────────────

class ExactlyOnceTests(MailboxTest):

    def test_every_handoff_is_delivered_exactly_once_while_claimers_race(self):
        self.join("coder", CODER)
        coder_env = self.env(**CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")

        lock = threading.Lock()
        outputs: list[tuple[str, str]] = []
        posted: dict[str, str] = {}  # body marker -> message id
        errors: list[BaseException] = []
        stop = threading.Event()

        def guarded(fn):
            def run():
                try:
                    fn()
                except BaseException as e:  # surfaced in the main thread
                    with lock:
                        errors.append(e)
            return run

        def reader():
            while not stop.is_set():
                r = self.run_tool("read", "--all", "--as", "coder", env=coder_env)
                if r.returncode != 0:
                    raise AssertionError(f"read --all failed: {r.stderr}")
                with lock:
                    outputs.append(("read --all", r.stdout))

        def stop_hook():
            payload = self.hook_input(CODER["sid"])
            while not stop.is_set():
                r = self.run_tool("hook", "stop", stdin=payload)
                if r.returncode != 0:
                    raise AssertionError(f"hook stop failed: {r.stderr}")
                if r.stdout.strip():
                    with lock:
                        outputs.append(("hook stop", json.loads(r.stdout)["reason"]))

        def poster(k):
            def run():
                for j in range(5):
                    i = k * 5 + j
                    marker = f"MARK-{i:02d}-END"
                    mid, _ = self.post("designer", "coder", f"part {i:02d}",
                                       f"payload for part {i}\n{marker}\n")
                    with lock:
                        posted[marker] = mid
            return run

        claimers = [threading.Thread(target=guarded(reader)) for _ in range(3)]
        claimers.append(threading.Thread(target=guarded(stop_hook)))
        posters = [threading.Thread(target=guarded(poster(k))) for k in range(4)]
        for t in claimers + posters:
            t.start()
        try:
            for t in posters:
                t.join(30)
            self.assertEqual(errors, [])
            self.assertEqual(len(posted), 20)
            self.until(lambda: len(self.delivered("coder")) >= 20 and not self.unread("coder"),
                       20, "all 20 handoffs to be claimed")
        finally:
            stop.set()
            for t in claimers:
                t.join(30)
        self.assertEqual(errors, [])

        if w.running():
            # the readers won every race; one more handoff, now only the wait can claim it
            mid, _ = self.post("designer", "coder", "sentinel", "MARK-20-END\n")
            posted["MARK-20-END"] = mid
        code, out, err = self.finish(w, 10)
        self.assertEqual(code, 0, f"stdout: {out}\nstderr: {err}")
        outputs.append(("wait", out))

        ids = sorted(posted.values())
        self.assertEqual(len(set(ids)), len(ids), "every post got its own id")
        for marker, mid in sorted(posted.items()):
            with self.subTest(marker=marker):
                seen = sum(text.count(marker) for _, text in outputs)
                self.assertEqual(seen, 1, f"{mid} delivered {seen} times")
                holders = [src for src, text in outputs if mid in text]
                self.assertEqual(len(holders), 1, f"{mid} appeared in {holders}")
        self.assertEqual(self.unread("coder"), [])
        self.assertEqual(self.delivered("coder"), ids, "every handoff ends in read/")

    # Regression (was a bug): concurrent posts with the same title from the same sender in the same second get the
    # same id; the later write replaces the earlier file, so handoffs vanish although every
    # post printed "posted <id>".
    def test_concurrent_posts_with_the_same_title_are_all_kept(self):
        for rnd in range(3):
            to = f"coder{rnd}"
            barrier = threading.Barrier(10)
            ids: list[str] = []
            lock = threading.Lock()

            def go(i):
                barrier.wait()
                mid, _ = self.post("owner", to, "status update", f"UPDATE-{rnd}-{i}\n")
                with lock:
                    ids.append(mid)

            threads = [threading.Thread(target=go, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join(30)
            self.assertEqual(len(ids), 10)
            self.assertEqual(len(set(ids)), 10, f"duplicate ids handed out: {sorted(ids)}")
            self.assertEqual(len(self.unread(to)), 10, "every posted handoff must be in the inbox")


# ── orphan detection ──────────────────────────────────────────────────────────

class OrphanTests(MailboxTest):

    def test_waiter_exits_4_when_its_claude_process_dies(self):
        claude = self.spawn_sleep()
        ident = dict(CODER, claude_pid=claude.pid)
        self.join("coder", ident)
        w = self.start_wait("coder", ident=ident)
        self.wait_armed("coder", w, "bash")
        time.sleep(0.3)
        self.assertTrue(w.running(), "the session is alive, so the waiter keeps waiting")

        claude.kill()
        claude.wait()  # reaped: a zombie would still answer kill(pid, 0)
        code, out, err = self.finish(w, 5)
        self.assertEqual(code, 4, f"stdout: {out}\nstderr: {err}")
        # Regression (was a bug): exit 4 (ORPHANED) printed nothing; it now prints an ORPHANED line.
        self.assertIn("ORPHANED", out)
        self.assertIsNone(self.waiter_rec("coder"))

        mid, _ = self.post("designer", "coder", "After the session died", "NOBODY-HOME\n")
        self.assertEqual(self.unread("coder"), [mid], "an orphaned waiter claims nothing")

    def test_waiter_exits_4_when_its_parent_process_dies(self):
        self.join("coder", CODER)
        pidfile, codefile = self.tmp / "waiter.pid", self.tmp / "waiter.code"
        parent = subprocess.Popen(
            [PY, "-c", PARENT, str(pidfile),
             PY, "-c", RUNNER, str(codefile), str(SCRIPT), "wait", "--as", "coder", *FAST],
            cwd=self.tmp, env=self.env(**CODER),
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        self.procs.append(parent)
        wpid = int(self.until(lambda: pidfile.exists() and pidfile.read_text().strip(), 10,
                              "the waiter's pid"))
        self.stray_pids.append(wpid)
        self.until(lambda: (self.waiter_rec("coder") or {}).get("pid") == wpid, 10,
                   "the waiter to arm")
        time.sleep(0.3)
        self.assertTrue(pid_alive(wpid))

        parent.kill()  # the waiter is reparented
        parent.wait()
        code = self.until(lambda: codefile.exists() and codefile.read_text().strip(), 5,
                          "the orphaned waiter to exit")
        self.assertEqual(code, "4")
        self.until(lambda: not pid_alive(wpid), 5, "the orphaned waiter's pid to go away")
        self.assertIsNone(self.waiter_rec("coder"))

    # Regression (was a bug): a waiter takes claude_pid from the role's record, not from its own CLAUDE_PID, so a
    # wait armed outside any Claude session (a plain terminal) exits 4 at once when the role's
    # last session is gone, although the process that armed it is alive.
    def test_wait_armed_outside_a_session_is_not_orphaned_by_the_roles_dead_session(self):
        # spec: exit 4 means "the session that armed it is gone"
        ghost = self.spawn_sleep()
        self.join("coder", dict(CODER, claude_pid=ghost.pid))
        ghost.kill()
        ghost.wait()

        w = self.start_wait("coder", env=self.env())  # a terminal: no CLAUDE_* at all
        time.sleep(1.0)
        self.assertTrue(w.running(), f"wait exited {w.popen.returncode} though its armer lives")


# ── the .waiters record and status ────────────────────────────────────────────

class WaiterRecordTests(MailboxTest):

    def test_sigterm_removes_the_waiters_record(self):
        self.join("coder", CODER)
        self.join("designer", DESIGNER)
        bash = self.start_wait("coder")
        hook = self.start_hook_idle(DESIGNER["sid"])
        for role, bg, kind in (("coder", bash, "bash"), ("designer", hook, "hook")):
            with self.subTest(kind=kind):
                self.wait_armed(role, bg, kind)
                time.sleep(0.3)  # settled into its poll loop
                os.kill(bg.pid, signal.SIGTERM)
                code, out, err = self.finish(bg, 5)
                self.assertNotEqual(code, 0, "a terminated waiter claimed nothing")
                self.assertFalse(self.waiter_file(role).exists(), f"{role} record left behind")
                self.assertIn("not waiting", self.status_line(role))

    def test_stale_record_with_a_dead_pid_is_not_reported_live(self):
        self.join("coder", CODER)
        w = self.start_wait("coder")
        self.wait_armed("coder", w, "bash")
        self.assertIn(f"waiting (bash, pid {w.pid})", self.status_line("coder"))
        w.popen.kill()  # SIGKILL cannot be caught: the record stays, with a dead pid
        w.popen.wait()
        self.assertEqual((self.waiter_rec("coder") or {}).get("pid"), w.pid, "record is stale now")

        self.assertIn("not waiting", self.status_line("coder"))
        mid, out = self.post("designer", "coder", "Nobody is waiting", "STALE-CHECK\n")
        self.assertNotIn("is live", out)
        self.assertIn("SendMessage", out, "no live waiter: post must print the ping")
        self.assertIn(CODER["host"], out, "the ping targets the session's Desktop local_ id")
        self.assertEqual(self.unread("coder"), [mid])

    def test_record_whose_pid_now_belongs_to_another_program_is_not_live(self):
        self.join("coder", CODER)
        other = self.spawn_sleep()  # a live pid that is not a handoff waiter
        self.waiter_file("coder").parent.mkdir(parents=True, exist_ok=True)
        self.waiter_file("coder").write_text(json.dumps(
            {"pid": other.pid, "kind": "bash", "claude_pid": None, "session_id": CODER["sid"]}))

        self.assertIn("not waiting", self.status_line("coder"))
        _, out = self.post("designer", "coder", "Pid reuse", "PID-REUSE\n")
        self.assertNotIn("is live", out)
        self.assertIn("SendMessage", out)

    def test_status_shows_how_each_role_is_waiting(self):
        self.join("coder", CODER)
        self.join("designer", DESIGNER)
        self.join("reviewer", REVIEWER)
        w = self.start_wait("coder")
        h = self.start_hook_idle(DESIGNER["sid"])
        self.wait_armed("coder", w, "bash")
        self.wait_armed("designer", h, "hook")

        self.assertIn(f"waiting (bash, pid {w.pid})", self.status_line("coder"))
        self.assertIn(f"waiting (hook, pid {h.pid})", self.status_line("designer"))
        self.assertIn("not waiting", self.status_line("reviewer"))

        _, out = self.post("reviewer", "coder", "Review notes", "REVIEW-NOTES\n")
        self.assertIn("live", out, "post reports the recipient's live waiter")
        self.assertNotIn("SendMessage", out)
        code, _, _ = self.finish(w, 10)
        self.assertEqual(code, 0)
        os.kill(h.pid, signal.SIGTERM)
        self.finish(h, 5)

        self.assertIn("not waiting", self.status_line("coder"))
        self.assertIn("not waiting", self.status_line("designer"))
        self.assertIn("not waiting", self.status_line("reviewer"))


if __name__ == "__main__":
    unittest.main()
