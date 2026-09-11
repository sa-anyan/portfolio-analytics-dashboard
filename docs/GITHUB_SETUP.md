# GitHub Setup

From the project folder:

```bash
git init
git status
git add .
git status
git commit -m "Initial portfolio analytics project"
git branch -M main
git remote add origin https://github.com/YOUR-USERNAME/portfolio-analytics.git
git push -u origin main
```

Always review `git status` before committing. Do not commit brokerage exports, credentials, `.venv`, or `.streamlit/secrets.toml`.
