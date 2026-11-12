"""The ghp command line: a registry of verbs, one parser, and the exit codes.

Exit codes: 0 done; 1 a GitHub call failed (GhError) or the verb reports failure; 2 refused with nothing written
(unknown verb or bad usage, a config that is missing or invalid, a plan that changed since approval,
a ValueError from a verb, an unwritable plan file). `gate`, `strict`, `done` and `status` have their own handlers (an invalid config or an ambiguous ledger is 3 there, so CI fails closed; a blocking
finding is 1 for the gate, 2 for strict) and so has `branch --id-from` (an invalid config is 3) and `calibrate` returns its own codes (1: the model's reading is not good enough, 2: refused).
A verb is `REGISTRY[name] = (register, run)`: `register(sub)` adds its subparser (write verbs call
`plan.add_write_flags`), `run(args, gh) -> int`. Each task that adds a verb adds its entry here.
"""
import argparse
import sys

from ghp import __version__, activate, approvals, branch, calibrate, comments, decisions, doctor, dod, done, gate, issues, kits, projects, prs, push, questions, rules, setup_cmd, stack, status, strict
from ghp.config import ConfigError, ConfigInvalid
from ghp.gh import Gh, GhError, scrub
from ghp.plan import PlanRefused

REGISTRY = {"get": (issues.register, issues.run), "comments": (comments.register, comments.run),
            "project": (projects.register, projects.run), "stack": (stack.register, stack.run),
            "dod": (dod.register, dod.run), "rules": (rules.register, rules.run),
            "questions": (questions.register, questions.run),
            "decisions": (decisions.register, decisions.run),
            "push": (push.register, push.run), "activate": (activate.register, activate.run),
            "branch": (branch.register, branch.run), "pr": (prs.register, prs.run),
            "gate": (gate.register, gate.run), "calibrate": (calibrate.register, calibrate.run),
            "kits": (kits.register, kits.run), "strict": (strict.register, strict.run),
            "done": (done.register, done.run), "status": (status.register, status.run),
            "approvals": (approvals.register, approvals.run), "doctor": (doctor.register, doctor.run_verb),
            "setup": (setup_cmd.register, setup_cmd.run)}  # also the seam the tests use to add throwaway verbs


def main(argv=None, gh=None):
    """`gh` is for tests only: a supplied Gh must be unarmed (arm() is one-way, so main cannot check it; only the
    write gate may arm it). Real runs get a fresh Gh()."""
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--version"]:  # returns 0 like every verb, instead of argparse's SystemExit
        print("ghp " + __version__)
        return 0
    parser = argparse.ArgumentParser(prog="ghp", description="GitHub Issues and Projects CLI for Spec Kit",
                                     epilog=approvals.LIMITS)
    parser.add_argument("--version", action="version", version="ghp " + __version__)
    sub = parser.add_subparsers(dest="command", metavar="VERB", title="verbs", required=True)
    for register, _ in REGISTRY.values():
        register(sub)
    args = parser.parse_args(argv)
    if getattr(args, "expect_plan", None) is not None and not args.apply:
        parser.error("--expect-plan needs --apply")
    try:
        return REGISTRY[args.command][1](args, gh or Gh())
    except (ConfigError, PlanRefused, ValueError) as e:
        # 3 lets CI fail closed (as `gate` and `strict`, which map their own): an invalid config under `branch --id-from`, whose result CI trusts
        err, code = e, 3 if isinstance(e, ConfigInvalid) and args.command == "branch" and getattr(args, "id_from", None) is not None else 2
    except GhError as e:
        err, code = e, 1
    print("error: " + " ".join(scrub(err).split()), file=sys.stderr)  # one line, token-shaped text removed
    return code


if __name__ == "__main__":
    raise SystemExit(main())
