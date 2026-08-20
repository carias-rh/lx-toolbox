# Peek and commit Round-Robin by engineer name

Auto-Assign skipped the next engineer when the unassigned queue was empty: a mutating GET stored a slot index that reset when the On-Shift Pool changed, and idle polls could consume a turn. We use two verbs instead: peek never writes; commit records the engineer who actually received the work, by name, per group. Auto-Assign does not call the Shift frontend unless there is unassigned work. One contract for T1, T2, and CX.

## Considered Options

- **Mutating GET with `advance`:** smallest diff. Rejected because omitting the flag consumed a turn (T2 defaulted to true) and T1 `GET /api/round_robin` always rotated, including empty polls.
- **Index keyed by overlap composition:** looks like rotation until a Shift start/end mints a new key and the first name in the new list gets every Feedback. Rejected; identity is the engineer.
- **Auto-Assign holds the next name in the pod:** dies every 60s loop and on restart. Rejected; the Shift frontend is the ledger.
- **Peek GET + commit POST, last name per group** (chosen): idle peeks are stable; pool changes follow the next name still on Shift.

## Consequences

- Auto-Assign peeks with GET and commits with POST for T1, T2, and CX. GET never writes the last-assigned name.
