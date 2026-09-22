"""The phone-rail executor (TASK-130, TASK-142, TASK-143). It runs on the handset machine.

This package is the only code of ours that ever touches the handset. It is stdlib-only and imports
nothing from ``app/`` -- it runs on a machine where our repo's dependencies (fastapi, requests, ...)
are not installed and must not be. It also imports nothing from anyone else's tree: TASK-142 replaced
the wrapper around a colleague's package with our own adb driver, because that package lives in a
disposable agent worktree and its send path reports an unmatched bubble as sent.

What each module is, in one line:
  adb_driver.py  our own adb: open a chat, prove whose it is, type, press send, read the tick back
  inbound.py     pure parsing of the notification shade and the id we mint for an inbound message
  driver.py      the PhoneDriver interface, the FakeDriver, and the tick rules
  ledger.py      the write-ahead journal, first-body-wins idempotency, and the inbound outbox
  governor.py    the last fuse before a message reaches a person: hours, caps, pacing
  watcher.py     the inbound poll loop, so a candidate's message does not wait for a timer
  executor.py    the one place the pieces are sequenced; the HTTP handlers hold no logic
  operations.py  the handset operations as callable code: list, read, send, clear, delete
  broadcast.py   many recipients as a ledger-backed run that survives a restart, plus its runner
  server.py      loopback-only HTTP, bearer token, one route per operation
  envelope.py    one inbound message in the verbatim Meta shape our webhook already parses
  relay_pull.py  THE EXCEPTION: this one runs on our VPS and drains the mini over ssh

The invariant this package exists to hold (decision-8, 2026-09-21): on this rail there is no provider
message id, so "no sent without a confirmed provider message id" becomes **no sent without a verified
delivery tick**. ``unverified`` is a 504 and never a ``sent``.

The rail is pull-only inbound: nothing on the handset machine ever connects to our VPS. It appends to
a local outbox with a monotonic cursor, and we drain it over a connection we open ourselves.
"""
