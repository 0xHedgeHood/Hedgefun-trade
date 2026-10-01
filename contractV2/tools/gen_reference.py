#!/usr/bin/env python3
"""Generate docs/REFERENCE.md from the compiled ABI and the Solidity source.

    python3 tools/gen_reference.py            # rewrite docs/REFERENCE.md
    python3 tools/gen_reference.py --stdout   # print it instead
    python3 tools/gen_reference.py --gaps     # print only the members that have no NatSpec

Two sources, because neither is enough alone:

  * Foundry (`forge build --sizes`, `forge inspect <C> abi|methodIdentifiers|errors|events`) is the authority on
    what is callable, every selector, and the bytecode size.
  * The source is the only place that carries the `///` NatSpec, `constant` values, struct fields and their
    comments, enums, modifiers, and the `msg.sender` checks that decide who may call what.

The output is deterministic: fixed contract order, members sorted by name, and no timestamp. The only line that
varies between two runs on the same tree is the commit hash, which tools/check_docs.py ignores when it diffs.

Standard library only.
"""
import json
import glob
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "REFERENCE.md")
COMMIT_MARKER = "Generated from commit:"
EIP170 = 24576

# (source file, contract) in the order they are documented
CONTRACTS = [
    ("src/HedgeFunFactory.sol", "HedgeFunFactory"),
    ("src/HedgeFunDeployers.sol", "BoundDeployer"),
    ("src/HedgeFunDeployers.sol", "TreasuryDeployer"),
    ("src/HedgeFunDeployers.sol", "TokenDeployer"),
    ("src/hooks/HedgeFunHook.sol", "HedgeFunHook"),
    ("src/hooks/HedgeFunV2Hook.sol", "HedgeFunV2Hook"),
    ("src/HedgeFunTreasuryBase.sol", "HedgeFunTreasuryBase"),
    ("src/HedgeFunTreasury.sol", "HedgeFunTreasury"),
    ("src/PoolTrader.sol", "PoolTrader"),
    ("src/PriceOracle.sol", "PriceOracle"),
    ("src/TradingCalendar.sol", "TradingCalendar"),
    ("src/libraries/TwapRing.sol", "TwapRing"),
    ("src/HedgeFunToken.sol", "HedgeFunToken"),
    ("src/HedgeFunLaunchRouter.sol", "HedgeFunLaunchRouter"),
    ("src/HedgeFunTradeRouter.sol", "HedgeFunTradeRouter"),
    ("src/v2/HedgeFunV2Factory.sol", "HedgeFunV2Factory"),
    ("src/v2/HedgeFunBondingCurve.sol", "HedgeFunBondingCurve"),
    ("src/v2/CurveDeployer.sol", "CurveDeployer"),
    ("src/v2/V2LiquidityVault.sol", "V2LiquidityVault"),
    ("src/v2/HedgeFunV2Treasury.sol", "HedgeFunV2Treasury"),
    ("src/v2/HedgeFunV2EngineTreasury.sol", "HedgeFunV2EngineTreasury"),
    ("src/v2/strategy/V2RebalancePolicy.sol", "V2RebalancePolicy"),
    ("src/v2/V2TreasuryDeployer.sol", "V2TreasuryDeployer"),
    ("src/v2/HedgeFunV2TradeRouter.sol", "HedgeFunV2TradeRouter"),
    ("src/v2/HedgeFunV2NativeRouter.sol", "HedgeFunV2NativeRouter"),
]

HOOKS_LIB = "lib/v4-core/src/libraries/Hooks.sol"


# ------------------------------------------------------------------------------------------------ source scanning
def mask(src):
    """Return (masked, comments). `masked` is `src` with every comment and string body blanked to spaces (same
    length, newlines kept), so brace matching and regexes cannot be fooled by either. `comments` is a list of
    (start, end, kind, text) with kind in {'doc', 'line', 'block'}."""
    out, comments, i, n = list(src), [], 0, len(src)
    while i < n:
        c = src[i]
        if src.startswith("//", i):
            j = src.find("\n", i)
            j = n if j < 0 else j
            raw = src[i:j]
            if raw.startswith("///") and not raw.startswith("////"):
                comments.append((i, j, "doc", raw[3:]))
            else:
                comments.append((i, j, "line", raw[2:].strip()))
            for k in range(i, j):
                out[k] = " "
            i = j
        elif src.startswith("/*", i):
            j = src.find("*/", i + 2)
            j = n if j < 0 else j + 2
            raw = src[i:j]
            if raw.startswith("/**") and not raw.startswith("/**/"):
                body = "\n".join(re.sub(r"^\s*\*? ?", "", ln) for ln in raw[3:-2].split("\n"))
                comments.append((i, j, "doc", body))
            else:
                comments.append((i, j, "block", raw[2:-2].strip()))
            for k in range(i, j):
                if out[k] != "\n":
                    out[k] = " "
            i = j
        elif c in "\"'":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            for k in range(i + 1, min(j, n)):
                out[k] = " "
            i = j + 1
        else:
            i += 1
    return "".join(out), comments


def match_close(s, i, open_="{", close="}"):
    """index of the bracket closing the one at s[i]"""
    depth = 0
    for j in range(i, len(s)):
        if s[j] == open_:
            depth += 1
        elif s[j] == close:
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced %s at %d" % (open_, i))


def split_top(s, sep=","):
    """split on `sep` outside any bracket"""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


def squash(s):
    return re.sub(r"\s+", " ", s).strip()


def items_in(masked, comments, start, end):
    """Split the region (start, end) of a contract or struct body into declarations. Yields dicts with the
    declaration's text span, its `///` doc (the run of doc comments directly before it, with no plain comment in
    between) and its trailing `//` comment (one that starts on the line the declaration ends on)."""
    res, i, prev_end = [], start, start
    while True:
        while i < end and masked[i].isspace():
            i += 1
        if i >= end:
            break
        j, paren, body = i, 0, None
        while j < end:
            ch = masked[j]
            if ch in "([":
                paren += 1
            elif ch in ")]":
                paren -= 1
            elif ch == "{" and paren == 0:
                close = match_close(masked, j)
                # `Foo({a: 1})` inside an initializer is an expression, not a body; none in this repo start at
                # paren depth 0, so a brace here is always a body
                body = (j + 1, close)
                j = close
                break
            elif ch == ";" and paren == 0:
                break
            j += 1
        item_end = j + 1
        gap = [c for c in comments if prev_end <= c[0] < i]
        doc = []
        for c in gap:
            if c[2] == "doc":
                doc.append(c[3])
            else:
                doc = []
        # a plain `//` block on the lines directly above, used only when there is nothing better. Section rulers
        # (`// ------ views`) and a previous declaration's trailing comment are not descriptions of this one.
        lead = []
        for c in gap:
            own_line = not masked[masked.rfind("\n", 0, c[0]) + 1:c[0]].strip()
            if c[2] == "line" and own_line and not re.match(r"[-=]{4,}", c[3]):
                lead.append(c[3])
            else:
                lead = []
        if gap and masked[gap[-1][1]:i].count("\n") > 1:
            lead = []                                   # a blank line in between: not attached
        res.append({"start": i, "end": item_end, "body": body, "doc": "\n".join(doc), "trailing": " ".join(lead)})
        prev_end = item_end
        i = item_end
    # trailing comments: a plain `//` comment that begins on the same line the item ends on
    for k, it in enumerate(res):
        nxt = res[k + 1]["start"] if k + 1 < len(res) else end
        for c in comments:
            if it["end"] <= c[0] < nxt and c[2] == "line":
                if "\n" not in masked[it["end"]:c[0]]:
                    it["trailing"] = c[3]
                break
    return res


MODS_STATE = {"public", "private", "internal", "constant", "immutable", "override", "transient"}


def parse_var(text):
    """`type [modifiers] name [= value]` -> dict, or None"""
    m = re.search(r"(?<![=!<>])=(?![=>])", text)
    left, value = (text[:m.start()], text[m.end():].strip()) if m else (text, None)
    toks, depth, cur = [], 0, []
    for ch in left.strip():
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth -= 1
        if ch.isspace() and depth == 0:
            if cur:
                toks.append("".join(cur))
                cur = []
        else:
            cur.append(ch)
    if cur:
        toks.append("".join(cur))
    if len(toks) < 2:
        return None
    name = toks[-1]
    mods = [t for t in toks[:-1] if t in MODS_STATE]
    typ = " ".join(t for t in toks[:-1] if t not in MODS_STATE)
    vis = next((m_ for m_ in mods if m_ in ("public", "private", "internal")), "internal")
    return {"name": name, "type": typ, "vis": vis, "constant": "constant" in mods, "immutable": "immutable" in mods,
            "value": squash(value) if value is not None else None}


def parse_function(header, body_text):
    m = re.match(r"(function\s+(\w+)|constructor|receive|fallback)\s*\(", header)
    name = m.group(2) or m.group(1)
    p_open = m.end() - 1
    p_close = match_close(header, p_open, "(", ")")
    params = squash(header[p_open + 1:p_close])
    rest = header[p_close + 1:]
    returns = ""
    rm = re.search(r"\breturns\s*\(", rest)
    if rm:
        r_close = match_close(rest, rm.end() - 1, "(", ")")
        returns = squash(rest[rm.end():r_close])
        rest = rest[:rm.start()] + rest[r_close + 1:]
    vis, mut, mods, virtual, override = "public" if name == "constructor" else "", "", [], False, False
    for mm in re.finditer(r"(\w+)\s*(\([^)]*\))?", rest):
        w = mm.group(1)
        if w in ("external", "public", "internal", "private"):
            vis = w
        elif w in ("pure", "view", "payable"):
            mut = w
        elif w == "virtual":
            virtual = True
        elif w == "override":
            override = True
        else:
            mods.append(w)          # a modifier, or a base-constructor call on a constructor
    return {"name": name, "params": params, "returns": returns, "vis": vis, "mut": mut, "mods": mods,
            "virtual": virtual, "override": override, "body": body_text,
            "nparams": len(split_top(params))}


def revert_conditions(body):
    """[(condition, error)] for every `if (cond) revert Err(...)` and `require(cond, ...)` in a function body"""
    out = []
    if not body:
        return out
    for m in re.finditer(r"\bif\s*\(", body):
        close = match_close(body, m.end() - 1, "(", ")")
        r = re.match(r"\s*\{?\s*revert\s+(\w+)\s*\(", body[close + 1:])
        if r:
            out.append((squash(body[m.end():close]), r.group(1)))
    for m in re.finditer(r"\brequire\s*\(", body):
        close = match_close(body, m.end() - 1, "(", ")")
        parts = split_top(body[m.end():close])
        out.append(("!(%s)" % squash(parts[0]), "require"))
    return out


def parse_file(path):
    with open(os.path.join(ROOT, path)) as f:
        src = f.read()
    masked, comments = mask(src)
    imports = {}
    for m in re.finditer(r"import\s*\{([^}]*)\}\s*from\s*\"([^\"]+)\"", src):
        for nm in m.group(1).split(","):
            nm = nm.strip().split(" as ")[-1].strip()
            if nm:
                imports[nm] = m.group(2)
    decls, pos, prev_end = [], 0, 0
    pat = re.compile(r"\b(abstract\s+)?(contract|interface|library)\s+(\w+)([^{;]*)\{")
    while True:
        m = pat.search(masked, pos)
        if not m:
            break
        open_i = m.end() - 1
        close_i = match_close(masked, open_i)
        bases = []
        bm = re.match(r"\s*is\s+(.*)", m.group(4), re.S)
        if bm:
            bases = [re.match(r"[\w.]+", b).group(0) for b in split_top(bm.group(1))]
        gap = [c for c in comments if prev_end <= c[0] < m.start()]
        doc = []
        for c in gap:
            if c[2] == "doc":
                doc.append(c[3])
            else:
                doc = []
        d = {"file": path, "kind": m.group(2), "abstract": bool(m.group(1)), "name": m.group(3), "bases": bases,
             "doc": "\n".join(doc), "doc_misplaced": False, "imports": imports, "line": src.count("\n", 0, m.start()) + 1,
             "functions": [], "events": {}, "errors": {}, "vars": [], "structs": [], "enums": [], "raw": masked[open_i:close_i]}
        for it in items_in(masked, comments, open_i + 1, close_i):
            head_end = it["body"][0] - 1 if it["body"] else it["end"] - 1
            header = squash(masked[it["start"]:head_end])
            body_text = masked[it["body"][0]:it["body"][1]] if it["body"] else None
            base = {"doc": it["doc"], "trailing": it["trailing"]}
            if re.match(r"(function\b|constructor\b|receive\b|fallback\b)", header):
                fn = parse_function(header, body_text)
                fn.update(base)
                d["functions"].append(fn)
            elif header.startswith("event "):
                nm = re.match(r"event\s+(\w+)", header).group(1)
                d["events"][nm] = base
            elif header.startswith("error "):
                nm = re.match(r"error\s+(\w+)", header).group(1)
                d["errors"][nm] = base
            elif header.startswith("struct "):
                nm = re.match(r"struct\s+(\w+)", header).group(1)
                fields = []
                for f_ in items_in(masked, comments, it["body"][0], it["body"][1]):
                    v = parse_var(squash(masked[f_["start"]:f_["end"] - 1]))
                    if v:
                        v.update({"doc": f_["doc"], "trailing": f_["trailing"]})
                        fields.append(v)
                # a trailing comment after a one-line struct belongs to the struct, not its last field
                d["structs"].append(dict(base, name=nm, fields=fields))
            elif header.startswith("enum "):
                nm = re.match(r"enum\s+(\w+)", header).group(1)
                members = split_top(masked[it["body"][0]:it["body"][1]])
                d["enums"].append(dict(base, name=nm, members=members))
            elif header.startswith(("using ", "modifier ")):
                continue
            else:
                v = parse_var(header)
                if v:
                    v.update(base)
                    d["vars"].append(v)
        decls.append(d)
        prev_end = close_i + 1
        pos = close_i + 1
    # A contract whose header comment is separated from it by one-line interface declarations: Solidity attaches
    # that comment to the first interface. Recover it for the contract, and say so.
    for k, d in enumerate(decls):
        if d["kind"] != "interface" and not d["doc"]:
            j = k - 1
            while j >= 0 and decls[j]["kind"] == "interface":
                if decls[j]["doc"]:
                    d["doc"], d["doc_misplaced"] = decls[j]["doc"], decls[j]["name"]
                    break
                j -= 1
    return decls


# ------------------------------------------------------------------------------------------------ NatSpec
def parse_natspec(doc):
    """-> {'notice': str, 'dev': str, 'params': [(name, text)], 'returns': [text], 'inheritdoc': str}; text keeps
    its line structure for to_markdown()"""
    res = {"notice": "", "dev": "", "params": [], "returns": [], "inheritdoc": ""}
    if not doc.strip():
        return res
    cur_tag, cur = "notice", []
    chunks = []
    for ln in doc.split("\n"):
        m = re.match(r"\s*@(\w+)\s?(.*)", ln)
        if m:
            chunks.append((cur_tag, cur))
            cur_tag, cur = m.group(1), [m.group(2)]
        else:
            cur.append(ln[1:] if ln.startswith(" ") else ln)
    chunks.append((cur_tag, cur))
    for tag, lines in chunks:
        # continuation lines hang under the tag (`/// @dev foo` / `///      bar`): remove that common indent, so
        # that only a line indented FURTHER than its neighbours reads as a formula in to_markdown()
        cont = [l for l in lines[1:] if l.strip()]
        hang = min((len(l) - len(l.lstrip(" ")) for l in cont), default=0)
        lines = lines[:1] + [l[hang:] if l.strip() else "" for l in lines[1:]]
        if tag == "notice" and lines and lines[0].strip():
            first = len(lines[0]) - len(lines[0].lstrip(" "))
            lines[0] = lines[0][min(first, hang):]
        text = "\n".join(lines).strip("\n")
        if not text.strip():
            continue
        if tag in ("notice", "dev"):
            res[tag] = (res[tag] + "\n\n" + text) if res[tag] else text
        elif tag == "param":
            nm, _, rest = text.partition(" ")
            res["params"].append((nm, rest))
        elif tag == "return":
            res["returns"].append(text)
        elif tag == "inheritdoc":
            res["inheritdoc"] = text.strip()
    return res


def esc(text, cell=False):
    """escape what Markdown would otherwise eat, outside `code spans`: <tags>, ~strike~, $math$, and | in tables"""
    parts = re.split(r"(`[^`]*`)", text)
    for k in range(0, len(parts), 2):
        p = parts[k].replace("<", "\\<").replace("~", "\\~").replace("$", "\\$")
        if cell:
            p = p.replace("|", "\\|")
        parts[k] = p
    if cell:
        for k in range(1, len(parts), 2):
            parts[k] = parts[k].replace("|", "\\|")
    return "".join(parts)


def flat(text):
    return squash(text)


def first_sentence(text):
    t = flat(text.split("\n\n")[0]) if text else ""
    m = re.search(r"(?<=[.!?])\s+(?=[A-Z`(])", t)
    return t[:m.start()] if m else t


def to_markdown(text, indent=""):
    """NatSpec prose -> Markdown blocks: paragraphs, `- ` bullets, and an indented line on its own as code"""
    blocks, para, bullet = [], [], None

    def flush():
        nonlocal para, bullet
        if bullet is not None:
            blocks.append(("bullet", flat(" ".join(bullet))))
            bullet = None
        if para:
            if all(l.startswith("    ") for l in para):
                blocks.append(("code", flat(" ".join(para))))
            else:
                blocks.append(("para", flat(" ".join(para))))
            para = []

    for ln in text.split("\n"):
        s = ln.strip()
        if not s:
            flush()
        elif s.startswith("- "):
            flush()
            bullet = [s[2:]]
        elif bullet is not None:
            bullet.append(s)
        else:
            para.append(ln)
    flush()
    out = []
    for kind, t in blocks:
        if kind == "bullet":
            out.append(indent + "- " + esc(t))
        elif kind == "code":
            out.append(indent + "`" + t + "`")
        else:
            out.append(indent + esc(t))
    # bullets that follow each other stay one list; everything else is separated by a blank line
    res = []
    for k, line in enumerate(out):
        if k and not (blocks[k][0] == "bullet" and blocks[k - 1][0] == "bullet"):
            res.append("")
        res.append(line)
    return "\n".join(res)


# ------------------------------------------------------------------------------------------------ Foundry
def run(cmd, check=True):
    p = subprocess.run(cmd, cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    if check and p.returncode != 0:
        sys.exit("gen_reference: `%s` failed:\n%s" % (" ".join(cmd), p.stderr.strip() or p.stdout.strip()))
    return p.stdout


def inspect(target, field):
    out = run(["forge", "inspect", target, field, "--json"])
    try:
        return json.loads(out) if out.strip() else {}
    except ValueError:
        sys.exit("gen_reference: `forge inspect %s %s --json` did not print JSON:\n%s" % (target, field, out[:400]))


def build_sizes():
    # exits non-zero when ANY contract is over the limit (a test harness may be); the JSON is still complete
    out = run(["forge", "build", "--sizes", "--json"], check=False)
    start = out.find("{")
    if start < 0:
        sys.exit("gen_reference: `forge build --sizes --json` produced no JSON; run `forge build` to see why")
    # Foundry 1.5 may append a non-JSON warning after the size object (for example, when its optional signature
    # cache cannot be flushed). Decode exactly the first JSON value while still rejecting a missing/malformed one.
    try:
        sizes, _ = json.JSONDecoder().raw_decode(out[start:])
    except ValueError:
        sys.exit("gen_reference: `forge build --sizes --json` printed malformed JSON:\n%s" % out[start:start + 400])
    # Foundry omits contracts it classifies as tests, even production contracts with an
    # `invariant()` getter. Recover their compiled sizes rather than silently losing the gate.
    for path, name in CONTRACTS:
        if name in sizes or "%s (%s)" % (name, path) in sizes:
            continue
        target = "%s:%s" % (path, name)
        runtime = run(["forge", "inspect", target, "deployedBytecode"]).strip()
        creation = run(["forge", "inspect", target, "bytecode"]).strip()
        if runtime == "0x":  # abstract contracts have no runtime of their own
            continue
        if not re.fullmatch(r"0x[0-9a-fA-F]*", runtime) or not re.fullmatch(r"0x[0-9a-fA-F]*", creation):
            sys.exit("gen_reference: unlinked or malformed bytecode for %s" % target)
        rs, cs = (len(runtime) - 2) // 2, (len(creation) - 2) // 2
        if rs > EIP170 or cs > 49152:
            sys.exit("gen_reference: %s exceeds an EVM bytecode size limit" % target)
        sizes[name] = {"runtime_size": rs, "runtime_margin": EIP170 - rs, "init_size": cs,
                       "init_margin": 49152 - cs}
    return sizes


# ------------------------------------------------------------------------------------------------ ABI helpers
def canonical(t):
    if t["type"].startswith("tuple"):
        return "(" + ",".join(canonical(c) for c in t.get("components", [])) + ")" + t["type"][5:]
    return t["type"]


def nice_type(t):
    it = t.get("internalType") or t["type"]
    return re.sub(r"^(struct|contract|enum) ", "", it)


def sig_params(items, indexed=False):
    out = []
    for t in items:
        s = nice_type(t)
        if indexed and t.get("indexed"):
            s += " indexed"
        if t.get("name"):
            s += " " + t["name"]
        out.append(s)
    return ", ".join(out)


def abi_canonical(e):
    return "%s(%s)" % (e["name"], ",".join(canonical(i) for i in e["inputs"]))


# ------------------------------------------------------------------------------------------------ model
class World:
    def __init__(self):
        self.decls = {}          # name -> decl, for everything declared under src/
        self.by_file = {}
        interface_files = sorted(os.path.relpath(f, ROOT) for f in glob.glob(os.path.join(ROOT, "src/interfaces/*.sol")))
        for path in sorted({c[0] for c in CONTRACTS} | set(interface_files)):
            ds = parse_file(path)
            self.by_file[path] = ds
            for d in ds:
                self.decls[d["name"]] = d
        self.sizes = build_sizes()
        jobs = []
        for path, name in CONTRACTS:
            for field in ("abi", "methodIdentifiers", "errors", "events"):
                jobs.append(("%s:%s" % (path, name), field))
        for path in self.by_file:
            for d in self.by_file[path]:
                if d["kind"] == "interface":
                    jobs.append(("%s:%s" % (path, d["name"]), "methodIdentifiers"))
        # bases that live under lib/: their member sets, so an inherited member can be attributed
        self.lib_bases = {}
        for path, name in CONTRACTS:
            d = self.decls[name]
            for b in d["bases"]:
                if b not in self.decls and b in d["imports"]:
                    self.lib_bases[b] = b        # by bare name: `forge inspect` does not resolve a path under lib/
        for b in sorted(self.lib_bases):
            for field in ("methodIdentifiers", "errors", "events"):
                jobs.append((self.lib_bases[b], field))
        jobs = sorted(set(jobs))
        # one at a time: every `forge` process rewrites the compiler cache on exit, and parallel ones race on it
        # (seen as "foundry_compilers::cache: EOF while parsing a value" logged into stdout ahead of the JSON)
        results = [inspect(*j) for j in jobs]
        self.insp = dict(zip(jobs, results))

    def linearize(self, name, seen=None):
        """the contract, then its bases most-derived first; only those declared under src/"""
        seen = [] if seen is None else seen
        if name in self.decls and name not in seen:
            seen.append(name)
            for b in reversed(self.decls[name]["bases"]):
                self.linearize(b, seen)
        return seen

    def all_bases(self, name, acc=None):
        acc = [] if acc is None else acc
        for b in self.decls.get(name, {"bases": []})["bases"]:
            if b not in acc:
                acc.append(b)
                self.all_bases(b, acc)
        return acc

    def lib_owner(self, contract, field, key):
        for b in self.all_bases(contract):
            if b in self.lib_bases and key in self.insp.get((self.lib_bases[b], field), {}):
                return b
        return None

    def find_fn(self, contract, name, nparams):
        """[(decl name, fn)] for every definition of the function along the linearization, most derived first"""
        hits = []
        for cn in self.linearize(contract):
            for fn in self.decls[cn]["functions"]:
                if fn["name"] == name and fn["nparams"] == nparams and fn["vis"] in ("external", "public"):
                    hits.append((cn, fn))
        return hits

    def find_var(self, contract, name):
        for cn in self.linearize(contract):
            for v in self.decls[cn]["vars"]:
                if v["name"] == name and v["vis"] == "public":
                    return cn, v
        return None, None

    def find_helper(self, contract, name):
        for cn in self.linearize(contract):
            for fn in self.decls[cn]["functions"]:
                if fn["name"] == name:
                    return fn
        return None


def access(world, contract, fn):
    """who may call: from the modifiers, the `msg.sender` checks in the body, and one level of `_helper()` calls"""
    notes, restricted = [], False
    if fn["body"] is None:
        return "declared here without a body: see the inheriting contract", False
    if "onlyOwner" in fn["mods"]:
        notes.append("owner only (`onlyOwner`)")
        restricted = True
    conds = [(c, e) for c, e in revert_conditions(fn["body"]) if "msg.sender" in c]
    for m in re.finditer(r"(?<![\w.])(_\w+)\s*\(", fn["body"] or ""):
        h = world.find_helper(contract, m.group(1))
        if h and h is not fn:
            for c, e in revert_conditions(h["body"]):
                if "msg.sender" in c or h["name"].startswith("_only"):
                    if (c, e) not in conds:
                        conds.append((c, e))
    for c, e in conds:
        restricted = True
        notes.append("reverts `%s` if `%s`" % (e, c) if e != "require" else "requires `%s`" % c[2:-1])
    body = squash(fn["body"] or "")
    m = re.fullmatch(r"revert (\w+)\(\);", body)
    if m:
        return "nobody: always reverts `%s`" % m.group(1), True
    text = "; ".join(notes) if restricted else "anyone"
    others = [x for x in fn["mods"] if x != "onlyOwner"]
    if others:
        text += " · " + ", ".join("`%s`" % x for x in others)
    return text, restricted


def describe(doc, trailing, inherited_from=None):
    """-> (table cell, has_natspec, has_more)"""
    ns = parse_natspec(doc)
    if ns["notice"]:
        cell = first_sentence(ns["notice"])
    elif ns["dev"]:
        cell = "*dev:* " + first_sentence(ns["dev"])
    elif ns["returns"]:
        cell = "*returns:* " + first_sentence(ns["returns"][0])
    elif trailing:
        cell = trailing
    else:
        cell = "—"
    has = bool(doc.strip())
    full_len = len(flat(ns["notice"])) + len(flat(ns["dev"])) + sum(len(p[1]) for p in ns["params"]) + sum(len(r) for r in ns["returns"])
    more = has and full_len > len(re.sub(r"^\*\w+:\* ", "", cell)) + 1
    if inherited_from and has:
        cell += " *(NatSpec from `%s`)*" % inherited_from
    return esc(cell, cell=True), has, more


def full_natspec(label, doc):
    ns = parse_natspec(doc)
    lines = ["- **%s**" % label, ""]
    if ns["notice"]:
        lines += [to_markdown(ns["notice"], "  "), ""]
    if ns["dev"]:
        md = to_markdown(ns["dev"], "  ")
        lines += ["  *Dev:* " + md.lstrip(), ""]
    for nm, t in ns["params"]:
        lines += ["  *Param* `%s`: %s" % (nm, esc(flat(t))), ""]
    for t in ns["returns"]:
        lines += ["  *Returns:* " + esc(flat(t)), ""]
    return lines


UNITS = {"seconds": 1, "minutes": 60, "hours": 3600, "days": 86400, "weeks": 604800, "wei": 1, "gwei": 10**9, "ether": 10**18}


def evaluate(expr):
    """the integer a simple constant expression denotes, or None"""
    e = expr
    for u, v in UNITS.items():
        e = re.sub(r"\b(\d[\d_]*)\s+%s\b" % u, lambda m: "(%s*%d)" % (m.group(1), v), e)
    e = re.sub(r"\b(\d+)e(\d+)\b", r"(\1*10**\2)", e).replace("_", "")
    if not re.fullmatch(r"[\d\s+\-*/()<]+", e) or re.fullmatch(r"\s*\d+\s*", e):
        return None
    try:
        return int(eval(e.replace("/", "//"), {"__builtins__": {}}, {}))
    except Exception:
        return None


def hook_flags():
    flags = {}
    with open(os.path.join(ROOT, HOOKS_LIB)) as f:
        for m in re.finditer(r"uint160 internal constant (\w+)_FLAG = 1 << (\d+);", f.read()):
            flags[int(m.group(2))] = m.group(1)
    return flags


# ------------------------------------------------------------------------------------------------ rendering
def table(headers, rows):
    out = ["| " + " | ".join(headers) + " |", "|" + "|".join(["---"] * len(headers)) + "|"]
    out += ["| " + " | ".join(r) + " |" for r in rows]
    return out + [""]


def anchor(text):
    return re.sub(r"\s", "-", re.sub(r"[^\w\s-]", "", text.lower()))


def render_contract(world, path, name, gaps):
    d = world.decls[name]
    target = "%s:%s" % (path, name)
    abi = world.insp[(target, "abi")]
    ids = world.insp[(target, "methodIdentifiers")]
    errs = world.insp[(target, "errors")]
    evs = world.insp[(target, "events")]
    L, details, tuple_sigs = [], [], []
    gap = {"functions": [], "events": [], "errors": []}

    kind = ("abstract contract" if d["abstract"] else d["kind"])
    L += ["## %s" % name, ""]
    L += ["`%s` in [`%s`](../%s), line %d." % (kind, path, path, d["line"]), ""]
    ns = parse_natspec(d["doc"])
    purpose = (ns["notice"] or ns["dev"]).split("\n\n")[0]
    if purpose.strip():
        L += [esc(flat(purpose)), ""]
        if len(flat(ns["notice"] + ns["dev"])) > len(flat(purpose)) + 1:
            L += ["The header comment in the source continues with the full rationale; the paragraph above is its first.", ""]
        if d["doc_misplaced"]:
            L += ["Note: in the source this comment sits above `interface %s`, which is declared between it and the "
                  "contract, so the compiler's own NatSpec output attaches it to that interface." % d["doc_misplaced"], ""]
    else:
        L += ["—", ""]

    # size
    size = world.sizes.get(name) or world.sizes.get("%s (%s)" % (name, path))
    if d["abstract"]:
        L += ["- **Bytecode:** none of its own: abstract, deployed only as part of the contracts that inherit it."]
    elif size:
        L += ["- **Runtime bytecode:** {:,} bytes; {:,} under the EIP-170 limit of {:,}. Init code: {:,} bytes.".format(
            size["runtime_size"], size["runtime_margin"], EIP170, size["init_size"])]
        if d["kind"] == "library":
            L += ["- Every function is `internal`, so the library is inlined into its callers and never deployed."]
    L += ["- **Inherits:** " + (", ".join("`%s`" % b for b in world.all_bases(name)) or "nothing")]
    hm = re.search(r"&\s*(0x[0-9a-fA-F]+)\s*!=\s*(0x[0-9a-fA-F]+)", d["raw"])
    if hm and "IHooks" in d["bases"]:
        bits, flags = int(hm.group(2), 16), hook_flags()
        names = ["`%s` (bit %d)" % (flags.get(b, "?"), b) for b in sorted(flags, reverse=True) if bits >> b & 1]
        L += ["- **Hook permission bits:** the constructor reverts unless `address(this) & %s == %s`, which is %s. "
              "Flag names are read from `%s`." % (hm.group(1), hm.group(2), " + ".join(names), HOOKS_LIB)]
    L += [""]

    # functions
    writes, views = [], []
    for e in sorted((x for x in abi if x["type"] == "function"), key=abi_canonical):
        canon = abi_canonical(e)
        sel = "`0x%s`" % ids[canon]
        sig = "%s(%s)" % (e["name"], sig_params(e["inputs"]))
        if e["stateMutability"] == "payable":
            sig += " payable"
        if e["outputs"]:
            sig += " → (%s)" % sig_params(e["outputs"])
        if "(" in canon[len(e["name"]) + 1:]:
            tuple_sigs.append((e["name"], canon, ids[canon]))
        hits = world.find_fn(name, e["name"], len(e["inputs"]))
        vcn, var = (None, None) if hits else world.find_var(name, e["name"])
        who, kindnote, doc, trailing, doc_from, owner = "anyone", "", "", "", None, None
        if hits:
            owner, fn = hits[0]
            who, restricted = access(world, name, fn)
            doc, trailing = fn["doc"], fn["trailing"]
            nsf = parse_natspec(doc)
            if (not doc.strip() or (nsf["inheritdoc"] and not nsf["notice"])) and len(hits) > 1:
                for cn, base_fn in hits[1:]:
                    if base_fn["doc"].strip():
                        doc_extra = doc if nsf["dev"] else ""
                        doc, doc_from = base_fn["doc"] + ("\n" + doc_extra if doc_extra else ""), cn
                        break
            where = "this contract" if owner == name else "`%s`" % owner
            if len(hits) > 1:
                where += " (overrides `%s`)" % hits[1][0]
        elif var:
            owner = vcn
            doc, trailing = var["doc"], var["trailing"]
            kindnote = "constant" if var["constant"] else ("immutable" if var["immutable"] else "storage")
            where = "this contract" if owner == name else "`%s`" % owner
            restricted = False
        else:
            lib = world.lib_owner(name, "methodIdentifiers", canon)
            where = "inherited via `%s`" % lib if lib else "inherited"
            restricted = False
            if lib == "Ownable2Step" and e["name"] in ("transferOwnership", "renounceOwnership"):
                who = "owner only (`onlyOwner`)"
            elif lib == "Ownable2Step" and e["name"] == "acceptOwnership":
                who = "the pending owner"
        cell, has, more = describe(doc, trailing, doc_from)
        if owner == name and not has:
            gap["functions"].append(e["name"] + ("" if hits else " (getter)"))
        if more and owner == name and not doc_from:
            details.append(("`%s`" % sig.split(" → ")[0], doc))
        if e["stateMutability"] in ("view", "pure"):
            if hits and (restricted or who.startswith("nobody")):
                cell = "**%s.** %s" % (who[0].upper() + who[1:], "" if cell == "—" else cell)
            kindcol = kindnote or e["stateMutability"]
            views.append(["`%s`" % sig, sel, kindcol, where, cell.strip()])
        else:
            writes.append(["`%s`" % sig, sel, who, where, cell])

    if writes:
        L += ["### %s state-changing functions" % name, ""]
        L += table(["Function", "Selector", "Who may call", "Defined in", "Description"], writes)
    if views:
        L += ["### %s views" % name, "",
              "Kind is `view`/`pure` for a function, or `constant`/`immutable`/`storage` for the getter of a public variable.", ""]
        L += table(["Function", "Selector", "Kind", "Defined in", "Description"], views)
    if tuple_sigs:
        L += ["Canonical signatures, for `cast sig` and for anything that hashes them, where a parameter is a struct:", ""]
        L += ["- `%s` → `0x%s`" % (c, s) for _, c, s in tuple_sigs] + [""]

    def member_rows(abi_type, insp, src_field, gapkey, selfmt):
        rows = []
        # forge keys its `events` output by internal type names (`HedgeFunFactory.Venue`, not `uint8`), so an ABI
        # entry is matched by name and arity rather than by canonical signature
        entries = {(x["name"], len(x["inputs"])): x for x in abi if x["type"] == abi_type}
        for canon in sorted(insp):
            nm = canon.split("(")[0]
            inner = canon[len(nm) + 1:-1]
            e = entries.get((nm, len(split_top(inner))))
            sig = "%s(%s)" % (nm, sig_params(e["inputs"], indexed=True)) if e else canon
            owner = next((cn for cn in world.linearize(name) if nm in world.decls[cn][src_field]), None)
            if owner:
                info = world.decls[owner][src_field][nm]
                cell, has, more = describe(info["doc"], info["trailing"])
                where = "this contract" if owner == name else "`%s`" % owner
                if owner == name and not has:
                    gap[gapkey].append(nm)
                if more and owner == name:
                    details.append(("`%s`" % sig, info["doc"]))
            else:
                lib = world.lib_owner(name, src_field, canon)
                cell, where = "—", ("inherited via `%s`" % lib if lib else "a dependency under `lib/`")
            rows.append(["`%s`" % sig, "`%s`" % selfmt(insp[canon]), where, cell])
        return rows

    rows = member_rows("event", evs, "events", "events", lambda s: s)
    if rows:
        L += ["### %s events" % name, ""] + table(["Event", "topic0", "Defined in", "Description"], rows)
    rows = member_rows("error", errs, "errors", "errors", lambda s: "0x" + s)
    if rows:
        L += ["### %s errors" % name, "",
              "Every custom error this contract's ABI carries, including those that bubble up from libraries it uses.", ""]
        L += table(["Error", "Selector", "Defined in", "Description"], rows)

    consts = [v for v in d["vars"] if v["constant"]]
    if consts:
        rows = []
        for v in sorted(consts, key=lambda v: v["name"]):
            val = "`%s`" % v["value"]
            n = evaluate(v["value"])
            if n is not None:
                val += " = {:,}".format(n)
            cell, _, more = describe(v["doc"], v["trailing"])
            if more and v["vis"] != "public":
                details.append(("`%s`" % v["name"], v["doc"]))
            rows.append(["`%s`" % v["name"], "`%s`" % v["type"], v["vis"], val, cell])
        L += ["### %s constants" % name, ""] + table(["Name", "Type", "Visibility", "Value", "Meaning"], rows)

    hidden = [v for v in d["vars"] if not v["constant"] and v["vis"] != "public"]
    if hidden:
        rows = []
        for v in hidden:                      # declaration order: it is the storage order
            cell, _, more = describe(v["doc"], v["trailing"])
            if more:
                details.append(("`%s`" % v["name"], v["doc"]))
            rows.append(["`%s`" % v["name"], "`%s`" % v["type"], v["vis"] + (" immutable" if v["immutable"] else ""), cell])
        L += ["### %s non-public state" % name, "", "In declaration order. No getter; listed because the functions above read and write it.", ""]
        L += table(["Name", "Type", "Visibility", "Meaning"], rows)

    for s in d["structs"]:
        L += ["### %s struct %s" % (name, s["name"]), ""]
        sdoc = parse_natspec(s["doc"])
        if sdoc["notice"] or s["trailing"]:
            L += [to_markdown(sdoc["notice"]) if sdoc["notice"] else esc(s["trailing"]), ""]
        rows = []
        for f_ in s["fields"]:                # declaration order: it is the ABI order
            fd = parse_natspec(f_["doc"])
            cell = esc(flat(fd["notice"] or fd["dev"]) or f_["trailing"] or "—", cell=True)
            rows.append(["`%s`" % f_["name"], "`%s`" % f_["type"], cell])
        L += table(["Field", "Type", "Meaning"], rows)

    for en in d["enums"]:
        L += ["### %s enum %s" % (name, en["name"]), ""]
        edoc = parse_natspec(en["doc"])
        if edoc["notice"]:
            L += [to_markdown(edoc["notice"]), ""]
        L += ["ABI-encoded as `uint8`: " + ", ".join("`%d` = `%s`" % (k, m) for k, m in enumerate(en["members"])) + ".", ""]

    internal = [fn for fn in d["functions"] if fn["vis"] in ("internal", "private")]
    if internal:
        rows = []
        for fn in internal:
            sig = "%s(%s)" % (fn["name"], fn["params"]) + (" → (%s)" % fn["returns"] if fn["returns"] else "")
            attrs = " ".join(x for x in [fn["vis"], fn["mut"], "virtual" if fn["virtual"] else "", "override" if fn["override"] else ""] if x)
            doc, doc_from = fn["doc"], None
            if not doc.strip() and fn["override"]:
                for cn in world.linearize(name)[1:]:
                    b = next((x for x in world.decls[cn]["functions"] if x["name"] == fn["name"] and x["doc"].strip()), None)
                    if b:
                        doc, doc_from = b["doc"], cn
                        break
            cell, _, more = describe(doc, fn["trailing"], doc_from)
            if more and not doc_from:
                details.append(("`%s` (%s)" % (sig.split(" → ")[0], fn["vis"]), doc))
            rows.append(["`%s`" % sig, attrs, cell])
        L += ["### %s internal functions" % name, "", "Not callable from outside; listed because inheriting contracts and reviewers need them.", ""]
        L += table(["Function", "Attributes", "Description"], rows)

    ctor = next((fn for fn in d["functions"] if fn["name"] == "constructor"), None)
    if ctor:
        L += ["### %s constructor" % name, "", "`constructor(%s)`" % ctor["params"], ""]
        if ctor["doc"].strip():
            details.append(("`constructor`", ctor["doc"]))

    if details:
        L += ["### %s NatSpec in full" % name, "",
              "The tables above carry the first sentence only. The complete comment for every member that has more, "
              "reflowed but not reworded:", ""]
        seen = set()
        for label, doc in details:
            if (label, doc) in seen:
                continue
            seen.add((label, doc))
            L += full_natspec(label, doc)
    gaps[name] = gap
    return L


def render_interfaces(world):
    L = ["## External interfaces the contracts call", "",
         "Declared in `src/interfaces/`. These are the selectors this code sends to contracts it does "
         "not own: the stock token, the Chainlink aggregators, the Uniswap V3 pool and factory, and each other.", ""]
    rows = []
    for path in sorted(world.by_file):
        for d in world.by_file[path]:
            if d["kind"] != "interface":
                continue
            ids = world.insp[("%s:%s" % (path, d["name"]), "methodIdentifiers")]
            for canon in sorted(ids):
                rows.append(["`%s`" % d["name"], "`%s`" % canon, "`0x%s`" % ids[canon], "[`%s`](../%s)" % (path, path)])
    return L + table(["Interface", "Function", "Selector", "Declared in"], rows)


def render_error_index(world):
    L = ["## Error selector index", "",
         "Sorted by selector, for decoding a revert: take the first four bytes of the revert data and look it up. "
         "The same error name declared in two contracts has the same selector, so a name can have several sources.", ""]
    idx = {}
    for path, name in CONTRACTS:
        for canon, sel in world.insp[("%s:%s" % (path, name), "errors")].items():
            idx.setdefault((sel, canon), []).append(name)
    rows = [["`0x%s`" % sel, "`%s`" % canon, ", ".join("`%s`" % n for n in names)] for (sel, canon), names in sorted(idx.items())]
    return L + table(["Selector", "Error", "In the ABI of"], rows)


def render_gaps(gaps):
    L = ["## NatSpec coverage", "",
         "External and public members declared in each contract that have no `///` comment of their own. A `//` "
         "comment at the end of the line is shown in the tables above but is not NatSpec: the compiler does not emit "
         "it. An entry marked (getter) is a public variable.", ""]
    rows = []
    for _, name in CONTRACTS:
        g = gaps[name]
        rows.append(["`%s`" % name] + [", ".join("`%s`" % x for x in g[k]) or "none" for k in ("functions", "events", "errors")])
    return L + table(["Contract", "Functions and getters", "Events", "Errors"], rows)


def generate():
    world = World()
    commit = run(["git", "rev-parse", "HEAD"]).strip()
    gaps, bodies = {}, []
    for path, name in CONTRACTS:
        bodies.append(render_contract(world, path, name, gaps))

    L = ["# Contract reference", "",
         "**Generated file: do not edit by hand.** Run `python3 tools/gen_reference.py` and commit the result. "
         "`python3 tools/check_docs.py` fails when this file no longer matches the source.", "",
         "Generated by [`tools/gen_reference.py`](../tools/gen_reference.py) from `forge inspect` (ABI, selectors, "
         "errors, events), `forge build --sizes`, and the `///` NatSpec, constants, structs and modifiers in `src/`.", "",
         COMMIT_MARKER + " `%s`" % commit, "",
         "Where the source has no NatSpec for a member the description reads —, rather than a guess. "
         "[NatSpec coverage](#natspec-coverage) lists every such member.", "",
         "## Contents", ""]
    L += ["| Contract | Kind | Runtime bytes | Under EIP-170 | Source |", "|---|---|---|---|---|"]
    for path, name in CONTRACTS:
        d = world.decls[name]
        size = world.sizes.get(name) or world.sizes.get("%s (%s)" % (name, path))
        kind = "abstract" if d["abstract"] else d["kind"]
        rs, rm = ("—", "—") if d["abstract"] or not size else ("{:,}".format(size["runtime_size"]), "{:,}".format(size["runtime_margin"]))
        L += ["| [%s](#%s) | %s | %s | %s | [`%s`](../%s) |" % (name, anchor(name), kind, rs, rm, path, path)]
    L += ["", "Also: [External interfaces the contracts call](#external-interfaces-the-contracts-call), "
          "[Error selector index](#error-selector-index), [NatSpec coverage](#natspec-coverage).", "",
          "\"Who may call\" is read from the source: the function's modifiers, every `if (...) revert` in its body whose "
          "condition mentions `msg.sender`, and the same for one level of `_helper()` it calls. \"anyone\" means none "
          "of those was found, not that the call will succeed.", ""]
    for b in bodies:
        L += b
    L += render_interfaces(world)
    L += render_error_index(world)
    L += render_gaps(gaps)
    text = "\n".join(L)
    text = re.sub(r"\n{3,}", "\n\n", text).rstrip("\n") + "\n"
    return text, gaps


def main(argv):
    text, gaps = generate()
    if "--gaps" in argv:
        for _, name in CONTRACTS:
            for k in ("functions", "events", "errors"):
                if gaps[name][k]:
                    print("%s %s: %s" % (name, k, ", ".join(gaps[name][k])))
        return 0
    if "--stdout" in argv:
        sys.stdout.write(text)
        return 0
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with open(OUT, "w") as f:
        f.write(text)
    print("wrote %s (%d lines)" % (os.path.relpath(OUT, ROOT), text.count("\n")))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
