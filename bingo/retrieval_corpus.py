"""Safe repository enumeration, source-preserving chunks and incremental FTS5 index."""

import ast
import hashlib
import json
import re
import sqlite3
import stat
from pathlib import Path

from pathspec import GitIgnoreSpec

EXCLUDED = {".git", ".bingo", ".pico", ".venv", "venv", "node_modules", "__pycache__",
            ".pytest_cache", ".ruff_cache", ".idea", "dist", "build", "artifacts", "tmp",
            "readme_intro_locked", "coverage", "vendor"}
EXTENSIONS = {".py", ".js", ".jsx", ".ts", ".tsx", ".java", ".go", ".rs", ".c", ".h",
              ".cpp", ".hpp", ".cs", ".rb", ".php", ".swift", ".kt", ".md", ".rst",
              ".txt", ".toml", ".yaml", ".yml", ".json", ".sql", ".sh", ".ps1", ".vue"}


def digest(value):
    return hashlib.sha256(value.encode("utf-8") if isinstance(value, str) else value).hexdigest()


def is_link(path):
    """Python 3.11 is_symlink does not detect Windows junctions/reparse points."""
    try:
        attributes = getattr(path.lstat(), "st_file_attributes", 0)
        return path.is_symlink() or bool(attributes & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400))
    except OSError:
        return True


def tokens(text):
    """Identifiers remain searchable whole and by component; Chinese uses bigrams."""
    words = re.findall(r"[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[\u3400-\u9fff]+", text)
    result = []
    for word in words:
        if re.fullmatch(r"[\u3400-\u9fff]+", word):
            result.extend(word[i:i+2] for i in range(max(1, len(word)-1)))
        else:
            result.append(word.lower())
            parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).replace("_", " ").lower().split()
            if parts != [word.lower()]:
                result.extend(parts)
    return result


SYMBOL_CARD_SCHEMA = "symbol-card-v1"
MAX_SIGNATURE_CHARS = 160
MAX_DOCSTRING_CHARS = 240
MAX_CALLS = 12
MAX_ATTRIBUTES = 10
MAX_LITERALS = 6
MAX_CARD_CHARS = 1200


def _clip(value, limit):
    value = " ".join(str(value or "").split())
    return value if len(value) <= limit else value[:limit - 1] + "…"


def _symbol_id(path, qualified_name, kind):
    return digest(f"{path}\0{qualified_name}\0{kind}")


def _call_name(node):
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _call_name(node.value)
        return f"{prefix}.{node.attr}" if prefix else node.attr
    return ""


class _BehaviorVisitor(ast.NodeVisitor):
    """Collect bounded, deterministic behavior hints without copying source bodies."""

    def __init__(self):
        self.calls, self.attributes, self.literals, self.returns = [], [], [], []

    def visit_FunctionDef(self, node):  # nested definitions belong to their own symbol
        return

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node):
        return

    def visit_Call(self, node):
        name = _call_name(node.func)
        if name:
            self.calls.append(name)
        for arg in node.args:
            if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
                literal = arg.value
                if ("/" in literal or "*" in literal or "." in literal) and not re.search(
                        r"(?i)(secret|password|token|api[_-]?key|credential)", literal):
                    self.literals.append(literal)
        self.generic_visit(node)

    def visit_Attribute(self, node):
        name = _call_name(node)
        if name:
            self.attributes.append(name)
        self.generic_visit(node)

    def visit_Return(self, node):
        if isinstance(node.value, ast.Call):
            name = _call_name(node.value.func)
            if name:
                self.returns.append(f"return {name}")
        elif isinstance(node.value, (ast.Name, ast.Attribute)):
            name = _call_name(node.value)
            if name:
                self.returns.append(f"return {name}")
        elif node.value is None:
            self.returns.append("return None")
        self.generic_visit(node)


def _unique(values, limit, chars=80):
    return list(dict.fromkeys(_clip(value, chars) for value in values if value))[:limit]


def extract_symbols(path, text):
    """Return Python symbols and factual, statically resolved graph edges."""
    if not path.endswith(".py"):
        return [], []
    try:
        tree = ast.parse(text)
    except (SyntaxError, ValueError):
        return [], []

    symbols = []

    def visit_body(body, parents):
        for node in body:
            if not isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            parent_names = [item[0] for item in parents]
            qualified_name = ".".join([*parent_names, node.name])
            if isinstance(node, ast.ClassDef):
                kind = "class"
                signature = f"{node.name}({', '.join(ast.unparse(base) for base in node.bases)})" if node.bases else node.name
            elif parents and parents[-1][1] == "class":
                kind = "method"
                signature = f"{node.name}({_clip(ast.unparse(node.args), MAX_SIGNATURE_CHARS)})"
            elif parents:
                kind = "nested_function"
                signature = f"{node.name}({_clip(ast.unparse(node.args), MAX_SIGNATURE_CHARS)})"
            else:
                kind = "function"
                signature = f"{node.name}({_clip(ast.unparse(node.args), MAX_SIGNATURE_CHARS)})"
            start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
            visitor = _BehaviorVisitor()
            for statement in node.body:
                visitor.visit(statement)
            row = {
                "symbol_id": _symbol_id(path, qualified_name, kind),
                "path": path,
                "qualified_name": qualified_name,
                "simple_name": node.name,
                "kind": kind,
                "parent_qualified_name": ".".join(parent_names),
                "signature": _clip(signature, MAX_SIGNATURE_CHARS),
                "docstring": _clip(ast.get_docstring(node, clean=True), MAX_DOCSTRING_CHARS),
                "start_line": start,
                "end_line": node.end_lineno,
                "content_hash": digest(text),
                "calls": _unique(visitor.calls, MAX_CALLS),
                "attributes": _unique(visitor.attributes, MAX_ATTRIBUTES),
                "literals": _unique(visitor.literals, MAX_LITERALS, 100),
                "returns": _unique(visitor.returns, 4),
                "bases": _unique([ast.unparse(base) for base in node.bases], 8) if isinstance(node, ast.ClassDef) else [],
            }
            symbols.append(row)
            visit_body(node.body, [*parents, (node.name, "class" if isinstance(node, ast.ClassDef) else "function")])

    visit_body(tree.body, [])
    by_qualified = {row["qualified_name"]: row for row in symbols}
    by_simple = {}
    for row in symbols:
        by_simple.setdefault(row["simple_name"], []).append(row)
    edges = []
    for row in symbols:
        parent = by_qualified.get(row["parent_qualified_name"])
        if parent:
            edges.append({"source_symbol_id": parent["symbol_id"], "target_symbol_id": row["symbol_id"],
                          "source_qualified_name": parent["qualified_name"], "target_qualified_name": row["qualified_name"],
                          "relation": "contains", "confidence": 1.0})
        for base in row["bases"]:
            candidates = by_simple.get(base.rsplit(".", 1)[-1], [])
            if len(candidates) == 1:
                target = candidates[0]
                edges.append({"source_symbol_id": row["symbol_id"], "target_symbol_id": target["symbol_id"],
                              "source_qualified_name": row["qualified_name"], "target_qualified_name": target["qualified_name"],
                              "relation": "inherits", "confidence": 1.0})
        for call in row["calls"]:
            name = call.rsplit(".", 1)[-1]
            candidates = by_simple.get(name, [])
            if len(candidates) == 1 and candidates[0]["symbol_id"] != row["symbol_id"]:
                target = candidates[0]
                edges.append({"source_symbol_id": row["symbol_id"], "target_symbol_id": target["symbol_id"],
                              "source_qualified_name": row["qualified_name"], "target_qualified_name": target["qualified_name"],
                              "relation": "calls", "confidence": 0.9})
    unique_edges = {(e["source_symbol_id"], e["target_symbol_id"], e["relation"]): e for e in edges}
    return symbols, list(unique_edges.values())


def symbol_cards(symbol):
    """Create short embedding inputs; exact source and volatile location data stay outside."""
    module = Path(symbol["path"]).with_suffix("").as_posix().replace("/", ".")
    identity = "\n".join(filter(None, (
        f"kind: {symbol['kind']}", f"qualified_name: {symbol['qualified_name']}",
        f"parent: {symbol.get('parent_qualified_name', '')}" if symbol.get("parent_qualified_name") else "",
        f"signature: {symbol['signature']}", f"module: {module}",
    )))
    behavior_fields = [
        f"qualified_name: {symbol['qualified_name']}",
        f"purpose: {symbol.get('docstring', '')}" if symbol.get("docstring") else "",
        f"calls: {', '.join(symbol.get('calls', [])[:MAX_CALLS])}" if symbol.get("calls") else "",
        f"attributes: {', '.join(symbol.get('attributes', [])[:MAX_ATTRIBUTES])}" if symbol.get("attributes") else "",
        f"literals: {', '.join(symbol.get('literals', [])[:MAX_LITERALS])}" if symbol.get("literals") else "",
        f"returns: {', '.join(symbol.get('returns', [])[:4])}" if symbol.get("returns") else "",
    ]
    return {"identity": _clip(identity, MAX_CARD_CHARS),
            "behavior": _clip("\n".join(filter(None, behavior_fields)), MAX_CARD_CHARS)}


def chunk_file(path, text, max_chars=1600, max_lines=40):
    lines = text.splitlines()
    spans = []
    if path.endswith(".py"):
        try:
            tree = ast.parse(text)
            end = 0
            for node in tree.body:
                start = min([node.lineno, *[d.lineno for d in getattr(node, "decorator_list", [])]])
                if start > end + 1:
                    spans.append((end + 1, start - 1, ""))
                spans.append((start, node.end_lineno, getattr(node, "name", "")))
                end = node.end_lineno
            if end < len(lines):
                spans.append((end + 1, len(lines), ""))
        except (SyntaxError, ValueError):
            pass
    if not spans:
        spans = [(1, len(lines), "")]
    file_hash = digest(text)
    chunks = []
    for first, last, symbol in spans:
        start = first
        while start <= last:
            end = start
            size = len(lines[start-1])
            while end < last and end-start+1 < max_lines and size+len(lines[end])+1 <= max_chars:
                size += len(lines[end])+1
                end += 1
            content = "\n".join(lines[start-1:end])
            # Minified/very long single lines are not useful code context.
            if content.strip() and len(content) <= max_chars:
                chunks.append({"path": path, "start_line": start, "end_line": end, "symbol": symbol,
                               "content_hash": file_hash, "content": content})
            if end == last:
                break
            start = max(start+1, end-3)
    return chunks


def repository_files(root, max_file_bytes=524288):
    """Respect nested gitignore/bingoignore, prune generated folders and all symlinks."""
    counters = {"skipped_files": 0}

    def walk(directory, rules):
        rules = list(rules)
        for name in (".gitignore", ".bingoignore"):
            ignore = directory / name
            if ignore.is_file() and not is_link(ignore) and ignore.resolve().is_relative_to(root):
                rules.append((directory, GitIgnoreSpec.from_lines(ignore.read_text(encoding="utf-8", errors="replace").splitlines())))
        for entry in sorted(directory.iterdir()):
            if is_link(entry) or not entry.resolve().is_relative_to(root) or entry.name in EXCLUDED or entry.name.endswith(".egg-info"):
                continue
            if entry.name.startswith(".env") or entry.name.lower() in {"credentials.json", "secrets.json"}:
                continue
            is_dir = entry.is_dir()
            ignored = False
            for base, spec in rules:
                match = spec.check_file(entry.relative_to(base).as_posix() + ("/" if is_dir else ""))
                if match.include is not None:
                    ignored = match.include
            if ignored:
                continue
            if is_dir:
                yield from walk(entry, rules)
            elif entry.suffix.lower() in EXTENSIONS and not entry.name.endswith((".min.js", ".lock")):
                try:
                    if entry.stat().st_size > max_file_bytes:
                        counters["skipped_files"] += 1
                        continue
                    raw = entry.read_bytes()
                    if b"\x00" in raw:
                        counters["skipped_files"] += 1
                        continue
                    text = raw.decode("utf-8")
                    yield entry.relative_to(root).as_posix(), digest(text), text
                except (OSError, UnicodeError):
                    counters["skipped_files"] += 1
    return walk(root, []), counters


class CorpusIndex:
    def __init__(self, root):
        self.root = Path(root).resolve()
        folder = self.root / ".bingo" / "retrieval"
        for p in (self.root / ".bingo", folder):
            if (is_link(p) and p.exists()) or not p.resolve().is_relative_to(self.root):
                raise ValueError("index path escapes workspace")
        folder.mkdir(parents=True, exist_ok=True)
        db_path = folder / "index.sqlite3"
        if db_path.exists() and is_link(db_path):
            raise ValueError("index path escapes workspace")
        self.db = sqlite3.connect(db_path, timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
            PRAGMA foreign_keys=ON;
            CREATE TABLE IF NOT EXISTS files(path TEXT PRIMARY KEY, hash TEXT NOT NULL, parse_error INTEGER NOT NULL DEFAULT 0);
            CREATE TABLE IF NOT EXISTS chunks(
                id INTEGER PRIMARY KEY AUTOINCREMENT, path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                start_line INTEGER, end_line INTEGER, symbol TEXT, content_hash TEXT, content TEXT);
            CREATE INDEX IF NOT EXISTS chunks_path ON chunks(path);
            CREATE VIRTUAL TABLE IF NOT EXISTS lexical USING fts5(terms);
            CREATE TABLE IF NOT EXISTS vectors(
                chunk_id INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
                model TEXT NOT NULL, vector TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS symbols(
                symbol_id TEXT PRIMARY KEY,
                path TEXT NOT NULL REFERENCES files(path) ON DELETE CASCADE,
                qualified_name TEXT NOT NULL,
                simple_name TEXT NOT NULL,
                kind TEXT NOT NULL,
                parent_qualified_name TEXT NOT NULL,
                signature TEXT NOT NULL,
                docstring TEXT NOT NULL,
                start_line INTEGER NOT NULL,
                end_line INTEGER NOT NULL,
                content_hash TEXT NOT NULL,
                calls TEXT NOT NULL,
                attributes TEXT NOT NULL,
                literals TEXT NOT NULL,
                returns_hint TEXT NOT NULL,
                bases TEXT NOT NULL,
                card_schema TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS symbols_path ON symbols(path);
            CREATE INDEX IF NOT EXISTS symbols_name ON symbols(qualified_name, simple_name);
            CREATE VIRTUAL TABLE IF NOT EXISTS symbol_lexical USING fts5(terms);
            CREATE TABLE IF NOT EXISTS symbol_edges(
                source_symbol_id TEXT NOT NULL REFERENCES symbols(symbol_id) ON DELETE CASCADE,
                target_symbol_id TEXT NOT NULL REFERENCES symbols(symbol_id) ON DELETE CASCADE,
                relation TEXT NOT NULL,
                confidence REAL NOT NULL,
                PRIMARY KEY(source_symbol_id,target_symbol_id,relation));
            CREATE TABLE IF NOT EXISTS symbol_vectors(
                symbol_id TEXT NOT NULL REFERENCES symbols(symbol_id) ON DELETE CASCADE,
                card_kind TEXT NOT NULL,
                model TEXT NOT NULL,
                card_schema TEXT NOT NULL,
                card_hash TEXT NOT NULL,
                vector TEXT NOT NULL,
                PRIMARY KEY(symbol_id,card_kind));
        ''')
        columns = {row[1] for row in self.db.execute("PRAGMA table_info(files)")}
        if "parse_error" not in columns:
            self.db.execute("ALTER TABLE files ADD COLUMN parse_error INTEGER NOT NULL DEFAULT 0")
        # Version 1 embedded complete chunk bodies. They are not used by symbol-card retrieval and are purged on migration.
        self.db.execute("DELETE FROM vectors")
        self.db.commit()
        self.generation = 0

    def sync(self):
        files, counters = repository_files(self.root)
        existing = dict(self.db.execute("SELECT path, hash FROM files"))
        seen, changed = set(), 0
        with self.db:
            for path, file_hash, text in files:
                seen.add(path)
                if existing.get(path) == file_hash:
                    continue
                changed += 1
                self.db.execute("DELETE FROM lexical WHERE rowid IN (SELECT id FROM chunks WHERE path=?)", (path,))
                self.db.execute("DELETE FROM symbol_lexical WHERE rowid IN (SELECT rowid FROM symbols WHERE path=?)", (path,))
                self.db.execute("DELETE FROM files WHERE path=?", (path,))
                parse_error = 0
                if path.endswith(".py"):
                    try:
                        ast.parse(text)
                    except (SyntaxError, ValueError):
                        parse_error = 1
                self.db.execute("INSERT INTO files(path,hash,parse_error) VALUES(?,?,?)", (path, file_hash, parse_error))
                for chunk in chunk_file(path, text):
                    cur = self.db.execute("INSERT INTO chunks(path,start_line,end_line,symbol,content_hash,content) VALUES(?,?,?,?,?,?)",
                                          tuple(chunk[k] for k in ("path", "start_line", "end_line", "symbol", "content_hash", "content")))
                    terms = " ".join(tokens(path + " " + chunk["symbol"] + " " + chunk["content"]))
                    self.db.execute("INSERT INTO lexical(rowid,terms) VALUES(?,?)", (cur.lastrowid, terms))
                symbols, edges = extract_symbols(path, text)
                for symbol in symbols:
                    cur = self.db.execute('''INSERT INTO symbols(
                        symbol_id,path,qualified_name,simple_name,kind,parent_qualified_name,signature,docstring,
                        start_line,end_line,content_hash,calls,attributes,literals,returns_hint,bases,card_schema)
                        VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (symbol["symbol_id"], symbol["path"], symbol["qualified_name"], symbol["simple_name"],
                         symbol["kind"], symbol["parent_qualified_name"], symbol["signature"], symbol["docstring"],
                         symbol["start_line"], symbol["end_line"], symbol["content_hash"],
                         json.dumps(symbol["calls"]), json.dumps(symbol["attributes"]), json.dumps(symbol["literals"]),
                         json.dumps(symbol["returns"]), json.dumps(symbol["bases"]), SYMBOL_CARD_SCHEMA))
                    card_text = " ".join(symbol_cards(symbol).values())
                    terms = " ".join(tokens(path + " " + card_text))
                    self.db.execute("INSERT INTO symbol_lexical(rowid,terms) VALUES(?,?)", (cur.lastrowid, terms))
                self.db.executemany("INSERT INTO symbol_edges VALUES(?,?,?,?)",
                                    [(edge["source_symbol_id"], edge["target_symbol_id"], edge["relation"], edge["confidence"])
                                     for edge in edges])
            removed = set(existing) - seen
            for path in removed:
                self.db.execute("DELETE FROM lexical WHERE rowid IN (SELECT id FROM chunks WHERE path=?)", (path,))
                self.db.execute("DELETE FROM symbol_lexical WHERE rowid IN (SELECT rowid FROM symbols WHERE path=?)", (path,))
                self.db.execute("DELETE FROM files WHERE path=?", (path,))
            self._rebuild_resolved_edges()
        if changed or removed:
            self.generation += 1
        self.allowed = seen
        return {"files": len(seen), "chunks": self.db.execute("SELECT count(*) FROM chunks").fetchone()[0],
                "symbols": self.db.execute("SELECT count(*) FROM symbols").fetchone()[0],
                "changed_files": changed, "deleted_files": len(removed),
                "parse_failures": self.db.execute("SELECT count(*) FROM files WHERE parse_error=1").fetchone()[0], **counters}

    def _rebuild_resolved_edges(self):
        """Resolve only unambiguous call/inheritance targets across the current corpus."""
        self.db.execute("DELETE FROM symbol_edges WHERE relation IN ('calls','inherits')")
        symbols = self.symbols()
        by_qualified = {row["qualified_name"]: row for row in symbols}
        by_simple = {}
        for row in symbols:
            by_simple.setdefault(row["simple_name"], []).append(row)

        def resolve(name, source):
            if name.startswith(("self.", "cls.")) and source["parent_qualified_name"]:
                candidate = by_qualified.get(source["parent_qualified_name"] + "." + name.split(".", 1)[1])
                if candidate:
                    return candidate
            candidate = by_qualified.get(name)
            if candidate:
                return candidate
            matches = by_simple.get(name.rsplit(".", 1)[-1], [])
            return matches[0] if len(matches) == 1 else None

        edges = []
        for row in symbols:
            for relation, names, confidence in (("calls", row["calls"], 0.9), ("inherits", row["bases"], 1.0)):
                for name in names:
                    target = resolve(name, row)
                    if target and target["symbol_id"] != row["symbol_id"]:
                        edges.append((row["symbol_id"], target["symbol_id"], relation, confidence))
        self.db.executemany("INSERT OR IGNORE INTO symbol_edges VALUES(?,?,?,?)", edges)

    def scope(self, path):
        requested = self.root / path
        if any(is_link(p) for p in (requested, *requested.parents) if p != self.root and p.is_relative_to(self.root)):
            raise ValueError("symlink paths are excluded from retrieval")
        target = requested.resolve()
        if not target.is_relative_to(self.root):
            raise ValueError("path escapes workspace")
        relative = target.relative_to(self.root).as_posix()
        allowed = sorted(p for p in self.allowed if relative == "." or p == relative or p.startswith(relative+"/"))
        if relative != "." and not allowed:
            raise ValueError("path is excluded, missing, or has no indexable text")
        return relative, allowed

    def chunks(self, relative="."):
        # Use substr rather than SQL LIKE so '_' and '%' in paths remain literal.
        sql = "SELECT * FROM chunks"
        args = ()
        if relative != ".":
            sql += " WHERE path=? OR substr(path,1,?)=?"
            args = (relative, len(relative)+1, relative+"/")
        return self.db.execute(sql + " ORDER BY path,start_line", args)

    def keyword(self, query, relative=".", limit=60):
        terms = list(dict.fromkeys(tokens(query)))[:64]
        if len(terms) > 1:
            stopwords = {"a", "an", "the", "is", "where", "do", "we", "how", "to", "of", "and", "in", "for", "with", "not"}
            terms = [t for t in terms if t not in stopwords]
        if not terms:
            return []
        expression = " OR ".join('"'+word+'"' for word in terms)
        return [dict(row) for row in self.db.execute('''
            SELECT c.*, bm25(lexical) AS lexical_score FROM lexical
            JOIN chunks c ON c.id=lexical.rowid WHERE lexical MATCH ?
            AND (?='.' OR c.path=? OR substr(c.path,1,?)=?)
            ORDER BY bm25(lexical),c.path,c.start_line LIMIT ?
        ''', (expression, relative, relative, len(relative)+1, relative+"/", limit))]

    @staticmethod
    def _decode_symbol(row):
        if row is None:
            return None
        result = dict(row)
        for source_name, target_name in (("calls", "calls"), ("attributes", "attributes"),
                                         ("literals", "literals"), ("returns_hint", "returns"), ("bases", "bases")):
            result[target_name] = json.loads(result[source_name])
            if source_name != target_name:
                result.pop(source_name, None)
        return result

    def symbols(self, relative="."):
        sql = "SELECT * FROM symbols"
        args = ()
        if relative != ".":
            sql += " WHERE path=? OR substr(path,1,?)=?"
            args = (relative, len(relative)+1, relative+"/")
        return [self._decode_symbol(row) for row in self.db.execute(sql + " ORDER BY path,start_line", args)]

    def symbol_by_id(self, symbol_id):
        return self._decode_symbol(self.db.execute("SELECT * FROM symbols WHERE symbol_id=?", (symbol_id,)).fetchone())

    def symbol_search(self, query, relative=".", limit=60):
        query = query.strip()
        exact = [self._decode_symbol(row) for row in self.db.execute('''
            SELECT * FROM symbols WHERE (qualified_name=? COLLATE NOCASE OR simple_name=? COLLATE NOCASE)
            AND (?='.' OR path=? OR substr(path,1,?)=?)
            ORDER BY qualified_name=? COLLATE NOCASE DESC,path,start_line LIMIT ?''',
            (query, query, relative, relative, len(relative)+1, relative+"/", query, limit))]
        terms = list(dict.fromkeys(tokens(query)))[:32]
        if len(terms) > 1:
            stopwords = {"a", "an", "the", "is", "where", "do", "we", "how", "to", "of", "and", "in",
                         "for", "with", "not", "real", "symbol", "class", "function", "method"}
            terms = [term for term in terms if term not in stopwords]
        if not terms:
            return exact
        expression = " OR ".join('"'+word+'"' for word in terms)
        lexical = [self._decode_symbol(row) for row in self.db.execute('''
            SELECT s.* FROM symbol_lexical JOIN symbols s ON s.rowid=symbol_lexical.rowid
            WHERE symbol_lexical MATCH ? AND (?='.' OR s.path=? OR substr(s.path,1,?)=?)
            ORDER BY bm25(symbol_lexical),s.path,s.start_line LIMIT ?''',
            (expression, relative, relative, len(relative)+1, relative+"/", limit))]
        return list({row["symbol_id"]: row for row in [*exact, *lexical]}.values())[:limit]

    def graph_neighbors(self, symbol_id, relations=None, limit=8):
        relations = set(relations or {"contains", "inherits", "calls"})
        placeholders = ",".join("?" for _ in relations)
        rows = self.db.execute(f'''SELECT e.*, s.*, CASE WHEN e.source_symbol_id=? THEN 'out' ELSE 'in' END AS direction
            FROM symbol_edges e JOIN symbols s ON s.symbol_id=CASE WHEN e.source_symbol_id=? THEN e.target_symbol_id ELSE e.source_symbol_id END
            WHERE (e.source_symbol_id=? OR e.target_symbol_id=?) AND e.relation IN ({placeholders})
            ORDER BY e.confidence DESC,s.path,s.start_line LIMIT ?''',
            (symbol_id, symbol_id, symbol_id, symbol_id, *sorted(relations), limit)).fetchall()
        result = []
        for row in rows:
            item = self._decode_symbol(row)
            item["relation"], item["confidence"], item["direction"] = row["relation"], row["confidence"], row["direction"]
            result.append(item)
        return result

    def close(self):
        self.db.close()
