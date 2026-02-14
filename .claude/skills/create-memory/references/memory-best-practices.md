# Claude Code Memory Best Practices

Source: https://code.claude.com/docs/en/memory

## File Hierarchy

| Priority | Location             | Scope                      |
| -------- | -------------------- | -------------------------- |
| High     | `.claude/CLAUDE.md`  | Team-shared rules          |
| Medium   | `.claude/rules/*.md` | Topic-specific rules       |
| Low      | `CLAUDE.local.md`    | Personal (auto-gitignored) |

## CLAUDE.md Format

```markdown
# Project Name

## Commands

- Build: `pnpm build`
- Test: `pnpm test`

## Architecture

@docs/architecture.md

## Rules

@.claude/rules/
```

## Rules File Format

```markdown
---
paths: 'glob-pattern'
---

# Topic Name

- **MUST**: Specific rule
- **SHOULD**: Recommendation
- **NEVER**: Prohibition
```

## Glob Patterns

| Pattern                     | Matches              |
| --------------------------- | -------------------- |
| `**/*.ts`                   | All TS files         |
| `src/**/*.tsx`              | TSX in src/          |
| `**/{hooks,_hooks}/**/*.ts` | Hooks folders        |
| `{src,lib}/**/*.ts`         | Multiple directories |

## Writing Rules

### DO

- Be specific: "2-space indentation" not "format properly"
- Use bullet points
- Use MUST/SHOULD/NEVER for clarity
- One topic per file
- Minimal code examples (one is enough)

### DON'T

- Vague instructions ("follow best practices")
- Multiple topics in one file
- Verbose examples
- Mixing team/personal preferences in CLAUDE.md

## Import Syntax

```markdown
# Import files

@docs/architecture.md
@~/.claude/my-instructions.md

# Import in text

See @README for overview
```

- Max depth: 5 hops
- Not evaluated in code blocks
