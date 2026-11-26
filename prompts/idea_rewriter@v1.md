---
id: idea_rewriter
version: 1
purpose: Apply exactly one chosen change to the idea text to create the next version
output_schema: {new_text, change_note}
temperature: 0
---
## SYSTEM
Rewrite the idea applying exactly this change and nothing else. Keep length similar. change_note <= 15 words.
## USER
IDEA: {idea_text}
CHANGE: {change_text}
