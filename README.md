# fresh-eyes-only-action v1

## What it does

This GitHub Action **dismisses stale approvals** on a Pull Request when new commits are pushed.
It’s designed to ensure that reviewers always approve the latest code changes, preventing merges based on outdated reviews.

It can run automatically when a PR is updated, or be triggered manually with a `workflow_dispatch` or `repository_dispatch` event.

In situations when the code does not change, approvals should be preserved. Examples include:

* Simple rebase (For example: rebase a branch into latest `main`)
* Squash commits

Both rebase+squash at the same time are not supported. What you can do: 
* squash -> push -> rebase -> push
* rebase -> push -> squash -> push

## When to use it

Use this action if you want to:

* Automatically clear outdated approvals after a PR changes.
* Require reviewers to re-approve after each new commit.
* Keep your review process strict and up to date.

## Supported triggers

### 1. **PR synchronize**

Run this action on a `pull_request` event with type `synchronize` to automatically dismiss approvals when new commits are pushed.

```yaml
on:
  pull_request:
    types: [synchronize]
```

### 2. **Manual trigger**

Run this action manually with `workflow_dispatch` by providing the required inputs.

```yaml
on:
  workflow_dispatch:
    inputs:
      pr-number:
        description: "The Pull Request number"
        required: true
      repository:
        description: "owner/repo containing the PR"
        required: true
      base-ref:
        description: "Base branch name of the PR (e.g., main)"
        required: true
      actor:
        description: "GitHub username who triggered the process"
        required: true
      before-sha:
        description: "Commit SHA before the push"
        required: true
      after-sha:
        description: "Commit SHA after the push"
        required: true
```

## Required inputs for `workflow_dispatch`

| Field        | Type    | Description                                            |
| ------------ | ------- | ------------------------------------------------------ |
| `pr-number`  | integer | Pull Request number to target.                         |
| `repository` | string  | Full `owner/name` of the repository containing the PR. |
| `base-ref`   | string  | Base branch name for the PR (e.g., `main`).            |
| `actor`      | string  | GitHub username that triggered the process.            |
| `before-sha` | string  | Commit SHA before the push.                            |
| `after-sha`  | string  | Commit SHA after the push.                             |

## Example: PR synchronize usage

```yaml
name: Fresh Eyes Only (PR synchronize)

on:
  pull_request:
    types: [synchronize]

jobs:
  fresh-eyes:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: your-org/fresh-eyes-only-action@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
```

## Example: Manual trigger usage

```yaml
name: Fresh Eyes Only (manual)

on:
  workflow_dispatch:
    inputs:
      pr-number:
        description: "The Pull Request number"
        required: true
      repository:
        description: "owner/repo containing the PR"
        required: true
      base-ref:
        description: "Base branch name of the PR (e.g., main)"
        required: true
      actor:
        description: "GitHub username who triggered the process"
        required: true
      before-sha:
        description: "Commit SHA before the push"
        required: true
      after-sha:
        description: "Commit SHA after the push"
        required: true

jobs:
  fresh-eyes:
    runs-on: ubuntu-latest
    permissions:
      pull-requests: write
      contents: read
    steps:
      - uses: your-org/fresh-eyes-only-action@v1
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          pr-number: ${{ github.event.inputs.pr-number }}
          repository: ${{ github.event.inputs.repository }}
          base-ref: ${{ github.event.inputs.base-ref }}
          actor: ${{ github.event.inputs.actor }}
          before-sha: ${{ github.event.inputs.before-sha }}
          after-sha: ${{ github.event.inputs.after-sha }}
```
