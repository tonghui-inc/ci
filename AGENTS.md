# AGENTS.md — Agent Working Guidelines

## Comment Policy: No Comments in Any File

Comments are **strictly prohibited** in every file in this repository. When working in this repository, agents MUST follow these rules:

1. **Do not** add comments of any kind (`#`, `//`, `/* */`, `<!-- -->`, docstrings, etc.) to any file, regardless of language or format.
2. **Do not** add trailing (end-of-line) comments, including version annotations such as `# v3`.
3. Code and configuration must be self-explanatory through clear naming and structure.
4. Explanations belong in commit messages or documentation prose (e.g., this file's Markdown text), never in file comments.
5. When editing an existing file, remove any comments already present.

## Git History Policy: No History Retention

This repository **does not retain any git history**. When working in this repository, agents MUST follow these rules:

1. **Do not** integrate changes in ways that preserve old commits, such as `git merge` or `git rebase`.
2. **Do not** create backup branches or tags to archive old history.
3. **On every commit**, rewrite the entire history into a single commit whose message is exactly `Initial commit`. Never accumulate multiple commits, and never use any other commit message — each commit MUST replace all previous history (use the commands in the "Commands to Rewrite This Repository's History" section below).
4. Always push with `--force` to overwrite the remote history.
5. Never ask whether history should be preserved — always treat it as discardable.

## Commands to Rewrite This Repository's History

### Method 1: Orphan Branch (recommended, completely discards all history)

```bash
git checkout --orphan fresh-main

git add -A
git commit -m "Initial commit"

git branch -D main
git branch -m main

git push --force origin main
```

### Method 2: Soft Reset (keeps the current branch ref)

```bash
git reset --soft $(git rev-list --max-parents=0 HEAD)

git commit --amend -m "Initial commit"

git push --force origin main
```

### Method 3: Wipe and Rebuild (most thorough)

```bash
rm -rf .git

git init -b main
git add -A
git commit -m "Initial commit"

git remote add origin https://github.com/tonghui-inc/ci.git
git push --force origin main
```

### Cleanup: Remove Local History Residue

```bash
git reflog expire --expire=now --all
git gc --prune=now --aggressive

git branch | grep -v '^\*\s*main$' | xargs -r git branch -D
git tag | xargs -r git tag -d

git push origin --force --delete $(git branch -r | grep -v 'origin/main$' | sed 's|origin/||')
git ls-remote --tags origin | awk '{print $2}' | sed 's|refs/tags/||' | xargs -r -I{} git push origin --delete {}
```

## Notes

- History rewriting is a **destructive operation**. Old remote commits become permanently inaccessible, and all collaborators must re-clone afterwards.
- If the remote branch has protection rules (protected branch) enabled, temporarily disable them in the GitHub settings before force-pushing.
