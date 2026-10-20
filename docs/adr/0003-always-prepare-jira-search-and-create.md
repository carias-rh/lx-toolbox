# Always prepare Jira search and create dialog for ordinary Feedback

For every Feedback except SSH Lab Access and Resolution Follow-up, snowai now opens
a Jira deduplication search tab and a prefilled create dialog, regardless of what the
LLM says about `reply_mode`. We chose this over the previous gate
(`confirm_defect` only) because with full LLM inference the model's classification is
evidence, not a verdict — the LX Engineer must be able to file a Defect even when the
model says teach, explain_expected, ask_more, or lab_pending.

## Considered Options

- **reply_mode == confirm_defect gate** (previous): no Jira tabs unless the LLM
  confirmed a Defect. Kept latency low when Ollama was slow. The model's verdict was
  final — the engineer had no Selenium path to override it.
- **Always prepare, skip only SSH Lab Access and Resolution Follow-up** (chosen):
  SSH Lab Access is structurally never a Defect; a Resolution Follow-up has nothing
  left to file. Every other Feedback could legitimately produce a Defect, so the
  engineer gets the dialog every time and decides.

## Consequences

- Opening two extra tabs (search + create) adds a few seconds per ticket even on
  teach/explain_expected; acceptable given that inference is now fast.
- The dialog is still a draft until submitted. The Response to the Learner is still
  governed by `reply_mode`; opening the dialog does not change what is communicated
  to them.
- `skip_jira` in the orchestration loop now only fires on
  `is_ssh_lab_access_ticket` and `reply_mode == acknowledge_resolved`.
