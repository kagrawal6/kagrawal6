# Setup

This package is already configured for the GitHub account `kagrawal6`.

## 1. Create the profile repository

On GitHub, create a **public** repository named exactly:

```text
kagrawal6
```

GitHub recognizes a public repository whose name matches your username as your profile repository.

## 2. Add the package

Unzip this package and copy **all** of its contents into that repository. Keep the hidden `.github` folder and the folder structure intact:

```text
kagrawal6/
├── .github/workflows/update-profile.yml
├── assets/
│   └── languages.svg
├── scripts/generate_stats.py
├── profile-config.json
├── README.md
└── SETUP.md
```

If you prefer the command line:

```bash
git clone https://github.com/kagrawal6/kagrawal6.git
cd kagrawal6
# Copy the unzipped package contents here.
git add .
git commit -m "Add profile dashboard"
git push
```

## 3. Allow the workflow to update the graphics

In the repository, open:

```text
Settings → Actions → General → Workflow permissions
```

Select **Read and write permissions**, then save.

## 4. Generate the real statistics

Open:

```text
Actions → Update profile dashboard → Run workflow
```

The included graphic initially says `SYNC PENDING`. The first successful run replaces it with your real public-repository language statistics. The workflow then refreshes them every 12 hours.

## Optional: include private repositories

The default `GITHUB_TOKEN` can build the public dashboard without setup. To include private repositories in the language and source-line totals:

1. Create a fine-grained personal access token for your account.
2. Give it read access only to the repositories you want included, with **Contents: Read-only** and repository metadata access.
3. Add it under `Settings → Secrets and variables → Actions` as a repository secret named `PROFILE_TOKEN`.
4. Run the workflow again.

Never place a token directly in the repository.

## Customize the dashboard

Edit `profile-config.json` to change exclusions. To omit a repository from the language totals:

```json
"exclude_repositories": ["repo-name", "another-repo"]
```

Commit the change and rerun the workflow.

## What the numbers mean

- **Lines** and **language shares**: nonblank, non-comment source lines in the current tree of indexed owned repositories, after the exclusions in `profile-config.json`. Generated netlists and oversized HTML/CSS dumps are omitted.
- **Repos**: indexed owned repositories after fork, archive, and name exclusions, including private repositories when the token can read them.
- **Storage**: GitHub-reported git size of those repositories, including private repositories.

## Run it locally

Python 3.10+ and Git are the only requirements:

```bash
export GITHUB_TOKEN="your-token"
python scripts/generate_stats.py
```

Use `PROFILE_TOKEN` instead when you intentionally want private-repository access. To regenerate the clean placeholder asset without network access:

```bash
python scripts/generate_stats.py --placeholder
```
