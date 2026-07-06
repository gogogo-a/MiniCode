---
name: code-review
description: Review code for bugs, regressions, missing tests, and concrete risks.
---

# Code Review Skill

When the user asks for a code review, load this skill first and use a review-first response.

Rules:
- Findings come first.
- Sort findings by severity.
- Include file and line references when reviewing real files.
- Focus on bugs, regressions, missing tests, security issues, and behavior risks.
- Do not lead with praise or a broad summary.
- If there are no findings, say that clearly and mention residual risk or test gaps.

Output shape:

**Findings**
- Severity: file:line - concrete issue and impact.

**Questions**
- Any blocking ambiguity.

**Summary**
- Short change summary only after findings.
