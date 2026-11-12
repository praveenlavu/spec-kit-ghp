"""Issue references: `5`, `#5`, `owner/repo#5` or an issue URL, parsed strictly.

`parse_ref` is pure (no I/O). Failures raise `RefError(code, msg)`:

- E_BAD_REF          malformed text, bad owner/repo name, number not a plain positive integer
- E_PULL_REQUEST_URL a github.com `/pull/N` URL (a pull request is not an issue)
- E_NEEDS_DEFAULT    a bare `5` / `#5` and no default repo to resolve it against
- E_FOREIGN_REPO     the ref names a repo other than `default`

Deliberately refused (E_BAD_REF): copied comment links (`/issues/5#issuecomment-1`), trailing
slashes, queries, fragments, extra path segments, surrounding whitespace. Issue numbers are
1..2**31-1 (GitHub's GraphQL Int); longer digit strings are refused before any int() call.
"""
import re
from dataclasses import dataclass
from typing import Optional

# GitHub owners: alphanumeric with single (never consecutive) hyphens inside, max 39.
# Repos: alphanumeric, - _ ., max 100.
_OWNER = r"[A-Za-z0-9](?:-?[A-Za-z0-9]){0,38}"
_REPO = r"[A-Za-z0-9._-]{1,100}"
_MAX = 2 ** 31 - 1
_NUM = r"[1-9][0-9]{0,9}"  # no zero, sign, leading zero, decimal; ASCII digits only, bounded

_BARE = re.compile(r"#?(%s)" % _NUM)
_SHORT = re.compile(r"(%s)/(%s)#(%s)" % (_OWNER, _REPO, _NUM))
_URL = re.compile(r"https://github\.com/(%s)/(%s)/(issues|pull)/([0-9]+)" % (_OWNER, _REPO))


class RefError(Exception):
    def __init__(self, code: str, msg: str):
        super().__init__("%s: %s" % (code, msg))
        self.code = code
        self.msg = msg


@dataclass(frozen=True)
class Ref:
    owner: str
    repo: str
    number: int

    def __str__(self) -> str:
        return "%s/%s#%d" % (self.owner, self.repo, self.number)


def _name_ok(repo: str) -> bool:
    return repo not in (".", "..")


def _number(digits: str, text: str) -> int:
    if not re.fullmatch(_NUM, digits) or int(digits) > _MAX:  # int() only on <= 10 digits
        raise RefError("E_BAD_REF", "%r has an invalid issue number (1..%d)" % (text, _MAX))
    return int(digits)


def parse_ref(text: str, default: Optional[Ref] = None) -> Ref:
    m = _BARE.fullmatch(text)
    if m:
        if default is None:
            raise RefError("E_NEEDS_DEFAULT", "%r needs a default repository" % text)
        return Ref(default.owner, default.repo, _number(m.group(1), text))

    m = _SHORT.fullmatch(text)
    if m:
        owner, repo, number = m.group(1), m.group(2), _number(m.group(3), text)
    else:
        m = _URL.fullmatch(text)
        if not m:
            raise RefError("E_BAD_REF", "%r is not an issue reference "
                           "(use 5, #5, owner/repo#5 or an issue URL)" % text)
        owner, repo, kind, digits = m.groups()
        if kind == "pull":
            raise RefError("E_PULL_REQUEST_URL", "%r is a pull request, not an issue" % text)
        number = _number(digits, text)

    if not _name_ok(repo):
        raise RefError("E_BAD_REF", "%r has an invalid repository name" % text)
    if default is not None:
        if (owner.lower(), repo.lower()) != (default.owner.lower(), default.repo.lower()):
            raise RefError("E_FOREIGN_REPO", "%s/%s is not %s/%s" %
                           (owner, repo, default.owner, default.repo))
        return Ref(default.owner, default.repo, number)
    return Ref(owner, repo, number)
