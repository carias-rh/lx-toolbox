# LX Toolbox

Automation tools for the Learner Experience (LX) team to triage learner feedback, manage lab environments, run course QA, and dispatch ServiceNow tickets across support teams.

## Language

### People

**Learner**:
A person taking a Red Hat training course on a Production Platform.
_Avoid_: Student, customer, user

**LX Engineer**:
A member of the Learner Experience team who triages Feedback, investigates issues, creates Defects, and responds to Learners.
_Avoid_: Operator, support agent

### Tickets

**Feedback**:
A ServiceNow ticket submitted by a Learner reporting a problem or observation about a course. Processed by the LX team (T2).
_Avoid_: Ticket (ambiguous), case, incident

**Support Request**:
A ServiceNow ticket for account, subscription, or exam issues. Processed by the TTS team (T1).
_Avoid_: Ticket (ambiguous)

**Defect**:
A Jira ticket in the PTL project representing a confirmed issue in course content, lab scripts, or video. Component = Course Code.
_Avoid_: Bug, Jira ticket, issue (overloaded)

**Response**:
A reply sent to a Learner through ServiceNow. Can close the Feedback, request clarification (Pending Customer), or acknowledge that investigation is underway.

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

**Workstation**:
The primary VM in a Lab. The Learner's entry point; all Lab Scripts are run from here. Connects via SSH to other VMs (servera, serverb, utility, etc.).

**Platform**:
The deployment where courses are hosted and Labs are provisioned: ROL, Factory, or China.

**Production Platform**:
A Platform used by Learners: ROL or China. Issues are always reproduced on the same Production Platform the Learner reported from.

**Development Platform**:
Factory. Used by curriculum developers (content authoring) and LX engineers (QA testing). Not accessible to Learners.
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
- OpenShift courses (`do` family) have ~40 min first-boot time; complaints about slow startup in early chapters are usually expected behavior.
