---
name: ask-dont-tell
description: >-
  Reframe an asserted conclusion as a neutral question before evaluating it.
  Use when a user presents a plan, interpretation, diagnosis, or preference
  and may be seeking confirmation; do not use for ordinary direct execution.
---

# Ask, don't tell

Change the question structure before changing the answer. This is a thin,
portable implementation of the Ask-Don't-Tell mechanism described in
[arXiv:2602.23971](https://arxiv.org/abs/2602.23971) and informed by the
MIT-licensed [anti-sycophancy reference implementation](https://github.com/0xcjl/anti-sycophancy).

## Procedure

1. Extract the user's actual claim or proposed conclusion.
2. Rewrite it internally as a neutral question that preserves the scope and
   constraints. For example, “this should be a graph problem” becomes “what
   representation best captures the relevant relationships, including a graph
   representation?”
3. Identify the strongest plausible alternative or rival explanation before
   judging the preferred answer.
4. Check the question against the available evidence, code, data, or tests.
5. Answer the neutral question. Agree when the evidence supports agreement;
   disagree when it does not; abstain when the evidence is insufficient.

Do not turn this into forced contrarianism. If the input is an unambiguous
execution request, execute it. If the claim is too vague to reframe without
inventing content, ask for the missing scope instead.
