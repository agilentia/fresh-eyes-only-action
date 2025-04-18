# fresh-eyes-only-action v1

# Usage
<!-- start usage -->
```yaml
name: Dismiss Stale Approvals

on:
  pull_request:
    types: [synchronize] 

permissions:
  pull-requests: write 
  contents: read      

jobs:
  dismiss_approvals:
    name: Dismiss Stale PR Approvals
    runs-on: ubuntu-latest
    steps:
      - name: Dismiss approvals on change
        uses: agilentia/fresh-eyes-only-action@main
        with:
          github-token: ${{ secrets.GITHUB_TOKEN }}
          pr-number: ${{ github.event.pull_request.number }}

          # Optional: Comma-separated list of GitHub logins to ignore.
          # Pushes from these users will NOT trigger dismissals. Case-insensitive.
          # exempt-actors: 'github-actions[bot],dependabot[bot],my-service-account'

          # Optional: Custom message for the dismissal notification.
          # dismissal-message: 'Hold up! Code changed, please re-approve.'

          # Optional: Silence informational logs (only show warnings/errors). Default is false.
          # quiet: true
```
<!-- end usage -->
