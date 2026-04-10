# Watchlist Management

Manage topics you want to track continuously. Findings accumulate in the SQLite database for briefings and history queries.

## Commands

| Command | Action |
|---|---|
| `watch add "topic"` | Add a topic (daily schedule) |
| `watch add "topic" --weekly` | Add with weekly schedule |
| `watch "topic"` | Shorthand for `watch add` |
| `watch remove "topic"` | Remove a topic |
| `watch list` | Show all topics with status |
| `watch config delivery [channel]` | Set delivery channel |
| `watch config budget AMOUNT` | Set daily cost budget |
| `watch run-all` | Run research for all topics now |
| `watch run-one "topic"` | Run research for one topic now |

## Adding a Topic

```bash
python3 "${SKILL_ROOT}/scripts/watchlist.py" add "TOPIC_NAME" [--weekly] [--queries "q1,q2"]
```

The script auto-bootstraps the SQLite database on first add.

**Default schedule**: Daily at 8am (`0 8 * * *`).
**Weekly**: Mondays at 8am (`0 8 * * 1`).

**After adding**, confirm to the user:
```
Added "TOPIC_NAME" to watchlist.
Schedule: daily at 8am (or weekly on Mondays)

To run research now: /last30days watch run-one "TOPIC_NAME"
To set up automated runs: add a cron/launchd job for `python3 ${SKILL_ROOT}/scripts/watchlist.py run-all`
```

## Removing a Topic

```bash
python3 "${SKILL_ROOT}/scripts/watchlist.py" remove "TOPIC_NAME"
```

Show confirmation or "not found" message.

## Listing Topics

```bash
python3 "${SKILL_ROOT}/scripts/watchlist.py" list
```

Display as a formatted table:
```
Topic         | Schedule     | Last Run     | Findings | Status
--------------+--------------+--------------+----------+--------
AI video      | daily 8am    | 2h ago       | 47       | ok
NVIDIA news   | weekly Mon   | 3d ago       | 23       | ok

Budget: $0.42 / $5.00 today
```

## Running Research

```bash
# All enabled topics (with budget guard)
python3 "${SKILL_ROOT}/scripts/watchlist.py" run-all

# Single topic
python3 "${SKILL_ROOT}/scripts/watchlist.py" run-one "TOPIC_NAME"
```

Show results: new findings count, updated findings, duration, and any errors.

## Configuration

```bash
# Set delivery channel (for future notification support)
python3 "${SKILL_ROOT}/scripts/watchlist.py" config delivery telegram

# Set daily budget limit
python3 "${SKILL_ROOT}/scripts/watchlist.py" config budget 10.00
```

## Scheduling

The watchlist doesn't auto-schedule. To automate, set up a system job:

**macOS (launchd)**:
```bash
# Run daily at 8am
crontab -e
# Add: 0 8 * * * python3 /path/to/scripts/watchlist.py run-all
```

**Linux (cron)**:
```bash
crontab -e
# Add: 0 8 * * * python3 /path/to/scripts/watchlist.py run-all
```

## Error Handling

- Duplicate topic: update the existing schedule
- Topic not found on remove: show "not found" message
- Budget exceeded: skip remaining topics, show which were skipped
- Research failure: record error, continue to next topic
