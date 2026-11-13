"""T33: the shipped Spec Kit workflow `workflows/ghp-lifecycle/workflow.yml` (AC1, AC2, AC3; AC4 is tests/test_speckit_contract.py).

PyYAML is not allowed (X1), so `load_workflow` is a small strict reader for the one shape the workflow uses: block mappings and block lists of
2-space indentation, values a double-quoted string (escapes `\\"` and `\\\\` only), an integer, `true`/`false`, or a `|` literal block. A duplicate
key, a tab, an odd indent, a flow form, an anchor, an unquoted string or a trailing comment is an error, never a guess.

Each rule is a checker that returns a list of problems: the named tests run it on the shipped file, and the mutation tests run every checker on a
broken copy (a preview removed, a gate removed, an apply before its gate, an uncapped loop, another interpolation, a `done` step, ...) and require
that at least one checker objects."""
import copy
import re
from pathlib import Path

import pytest

import ghp
from ghp import approvals

ROOT = Path(__file__).resolve().parent.parent
SHIPPED = ROOT / "workflows" / "ghp-lifecycle" / "workflow.yml"
QUOTED = re.compile(r'"((?:[^"\\]|\\["\\])*)"$')
KEY = re.compile(r'([A-Za-z_][\w-]*):(?: (.*))?$')
INTERP = re.compile(r"\{\{.*?\}\}", re.S)
ISSUE = "{{ inputs.issue_number }}"
# the first line of every shell step that uses the issue number: the only interpolation, single-quoted, then checked to be digits (the `number` type,
# checked on `run --input` and `resume --input`, still lets negatives and floats through; a value edited into the run's state.json is not re-checked
# by the engine at all, and that is no boundary: whoever can edit it can edit the installed workflow file too)
GUARD = ("n='{{ inputs.issue_number }}'; case \"$n\" in ''|0*|*[!0-9]*) echo 'ghp-lifecycle: issue_number must be a positive whole number' >&2; "
         "exit 2;; esac")
VERDICT_ENUM = ["", "approve", "reject"]
CONDITION = "{{ steps.%s.output.stderr | contains('plan changed since approval') }}"
WRITE_VERBS = re.compile(r"\bghp (questions|decisions|push|pr create|activate|done)\b")
FORBIDDEN = [re.compile(p) for p in (r"\bgh ", r"\bgit push\b", r"\bghp done\b", r"speckit\.ghp\.done", r"--force\b", r"--allow-", r"\bset \+e\b",
                                     r"\bexit 0\b", r"\btrap\b", r"\|\| *(true|:)", r";\s*true\b", r"\bspecify\b")]
END_STEPS = ("wait-po-validation", "sync-po-validation", "check-wait-po-validation")  # after the PR is open: wait, read the verdicts, check; never `done`


class WorkflowError(ValueError):
    pass


def _scalar(text, n):
    text = text.strip()
    if text in ("true", "false"):
        return text == "true"
    if re.fullmatch(r"-?[0-9]+", text):
        return int(text)
    m = QUOTED.match(text)
    if not m:
        raise WorkflowError("line %d: a value must be a double-quoted string, an integer, true/false or |: %r" % (n, text))
    return m.group(1).replace('\\"', '"').replace("\\\\", "\\")


def load_workflow(text):
    lines = text.split("\n")
    rows, i = [], 0
    while i < len(lines):
        raw, n = lines[i], i + 1
        i += 1
        if "\t" in raw:
            raise WorkflowError("line %d: a tab" % n)
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise WorkflowError("line %d: an odd indent" % n)
        body = raw.strip()
        if body.endswith(": |") or body == "- |":
            block = []
            while i < len(lines) and (not lines[i].strip() or len(lines[i]) - len(lines[i].lstrip(" ")) > indent):
                if "\t" in lines[i]:
                    raise WorkflowError("line %d: a tab" % (i + 1))
                block.append(lines[i])
                i += 1
            while block and not block[-1].strip():
                block.pop()
            if not block:
                raise WorkflowError("line %d: an empty | block" % n)
            cut = len(block[0]) - len(block[0].lstrip(" "))
            if any(ln.strip() and len(ln) - len(ln.lstrip(" ")) < cut for ln in block):
                raise WorkflowError("line %d: a | block indented less than its first line" % n)
            rows.append([n, indent, body[:-1].rstrip(), "\n".join(ln[cut:] for ln in block) + "\n"])
        else:
            rows.append([n, indent, body, None])
    value, i = _block(rows, 0, 0)
    if i != len(rows):
        raise WorkflowError("line %d: unexpected content" % rows[i][0])
    return value


def _block(rows, i, indent):
    if i >= len(rows) or rows[i][1] != indent:
        raise WorkflowError("line %d: bad indent" % (rows[i][0] if i < len(rows) else 0))
    if rows[i][2].startswith("- ") or rows[i][2] == "-":
        out = []
        while i < len(rows) and rows[i][1] == indent and rows[i][2].startswith("- "):
            n, _, body, block = rows[i]
            rest = body[2:].strip()
            if KEY.match(rest):  # "- key: v" opens a mapping at indent + 2
                rows[i] = [n, indent + 2, rest, block]
                item, i = _block(rows, i, indent + 2)
                out.append(item)
            elif block is not None:
                raise WorkflowError("line %d: a | block as a list item" % n)
            else:
                out.append(_scalar(rest, n))
                i += 1
        return out, i
    out = {}
    while i < len(rows) and rows[i][1] == indent:
        n, _, body, block = rows[i]
        m = KEY.match(body)
        if not m:
            raise WorkflowError("line %d: not a key: %r" % (n, body))
        key, rest = m.group(1), m.group(2)
        if key in out:
            raise WorkflowError("line %d: duplicate key %r" % (n, key))
        i += 1
        if block is not None:
            out[key] = block
        elif rest is None or not rest.strip():
            if i < len(rows) and rows[i][1] > indent:
                out[key], i = _block(rows, i, indent + 2)
            else:
                raise WorkflowError("line %d: key %r has no value" % (n, key))
        else:
            out[key] = _scalar(rest, n)
    if i < len(rows) and rows[i][1] > indent:
        raise WorkflowError("line %d: bad indent" % rows[i][0])
    return out, i


def shipped():
    return load_workflow(SHIPPED.read_text(encoding="utf-8"))


def walk(steps, loop=None):
    """(step, the do-while it sits in or None), in run order."""
    for s in steps:
        yield s, loop
        if isinstance(s.get("steps"), list):
            yield from walk(s["steps"], s)


def kind(s):
    return s.get("type", "command" if "command" in s else None)


def all_ids(w):
    return [s.get("id") for s, _ in walk(w["steps"])]


# ---- the checkers: each returns the problems it finds ([] = the rule holds) ----

def check_triplets(w):
    """AC1: every GitHub write is a do-while of exactly [shell preview --plan-out P, gate showing P, shell apply --apply --expect-plan P] with the same
    verb and flags, P the gate's fixed plan file, followed by `<loop>-applied` that stops the run when P.new exists. No write outside such a loop."""
    probs = []
    for s, loop in walk(w["steps"]):
        run = s.get("run", "") if kind(s) == "shell" else ""
        if kind(s) == "shell" and (WRITE_VERBS.search(run) and "--apply" in run) and (loop is None or loop["steps"][-1] is not s):
            probs.append("%s: an apply outside the last place of a preview/gate/apply loop" % s.get("id"))
        if "--apply" in run and not WRITE_VERBS.search(run):
            probs.append("%s: --apply on something that is not a ghp write verb" % s.get("id"))
    top = w["steps"]
    for k, s in enumerate(top):
        if kind(s) != "do-while":
            continue
        sid, inner = s.get("id", ""), s.get("steps") or []
        if not sid.endswith("-loop") or [kind(x) for x in inner] != ["shell", "gate", "shell"]:
            probs.append("%s: a loop must be exactly preview (shell), gate, apply (shell)" % sid)
            continue
        pre, gate, app = inner
        name = sid[:-len("-loop")]
        if (pre.get("id"), gate.get("id"), app.get("id")) != (name + "-preview", "approve-" + name, name + "-apply"):
            probs.append("%s: the steps must be %s-preview, approve-%s, %s-apply" % (sid, name, name, name))
        plan = approvals.APPLY_FILES.get(gate.get("id"))
        prun, arun = pre.get("run", ""), app.get("run", "")
        if plan is None:
            probs.append("%s: the gate %s has no fixed plan file in approvals.APPLY_FILES" % (sid, gate.get("id")))
            continue
        pm = re.search(r" --plan-out (\S+)\n", prun)
        if not pm or pm.group(1) != plan or "--apply" in prun or "--expect-plan" in prun:
            probs.append("%s: the preview must end with --plan-out %s and must not apply" % (sid, plan))
        if gate.get("show_file") != plan:
            probs.append("%s: the gate must show %s" % (sid, plan))
        tail = " || { [ $? -eq 2 ] && [ -e %s.new ]; }\n" % plan
        if not arun.endswith(" --apply --expect-plan %s%s" % (plan, tail)):
            probs.append("%s: the apply must end with --apply --expect-plan %s and the refused-plan tail" % (sid, plan))
        elif prun.replace(" --plan-out %s\n" % plan, "") != arun.replace(" --apply --expect-plan %s%s" % (plan, tail), ""):
            probs.append("%s: the apply must run exactly the preview's command and flags" % sid)
        nxt = top[k + 1] if k + 1 < len(top) else {}
        want = "if [ -e %s.new ]; then echo 'ghp-lifecycle: the %s plan changed after approval; start a new run' >&2; exit 1; fi\n" % (plan, name)
        if nxt.get("id") != name + "-applied" or kind(nxt) != "shell" or nxt.get("run") != want:
            probs.append("%s: must be followed by %s-applied, which stops the run when %s.new exists" % (sid, name, plan))
        if s.get("condition") != CONDITION % app.get("id"):
            probs.append("%s: the condition must test the apply's 'plan changed since approval'" % sid)
    return probs


def check_loops(w):
    """AC1: each loop is a do-while capped at ONE pass (a second pass would reuse a supplied verdict on a plan nobody saw); no other loop kind, no
    nesting; every gate is a fixed approve/reject gate that aborts on reject."""
    probs = []
    for s, loop in walk(w["steps"]):
        t = kind(s)
        if t not in ("shell", "gate", "command", "do-while"):
            probs.append("%s: step type %r is not allowed" % (s.get("id"), t))
        if t == "do-while":
            if loop is not None:
                probs.append("%s: a nested loop" % s.get("id"))
            if not (type(s.get("max_iterations")) is int and s["max_iterations"] == 1):
                probs.append("%s: max_iterations must be 1" % s.get("id"))
        if t == "gate" and (s.get("options") != ["approve", "reject"] or s.get("on_reject") != "abort" or not s.get("message")):
            probs.append("%s: a gate must offer approve/reject and abort on reject" % s.get("id"))
    return probs


def check_interpolation(w):
    """AC2: the only `{{ }}` in a shell command is `inputs.issue_number`, exactly once, in the guard line that starts the step; command steps pass it in
    `args`; the other fields allow one fixed form each. Every gate has its `verdict_<gate>` input and every verdict input has its gate."""
    probs = []
    inputs = w.get("inputs") or {}
    num = inputs.get("issue_number") or {}
    if num.get("type") != "number" or num.get("required") is not True:
        probs.append("inputs.issue_number must be a required number")
    gates = []
    for s, _ in walk(w["steps"]):
        sid, t = s.get("id"), kind(s)
        for key, value in s.items():
            if key == "steps" or not isinstance(value, (str, dict)):
                continue
            texts = [value] if isinstance(value, str) else [v for v in value.values() if isinstance(v, str)]
            for text in texts:
                found = INTERP.findall(text)
                if not found:
                    continue
                if t == "shell" and key == "run":
                    if found != [ISSUE] or not text.startswith(GUARD + "\n") and not text.startswith("bash <<'EOF'\n" + GUARD + "\n"):
                        probs.append("%s: a shell command may interpolate only inputs.issue_number, once, in the guard line" % sid)
                elif t == "command" and key == "input":
                    if found != [ISSUE] or not text.startswith(ISSUE):
                        probs.append("%s: a command step may pass only inputs.issue_number" % sid)
                elif (t, key) == ("command", "integration") and text == "{{ inputs.integration }}":
                    pass
                elif (t, key) == ("command", "model") and text == "{{ inputs.review_model }}":
                    pass
                elif (t, key) == ("do-while", "condition") and re.fullmatch(re.escape(CONDITION).replace("%s", r"[a-z-]+-apply"), text):
                    pass
                else:
                    probs.append("%s: interpolation in %s is not allowed" % (sid, key))
        if t == "shell" and "$n" in s.get("run", "") and ISSUE not in s.get("run", ""):
            probs.append("%s: uses $n without the guard" % sid)
        if t == "gate":
            gates.append(sid)
            v = s.get("verdict_input")
            d = inputs.get(v) if isinstance(v, str) else None
            if v != approvals._input(sid) or not isinstance(d, dict):
                probs.append("%s: the gate needs its own input verdict_%s" % (sid, sid.replace("-", "_")))
            elif d.get("type") != "string" or d.get("enum") != VERDICT_ENUM or d.get("default") != "":
                probs.append("%s: its verdict input must be a string, enum ['', approve, reject], default ''" % sid)
    extra = sorted(k for k in inputs if k.startswith("verdict_") and k not in {approvals._input(g) for g in gates})
    if extra:
        probs.append("verdict inputs without a gate: %s" % extra)
    if set(inputs) - {"issue_number", "integration", "review_model"} - {approvals._input(g) for g in gates}:
        probs.append("unexpected inputs: %s" % sorted(set(inputs) - {"issue_number", "integration", "review_model"}))
    return probs


def check_ends_at_pr(w):
    """AC3: the run ends at the open PR: the PR triplet is the last write, and after it only the wait for the product owner's verdicts. No step closes the
    story (`ghp done`, `speckit.ghp.done`), merges or pushes anything else."""
    probs = []
    ids = [s.get("id") for s in w["steps"]]
    if "pr-applied" not in ids:
        return ["no pr-loop / pr-applied: the run must end at the open PR"]
    after = ids[ids.index("pr-applied") + 1:]
    if tuple(after) != END_STEPS:
        probs.append("after the PR only %s may run, got %s" % (list(END_STEPS), after))
    for s, _ in walk(w["steps"]):
        text = " ".join(str(v) for v in s.values() if isinstance(v, (str, dict)))
        if s.get("command") == "speckit.ghp.done" or re.search(r"\bghp done\b|speckit\.ghp\.done|\bgh pr merge\b", text):
            probs.append("%s: closes the story inside the run" % s.get("id"))
    return probs


def check_exit_codes(w):
    """A failing `ghp` (exit 1, 2 or 3) stops the run: no masking (`|| true`, `exit 0`, a pipe, `set +e`), every `||` either exits non-zero or is the
    apply's refused-plan tail. The first step makes the target branch resolvable (origin/HEAD) or stops. No `gh` call, no `git push`, no `--force`."""
    probs = []
    first = w["steps"][0] if w.get("steps") else {}
    if first.get("id") != "base-check" or "refs/remotes/origin/HEAD" not in first.get("run", "") or "exit 2" not in first.get("run", ""):
        probs.append("the first step must be base-check: stop (exit 2) when origin/HEAD is not set")
    for s, loop in walk(w["steps"]):
        if kind(s) != "shell":
            continue
        run = s.get("run", "").replace(GUARD, "")
        for p in FORBIDDEN:
            if p.search(run):
                probs.append("%s: %r is not allowed in a run" % (s.get("id"), p.pattern))
        if re.search(r"(?<!\|)\|(?!\|)", run):
            probs.append("%s: a pipe hides the exit code" % s.get("id"))
        for m in re.finditer(r"\|\|", run):
            rest = run[m.end():]
            ok = re.match(r" exit [12]\b", rest) or re.match(r" \{ [^{}]*; exit [12]; \}", rest)
            tail = re.match(r" \{ \[ \$\? -eq 2 \] && \[ -e (\S+)\.new \]; \}\n$", rest)
            if not ok and not (tail and loop is not None and loop["steps"][-1] is s):
                probs.append("%s: `||` must exit non-zero (or be the apply's refused-plan tail)" % s.get("id"))
    return probs


def check_step_ids(w):
    """The ids `ghp approvals sync` trusts in a run's state.json are exactly the shipped ones (approvals.STEP_IDS), the gates are approvals.GATES, the
    workflow id is approvals.RUN_WORKFLOW and the issue input is approvals.RUN_INPUT."""
    probs = []
    ids = all_ids(w)
    if len(ids) != len(set(ids)):
        probs.append("duplicate step ids")
    if approvals.STEP_IDS is None or tuple(ids) != tuple(approvals.STEP_IDS):
        probs.append("step ids drift from approvals.STEP_IDS")
    if not all(isinstance(x, str) and re.fullmatch(r"[a-z][a-z0-9-]{0,63}", x) for x in ids):
        probs.append("a step id is not of the shape approvals accepts")
    gates = [s.get("id") for s, _ in walk(w["steps"]) if kind(s) == "gate"]
    if not set(gates) <= set(approvals.GATES):
        probs.append("gates outside approvals.GATES: %s" % sorted(set(gates) - set(approvals.GATES)))
    if (w.get("workflow") or {}).get("id") != approvals.RUN_WORKFLOW or approvals.RUN_INPUT not in (w.get("inputs") or {}):
        probs.append("workflow id or issue input differ from approvals")
    return probs


# review round 1 (I3, M3): the exact agent arguments and the exact `ghp` line of every guarded step that is not a triplet. The engine runs `sh -c`
# without `-e`, so the LAST command's status is the step's: a second line or a `; echo ok` after the `ghp` call would hide its exit code.
COMMAND_ARGS = {"pull": ("speckit.ghp.pull", ISSUE + " --workflow"), "plan": ("speckit.ghp.plan", ISSUE + " --workflow"),
                "plan-with-answers": ("speckit.ghp.plan", ISSUE + " --workflow --close-assumed"), "tasks": ("speckit.ghp.tasks", ISSUE + " --workflow"),
                "implement": ("speckit.ghp.implement", ISSUE + " --workflow"), "gate": ("speckit.ghp.gate", ISSUE + " --workflow"),
                "review": ("speckit.ghp.review", ISSUE + " --workflow"), "pr-prepare": ("speckit.ghp.pr", ISSUE + " --prepare-only")}
CHECKED_GATES = ("review-spec", "wait-po-answers", "review-plan", "review-review", "wait-po-validation")  # approvals.REVIEW_GATES + WAIT_GATES
GHP_LINES = {**{"check-" + g: 'ghp approvals check "$n" --gate %s' % g for g in CHECKED_GATES},
             "sync-po-answers": 'ghp questions "$n" --sync --if-comment-mode', "sync-po-validation": 'ghp decisions "$n" --sync --if-comment-mode',
             "strict-check-implement": 'ghp strict check "$n" --phase implement', "strict-verify": 'ghp strict verify "$n"',
             "refresh-context": 'ghp comments "$n" > /dev/null'}
PLAIN_RUNS = {"stack-check": "ghp stack show --check\n", "pr-clear": "rm -f .ghp/pr-args.json .ghp/pr-body.md\n"}
GATE_LINE = 'ghp gate "$n" --src . --base "$base" "${args[@]}" > /dev/null < /dev/null'
APPLY_TAIL = re.compile(r" \|\| \{ \[ \$\? -eq 2 \] && \[ -e \S+\.new \]; \}$")


def check_commands(w):
    """Every command step runs its own command with exactly its arguments (`--workflow`: the agent makes no GitHub write; `--prepare-only`: pr-prepare
    opens nothing), on the chosen integration."""
    probs = []
    got = {s.get("id"): s for s, _ in walk(w["steps"]) if kind(s) == "command"}
    if sorted(got) != sorted(COMMAND_ARGS):
        probs.append("command steps %s, expected %s" % (sorted(got), sorted(COMMAND_ARGS)))
    for sid, (cmd, args) in COMMAND_ARGS.items():
        s = got.get(sid, {})
        want = {"id": sid, "command": cmd, "integration": "{{ inputs.integration }}", "input": {"args": args}}
        if sid == "review":
            want["model"] = "{{ inputs.review_model }}"
        if s and s != want:
            probs.append("%s: must be exactly %r" % (sid, want))
    return probs


def check_last_command(w):
    """Each guarded shell step is the guard line plus ONE `ghp` line (no `;`, `&&`, `&` or second line after it; the apply's refused-plan tail is the
    only `||`); the non-triplet ones run exactly their pinned line; each review and wait gate is followed by the check of that same gate."""
    probs = []
    ids = [s.get("id") for s in w["steps"]]
    for s, loop in walk(w["steps"]):
        sid, run = s.get("id"), s.get("run", "")
        if kind(s) != "shell" or sid == "base-check" or sid.endswith("-applied"):
            continue
        if sid in PLAIN_RUNS:
            if run != PLAIN_RUNS[sid]:
                probs.append("%s: must run exactly %r" % (sid, PLAIN_RUNS[sid]))
            continue
        if sid == "gate-check":
            lines = run.split("\n")
            if lines[:2] != ["bash <<'EOF'", GUARD] or lines[-3:] != [GATE_LINE, "EOF", ""]:
                probs.append("gate-check: the heredoc must start with the guard and end with the ghp gate line")
            continue
        lines = run.split("\n")
        cmd = lines[1] if len(lines) == 3 and lines[0] == GUARD and lines[2] == "" else None
        if cmd is None or not cmd.startswith("ghp "):
            probs.append("%s: must be the guard line and one ghp line" % sid)
            continue
        bare = APPLY_TAIL.sub("", cmd) if loop is not None and loop["steps"][-1] is s else cmd
        if re.search(r";|&&|\|\||(?<![>&])&(?!>)", bare):
            probs.append("%s: nothing may follow the ghp call" % sid)
        if sid in GHP_LINES and cmd != GHP_LINES[sid]:
            probs.append("%s: must run exactly %r" % (sid, GHP_LINES[sid]))
        if "approvals check" in cmd and sid not in GHP_LINES:
            probs.append("%s: an approvals check outside the pinned check steps" % sid)
    for g in CHECKED_GATES:
        c = "check-" + g
        if g not in ids or c not in ids or ids.index(c) < ids.index(g) or any(ids.index(g) < k < ids.index(c) and kind(w["steps"][k]) != "shell"
                                                                              for k in range(len(ids))):
            probs.append("%s: must be followed by %s before any other gate or agent step" % (g, c))
    if [x for x in GHP_LINES if x not in ids]:
        probs.append("pinned steps missing: %s" % [x for x in GHP_LINES if x not in ids])
    return probs


CHECKERS = (check_triplets, check_loops, check_interpolation, check_ends_at_pr, check_exit_codes, check_step_ids, check_commands, check_last_command)


def problems(w):
    return [p for c in CHECKERS for p in c(w)]


# ---- the reader ----
@pytest.mark.parametrize("bad", ['a: "x"\na: "y"\n', 'a:\n\tb: "x"\n', 'a:\n   b: "x"\n', "a: x\n", 'a: "x" # c\n', "a: [x]\n", "a: {b: 1}\n",
                                 'a: &x "y"\n', "a:\n", "a: |\n", 'a: "x\\n"\n', "a: 1.5\n", "- |\n  x\n"])
def test_the_reader_refuses_odd_forms(bad):
    with pytest.raises(WorkflowError):
        load_workflow(bad)


def test_the_reader_reads_the_shape_the_workflow_uses():
    doc = load_workflow('a: "1"\nb:\n  c: true\n  n: 1\n  r: |\n    x "y"\n\n      z\n  d:\n    - id: "x"\n      run: |\n        p\n    - "q"\ne: ""\n')
    assert doc == {"a": "1", "b": {"c": True, "n": 1, "r": 'x "y"\n\n  z\n', "d": [{"id": "x", "run": "p\n"}, "q"]}, "e": ""}


# ---- the shipped workflow: AC1, AC2, AC3 ----
def test_the_workflow_is_the_ghp_lifecycle_of_this_release():
    w = shipped()
    assert w["schema_version"] == "1.0" and w["workflow"]["id"] == "ghp-lifecycle"
    assert w["workflow"]["version"] == ghp.__version__ and w["requires"]["speckit_version"] == ">=1.2.1"


def test_every_apply_step_follows_a_gate_and_a_preview():
    w = shipped()
    assert check_triplets(w) == []
    loops = [s["id"] for s in w["steps"] if kind(s) == "do-while"]
    assert loops == ["questions-loop", "plan-questions-loop", "answers-questions-loop", "push-loop", "decisions-loop", "sync-loop", "pr-loop"]
    apply_gates = {s["id"] for s, _ in walk(w["steps"]) if kind(s) == "gate" and s["id"] in approvals.APPLY_FILES}
    assert apply_gates == {"approve-" + x[:-5] for x in loops}


def test_loops_are_capped_at_one_pass():
    assert check_loops(shipped()) == []


def test_only_issue_number_is_interpolated():
    w = shipped()
    assert check_interpolation(w) == []
    gates = [s["id"] for s, _ in walk(w["steps"]) if kind(s) == "gate"]
    assert len(gates) == 12 and all(approvals._input(g) in w["inputs"] for g in gates)


def test_workflow_ends_at_the_open_pr():
    w = shipped()
    assert check_ends_at_pr(w) == []
    assert [s["id"] for s in w["steps"]][-4:] == ["pr-applied", *END_STEPS]


def test_a_failing_step_stops_the_run_and_the_base_is_checked_first():
    assert check_exit_codes(shipped()) == []


def test_command_steps_and_ghp_lines_are_pinned():
    w = shipped()
    assert check_commands(w) == [] and check_last_command(w) == []
    assert set(CHECKED_GATES) == set(approvals.REVIEW_GATES) | set(approvals.WAIT_GATES)


def test_loop_steps_match_approvals():
    w = shipped()
    assert approvals.LOOP_STEPS == {s["id"]: tuple(x["id"] for x in s["steps"]) for s in w["steps"] if kind(s) == "do-while"}


def test_step_ids_are_the_ones_approvals_sync_trusts():
    w = shipped()
    assert check_step_ids(w) == []
    assert approvals.RUN_INPUT == "issue_number"
    gates = {s["id"] for s, _ in walk(w["steps"]) if kind(s) == "gate"}
    assert set(approvals.GATES) - gates == {"approve-done", "approve-activate"}  # applied by hand (speckit.ghp.done / pull), never by the run
    assert {g: approvals.APPLY_FILES[g] for g in gates if g in approvals.APPLY_FILES} == \
        {s["steps"][1]["id"]: s["steps"][1]["show_file"] for s in w["steps"] if kind(s) == "do-while"}


README = SHIPPED.parent / "README.md"
SECTION_REF = re.compile(r"README's section '([^']+)'")
README_PINS = ("**Anyone who can run `specify` can set them**", "so a verdict is never the approval",
               "**Nothing technical stops a script or an agent that passes the verdict inputs**", "Terminal mode trusts whoever runs `specify`.",
               "A run in comment mode (`approval: \"comment\"`) and the poller (`ghp approvals sync <run> --resume`) have not run live", "The contract test", "Spec Kit 1.2.4.dev0 only; the minimum, 1.2.1, has not been tried")


def readme_problems(wf_text, readme):
    """Every `README's section '<title>'` in the workflow and every `"<title>"` cross-reference in the README names a `## ` heading of the README;
    no other form of a README section reference; the honesty sentences (who can set verdicts, the terminal-mode limit, what is unverified) stay."""
    heads = set(re.findall(r"^## (.+)$", readme, re.M))
    probs = ["workflow.yml: no README section %r" % t for t in SECTION_REF.findall(wf_text) if t not in heads]
    if re.search(r"README(?:'s)? (?!section ')[^\n\"]{0,40}section", wf_text):
        probs.append("workflow.yml: a README section reference not in the form README's section '<title>'")
    probs += ["README.md: no section %r" % t for t in re.findall(r'see "([^"]+)"', readme) if t not in heads]
    flat = " ".join(readme.split())
    probs += ["README.md lost: %r" % p for p in README_PINS if p not in flat]
    return probs


def test_readme_section_references_resolve_and_the_limits_are_stated():
    wf_text, readme = SHIPPED.read_text(encoding="utf-8"), README.read_text(encoding="utf-8")
    assert readme_problems(wf_text, readme) == []
    assert SECTION_REF.findall(wf_text) == ["Stopping, resuming and recovering"]
    assert not re.search(r"\btask T\d", readme)  # T63: the shipped page names no internal task id
    assert {"Where approval is enforced", "Stopping, resuming and recovering"} <= set(re.findall(r"^## (.+)$", readme, re.M))
    top = " ".join((ROOT / "README.md").read_text(encoding="utf-8").split())
    assert "It has run end to end against GitHub once, in terminal mode" in top and "comment mode and its poller have not run live yet" in top
    assert "has not been run end to end against GitHub yet" not in top


@pytest.mark.parametrize("old, new, where", [
    ("section 'Stopping, resuming and recovering'", "section 'Recovery'", "wf"),
    ("as the workflow README's section 'Stopping, resuming and recovering' says", "as the workflow README's recovery section says", "wf"),
    ("## Stopping, resuming and recovering", "## Recovering", "readme"),
    ('see "Where approval is enforced"', 'see "Who approves"', "readme"),
    ("**Anyone who can run `specify` can set them**", "The poller sets them", "readme"),
    ("**Nothing technical stops a script or an agent that passes the verdict inputs**", "The gate is safe", "readme"),
    ("A run in comment mode (`approval: \"comment\"`) and the poller", "The poller", "readme"),
])
def test_a_broken_readme_reference_or_a_dropped_limit_is_caught(old, new, where):
    wf_text, readme = SHIPPED.read_text(encoding="utf-8"), README.read_text(encoding="utf-8")
    assert old in (wf_text if where == "wf" else readme)
    if where == "wf":
        wf_text = wf_text.replace(old, new)
    else:
        readme = readme.replace(old, new)
    assert readme_problems(wf_text, readme)


QUESTION_GATES = ("approve-questions", "approve-plan-questions", "approve-answers-questions")


def test_the_questions_texts_say_a_change_is_a_new_comment():
    """questions.py never edits a comment (a changed set is a NEW one; the T34 live run added two), so no workflow text may say it is edited."""
    import ghp.questions as q
    assert "A comment is never edited" in q.__doc__
    wf_text, readme = SHIPPED.read_text(encoding="utf-8"), " ".join(README.read_text(encoding="utf-8").split())
    gates = {s["id"]: s for s, _ in walk(shipped()["steps"]) if kind(s) == "gate"}
    for gid in QUESTION_GATES:
        msg = gates[gid]["message"]
        assert "a changed set is posted as a new comment; no comment is ever edited" in msg, gid
        assert not re.search(r"updat|edit(?!ed\b)|rewrit", msg.replace("ever edited", "")), gid
    comment = wf_text.split('- id: "plan-questions-loop"')[0].rstrip().rsplit("\n", 1)[-1]
    assert "a changed set is a new marked comment, never an edit" in comment and "is edited" not in comment
    row = next(ln for ln in readme.split("| `") if ln.startswith("plan-questions-loop`"))
    assert "(a changed set is a new comment; no comment is edited)" in row and "same comment" not in readme
    for cmd in ("pull", "plan"):
        text = " ".join((ROOT / "commands" / (cmd + ".md")).read_text(encoding="utf-8").split())
        assert "edits the earlier" not in text and "a changed set is a new comment" in text, cmd


def test_the_sync_texts_say_what_the_second_push_does():
    """`ghp push` never opens or closes an issue (a checked box changes nothing; T34 live: 7 ticked, `unchanged 7`), so sync-loop syncs no ticks."""
    import ghp.push as p
    assert "Push never opens or closes an issue" in p.__doc__ and "a checked box changes nothing" in p.__doc__
    msg = {s["id"]: s for s, _ in walk(shipped()["steps"]) if kind(s) == "gate"}["approve-sync"]["message"]
    assert "brings the sub-issues on GitHub in line with tasks.md" in msg and "Ticking a task changes nothing on GitHub" in msg
    assert "no sub-issue is opened or closed here" in msg and not re.search(r"(?i)\bsync(ing)? the task states|states ticked", msg)
    readme = " ".join(README.read_text(encoding="utf-8").split())
    row = next(ln for ln in readme.split("| `") if ln.startswith("sync-loop`"))
    assert "Preview, then push again: new or renamed tasks reach their sub-issues; a ticked task closes nothing" in row
    assert "ticked during implement" not in readme and "tasks ticked after `sync-loop` are not synced" not in readme


def test_the_recovery_section_covers_a_push_that_stopped_part_way():
    """T34 live: push-apply stopped after one create (exit 1, `re-run ghp push, which resumes`); a resume re-ran the apply on the old plan, which
    refused, and only a new run finished the push. The README says so, in its recovery section."""
    import ghp.push as p
    readme = README.read_text(encoding="utf-8")
    section = " ".join(readme.split("## Stopping, resuming and recovering", 1)[1].split("\n## ", 1)[0].split())
    assert "re-run ghp push, which resumes" in p.render_result({"created": [], "updated": [], "failed": [
        {"task_id": "T001", "number": 10, "error": "E", "unknown": False}]})
    for needle in ("A push that stopped part way (`push-apply` failed with `push stopped at`) is not finished by a resume",
                   "and the approved plan no longer matches GitHub, so the apply refuses it",
                   "Start a new run of the story", "its push preview reads what exists and plans only the rest",
                   "Outside the workflow, `ghp push <n>` (preview, yes, apply) finishes it"):
        assert needle in section, needle


def test_the_workflow_ships_outside_the_extension():
    ignore = (ROOT / ".extensionignore").read_text(encoding="utf-8").splitlines()
    assert "workflows/" in ignore  # installed with `specify workflow add`, not copied by `specify extension add`
    assert (ROOT / "workflows" / "ghp-lifecycle" / "README.md").is_file()
    # T52: Spec Kit's workflow guide asks for a LICENSE in the folder and recommends a CHANGELOG.md
    assert sorted(p.name for p in SHIPPED.parent.iterdir()) == ["CHANGELOG.md", "LICENSE", "README.md", "workflow.yml"]


# ---- mutation checks: every broken copy is caught ----
def _find(w, sid):
    for s, loop in walk(w["steps"]):
        if s.get("id") == sid:
            return s, (loop["steps"] if loop is not None else w["steps"])
    raise KeyError(sid)


def _drop(sid):
    def f(w):
        s, where = _find(w, sid)
        where.remove(s)
    return f


def _set(sid, key, value):
    def f(w):
        _find(w, sid)[0][key] = value
    return f


def _edit(sid, old, new, key="run"):
    def f(w):
        s = _find(w, sid)[0]
        assert old in s[key], (sid, old)
        s[key] = s[key].replace(old, new)
    return f


def _swap_gate_and_apply(w):
    s = _find(w, "push-loop")[0]
    s["steps"][1], s["steps"][2] = s["steps"][2], s["steps"][1]


def _drop_input(name):
    def f(w):
        del w["inputs"][name]
    return f


def _append(step):
    def f(w):
        w["steps"].append(step)
    return f


def _rename(old, new):
    def f(w):
        _find(w, old)[0]["id"] = new
    return f


MUTANTS = {
    "preview removed": _drop("questions-preview"),
    "gate removed": _drop("approve-push"),
    "apply before gate": _swap_gate_and_apply,
    "loop uncapped": lambda w: _find(w, "pr-loop")[0].pop("max_iterations"),
    "loop capped at two": _set("sync-loop", "max_iterations", 2),
    "loop cap is a bool": _set("sync-loop", "max_iterations", True),
    "another interpolation": _edit("refresh-context", "ghp comments", "ghp comments {{ inputs.review_model }}"),
    "step output in a shell": _edit("pr-applied", "exit 1", "echo '{{ steps.pr-apply.output.stderr }}'; exit 1"),
    "unguarded interpolation": _edit("check-review-spec", GUARD + "\n", "n={{ inputs.issue_number }}\n"),
    "interpolation in a gate": _set("review-plan", "message", "Plan of {{ inputs.issue_number }}"),
    "done step added": _append({"id": "close-story", "type": "shell", "run": GUARD + "\nghp done \"$n\" --plan-out .ghp/plan-done.md\n"}),
    "done command added": _append({"id": "done", "command": "speckit.ghp.done", "integration": "{{ inputs.integration }}",
                                   "input": {"args": ISSUE + " --workflow"}}),
    "apply without expect-plan": _edit("push-apply", " --expect-plan .ghp/plan-push.md", ""),
    "plan path mismatch": _edit("decisions-apply", "--expect-plan .ghp/plan-decisions.md", "--expect-plan .ghp/plan-other.md"),
    "gate shows another file": _set("approve-sync", "show_file", ".ghp/plan-push.md"),
    "apply flags differ": _edit("pr-apply", "--push --apply", "--push --allow-incomplete --apply"),
    "exit code ignored": _edit("check-review-plan", "--gate review-plan\n", "--gate review-plan || true\n"),
    "exit code piped": _edit("strict-verify", "ghp strict verify \"$n\"\n", "ghp strict verify \"$n\" | cat\n"),
    "applied guard removed": _edit("push-applied", "exit 1", "echo stale"),
    "missing verdict input": _drop_input("verdict_review_plan"),
    "verdict input of another gate": _set("approve-pr", "verdict_input", "verdict_approve_push"),
    "step id drift": _rename("refresh-context", "refresh-ctx"),
    "base check removed": _drop("base-check"),
    "a gh write": _edit("refresh-context", "ghp comments \"$n\"", "gh issue close \"$n\""),
    "reject does not abort": _set("review-spec", "on_reject", "continue"),
    # review round 1 (I3, M3)
    "implement without --workflow": lambda w: _find(w, "implement")[0].update(input={"args": ISSUE}),
    "tasks without --workflow": lambda w: _find(w, "tasks")[0].update(input={"args": ISSUE}),
    "pr-prepare without --prepare-only": lambda w: _find(w, "pr-prepare")[0].update(input={"args": ISSUE + " --workflow"}),
    "plan-with-answers without --close-assumed": lambda w: _find(w, "plan-with-answers")[0].update(input={"args": ISSUE + " --workflow"}),
    "pull runs another command": _set("pull", "command", "speckit.ghp.plan"),
    "command on a fixed integration": _set("tasks", "integration", "claude"),
    "check of another gate": _edit("check-review-review", "--gate review-review", "--gate review-plan"),
    "check step dropped": _drop("check-wait-po-answers"),
    "check moved after an agent step": lambda w: w["steps"].insert(w["steps"].index(_find(w, "pr-prepare")[0]) + 1,
                                                                   w["steps"].pop(w["steps"].index(_find(w, "check-review-review")[0]))),
    "echo after a check": _edit("check-review-spec", "--gate review-spec\n", "--gate review-spec; echo ok\n"),
    "second line after a check": _edit("check-review-spec", "--gate review-spec\n", "--gate review-spec\necho checked\n"),
    "true line in gate-check": _edit("gate-check", " < /dev/null\n", " < /dev/null\ntrue\n"),
    "echo after a preview": _edit("push-preview", "plan-push.md\n", "plan-push.md; echo ok\n"),
    "echo after an apply tail": _edit("sync-apply", ".new ]; }\n", ".new ]; }; echo ok\n"),
    "and-chain after a strict step": _edit("strict-check-implement", "implement\n", "implement && echo ok\n"),
    "background a sync": _edit("sync-po-answers", "--if-comment-mode\n", "--if-comment-mode &\n"),
    "sync-po-answers without --if-comment-mode": _edit("sync-po-answers", " --if-comment-mode", ""),
    "sync-po-validation without --if-comment-mode": _edit("sync-po-validation", " --if-comment-mode", ""),
    "stack-check masked": _edit("stack-check", "--check\n", "--check; true\n"),
    "pr-clear does more": _edit("pr-clear", "pr-body.md\n", "pr-body.md .ghp/plan-pr.md\n"),
}


@pytest.mark.parametrize("name", sorted(MUTANTS))
def test_a_broken_workflow_is_caught(name):
    w = copy.deepcopy(shipped())
    MUTANTS[name](w)
    assert problems(w), name


def test_there_are_at_least_twelve_mutants_and_the_shipped_file_is_clean():
    assert len(MUTANTS) >= 12 and problems(shipped()) == []


@pytest.mark.parametrize("value, ok", [("5", True), ("123456789", True), ("0", False), ("05", False), ("-1", False), ("1.5", False), ("1e+20", False),
                                       ("", False), ("inf", False)])
def test_the_guard_lets_only_a_positive_whole_number_through(value, ok):
    import subprocess
    script = GUARD.replace(ISSUE, value) + "\necho ran \"$n\"\n"
    cp = subprocess.run(["sh", "-c", script], capture_output=True, text=True, timeout=30)
    assert (cp.returncode, cp.stdout) == ((0, "ran %s\n" % value) if ok else (2, ""))
