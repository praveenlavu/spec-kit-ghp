"""The only door to GitHub: one `gh api` subprocess per call.

`gh` does its own authentication; this module never reads, stores or forwards a credential (the subprocess
environment is inherited unchanged). Request bodies travel on stdin, never in argv. Reads are retried (3 attempts,
rate-limit aware); writes are armed, paced and never retried. Every message is scrubbed of token-shaped strings.

For callers: `graphql()` returns the response's `data` dict, not the {data, errors} envelope, and a response that has
`errors` ALWAYS raises, even when it also carries partial data. `rest()` returns the parsed JSON body (None if empty).
A 4xx reply's `errors[]` (the reason behind a bare "Validation Failed", T26d) is appended to the message as a bounded, ASCII-escaped summary
(`errors_summary`: strings of resource/field/code/message only).
Known limits: write pacing is per Gh instance; `arm()` is one-way; CRLF in a text (non-JSON) body is normalised to LF.
"""
import collections
import json
import re
import subprocess
import time
import unicodedata
import urllib.parse

API_VERSION = "2022-11-28"
TIMEOUT = 60  # seconds per `gh` process
MAX_ATTEMPTS = 3  # reads only
MAX_WAIT = 120  # never sleep longer than this for a rate limit; raise RateLimited instead
DEFAULT_WAIT = 60  # a limit without retry-after or a reset header: GitHub asks for at least a minute
WRITE_GAP = 1.0  # GitHub asks for >= 1 s between mutative requests
READ_METHODS = ("GET", "HEAD")
METHODS = READ_METHODS + ("POST", "PATCH", "PUT", "DELETE")
INSTALL_HINT = "the GitHub CLI (`gh`) was not found on PATH; install it from https://cli.github.com and run `gh auth login`"
UNKNOWN_OUTCOME = "unknown whether applied; re-read before retrying"

_SECRETS = (
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "***"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "***"),
    (re.compile(r"""(?i)("?authorization"?\s*[:=]\s*"?)(?:(?:bearer|token|basic)\s+)?[^\s",}]+"""), r"\1***"),
)


def scrub(text):
    """Replace token-shaped strings (gh*_ tokens, fine-grained PATs, Authorization values) with ***."""
    text = str(text)
    for pattern, repl in _SECRETS:
        text = pattern.sub(repl, text)
    return text


class GhError(Exception):
    """Any failed GitHub call. `code` is a stable E_GH_* string; `status` the HTTP status or None (no response)."""

    def __init__(self, code, status=None, detail="", more=""):
        """`more`: text that is already bounded, escaped and scrubbed (the `errors[]` summary of a 4xx), appended after the 400-character detail."""
        self.code, self.status, self.detail = code, status, scrub(detail)[:400] + more
        self.graphql_errors = []  # E_GH_GRAPHQL: (type, path tuple) per error, None where GitHub sent no string / list of strings
        super().__init__(code + (f" (HTTP {status})" if status else "") + (f": {self.detail}" if self.detail else ""))


class ScopeMissing(GhError):
    def __init__(self, scopes):
        self.scopes = list(scopes)
        what = ("the token lacks a required scope (one of: " + ", ".join(self.scopes) + ")" if self.scopes
                else "the token lacks a scope this call needs (GitHub did not name it)")
        super().__init__("E_GH_SCOPE_MISSING", 200, what + "; run `gh auth refresh -s <scope>` as the right account")


class RateLimited(GhError):
    def __init__(self, wait):
        self.wait = wait
        super().__init__("E_GH_RATE_LIMITED", None, f"GitHub rate limit; asks for a wait of {wait} s (the cap is {MAX_WAIT} s)")


class GhMissing(GhError):
    def __init__(self):
        super().__init__("E_GH_MISSING", None, INSTALL_HINT)


class GhTimeout(GhError):
    def __init__(self, write=False):
        super().__init__("E_GH_TIMEOUT", None, f"gh did not answer within {TIMEOUT} s" + (f": {UNKNOWN_OUTCOME}" if write else ""))


class WriteNotArmed(GhError):
    def __init__(self):
        super().__init__("E_GH_WRITE_NOT_ARMED", None, "a GitHub write was attempted before the human gate armed it; nothing was sent")


# id: the numeric user id (GraphQL databaseId); scopes: list, or None when X-OAuth-Scopes is absent; typename: "User" or "Bot" (the
# Actions token is a User in `viewer` though its comments' author is a Bot, T31), None from a constructor that does not say
Viewer = collections.namedtuple("Viewer", "id login scopes typename", defaults=(None,))
VIEWER_QUERY = "query { viewer { databaseId login __typename } }"
Response = collections.namedtuple("Response", "status headers body")


def subprocess_runner(argv, stdin, timeout):
    """Run argv (stdin: bytes or None) -> (returncode, stdout, stderr). The environment is inherited unchanged.
    Raises FileNotFoundError (no gh) or subprocess.TimeoutExpired; Gh turns both into its own errors."""
    kw = {"input": stdin} if stdin is not None else {"stdin": subprocess.DEVNULL}
    done = subprocess.run(argv, capture_output=True, timeout=timeout, **kw)
    return done.returncode, done.stdout.decode("utf-8", "replace"), done.stderr.decode("utf-8", "replace")


MAX_ERRORS_SHOWN = 500  # characters of the `errors[]` summary of a 4xx reply, wrapper included
_ERROR_KEYS = ("resource", "field", "code", "message")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")


def _escape(ch):
    n = ord(ch)
    return ch if " " <= ch < "\x7f" else "\\x%02x" % n if n < 0x100 else "\\u%04x" % n if n < 0x10000 else "\\U%08x" % n  # as backslashreplace; controls too


def _escaped(value, limit=200):
    """`value` (a str) scrubbed (the WHOLE value, so no part of a token is left by the cut), then as printable ASCII (non-ASCII as \\xNN \\uNNNN
    \\UNNNNNNNN, control characters as \\xNN), cut at the last whole character that fits in `limit`: an escape is never cut in half."""
    out, size = [], 0
    for ch in scrub(value):
        piece = _escape(ch)
        if size + len(piece) > limit:
            break
        out.append(piece)
        size += len(piece)
    return "".join(out)


_HALF_ESCAPE = re.compile(r"\\(?:x[0-9a-f]?|u[0-9a-f]{0,3}|U[0-9a-f]{0,7})\Z")  # bounded and anchored: linear


def errors_summary(body):
    """` (errors: resource=R field=f code=c message=m; ...)` for a 4xx reply body with an `errors` array, else "". Strings of the four named keys of the
    first 20 entries that are objects, nothing else (a non-string, a nested object, any other key, a request body or a header is ignored); every value
    is at most 200 characters, scrubbed and ASCII-escaped; the whole summary is at most MAX_ERRORS_SHOWN characters (cut with `...`)."""
    errors = body.get("errors") if isinstance(body, dict) else None
    if not isinstance(errors, list):
        return ""
    items = []
    for e in errors[:20]:
        if isinstance(e, dict):
            parts = ["%s=%s" % (k, _escaped(e[k])) for k in _ERROR_KEYS if isinstance(e.get(k), str) and e[k]]
            if parts:
                items.append(" ".join(parts))
    if not items:
        return ""
    head, tail = " (errors: ", ")"
    text = "; ".join(items)
    room = MAX_ERRORS_SHOWN - len(head) - len(tail)
    return head + (text if len(text) <= room else _HALF_ESCAPE.sub("", text[:room - 3]) + "...") + tail


_STATUS_LINE = re.compile(r"HTTP/\S+ (\d{3})(?: |$)")


def parse_include(raw):
    """`gh api -i` output -> (status or None, headers with lower-cased names, body: parsed JSON, None if empty, else text).
    A malformed status line gives status None (the whole text is the body). Only a 1xx block may precede the final one."""
    body, status, headers = raw.replace("\r\n", "\n"), None, {}
    while True:
        first = body.split("\n", 1)[0]
        m = _STATUS_LINE.match(first)
        if not m:
            break
        head, _, body = body.partition("\n\n")
        status, headers = int(m.group(1)), {}
        for line in head.split("\n")[1:]:
            name, sep, value = line.partition(":")
            if sep:
                headers[name.strip().lower()] = value.strip()
        if status >= 200:
            break
    if not body.strip():
        return status, headers, None
    try:
        return status, headers, json.loads(body)
    except ValueError:
        return status, headers, body.strip()


_CLOSER = {"{": "}", "(": ")", "[": "]"}
_NAME_START = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ_"
_NAME = _NAME_START + "0123456789"


def is_graphql_write(query):
    """True when the document holds a `mutation` operation. Scans the whole document, not just the first token:
    comments, strings and anything inside {} () [] are skipped, and `mutation` at the top level (any case) makes it a
    write, so a leading comment, a fragment before the operation, or several operations cannot hide one. The
    `{ ... }` shorthand is a query. An unterminated string, an unbalanced or interleaved bracket (`{ a ( } mutation { b ) }`)
    cannot be read safely, so each counts as a write.
    shortcut: a top-level name `mutation` that is not the keyword (a fragment so named) is a false positive, which only
    means it needs arming; a hostile document is out of scope (queries are built by this package)."""
    i, n, stack = 0, len(query), []
    while i < n:
        c = query[i]
        if c == "#":
            while i < n and query[i] not in "\r\n":
                i += 1
        elif c == '"':
            if query.startswith('"""', i):
                j = i + 3
                while j < n and not query.startswith('"""', j):
                    j += 4 if query.startswith('\\"""', j) else 1  # \""" is an escaped delimiter, not the end
                end = j + 3
                j = j if j < n else -1
            else:
                j = i + 1
                while j < n and query[j] not in '"\r\n':
                    if query[j] == "\\":
                        if query[j + 1:j + 2] in ("\r", "\n"):
                            return True  # a line terminator inside a string is invalid GraphQL: ambiguous, fail closed
                        j += 2
                    else:
                        j += 1
                end = j + 1
                j = j if j < n and query[j] == '"' else -1
            if j < 0:
                return True
            i = end
        elif c in "{([":
            stack.append(_CLOSER[c])
            i += 1
        elif c in "})]":
            if not stack or stack.pop() != c:
                return True  # unbalanced or interleaved ("{ ( }"): ambiguous, fail closed
            i += 1
        elif c in _NAME_START:
            j = i
            while j < n and query[j] in _NAME:
                j += 1
            if not stack and query[i:j].lower() == "mutation":
                return True
            i = j
        elif not c.isascii() and c.isalnum():
            return True  # GraphQL names are ASCII: a non-ASCII letter or digit outside a string is not a document GitHub reads
        else:
            i += 1
    return bool(stack)  # unbalanced at the end: ambiguous, fail closed


_DOT_SEGMENTS = (".", "..")


def check_rest_request(method, path, body=None):
    """Refuse a REST request `Gh.rest` must never send (ValueError, before any spawn); `FakeGh.rest` calls this too, so a
    fake-based test cannot pass with a request the real transport refuses. A body on GET/HEAD is refused. The GraphQL
    endpoint is reachable only through `graphql()` (the write scanner must see the document), so `rest()` refuses every
    spelling of it: percent-encoded (decoded until stable), `;` path parameters, a query or fragment, a trailing or doubled
    slash, any case, backslashes, and any `.`/`..` segment (so `repos/o/r/../../graphql` never resolves to it). A repository
    named graphql (`repos/o/graphql/issues/1`) is fine: only a path whose single non-empty segment starts with `graphql`
    (`graphql.json`, `graphql%00`, fullwidth, ...) is refused."""
    check_path(path)
    method = method.upper() if isinstance(method, str) else method
    if method not in METHODS:
        raise ValueError(f"unsupported method {method!r}")
    if method in READ_METHODS and body is not None:
        raise ValueError("a GET/HEAD request cannot carry a body")
    decoded = path
    for _ in range(8):  # %2567 -> %67 -> g (and fullwidth %): decode and normalise until stable
        again = urllib.parse.unquote(unicodedata.normalize("NFKC", decoded))
        if again == decoded:
            break
        decoded = again
    else:
        raise ValueError("refused path (encoded too deeply)")
    raw = re.split(r"[/\\]", re.split(r"[?#]", decoded, maxsplit=1)[0])
    if any(s in _DOT_SEGMENTS for s in raw):
        raise ValueError("refused path ('.' or '..' segment)")
    # ";x", "%00", "%20", ".json", ":" or "~" after the name, or ";" before it, must not turn the endpoint into another path
    segments = [t for t in (s.lower().strip(";. " + "".join(chr(k) for k in range(32)) + "\x7f") for s in raw) if t]
    if len(segments) == 1 and segments[0].startswith("graphql"):
        raise ValueError("graphql goes through graphql(), which detects mutations; refused as a REST path")


def check_path(path):
    if not isinstance(path, str) or not path or path.startswith("-") or any(c.isspace() for c in path):
        raise ValueError("refused path (not a string, empty, starts with '-' or has whitespace)")
    if "{" in path or "}" in path or "://" in path:
        # gh fills {owner}/{repo} from the cwd's git remote (the wrong repo, silently); an absolute URL leaves api.github.com
        raise ValueError("refused path (placeholder braces or an absolute URL)")


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class Gh:
    def __init__(self, runner=subprocess_runner, sleep=time.sleep, clock=time.monotonic, wall=time.time):
        """`wall` (epoch seconds) reads the x-ratelimit-reset header; `clock` (monotonic) paces writes."""
        self._runner, self._sleep, self._clock, self._wall = runner, sleep, clock, wall
        self._armed, self._last_write = False, None

    def arm(self):
        """Allow writes (the human gate calls this after approval). Until then every write raises WriteNotArmed."""
        self._armed = True

    @property
    def armed(self):
        """True only after `arm()`; read-only (a property, not a method: a primitive that reads before it writes checks `is True`)."""
        return self._armed

    def rest(self, method, path, body=None):
        method = method.upper() if isinstance(method, str) else method
        check_rest_request(method, path, body)
        return self._send(method, path, body, method not in READ_METHODS, False).body

    def graphql(self, query, variables=None):
        if not isinstance(query, str):
            raise ValueError("a GraphQL query must be a string")
        payload = {"query": query} if variables is None else {"query": query, "variables": variables}
        write = is_graphql_write(query)
        resp = self._send("POST", "graphql", payload, write, True)
        data = resp.body.get("data")
        if data is not None and not isinstance(data, dict):
            what = "the response's data was not a JSON object"
            raise GhError("E_GH_BAD_BODY", resp.status, f"{UNKNOWN_OUTCOME}: {what}" if write else what)
        return data or {}

    def viewer(self):
        """The token's identity from the GraphQL `viewer` READ (REST `GET /user` is 403 for the Actions token, T31a); the scopes
        from that same reply's X-OAuth-Scopes header. One path, no fallback: an error stands."""
        resp = self._send("POST", "graphql", {"query": VIEWER_QUERY}, False, True)
        data = resp.body.get("data")
        who = data.get("viewer") if isinstance(data, dict) else None
        if not (isinstance(who, dict) and type(who.get("databaseId")) is int and who["databaseId"] >= 1
                and isinstance(who.get("login"), str) and isinstance(who.get("__typename"), str)):
            raise GhError("E_GH_BAD_BODY", resp.status, "GraphQL viewer did not return a databaseId, a login and a __typename")
        scopes = resp.headers.get("x-oauth-scopes")
        return Viewer(who["databaseId"], who["login"],
                      None if scopes is None else [s.strip() for s in scopes.split(",") if s.strip()], who["__typename"])

    def _send(self, method, path, payload, write, is_graphql):
        if method not in METHODS:
            raise ValueError(f"unsupported method {method!r}")
        check_path(path)
        if write and not self._armed:
            raise WriteNotArmed()
        argv = ["gh", "api", "-X", method, path, "-H", f"X-GitHub-Api-Version: {API_VERSION}", "-i"]
        stdin = None
        if payload is not None:
            argv += ["--input", "-"]
            stdin = json.dumps(payload).encode()
        attempts = 1 if write else MAX_ATTEMPTS  # a write that timed out may have been applied: never repeat one
        for attempt in range(1, attempts + 1):
            if write:
                self._pace()
            try:
                err, resp = self._once(argv, stdin, write, is_graphql)
            finally:
                if write:
                    self._last_write = self._clock()
            if err is None:
                return resp
            wait = err.wait if isinstance(err, RateLimited) else 2 ** attempt
            retryable = isinstance(err, (RateLimited, GhTimeout)) or (err.status or 0) >= 500
            if attempt == attempts or not retryable or wait > MAX_WAIT:
                raise err
            self._sleep(wait)

    def _pace(self):
        if self._last_write is not None:
            gap = WRITE_GAP - (self._clock() - self._last_write)
            if gap > 0:
                self._sleep(gap)

    def _once(self, argv, stdin, write, is_graphql):
        """One spawn -> (error or None, Response). Errors are returned so the caller decides on a retry."""
        try:
            code, out, err = self._runner(argv, stdin, TIMEOUT)
        except FileNotFoundError:
            raise GhMissing() from None
        except subprocess.TimeoutExpired:
            return GhTimeout(write), None
        except OSError as e:
            raise GhError("E_GH_SPAWN", None, str(e)) from None
        status, headers, body = parse_include(out)
        resp = Response(status, headers, body)
        if status is None:
            detail = err.strip() or f"gh exited {code} without an HTTP response"
            return GhError("E_GH_NO_RESPONSE", None, f"{UNKNOWN_OUTCOME}: {detail}" if write else detail), resp
        message = body.get("message") if isinstance(body, dict) else str(body or "")
        if status >= 400:
            bad = message is not None and not isinstance(message, str)  # a hostile server: no str() of a list into the text
            limited = self._limited(status, headers, "" if bad or message is None else message)  # headers still decide
            if limited:
                return limited, resp
            if bad:
                return GhError("E_GH_BAD_BODY", status, "the error message was not a string"), resp
            hint = " (not signed in? run `gh auth login`)" if status == 401 else ""
            return GhError("E_GH_HTTP", status, (message or "") + hint, errors_summary(body) if status < 500 else ""), resp
        if not 200 <= status < 300:  # 1xx, 3xx or 000 as the FINAL status: neither success nor a reported error
            what = f"the final HTTP status {status} is neither success nor an error"
            return GhError("E_GH_BAD_STATUS", status, f"{UNKNOWN_OUTCOME}: {what}" if write else what), resp
        if is_graphql or isinstance(body, str):
            if not isinstance(body, dict):
                what = "the response was not a JSON object"
                return GhError("E_GH_BAD_BODY", status, f"{UNKNOWN_OUTCOME}: {what}" if write else what), resp
            errors = body.get("errors")
            if errors is not None and errors != []:
                if not isinstance(errors, list):
                    what = "the response's errors was not a list"
                    return GhError("E_GH_BAD_BODY", status, f"{UNKNOWN_OUTCOME}: {what}" if write else what), resp
                return self._graphql_error(status, headers, errors), resp
        return None, resp

    def _limited(self, status, headers, message):
        """RateLimited for a 429, or a 403 carrying retry-after, remaining=0 or the limit text; else None."""
        retry_after, remaining = _int(headers.get("retry-after")), headers.get("x-ratelimit-remaining")
        if status == 429 or (status == 403 and (retry_after is not None or remaining == "0" or "rate limit" in message.lower())):
            return RateLimited(self._wait(headers))

    def _wait(self, headers):
        retry_after, reset = _int(headers.get("retry-after")), _int(headers.get("x-ratelimit-reset"))
        if retry_after is not None:
            return max(1, retry_after)  # a zero or negative header must not become sleep(-5)
        if headers.get("x-ratelimit-remaining") == "0" and reset is not None:
            return max(1, int(reset - self._wall()))
        return DEFAULT_WAIT

    def _graphql_error(self, status, headers, errors):
        errors = [e for e in errors if isinstance(e, dict)] or [{}]
        scopes = {}  # an ordered set
        for e in errors:
            if e.get("type") == "INSUFFICIENT_SCOPES":
                text = e.get("message")
                named = re.search(r"following scopes: \[([^\]\[]*)\]", text if isinstance(text, str) else "")  # [^][]: linear
                scopes.update(dict.fromkeys(re.findall(r"'([^']+)'", named.group(1) if named else "")))
        if any(e.get("type") == "INSUFFICIENT_SCOPES" for e in errors):
            return ScopeMissing(list(scopes))
        messages = [str(e.get("message", "")) for e in errors]
        if any(e.get("type") == "RATE_LIMITED" for e in errors) or any("api rate limit exceeded" in m.lower() for m in messages):
            return RateLimited(self._wait(headers))
        err = GhError("E_GH_GRAPHQL", status, "; ".join(messages))
        err.graphql_errors = [(e.get("type") if isinstance(e.get("type"), str) else None,
                               tuple(e["path"]) if isinstance(e.get("path"), list) and all(isinstance(x, str) for x in e["path"]) else None)
                              for e in errors]
        return err
