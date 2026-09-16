# Suggestions still prepare a Bug; the Response is thanks, not a defect ack

A Suggestion is Feedback that is an improvement idea or praise, not a report of
something broken. We still open Jira search and a prefilled Bug create dialog
(ADR-0003: the model is not the gate). The Learner-facing Response uses
`acknowledge_suggestion`: thank them, say the idea or praise was received and
will be considered, do not ask for more information, and do not say a Defect
was found. We chose this over skipping Jira or using an Improvement issue type
because PTL filing stays a Bug the LX Engineer can submit or discard, and the
wrong Response (`ask_more`) was the actual failure on tickets such as
RHT0010800.

## Considered Options

- **Skip Jira for Suggestions**: fewer tabs. Rejected: the engineer still
  files many of these as PTL Bugs, and ADR-0003 already decided the dialog is
  a draft, not a verdict.
- **Create as Improvement / Story**: matches Jira semantics. Rejected: the
  create-dialog automation is built around Bug, and the user chose to keep
  that type.
- **Bug dialog + thanks Response** (chosen): same Selenium path as ordinary
  Feedback; Response mode is what changes.

## Consequences

- Mixed Feedback (something broken *and* an idea) is not a Suggestion — the
  broken thing wins.
- Unclear Feedback is not a Suggestion (stay `ask_more`).
- Content and video analysis may still set `acknowledge_suggestion` if the
  classifier misses (e.g. “please add a quiz” labelled as content).
