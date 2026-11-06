---
name: ai-factory-workbench
description: Prepare evidence-bound AI governance, platform, and operations reviews with AI Factory Workbench.
---

# AI Factory Workbench

Use this skill when a proposed AI change, platform exception, or business action needs review in AI Factory.

1. Call `workbench_get_profile` to read the active workspace profile. Use its evidence keys and review guidance. The operator selects the profile.
2. Collect the required evidence from authorized sources. Ask the user for missing facts, and record references to available evidence. Keep secrets and unnecessary personal data out of the dossier.
3. Call `workbench_prepare_review` with a stable subject, a descriptive title, the reason for review, and evidence keyed by the active profile. For a supported reference CRM opportunity, use `workbench_propose_opportunity`.
4. Give the reviewer the case ID, revision, and proposal hash from the result. A human can decide in the Companion. An organization's configured team integration or internal service can relay a verified decision under the workspace approval policy. The MCP agent prepares dossiers and reads their status.
5. Use `workbench_get_case` to check for a recorded decision. If the revision or hash changed, have the reviewer examine the updated proposal. Report execution only after the case records it.
