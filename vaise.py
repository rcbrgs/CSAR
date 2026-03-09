#!/usr/bin/env python3
"""
vaise.py v0.18
Minimal local CSAR agent. Runs on nata via Ollama HTTP API.

Window = seed + history + CONTEXT_RESERVE.
At 90% history: schedule compression after next prompt.
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

ANNOTATION_REMINDER = (
    "\n\nANNOTATION FORMAT — when asked to annotate, always produce a block exactly:\n"
    "{'timestamp': \"YYYY-MM-DDTHH:MM:SS | seq:NNN\", 'name': \"snake_case_id\","
    " 'facets': \"[FACET] [T:YYYY-MM-DDTHH:MM:SS]\", 'snippet': \"Content here.\"}\n"
    "Use single quotes. name and facets are required. No prose outside the block.\n"
    "For timestamp and [T:] use the exact datetime shown in NOW: above.\n"
"Include the timezone offset (e.g. 2026-03-08T14:32:00-0300).\n"
)

COMPRESSION_PROMPT = (
    "MEMORY COMPRESSION STEP.\n"
    "The conversation history below will be replaced by a single annotation — "
    "it is the only record that survives. Compress all key facts, decisions, "
    "names, open questions, and anything needed to continue coherently into "
    "one CSAR annotation block. Use the full snippet field; do not truncate. "
    "Output ONLY the annotation block, nothing else.\n"
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

def compress_history(history, model, archive_path, seed_content):
    """
    Compress history into a single annotation block.
    - Sends history to model with COMPRESSION_PROMPT.
    - Shows result to operator: accept / edit / retry / skip.
    - On accept: appends to archive, returns [annotation_as_history_item].
    - On skip: returns history unchanged.
    """
    history_text = "\n".join(
        f"{m['role'].upper()}: {m['content']}" for m in history
    )
    messages = [
        {"role": "system",  "content": seed_content + ANNOTATION_REMINDER},
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
            snip = block['snippet_preview'][:160]
            print(f"  │  snippet: {snip}{'…' if len(block['snippet_preview'])>160 else ''}")
            print("  ├────────────────────────────────────────────────────")
            print("  │  [y] accept   [r] retry   [s] skip (keep history)")
            print("  └────────────────────────────────────────────────────")

            try:
                answer = input("  compression> ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                print("\n  compression aborted — history unchanged.")
                return history

            if answer == 'y':
                raw_block = block['block']
                append_to_archive(archive_path, raw_block)
                h_before = sha256(Path(archive_path).expanduser().read_text())
                print(f"  archive: {archive_path}")
                print(f"  SHA-256: {sha256(Path(archive_path).expanduser().read_text())}")
                # Return annotation as single history item
                return [{"role": "assistant", "content": raw_block}]

            if answer == 'r':
                print("  retrying…")
                continue

            if answer == 's':
                print("  compression skipped — history unchanged.")
                return history

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

def prompt_annotation(ann, archive_path, no_write):
    """Present one annotation to operator. Write to archive on confirm."""
    print()
    print("  ┌── annotation ──────────────────────────────────────")
    print(f"  │  name:    {ann['name']}")
    print(f"  │  facets:  {ann['facets']}")
    if ann["snippet_preview"]:
        p = ann["snippet_preview"][:120]
        print(f"  │  snippet: {p}{'…' if len(ann['snippet_preview'])>120 else ''}")
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

def _exit_compress(history, compress_next, args, seed_content):
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
        compress_history(history, args.model, args.archive, seed_content)


def main():
    p = argparse.ArgumentParser(
        prog="vaise", description="vaise v0.18 — minimal local CSAR agent",
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
    p.add_argument("--threshold", type=float, default=0.60, metavar="FRAC",
                   help="History fraction (0.0-1.0) that schedules compression next prompt (default: 0.60).")
    p.add_argument("--no-write", action="store_true",
                   help="Parse annotations but do not write to archive.")
    p.add_argument("--quiet",    action="store_true",
                   help="Suppress annotation prompts (implies --no-write).")
    args = p.parse_args()

    if args.quiet:
        args.no_write = True

    effective_window = args.window if args.window else window_for(args.model)

    seed_content = ""
    if args.seed:
        seed_content = load_file(args.seed)
        print()
        file_info("seed", args.seed, seed_content)

    print()
    print(f"vaise v0.18 model: {args.model}")
    print(f"window: {effective_window} tok  reserve: {CONTEXT_RESERVE} tok")
    print(f"archive: {args.archive}")
    if args.no_write:
        print("write:  disabled")
    print("exit / Ctrl-D to end")
    print()

    history        = []
    compress_next  = False   # flag: compress after the upcoming prompt

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
            _exit_compress(history, compress_next, args, seed_content)
            break

        if not user_input:
            continue
        if user_input.lower() in ("exit", "quit", "bye", ":q"):
            print("vaise: session ended.")
            _exit_compress(history, compress_next, args, seed_content)
            break

        # ── build messages: seed + history + user ─────────────────────────
        # Use local timezone from OS (set TZ=America/Sao_Paulo if wrong)
        now_str  = datetime.now().astimezone().strftime("%Y-%m-%dT%H:%M:%S%z")
        header   = f"NOW: {now_str}\n\n"
        system   = header + seed_content + ANNOTATION_REMINDER if seed_content else header + ANNOTATION_REMINDER
        messages = [{"role": "system", "content": system}]
        messages.extend(history[-MAX_HISTORY:])
        messages.append({"role": "user", "content": user_input})

        # ── stream ────────────────────────────────────────────────────────
        print("vaise: ", end="", flush=True)
        response_text = chat_stream(args.model, messages)
        if not response_text:
            continue

        history.append({"role": "user",      "content": user_input})
        history.append({"role": "assistant", "content": response_text})

        # ── annotation handling ───────────────────────────────────────────
        if not args.quiet:
            annotations = parse_annotations(response_text)
            if not annotations:
                if any(w in response_text.lower() for w in
                       ("annotate", "annotation", "timestamp", "seq:")):
                    print("  ⚠  annotation keywords in response but no valid block parsed.")
            else:
                for ann in annotations:
                    prompt_annotation(ann, args.archive, args.no_write)

        # ── compression: execute if flagged ──────────────────────────────
        if compress_next:
            history = compress_history(
                history, args.model, args.archive, seed_content
            )
            compress_next = False

        # ── schedule compression if history ≥ 90% ────────────────────────
        hist_pct_now = len(history) / MAX_HISTORY
        if hist_pct_now >= args.threshold and not compress_next:
            compress_next = True
            print(f"  ◷ history at {int(hist_pct_now*100)}% — compression scheduled after next prompt.")


if __name__ == "__main__":
    main()
