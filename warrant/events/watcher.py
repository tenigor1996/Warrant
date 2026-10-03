"""
events/watcher.py — Watches an event source and emits normalized Events.

WHAT IT WILL DO
    - Continuously monitor an event source. The first source will be
      data/alerts.jsonl (one JSON alert per line, tailed for new lines).
      Later sources could be webhooks, syslog, a queue, a cron trigger, etc.
    - Convert each raw incoming record into the common
      models.schemas.Event structure, so the Agent never sees
      source-specific formats.
    - Hand each Event to the Agent (via a callback supplied by main.py).

RECEIVES
    - Path / settings for the event source (config.ALERTS_FILE)
    - A callback: on_event(Event) -> None

RETURNS
    Nothing; runs until stopped. (May also offer a generator of Events.)

CONNECTS TO
    main.py            — starts the watcher, supplies the callback
    agent/agent.py     — ultimately receives the Events
    models/schemas.py  — Event
    data/alerts.jsonl  — initial event source
    config.py          — ALERTS_FILE
"""


class EventWatcher:
    """Monitors an event source and emits Event objects. (Skeleton only.)"""

    def __init__(self, source_path, on_event):
        """Store the source location and the callback to invoke per Event."""
        raise NotImplementedError

    def run(self):
        """Loop forever: read new raw records, normalize, call on_event."""
        raise NotImplementedError

    def _to_event(self, raw: dict):
        """Convert one raw record into a models.schemas.Event."""
        raise NotImplementedError
