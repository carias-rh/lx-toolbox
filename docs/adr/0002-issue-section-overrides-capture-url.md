# Issue Section from Learner text overrides the Capture URL page

The Feedback form stamps the page the Learner was on, which is often preface (`pr01`) or a different Section than the complaint. We infer the Issue Section from the original description plus Learner Follow-up text (not the Title) with an LLM, only when that text looks like it names a Section. Formats vary: `8.8`, chapter 8 section 8, `ch08s08`, 演習8.8. Guide and Lab open there; Chapter, Section, and the investigation URL are updated. Course ID, Version, and Platform stay on the Capture URL. If the inferred page does not load, fall back to the Capture URL page. We chose this over always trusting the stamped URL because that fetches the wrong Guide Text (RHT0006759: `pr01` intro vs exercise 8.8).

## Considered Options

- **Capture URL always wins**: simpler, matches the ticket field. Rejected: Guide/Lab land on the wrong page whenever the Learner opened Feedback from elsewhere.
- **Regex only** (`N.M` → `chNNsMM`): no extra LLM call. Rejected: Learners write many equivalent forms, including other languages.
- **LLM on every ticket**: catches every mismatch. Rejected: extra call when the text never names a Section.
- **Heuristic then LLM, Course ID frozen** (chosen): skip the LLM unless the text looks section-like; do not invent a course; if unclear or two primaries, keep the Capture URL page.
