# NPTEL Enrollment GitHub App

This is a separate enrollment-only dashboard. It does not use or change the existing live dashboard folder.

## What it does

- Reads course IDs from `courses.csv`
- Calls the NPTEL course preview API for each course
- Creates `summary.json` and `enrollment_report.csv`
- Shows a static dashboard in `index.html`
- Works as a mobile-friendly installable web app
- GitHub Actions can update the data automatically about every 5 minutes

## Local test

```powershell
cd "C:\Users\NPTEL031\Documents\Enrollment script\enrollment_github_app"
py -m pip install -r requirements.txt
py update_enrollment.py
py -m http.server 8080
```

Then open:

```text
http://localhost:8080/
```

## Put online with GitHub

1. Create a new GitHub repository.
2. Upload all files from this folder into that new repository.
3. Go to repository `Settings > Pages`.
4. Select `Deploy from a branch`.
5. Select branch `main` and folder `/root`.
6. Go to `Actions`.
7. Open `Update enrollment dashboard`.
8. Click `Run workflow`.

After Pages is enabled, the live link will look like:

```text
https://YOUR-GITHUB-USERNAME.github.io/YOUR-REPO-NAME/
```

The page refreshes display data every 10 seconds. The GitHub Action regenerates the data on GitHub about every 5 minutes, depending on GitHub schedule delay.

## Mobile use

Open the GitHub Pages link in Chrome or Safari. On Android, tap `Install` if shown. On iPhone, use `Share > Add to Home Screen`. It will open like a mobile app and still need internet for fresh counts.
