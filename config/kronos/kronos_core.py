"""The decision engine of kronos: takes a request, decides, logs, and (only then) performs it.

    request   {"id": "...", "action": "pkg.install", "args": {...}, "passphrase": "optional"}
    peer      {"uid", "pid", "exe", "cmdline"}  - from SO_PEERCRED and /proc, never from the request

Order of events, and why:
  1. is this uid allowed to talk to kronos at all?                      (else refused)
  2. is it a known action with valid arguments and a clean policy check? (else refused, with the reason)
  3. is it passphrase-gated? then the peer needs an unlocked admin session, or must send the passphrase
     (unset -> "needs_setup"; wrong / locked / damaged are reported and logged, the passphrase itself never is)
  4. the DECISION is written to the audit log - if that fails nothing is done (fail closed)
  5. the plan runs through the runner; the RESULT is written to the audit log
The response says what happened and carries the (bounded) output. A refusal is always a normal response, never
an exception; the caller shows the reason.

The runner (root helper in production, a fake in tests) has three methods: run(argv, stdin, cwd) -> (rc, output),
read_file(path) -> text, write_file(path, text).
"""
import time

import kronos_actions as actions
import kronos_audit as audit
import kronos_passphrase as pp

MAX_OUTPUT = 65536


class NoAgent(Exception):
    """No prompt agent is connected, so the daemon can't ask the person itself."""


def peer_key(peer):
    """What an admin session belongs to: one running process (its pid and start time, both from the kernel). Not the program's name: for a
    Python program that is just the interpreter, and any program can rewrite its own command line, so a name could be borrowed by another
    program. A process gets its own session; another one - even the same program started again - asks for the passphrase itself."""
    return (peer.get("uid"), peer.get("pid"), peer.get("start"))


def describe_peer(peer):
    """Who is asking, in words for the prompt: what the kernel says the program is (the interpreter or binary), and its command line."""
    exe = (peer.get("exe") or "?").rsplit("/", 1)[-1]
    line = (peer.get("cmdline") or "")[:120]
    return f"{exe} (process {peer.get('pid')}): {line}" if line else f"{exe} (process {peer.get('pid')})"


def execute_plan(plan, runner, emit=None):
    """(exit code, output, short detail). Runs a plan's steps through `runner`, stopping at the first that fails unless it may fail.
    `emit(text)` is given live output when the runner supports it. Used by kronosd (with a runner that asks the root helper) and by the
    helper itself (with a runner that really runs things)."""
    output = []
    extra = {"emit": emit} if emit else {}
    for step in plan.steps:
        if step.kind == "read_file":
            try:
                return 0, runner.read_file(step.path), "read"
            except OSError as exc:
                return 1, str(exc), "read failed"
        if step.kind == "write_file":
            try:
                runner.write_file(step.path, step.data)
                output.append(f"wrote {step.path}\n")
                continue
            except OSError as exc:
                return 1, str(exc), "write failed"
        rc, text = 1, ""
        for argv in step.alternatives:
            rc, text = runner.run(argv, stdin=step.stdin, cwd=step.cwd, **extra)
            output.append(text)
            if rc == 0:
                break
        if rc != 0 and not step.allow_fail:
            return rc, "".join(output), f"{step.alternatives[-1][0]} failed"
    return 0, "".join(output), "done"


class Kronos:
    def __init__(self, audit_log, passphrase, sessions, runner, policy=None, app_check=None, clients=(1000,), now=time.time, prompter=None):
        self.audit = audit_log
        self.pass_ = passphrase
        self.sessions = sessions
        self.runner = runner
        self.ctx = actions.Context(policy, app_check)
        self.clients = set(clients)
        self.now = now
        # prompter(dict) -> passphrase text, or None if the person cancelled; raises NoAgent if nobody can be asked. Set by the daemon.
        self.prompter = prompter

    # ---- helpers
    def _log(self, event, request, peer, **fields):
        return self.audit.append(event, id=str((request or {}).get("id", "")), peer=peer, **fields)

    def _refuse(self, request, peer, reason, action=None, args=None):
        try:
            self._log("decision", request, peer, action=action, args=args or {}, decision="refused", reason=reason)
        except audit.AuditError:
            pass                                    # already refusing; the audit failure changes nothing here
        return {"status": "refused", "reason": reason}

    # ---- the unlock / set paths (the prompt uses these; handle() can also take a passphrase inline)
    def unlock(self, peer, passphrase, request=None):
        try:
            ok = self.pass_.verify(passphrase)
        except pp.Locked as exc:
            return self._passphrase_result(request, peer, "locked", str(exc), seconds=exc.seconds)
        except pp.Damaged as exc:
            return self._passphrase_result(request, peer, "damaged", str(exc))
        if not self.pass_.is_set():
            return {"status": "needs_setup", "reason": "No admin passphrase is set yet."}
        if not ok:
            return self._passphrase_result(request, peer, "wrong", "That passphrase is wrong.")
        self.sessions.grant(peer_key(peer))
        return {"status": "ok"}

    def _passphrase_result(self, request, peer, kind, message, **extra):
        try:
            self._log("passphrase", request, peer, decision=f"passphrase-{kind}", reason=message)
        except audit.AuditError:
            pass
        return {"status": kind if kind in ("locked", "damaged") else "wrong_passphrase", "reason": message, **extra}

    def set_passphrase(self, peer, new, old=None):
        if peer.get("uid") not in self.clients:
            return {"status": "refused", "reason": "Not allowed."}
        try:
            self.pass_.set(new, old=old)
        except (pp.Weak, pp.Wrong, pp.Locked, pp.Damaged) as exc:
            return {"status": "refused", "reason": str(exc)}
        try:
            self._log("passphrase", {"id": ""}, peer, decision="passphrase-set", reason="the admin passphrase was set or changed")
        except audit.AuditError as exc:
            return {"status": "refused", "reason": str(exc)}
        self.sessions.revoke()                      # every session ends when the passphrase changes
        self.sessions.grant(peer_key(peer))
        return {"status": "ok"}

    def _ask_the_person(self, request, peer, plan):
        """A passphrase action with no open session. If a prompt agent is connected the daemon asks the person ITSELF - showing what the
        kernel says is asking and what the action is - so the passphrase never passes through the requesting program. Returns None once
        the session is open (carry on), or the response to give the caller (needs_passphrase for a client that must ask by itself, or a
        refusal)."""
        action = request.get("action")
        status = self.pass_.status()
        if self.prompter is not None and status != "damaged":
            mode, error = ("create" if status == "unset" else "unlock"), ""
            try:
                for _ in range(3):
                    answer = self.prompter({"mode": mode, "text": plan.description, "program": describe_peer(peer), "error": error})
                    if answer is None:
                        return self._refuse(request, peer, "Cancelled - the admin passphrase wasn't entered.", action, plan.args)
                    done = self.set_passphrase(peer, answer) if mode == "create" else self.unlock(peer, answer, request)
                    if done["status"] == "ok":
                        return None
                    if done["status"] in ("locked", "damaged"):
                        return done
                    error = done.get("reason", "That didn't work.")
                return self._refuse(request, peer, "Too many wrong passphrases.", action, plan.args)
            except NoAgent:
                pass                                              # nobody to ask: the caller must (a terminal over SSH)
        try:
            self._log("decision", request, peer, action=action, args=plan.args, decision="passphrase-required", reason=plan.description)
        except audit.AuditError as exc:
            return {"status": "refused", "reason": str(exc)}
        return {"status": "needs_setup" if status == "unset" else "needs_passphrase", "description": plan.description, "action": action}

    # ---- the main entry point
    def handle(self, request, peer, emit=None):
        if not isinstance(request, dict):
            return self._refuse({}, peer, "Malformed request.")
        if peer.get("uid") not in self.clients:
            return self._refuse(request, peer, "This account isn't allowed to use kronos.", request.get("action"))
        action, args = request.get("action"), request.get("args", {})
        try:
            plan = actions.plan(action, args, self.ctx)
        except actions.Refused as exc:
            return self._refuse(request, peer, str(exc), action if isinstance(action, str) else None, args if isinstance(args, dict) else {})

        decision = "auto"
        if plan.tier == actions.PASSPHRASE:
            key = peer_key(peer)
            if not self.sessions.touch(key):
                candidate = request.get("passphrase")
                if candidate is None:
                    answered = self._ask_the_person(request, peer, plan)      # None: unlocked through the agent; a dict: the final answer
                    if answered is not None:
                        return answered
                else:
                    unlocked = self.unlock(peer, candidate, request)
                    if unlocked["status"] != "ok":
                        return unlocked
            decision = "passphrase-ok"

        # the decision goes in the log BEFORE anything happens; if that can't be written, nothing happens
        try:
            self._log("decision", request, peer, action=action, args=plan.args, decision=decision, reason=plan.description)
        except audit.AuditError as exc:
            return {"status": "refused", "reason": f"Not done: {exc}"}
        started = time.monotonic()
        rc, output, detail = self._execute(plan, emit)
        ms = int((time.monotonic() - started) * 1000)
        try:
            self._log("result", request, peer, action=action, decision=decision, exit=rc, ms=ms, reason=detail)
        except audit.AuditError:
            pass                                    # it already happened; the decision line is on record
        return {"status": "ok" if rc == 0 else "failed", "exit": rc, "output": output[:MAX_OUTPUT], "description": plan.description}

    def _execute(self, plan, emit=None):
        """(exit code, output, short detail). A runner with run_action (the root helper) is handed the whole action to re-check and do;
        any other runner (the tests' fake) is given the plan's steps."""
        if hasattr(self.runner, "run_action"):
            return self.runner.run_action(plan.action, plan.args, emit)
        return execute_plan(plan, self.runner, emit)
