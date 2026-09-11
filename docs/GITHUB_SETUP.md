# Uploading this project to GitHub

## First-time setup from PyCharm Terminal

1. Create a new empty repository on GitHub. Do not initialise it with a README, `.gitignore`, or licence because this project already contains repository files.
2. Open this project folder in PyCharm.
3. Open **Terminal** in PyCharm.
4. Run:

```bash
git init
git add .
git status
git commit -m "Initial portfolio analytics project"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/YOUR-REPOSITORY.git
git push -u origin main
```

Replace `YOUR-USERNAME` and `YOUR-REPOSITORY` with your own values.

## Normal updates afterwards

```bash
git status
git add .
git commit -m "Describe what changed"
git push
```

Always check `git status` before committing so you do not accidentally upload portfolio data, secrets, or local environment files.
