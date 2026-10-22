---
description: Implement a piece of work based on a spec or set of tickets.
disable-model-invocation: true
metadata:
    github-path: skills/engineering/implement
    github-pinned: 5beb35b040e3de97e54fa9e386d7e42d15c295c2
    github-ref: 5beb35b040e3de97e54fa9e386d7e42d15c295c2
    github-repo: https://github.com/mattpocock/skills
    github-tree-sha: 4804b466ab26c9b9d8d42917547db133d1f20a9f
name: implement
---
Implement the work described by the user in the spec or tickets.

If the user passes a ticket reference, fetch it from the issue tracker and state its title before starting. If the reference is ambiguous, ask.

Call the Skill tool with "tdd" where possible, at pre-agreed seams.

Run typechecking regularly, single test files regularly, and the full test suite once at the end.

Once done, call the Skill tool with "code-review" to review the work.

Commit your work to the current branch.
