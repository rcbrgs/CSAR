#!/usr/bin/env python3
"""
vaise.py v0.29
Minimal local CSAR agent. Runs on nata via Ollama HTTP API.

Window = seed + history + CONTEXT_RESERVE.
At --compression threshold (default 60%): schedule compression after next prompt.
Compression outputs an annotation block, which becomes the
single history item for the next cycle and is appended to --archive.

Usage:
  python3 vaise.py
  python3 vaise.py --seed   ~/claudette/CSAR/Private/vaise_seed.txt
  python3 vaise.py --seed   ~/claudette/CSAR/Private/vaise_seed.txt \\
                   --archive ~/claudette/CSAR/Private/archive.txt
  python3 vaise.py --seed ... --model llama3.2
  python3 vaise.py --seed ... --no-write
  python3 vaise.py --seed ... --quiet
"""

import argparse
import hashlib
import json
import re
import sys
import urllib.request
import urllib.error
from datetime import date, datetime
from pathlib import Path

# ── Config ────────────────────────────────────────────────────────────────

OLLAMA_URL         = "http://localhost:11434/api/chat"
DEFAULT_MODEL      = "qwen2.5-coder:3b"
DEFAULT_ARCHIVE    = "Private/archive.txt"
MAX_HISTORY        = 10          # messages (user+assistant pairs × 2)
CONTEXT_RESERVE    = 512         # tokens reserved for response + overhead

MODEL_WINDOWS = {
    "qwen2.5-coder:3b":  32768,
    "qwen2.5-coder:7b":  32768,
    "qwen2.5-coder:14b": 32768,
    "llama3.2":          131072,
    "llama3.1":          131072,
    "mistral":           32768,
    "phi3":              131072,
}
DEFAULT_WINDOW = 32768

SEED_FOOTER = "\n================================================================================\n\nvaise_seed"

RESPONSE_FORMAT = (
    "\n\nRESPONSE FORMAT\n"
    "Every response must end with exactly two CSAR annotation blocks:\n"
    "\n"
    "Block 1 — PROMPT ANNOTATION\n"
    "  Annotate only the current user message. Snake_case name, relevant facets.\n"
    "  Do not include information from previous turns.\n"
    "\n"
    "Block 2 — SESSION ANNOTATION\n"
    "  This block is your persistent memory. It is injected into your next prompt.\n"
    "  Build it by merging two sources:\n"
    "    (a) The SESSION CONTEXT block provided at the top of this prompt, if present.\n"
    "        Carry forward ALL factual content from it — names, titles, decisions,\n"
    "        open questions, owned items, preferences. Do not discard anything.\n"
    "    (b) The factual content of the current prompt annotation (Block 1).\n"
    "  Write the result as a dense factual summary, as many sentences as needed\n"
    "  (aim for 3-7). Prioritise facts over narrative. Do not truncate.\n"
    "  facets must include: [SESSION]\n"
    "\n"
    "Format for both (single quotes, no prose outside blocks):\n"
    "{'timestamp': \"YYYY-MM-DDTHH:MM:SS-0300\", 'name': \"name_here\","
    " 'facets': \"[FACET] [T:YYYY-MM-DDTHH:MM:SS-0300]\", 'snippet': \"Content.\"}\n"
    "\n"
    "Rules:\n"
    "  - Both blocks required every response. Session annotation always last.\n"
    "  - Timestamp: copy exact datetime from NOW: line, including -0300.\n"
    "  - No prose outside the two blocks.\n"
)
SESSION_BOOTSTRAP = (
    "I am Vase, an AI agent being developed by Renata and meant for note-taking. "
    "I am expected to follow all CSAR rules I know about. "
    "I will produce an annotation about Renata's input. "
    "I will end my response with a session annotation. "
    "The session annotation stores known facts. "
    "I will update it if I find something interesting. "
)

COMPRESSION_PROMPT = (
    "MEMORY COMPRESSION STEP.\n"
    "The history is full, and to make room for more input, the history must be "
    "compressed. The model should prepare a single annotation — "
    "it is the only record that survives. Compress all key facts, decisions, "
    "names, open questions, and anything needed to continue coherently into "
    "the session. Use the full snippet field; do not truncate. "
    "Do NOT elaborate, infer, or add information beyond what is explicitly "
    "present in the history below. Compress faithfully; do not interpret.\n"
    "For timestamp and [T:] use the exact datetime shown in NOW: above.\n\n"
    "Format:\n"
    "{'timestamp': \"YYYY-MM-DDTHH:MM:SS | seq:NNN\", 'name': \"session_compression_YYYYMMDDTHHMMSS\","
    " 'facets': \"[SESSION] [M:compression] [T:YYYY-MM-DDTHH:MM:SS]\","
    " 'snippet': \"...full compressed narrative...\"}\n\n"
    "History to compress:\n"
)


# ── Utilities ─────────────────────────────────────────────────────────────

def sha256(text):
    return hashlib.sha256(text.encode()).hexdigest()

def tok(text):
    return max(1, len(text) // 4)

def print_err(msg):
    print(f"vaise: {msg}", file=sys.stderr)

def window_for(model):
    for k, v in MODEL_WINDOWS.items():
        if model.startswith(k):
            return v
    return DEFAULT_WINDOW


# ── File I/O ──────────────────────────────────────────────────────────────

def load_file(path):
    p = Path(path).expanduser().resolve()
    if not p.exists():
        print_err(f"file not found: {p}")
        sys.exit(1)
    return p.read_text(encoding="utf-8")

def write_file(path, content):
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")

def file_info(label, path, content):
    print(f"{label}: {path}")
    print(f"       {sha256(content)}")
    print(f"       ~{tok(content)} tokens")

def append_to_archive(path, block):
    """Append an annotation block to the archive file."""
    p = Path(path).expanduser().resolve()
    p.parent.mkdir(parents=True, exist_ok=True)
    sep = "\n" + "=" * 80 + "\n"
    entry = f"{sep}{block.strip()}\n"
    with open(p, "a", encoding="utf-8") as f:
        f.write(entry)


def rotate_archive(archive_path, last_block):
    """
    Rotate archive on graceful exit:
      1. Rename archive.txt → archive.txt_YYYYMMDDTHHMMSS
      2. Write last_block as the seed content of the new archive.txt
    Only fires if archive file exists and last_block is not None.
    """
    if not last_block:
        return
    p = Path(archive_path).expanduser().resolve()
    if not p.exists():
        return
    stamp   = datetime.now().astimezone().strftime('%Y%m%dT%H%M%S')
    rotated = p.parent / f"{p.name}_{stamp}"
    p.rename(rotated)
    print(f"  archive rotated → {rotated.name}")
    # Seed new archive.txt with last compression block only
    sep = "=" * 80
    with open(p, 'w', encoding='utf-8') as f:
        f.write(f"{sep}\n{last_block.strip()}\n")
    print(f"  new archive seeded: {p.name}  ({sha256(p.read_text())[:16]}…)")


# ── Annotation parsing ────────────────────────────────────────────────────

_FIELD = re.compile(
    r'["\'](?P<key>\w+)["\']\s*:\s*["\'](?P<val>[^"\']*)["\']',
    re.DOTALL,
)
SKIP_NAME = re.compile(r'\[|\]|example_|YYYY|placeholder|snake_case', re.IGNORECASE)
SKIP_SNIP = re.compile(r'^(no change|unchanged|same as before)$', re.IGNORECASE)


def find_blocks(text):
    text = re.sub(r'```\w*\n?', '', text)
    blocks, i = [], 0
    while i < len(text):
        if text[i] == '{':
            depth, j = 1, i + 1
            while j < len(text) and depth:
                if   text[j] == '{': depth += 1
                elif text[j] == '}': depth -= 1
                j += 1
            blocks.append(text[i:j])
            i = j
        else:
            i += 1
    return blocks

def extract_fields(block):
    return {m.group('key'): m.group('val') for m in _FIELD.finditer(block)}

def parse_annotations(text):
    found, seen = [], set()
    for block in find_blocks(text):
        f      = extract_fields(block)
        name   = f.get('name', '').strip()
        facets = f.get('facets', '').strip()
        if not name or not facets:
            continue
        if SKIP_NAME.search(name) or name in seen:
            continue
        seen.add(name)
        preview = f.get('snippet', '').replace('\n', ' ').strip()
        if SKIP_SNIP.match(preview):
            continue
        found.append({"name": name, "facets": facets,
                      "snippet_preview": preview, "block": block})
    return found



# ── Timestamp rewrite (--hostclock) ─────────────────────────────────────

_TS_RE = re.compile(r"""(["'])(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[^"']*?)(["'])""")
_TF_RE = re.compile(r'\[T:[^\]]*\]')

def rewrite_timestamps(block, now_str):
    """Replace model-generated timestamp and [T:...] facets with now_str."""
    block = _TS_RE.sub(lambda m: m.group(1) + now_str + m.group(3), block, count=1)
    block = _TF_RE.sub('[T:' + now_str + ']', block)
    return block



# ── Ollama ────────────────────────────────────────────────────────────────

def chat_stream(model, messages):
    """Stream a chat response. Returns full text."""
    payload = json.dumps({"model": model, "messages": messages,
                          "stream": True}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    full = []
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            for raw in resp:
                line = raw.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    chunk = json.loads(line)
                except json.JSONDecodeError:
                    continue
                content = chunk.get("message", {}).get("content", "")
                if content:
                    print(content, end="", flush=True)
                    full.append(content)
                if chunk.get("done"):
                    break
    except urllib.error.URLError as e:
        print_err(f"Ollama unreachable: {e}\nIs Ollama running? Try: ollama serve")
        return ""
    print()
    return "".join(full)

def chat_once(model, messages):
    """Non-streaming single request. Returns text."""
    payload = json.dumps({"model": model, "messages": messages,
                          "stream": False}).encode()
    req = urllib.request.Request(
        OLLAMA_URL, data=payload,
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=120) as resp:
            return json.loads(resp.read()).get("message", {}).get("content", "")
    except Exception as e:
        print_err(f"chat_once failed: {e}")
        return ""


# ── Compression ───────────────────────────────────────────────────────────

def compress_history(history, model, archive_path, seed_content,
                     full=False, hostclock=False, now_str="", session_ann=None):
    """
    Compress history into a single annotation block.
    - Sends history to model with COMPRESSION_PROMPT.
    - Shows result to operator: accept / edit / retry / skip.
    - On accept: appends to archive, returns [annotation_as_history_item].
    - On skip: returns history unchanged.
    """
    if session_ann:
        raw_block = session_ann['block']
        if hostclock and now_str:
            raw_block = rewrite_timestamps(raw_block, now_str)
        print()
        print("  ┌── compression (session annotation) ─────────────────")
        snip = session_ann['snippet_preview'] if full else session_ann['snippet_preview'][:160]
        trunc = "..." if not full and len(session_ann["snippet_preview"]) > 160 else ""
        print(f"  |  snippet: {snip}{trunc}")
        print("  |  [y] accept   [s] skip")
        print("  └────────────────────────────────────────────────────")
        try:
            answer = input("  compression> ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\n  compression aborted.")
            return history, None
        if answer == 'y':
            append_to_archive(archive_path, raw_block)
            print(f"  archive: {archive_path}")
            print(f"  SHA-256: {sha256(Path(archive_path).expanduser().read_text())}")
            compressed_history = [
                {"role": "user",      "content": "[ARCHIVED — session compressed. Annotate only new messages.]"},
                {"role": "assistant", "content": raw_block},
            ]
            return compressed_history, raw_block
        print("  skipped.")
        return history, None

    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )
    messages = [
        {"role": "system",  "content": seed_content + RESPONSE_FORMAT},
        {"role": "user",    "content": COMPRESSION_PROMPT + history_text},
    ]

    print()
    print("  ┌── compression ─────────────────────────────────────")
    print(f"  │  {len(history)} messages → compressing…")
    print("  └────────────────────────────────────────────────────")

    while True:
        response = chat_once(model, messages)
        blocks   = parse_annotations(response)

        if not blocks:
            print()
            print("  ⚠  model produced no annotation block.")
            print(f"  raw output: {response[:200]}")
        else:
            block = blocks[0]
            print()
            print("  ┌── compressed annotation ───────────────────────────")
            print(f"  │  name:    {block['name']}")
            print(f"  │  facets:  {block['facets']}")
            snip = block['snippet_preview'] if full else block['snippet_preview'][:160]
            print(f"  │  snippet: {snip}{'…' if not full and len(block['snippet_preview'])>160 else ''}")
            print("  ├────────────────────────────────────────────────────")
            print("  │  [y] accept   [r] retry   [s] skip (keep history)")
            print("  └────────────────────────────────────────────────────")

            try:
                answer = input("  compression> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  compression aborted — history unchanged.")
                return history, None

            if answer == 'y':
                raw_block = block['block']
                if hostclock and now_str:
                    raw_block = rewrite_timestamps(raw_block, now_str)
                append_to_archive(archive_path, raw_block)
                print(f"  archive: {archive_path}")
                print(f"  SHA-256: {sha256(Path(archive_path).expanduser().read_text())}")
                # Return history framed as archived past context so model does not re-summarize
                compressed_history = [
                    {"role": "user",      "content": "[ARCHIVED — previous session compressed into the block below. Do not re-annotate this context. Annotate only new messages from here.]"},
                    {"role": "assistant", "content": raw_block},
                ]
                return compressed_history, raw_block

            if answer == 'r':
                print("  retrying…")
                continue

            if answer == 's':
                print("  compression skipped — history unchanged.")
                return history, None

            print("  unrecognised — enter y / r / s")

        # If no blocks, only option is retry or skip
        try:
            answer = input("  [r] retry  [s] skip: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            return history
        if answer == 's':
            return history
        # else retry


# ── Annotation prompt ─────────────────────────────────────────────────────

def prompt_annotation(ann, archive_path, no_write, full=False):
    """Present one annotation to operator. Write to archive on confirm."""
    print()
    print("  ┌── annotation ──────────────────────────────────────")
    print(f"  │  name:    {ann['name']}")
    print(f"  │  facets:  {ann['facets']}")
    if ann["snippet_preview"]:
        p = ann["snippet_preview"] if full else ann["snippet_preview"][:120]
        print(f"  │  snippet: {p}{'…' if not full and len(ann['snippet_preview'])>120 else ''}")
    print(f"  │  archive: {archive_path}")
    print("  └────────────────────────────────────────────────────")
    if no_write:
        print("  (--no-write: skipped)")
        return
    try:
        answer = input("  append to archive? [y/n]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print()
        return
    if answer == 'y':
        append_to_archive(archive_path, ann['block'])
        print(f"  SHA-256: {sha256(Path(archive_path).expanduser().read_text())}")


# ── Main ──────────────────────────────────────────────────────────────────

def _exit_compress(history, compress_next, args, seed_content, now_str="", session_ann=None):
    """
    On graceful exit: compress any unarchived history unless a compression
    was just completed this turn (compress_next would be False and history
    would be a single-item compression result).
    Skip if: no history, --no-write, or history is already a lone
    compression annotation (len==1 and name starts with session_compression).
    """
    if not history or args.no_write:
        return
    # Skip if history is already a single compression annotation
    if len(history) == 1:
        ann = parse_annotations(history[0].get('content', ''))
        if ann and ann[0]['name'].startswith('session_compression'):
            return
    if compress_next or len(history) >= 2:
        print("  ◷ exit compression — archiving unsaved history…")
        _, blk = compress_history(
            history, args.model, args.archive, seed_content,
            full=args.full, hostclock=args.hostclock, now_str=now_str,
            session_ann=session_ann
        )
        return blk
    return None


def main():
    p = argparse.ArgumentParser(
        prog="vaise", description="vaise v0.29 — minimal local CSAR agent",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  python3 vaise.py
  python3 vaise.py --seed ~/claudette/CSAR/Private/vaise_seed.txt
  python3 vaise.py --seed ~/claudette/CSAR/Private/vaise_seed.txt \\
                   --archive ~/claudette/CSAR/Private/archive.txt
  python3 vaise.py --seed ... --model llama3.2
  python3 vaise.py --seed ... --no-write
  python3 vaise.py --seed ... --quiet
        """)
    p.add_argument("--seed",     metavar="PATH",
                   help="Seed file. Injected as system context each prompt.")
    p.add_argument("--archive",  metavar="PATH", default=DEFAULT_ARCHIVE,
                   help=f"Archive file for annotations + compressions (default: {DEFAULT_ARCHIVE})")
    p.add_argument("--model",    default=DEFAULT_MODEL, metavar="MODEL",
                   help=f"Ollama model (default: {DEFAULT_MODEL})")
    p.add_argument("--window",    type=int,   default=0,    metavar="TOKENS",
                   help="Override model context window size.")
    p.add_argument("--compress", "--compression", "--threshold",
                   dest="compression", default="60", metavar="PCT",
                   help="History %% that schedules compression (default: 60). "
                        "Pass as integer 1-100 (e.g. 70) or fraction 0.0-1.0 (e.g. 0.70).")
    p.add_argument("--no-write", action="store_true",
                   help="Parse annotations but do not write to archive.")
    p.add_argument("--quiet",    action="store_true",
                   help="Suppress annotation prompts (implies --no-write).")
    p.add_argument("-v", "--verbose", action="store_true",
                   help="Print the NOW: timestamp sent with each prompt, as prefix to the response.")
    p.add_argument("--full", action="store_true",
                   help="Show full snippet text in annotation and compression prompts (no truncation).")
    p.add_argument("--hostclock", action="store_true",
                   help="Overwrite model-generated timestamps with Python host clock before archiving.")
    args = p.parse_args()

    # Normalise --compress: accept 70 (percent) or 0.70 (fraction)
    try:
        _c = float(args.compression)
        args.compression = _c / 100.0 if _c > 1.0 else _c
    except (TypeError, ValueError):
        args.compression = 0.60

    if args.quiet:
        args.no_write = True

    effective_window = args.window if args.window else window_for(args.model)

    seed_content = ""
    if args.seed:
        seed_content = load_file(args.seed)
        print()
        file_info("seed", args.seed, seed_content)

    print()
    print(f"vaise v0.29 model: {args.model}")
    print(f"window: {effective_window} tok  reserve: {CONTEXT_RESERVE} tok")
    print(f"archive: {args.archive}")
    if args.no_write:
        print("write:  disabled")
    print("exit / Ctrl-D to end")
    print()

    history        = []
    compress_next          = False  # flag: compress after the upcoming prompt
    last_compression_block = None   # most recently accepted compression block
    now_str        = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")  # updated each turn
    session_ann    = None   # last session_context_ block — injected as memory each turn

    while True:
        if args.seed:
            seed_content = load_file(args.seed)

        # ── history % for display ─────────────────────────────────────────
        hist_msgs = len(history)
        hist_pct  = min(int(hist_msgs / MAX_HISTORY * 100), 100)
        compress_flag = " [compress pending]" if compress_next else ""
        hbar = f"[hist {hist_pct:3d}%{compress_flag}]"

        try:
            user_input = input(f"you {hbar}: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nvaise: session ended.")
            _blk = _exit_compress(history, compress_next, args, seed_content,
                                  now_str=now_str, session_ann=session_ann)
            if _blk:
                last_compression_block = _blk
            rotate_archive(args.archive, last_compression_block)
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "bye", ":q"):
            print("vaise: session ended.")
            _blk = _exit_compress(history, compress_next, args, seed_content,
                                  now_str=now_str, session_ann=session_ann)
            if _blk:
                last_compression_block = _blk
            rotate_archive(args.archive, last_compression_block)
            break

        # ── build messages: seed + history + user ─────────────────────────
        # Use local timezone from OS (set TZ=America/Sao_Paulo if wrong)
        now_str  = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        header   = f"NOW: {now_str}\n\n"
        base_sys = header + seed_content + RESPONSE_FORMAT if seed_content else header + RESPONSE_FORMAT
        if session_ann:
            ctx_sec = (
                f"\n\nSESSION CONTEXT (your summary from previous turn):\n"
                f"{session_ann['block']}"
            )
        else:
            ctx_sec = (
                f"\n\nSESSION CONTEXT (bootstrap — no previous turn):\n"
                f"{SESSION_BOOTSTRAP}"
            )
        system   = base_sys + ctx_sec
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": user_input})

        # ── stream ────────────────────────────────────────────────────────
        if args.verbose:
            print(f"  [NOW sent: {now_str}]")
        print("vaise: ", end="", flush=True)
        response_text = chat_stream(args.model, messages)
        if not response_text:
            continue

        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant", "content": response_text})

        # ── split prompt annotation vs session annotation ─────────────────
        all_anns     = parse_annotations(response_text)
        prompt_anns  = [a for a in all_anns if '[SESSION]' not in a['facets'].upper()]
        session_anns = [a for a in all_anns if '[SESSION]'     in a['facets'].upper()]
        if session_anns:
            session_ann = session_anns[-1]

        if args.verbose and session_ann:
            _st = session_ann['snippet_preview'][:80] + ("..." if len(session_ann['snippet_preview']) > 80 else "")
            print(f"  [session: {_st}]")

        # ── archive prompt annotations ────────────────────────────────────
        if not args.quiet:
            if not all_anns:
                if any(w in response_text.lower() for w in
                       ("annotate", "annotation", "timestamp", "seq:")):
                    print("  ⚠  annotation keywords but no valid block parsed.")
            for ann in prompt_anns:
                if args.hostclock:
                    ann = dict(ann, block=rewrite_timestamps(ann['block'], now_str))
                prompt_annotation(ann, args.archive, args.no_write, full=args.full)

        # ── compression: execute if flagged ──────────────────────────────
        if compress_next:
            history, _blk = compress_history(
                history, args.model, args.archive, seed_content,
                full=args.full, hostclock=args.hostclock, now_str=now_str,
                session_ann=session_ann
            )
            compress_next = False
            if _blk:
                last_compression_block = _blk
                # Seed session_ann from compressed block so facts survive into next turn
                _blk_anns = parse_annotations(_blk)
                session_ann = _blk_anns[0] if _blk_anns else None

        # ── schedule compression if history ≥ --compression threshold ─────
        hist_pct_now = len(history) / MAX_HISTORY
        if hist_pct_now >= args.compression and not compress_next:
            compress_next = True
            print(f"  ◷ history at {int(hist_pct_now*100)}% — compression scheduled after next prompt.")


if __name__ == "__main__":
    main()
