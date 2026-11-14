# LMS Country selects a GLS CX Sub-region after Email Alias

GLS Customer Experience - EMEA is a mixed Assignment Group: Tickets with no Email Alias are dispatched from the Zone-wide On-Shift Pool, so a Spain Ticket can land on Türkiye GLS Support. We route those Tickets by LMS Country to a Sub-region, but we do **not** replace Email Alias routing — `training-fr@` and the other mailboxes are still live (partners, voicemails, and `@redhat.com` forwards often have no LMS record). LMS Country is the fallback after Subject-based routing and Email Alias.

## Considered Options

- **Replace Email Alias with LMS Country:** rejected. Recent EMEA Tickets still arrive via `training-fr@` (RingCentral voicemail, M2i, and similar) with zero LMS hits. The mailbox is the Sub-region; the sender often is not a Learner.
- **Always change the ServiceNow Assignment Group:** rejected. Iberia, France, and the rest of the mixed queue share GLS Customer Experience - EMEA's sys_id. Moving is a no-op unless we invent new groups. Only Türkiye GLS Support and IGC GLS Support own a distinct Assignment Group.
- **Hybrid (chosen):** LMS Country selects a Sub-region. Same Assignment Group → peek that Sub-region's On-Shift Pool this cycle (like Email Alias). Different Assignment Group → move the Ticket and stop (no ACK, no assignee, no Round-Robin turn on the source pool); the destination Auto-Assign dispatches. Nobody on that Sub-region's Shift → leave unassigned; do not fall back to the mixed pool.
- **Consensus across LMS records:** rejected. Auto-Assign uses the first LMS record's Country only. Later records are ignored, including when they disagree.
- **Country-route `@redhat.com` senders:** rejected. That is often the engineer forwarding mail, not the Learner. Internal Learners without an alias stay on the mixed pool.

## Consequences

- `SNOW_GROUPS_CONFIG` Sub-regions grow a `countries` list (ISO2, English names, known aliases). The mapper is data-generic; only the mixed EMEA worker runs it so Türkiye/IGC cannot loop.
- Collision winners in that list: Greece/Cyprus/Israel/Palestine → IGC GLS Support (move); Pakistan → MENA; Turkey/Türkiye → Türkiye GLS Support (move); Italy/Malta/San Marino/Vatican stay IT & IGC.
- A first LMS Country of `DE` with a later `ES` goes to Germany, not Iberia.
