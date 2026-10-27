---
name: colleague-message
description: >
  Drafts messages to colleagues (Slack, Teams, email, tickets, DevOps or helpdesk requests, PR comments) in the user's own voice, from a style file that learns from every draft the user approves or edits. Use whenever the user asks to write, reply to, ping or ask someone, request access, follow up on a stuck ticket, or says "draft a message", "what should I write to X". Drafts only; never sends.
argument-hint: "<who> <what you need>"
---

# colleague-message - your voice, learned from your edits

Style file: `~/.claude/autolearn/style/messages.md` (rules, approved examples, anti-examples).
Request: `$ARGUMENTS`.

## Draft
1. Read the style file. Then look at how the user writes in this conversation (length,
   greeting, language, punctuation): the conversation beats the file when they differ.
2. Default rules (the style file overrides them):
   - A greeting, then straight to the point. One or two short sentences.
   - A link or ticket key when the recipient needs it to act.
   - Do not explain your plans, readiness or deadlines unless the recipient needs them to do
     something. Do not rush people; the message itself is the ping.
   - End with a neutral question that is easy to answer in one line.
   - Access requests: context in one phrase (task, who said so, what it is for), what you
     already have and what you lack, the exact username or resource, a question with a ready
     option ("Is a ticket the right way to request this?").
3. Write the draft in the recipient's language. Show it in a code block, nothing else around it
   except at most one line of "why it is phrased this way" if a choice was non-obvious.

## Learn
- The user **edits** the draft or says what to change -> after they are happy, add the pair to
  the style file under "Edits" as `draft -> final` and distill one rule if the edit shows a
  pattern ("drop the second sentence", "no exclamation marks to leads").
- The user **approves** ("good style", "sent it as is") -> add the final text under "Approved
  examples" with one line on what makes it good. Keep at most 10 examples; replace the oldest
  similar one.
- The user rejects a phrasing -> "Anti-examples" with the reason.
- Commit `~/.claude/autolearn` if it is a git repo: `style: <rule or example>`.

## Never
- Send, post or schedule a message. The user sends.
- Put secrets, passwords or personal data of third parties into a draft.
