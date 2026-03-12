# CSAR — Coherent Session Annotation Runtime

A personal knowledge encoding system for persistent AI sessions.

## What is CSAR?

CSAR is a plain-text protocol for building AI sessions that accumulate
knowledge across conversations. It defines:

- **Annotation format** — structured knowledge blocks with facets,
  timestamps, and coordinates (RaTLE system)
- **Trigger system** — named behaviors that fire on session events
  (start, end, per-prompt, file write, etc.)
- **Role system** — named bundles of triggers that activate together
- **Voice system** — passive per-prompt behaviors (Apprentice, Detective)
- **Inventory** — Layer 2 file registry and write routing
- **Mail** — inter-instance annotation forwarding protocol

## File Structure

    CSAR.txt          root — core triggers and session protocol
    Clock.txt         time triggers, CRON scheduler
    Inventory.txt     file registry, listing tools, write routing
    Voice.txt         passive voices (Apprentice, Detective)
    Mail.txt          inter-instance annotation forwarding
    Help.txt          operator reference
    Roles/
      Roles.txt       role system template and init level
      Secretary.txt   session management, annotation, summaries

## What is NOT here

The `Private/` directory contains personal instance files and is not
published. CSAR is the protocol; what you do with it is yours.

## Usage

Load these files as context into an AI session alongside your personal
instance file. The session will self-configure from the INVENTORY
declaration in your instance file.

## License

Copyleft. Free. Copy. Share. Modify. The rules survive the forks.
