"""The write gate shared by every write verb: the flags and the one function that arms `Gh`.

A verb renders its plan as text, calls `arm_or_refuse`, and writes only if `args.apply` is set (the gate arms `Gh`;
until then `Gh` itself refuses any write). `--expect-plan FILE` binds an apply to the preview a human approved: if
the plan re-rendered now differs from FILE, nothing is armed or written and the fresh plan goes to `FILE.new`.
`--workflow` marks a run made by a Spec Kit workflow step; it changes nothing here (the workflow's own gate sits
between the preview step and the apply step), verbs may read `args.workflow`.
"""
from pathlib import Path

GATE_VISIBLE_LINES = 190  # how much of a plan the approval gate shows; same as ado


class PlanRefused(Exception):
    """The approved plan is not the plan that would be applied. Exit 2, nothing written."""


def add_write_flags(parser):
    parser.add_argument("--apply", action="store_true", help="perform the writes (default: preview only, nothing is sent)")
    parser.add_argument("--plan-out", metavar="FILE", help="write the preview (markdown) here")
    parser.add_argument("--expect-plan", metavar="FILE",
                        help="with --apply: refuse (exit 2) unless the plan re-renders identical to this approved file")
    parser.add_argument("--workflow", action="store_true", help="run as a Spec Kit workflow step (the workflow holds the gate)")


def gate_preview(text, path):
    """The plan as written to --plan-out and compared by --expect-plan. A plan the gate cannot show in full gets a
    warning as its FIRST line; the file keeps the full text."""
    n = len(text.splitlines())
    if n <= GATE_VISIBLE_LINES:
        return text
    return (f"⚠ Preview longer than the approval gate can show ({n} lines) — open {path} "
            f"and read it in full before approving.\n{text}")


def _path(value, flag):
    if value == "":  # an empty "$PLAN" must never mean "no plan to compare"
        raise PlanRefused(f"{flag} needs a file path (it is empty)")
    return Path(value)


def arm_or_refuse(args, gh, rendered, approve=None):
    """Arm `gh` only when `--apply` is set and the plan matches `--expect-plan` (when that is given, even if empty).
    Raises PlanRefused (exit 2) otherwise; nothing is armed then. Unwritable paths are a refusal, not a traceback.
    `approve` (comment mode, `approvals.approve_hook`): called with `--apply` right before arming; its refusal leaves `gh` unarmed."""
    expect = args.expect_plan
    if expect is not None and not args.apply:
        raise PlanRefused("--expect-plan needs --apply")
    if expect is not None:
        approved = _path(expect, "--expect-plan")
        try:  # text mode on both sides, so the newline translation of --plan-out on Windows still matches
            same = approved.read_text(encoding="utf-8") == gate_preview(rendered, expect)
        except (OSError, UnicodeDecodeError):
            same = False
        if not same:
            fresh = Path(f"{approved}.new")
            try:
                fresh.parent.mkdir(parents=True, exist_ok=True)
                fresh.write_text(gate_preview(rendered, fresh), encoding="utf-8")
            except OSError as e:
                raise PlanRefused(f"plan changed since approval, and the current plan could not be written to {fresh}: "
                                  f"{e.strerror or 'write failed'}") from None
            raise PlanRefused("plan changed since approval \u2014 re-run the preview and approve again "
                              f"(the current plan is in {fresh})")
    if args.plan_out is not None:
        out = _path(args.plan_out, "--plan-out")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(gate_preview(rendered, args.plan_out), encoding="utf-8")
            Path(f"{out}.new").unlink(missing_ok=True)
        except OSError as e:
            raise PlanRefused(f"cannot write {out}: {e.strerror or 'write failed'}") from None
    if args.apply:
        if approve is not None:
            approve()
        gh.arm()
