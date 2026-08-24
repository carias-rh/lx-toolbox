# LX Toolbox

Automation tools for the Learner Experience (LX) team to triage learner feedback, manage lab environments, run course QA, and dispatch ServiceNow tickets across support teams.

## Language

### People

**Learner**:
A person taking a Red Hat training course on a Production Platform.
_Avoid_: Student, customer, user

**Internal Learner**:
A Red Hat employee taking a course on ROLE. A kind of Learner; not partners or contractors.
_Avoid_: Employee, student, user

**LX Engineer**:
A member of the Learner Experience team who triages Feedback, investigates issues, creates Defects, and responds to Learners.
_Avoid_: Operator, support agent

### Tickets

**Feedback**:
A ServiceNow ticket submitted by a Learner reporting a problem or observation about a course. Processed by the LX team (T2).
_Avoid_: Ticket (ambiguous), case, incident

**Capture URL**:
The Guide page URL stamped into the Feedback when the Learner opened the form. Not necessarily the Issue Section.
_Avoid_: Ticket URL (ambiguous with ServiceNow)

**Issue Section**:
The Section the Learner is complaining about. Named in the Feedback text in many forms (8.8, chapter 8 section 8, ch08s08, 演習8.8).

**Support Request**:
A ServiceNow ticket for account, subscription, or exam issues. Processed by the TTS team (T1).
_Avoid_: Ticket (ambiguous)

**Defect**:
A Jira ticket in the PTL project representing a confirmed issue in course content, lab scripts, or video. Component = Course Code.
_Avoid_: Bug, Jira ticket, issue (overloaded)

**Response**:
A reply sent to a Learner through ServiceNow. Can close the Feedback, request clarification (Pending Customer), acknowledge that investigation is underway, or briefly acknowledge a Resolution Follow-up.

**Learner Follow-up**:
A portal comment or inbound email from the Learner on a Feedback after the original description. Not a Response.
_Avoid_: Customer update, journal entry, additional comments

**Resolution Follow-up**:
A Learner Follow-up that states the reported problem is gone or was not a course issue. Not a retraction of a remaining Defect claim, and not a workaround while the Learner still wants a fix.
_Avoid_: Customer acknowledgement, resolved ticket (ambiguous with ServiceNow state)

**SSH Lab Access Feedback**:
Feedback from an Internal Learner about connecting to a ROLE Lab via SSH (private key setup, jump host, permissions). Never a Defect. SSH Lab Access exists on ROLE and Factory; Feedback of this type only arrives from ROLE.

### Course structure

**Course Code**:
The prefix identifying a course without its version (e.g. `do180`, `rh124`).
_Avoid_: Course name, course prefix

**Course Version**:
The numeric version of a course (e.g. `4.18`, `9.3`).

**Course ID**:
The fully-qualified identifier: Course Code + Course Version (e.g. `do180-4.18`).
_Avoid_: Course name, slug

**Course Family**:
A grouping of courses by prefix that share infrastructure and operational heuristics. Families: `rh` (RHEL), `do` (OpenShift), `au` (Ansible), `cl` (OpenStack), `ad` (advanced developer), `ai` (AI).

**Guide**:
The entire written content of a course as delivered to the Learner on the platform.
_Avoid_: Courseware, textbook, ebook

**Guide Text**:
The content of a specific Section within the Guide — what the Learner reads on a given page.

**Section**:
A structural unit of a course, identified by `chXXsYY` (e.g. `ch02s07`). Every Section has a Section Type.

### Section types

**Theory Section**:
Instructional text with explanatory examples. Always odd-numbered. Commands shown are illustrative — not meant to be run in the Lab.
_Avoid_: Lecture

**Guided Exercise (GE)**:
A step-by-step exercise the Learner follows in the Lab. Always even-numbered. Structure: Outcomes → Instructions (with per-step Show Solution) → Finish.

**Lab Exercise**:
A hands-on assessment where the Learner achieves goals without step-by-step guidance. Odd-numbered, appears after the last GE in a chapter. Structure: Outcomes → Instructions (with per-step Show Solution) → Evaluation → Finish.

**Quiz**:
A knowledge-check section with questions, Check button, and Show Solution. Even-numbered, same slot as a GE.

**Summary**:
A chapter recap. Even-numbered, last section in a chapter.

**Comprehensive Review**:
A complex Lab Exercise spanning multiple topics, forming the entire final chapter of a course. Structure: Outcomes → Specifications (goal-oriented, with Show Solution hints) → Evaluation → Finish.

### Exercise anatomy

**Instructions**:
Step-by-step directions within a GE or Lab Exercise that the Learner follows. Prescriptive.
_Avoid_: Steps, tasks

**Specifications**:
Goal-oriented requirements within a Comprehensive Review. The Learner determines the steps themselves.
_Avoid_: Instructions (those are step-by-step)

**Show Solution**:
A collapsible block inside Instructions or Specifications revealing the correct approach. One per step (GE/Lab) or one overall with hints (newer CRs). Old courses may still have detailed per-step solutions in CRs.

**Evaluation**:
The `lab grade <exercise>` Lab Script action that checks the Learner's work. Present in Lab Exercises and Comprehensive Reviews, absent from GEs.

**Finish**:
The `lab finish <exercise>` Lab Script action that cleans up exercise resources.

### Lab and platform

**Lab**:
The set of VMs provisioned by the Platform for a Learner to perform exercises. Managed via Start Lab (provisioning) and Lab Scripts (exercise-level actions).
_Avoid_: Environment (ambiguous), sandbox

**Start Lab**:
Provisioning and powering on the Lab VMs from the Platform panel.
_Avoid_: Lab start (ambiguous with Lab Script)

**Lab Script**:
A command run inside the Workstation VM: `lab start <exercise>`, `lab grade <exercise>`, or `lab finish <exercise>`. Initializes, evaluates, or cleans up a specific exercise.
_Avoid_: Lab command

**First Boot**:
The first time a do/ai-family Lab brings up its OpenShift cluster after Start Lab / `lab start`. Lab Scripts run an OpenShift Cluster Readiness Check (waiting on operators such as authentication, kube-apiserver, network) and may take about 30–40 minutes before the Lab is usable. Expected platform warm-up, not a Defect by itself.
_Avoid_: Slow lab (ambiguous), certificate error, git clone failure, SSL error

**Workstation**:
The primary VM in a Lab. The Learner's entry point; all Lab Scripts are run from here. Connects via SSH to other VMs (servera, serverb, utility, etc.).

**Platform**:
The deployment where courses are hosted and Labs are provisioned: ROL, ROLE, Factory, or China.

**Production Platform**:
A Platform used by Learners: ROL, ROLE, or China. Issues are always reproduced on the same Production Platform the Learner reported from.

**ROLE**:
Production Platform for Internal Learners. Hosted at `role.rhu.redhat.com`. Internal Learners report ordinary Guide, Lab, and video Feedback as well as SSH Lab Access Feedback. Only SSH Lab Access Feedback is investigated on ROLE itself; other types are reproduced on ROL.

**SSH Lab Access**:
How Internal Learners reach a ROLE Lab: download an SSH private key from the Lab page, then SSH from their local machine through a jump host (`cloud-user@<ip>:22022`) as `student@workstation`. The jump host IP changes per Lab.
_Avoid_: Employee SSH, remote SSH, ROLE SSH

**Development Platform**:
Factory. Used by curriculum developers (content authoring) and LX engineers (QA testing). Not accessible to Learners. SSH Lab Access exists here but does not generate Feedback.
_Avoid_: Staging

### Operations

**QA Run**:
An execution of the QA automation against a Course ID, running through all exercises and recording results.

**QA Report**:
The artifact produced by a QA Run: pass/fail data, screenshots, AsciiDoc/CSV reports.

**Auto-Assign**:
A service (deployed on OpenShift) that dispatches incoming ServiceNow tickets to engineers across teams (LX, TTS, CX) based on team configuration and shift schedules.

**Shift**:
The time window during which a specific engineer handles incoming tickets. External concept — managed by team frontends, consumed by Auto-Assign.

## Domain rules

- Guide Text is the source of truth; video content that disagrees is a Defect in the video.
- Theory Section commands are examples — complaints about them failing in the Lab are Learner confusion, not Defects.
- Only GE and Lab Exercise sections have predictable, runnable outcomes in the Lab.
- Many Feedbacks can map to one Defect (deduplication). One Feedback can produce multiple Defects.
- A "missing information" complaint is often valid when Specifications lack detail only found in Show Solution.
- do/ai-family Labs often need ~30–40 minutes on First Boot (Cluster Readiness Check / operators still coming up); complaints that match that startup pattern are usually expected behavior.
- Git clone, TLS/SSL certificate, and application errors inside a running workbench are not First Boot — do not treat them as cluster warm-up.
- SSH Lab Access Feedback is never a Defect. Classify it from the Feedback text: the Internal Learner is stuck on SSH Lab Access (private key, jump host, `rht_classroom.rsa`, `cloud-user`, `DOWNLOAD SSH KEY`, `ssh -J`). A ROLE URL alone is not enough — Internal Learners also report ordinary Guide and Lab issues. Those stay content, environment, or video Feedback and are investigated on ROL. SSH Lab Access Feedback is investigated on ROLE in the Lab Environment tab.
- Course ID, Version, and Platform come from the Capture URL and do not change.
- Guide and Lab investigation use the Issue Section. A Learner "N.M" (or equivalent wording) means chapter N section M (`chNNsMM`, zero-padded). If the text does not name a Section, or names more than one with no clear primary, use the Capture URL page.
- Issue Section is inferred from the original description plus Learner Follow-up text, not from the Feedback Title. A Follow-up that names a Section is the Issue Section (it answers "which Section?").
- Inference runs only when that text looks like it names a Section; an LLM then returns the Issue Section or no change. Course ID stays frozen.
- When the Issue Section differs from the Capture URL page, Chapter, Section, and the investigation URL path are updated to that Section on the same Course ID.
- If the inferred Issue Section page does not load, investigation falls back to the Capture URL page.
- A Resolution Follow-up gets a brief Response: thank the Learner, confirm we are glad it is resolved, invite them to reach out if anything else comes up. Do not recap their diagnosis, re-explain the cause, or give unsolicited advice. This is not explain-expected behaviour.
- Whether a Learner Follow-up is a Resolution Follow-up is judged from the latest Learner Follow-up, not from earlier ones. An older "it is fixed" does not override a newer report that the problem is back.
- A latest Learner Follow-up that both resolves the original report and raises a new problem is not a Resolution Follow-up; investigate the remaining claim.
