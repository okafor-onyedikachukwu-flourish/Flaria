# Deploy Flaria for free on Render

What you end up with, at $0 and with no domain to buy:

| Part | Where | Address | Notes |
|---|---|---|---|
| Landing page | Render static site | `https://flaria.onrender.com` | Never sleeps. This is the page Google indexes. |
| App (sign-in, sessions, admin) | Render free web service | `https://flaria-app.onrender.com` | Sleeps after 15 minutes without visitors and takes about a minute to wake. |
| Database | Neon free plan | private | Permanent, 0.5 GB. Render's own free database is deleted after 30 days. |
| Email (password resets) | Brevo free plan | – | Render's free plan blocks normal email ports, so Flaria sends through Brevo's web API. |

The app runs in **demo mode**: until Flaria has a trained, clinically validated model, results are labelled as demonstrations and never give health advice. Warning symptoms that a person reports themselves still produce full emergency advice.

Plan for about an hour the first time. Most of it is waiting for the first build.

---

## 1. Put the code on GitHub

1. Create a free account at github.com if you don't have one.
2. Click **New repository**, name it `flaria`, and keep it **Public** or **Private** (both work with Render).
3. On your computer, unzip `flaria.zip` and send the `flaria` folder to GitHub. It has about 145 files, more than GitHub's browser upload accepts at once (100), so use one of these:
   - **GitHub Desktop** (desktop.github.com, no terminal needed): **File → Add local repository**, choose the unzipped `flaria` folder, accept **create a repository** if asked, commit everything with a message like "Flaria", then click **Publish repository**. It creates the `flaria` repository for you, so you can skip step 2.
   - **Terminal**, from inside the `flaria` folder:
     ```bash
     git init && git add . && git commit -m "Flaria"
     git branch -M main
     git remote add origin https://github.com/YOUR-USERNAME/flaria.git
     git push -u origin main
     ```
4. Check that `render.yaml` and `Dockerfile` appear at the top level of the repository.

## 2. Create the database on Neon

1. Sign up at neon.com (no credit card).
2. Create a project named `flaria`, Postgres version 16 or 17, region **AWS Europe Central 1 (Frankfurt)**, the same region as the app.
3. On the project dashboard, click **Connect** and copy the connection string. It looks like:
   `postgresql://neondb_owner:...@ep-something-123456.eu-central-1.aws.neon.tech/neondb?sslmode=require&channel_binding=require`

   Either the pooled string (with `-pooler` in it) or the direct one works. Keep it private: it contains the database password.

## 3. Set up email on Brevo

1. Sign up at brevo.com (free plan).
2. Verify the address emails will come from: **Settings → Senders, Domains & Dedicated IPs → Senders → Add a sender**. Use an inbox you control, such as your Gmail address, and click the confirmation link Brevo sends you.
3. Create an API key: **Settings → SMTP & API → API Keys → Generate a new API key**. Name it `flaria` and copy it. It's shown only once.

Without your own domain, some providers may file Flaria's emails under spam. Users should check their spam folder, and buying a domain later fixes this.

## 4. Create everything on Render with the Blueprint

1. Sign up at render.com with your GitHub account.
2. Click **New → Blueprint**, pick your `flaria` repository and confirm. Render reads `render.yaml` and plans two services: `flaria` (static site) and `flaria-app` (web service).
3. Render asks for the values marked as secret. Fill them in:

| Setting | Value |
|---|---|
| `FLARIA_DATABASE_URL` | The Neon connection string from step 2 |
| `FLARIA_BREVO_API_KEY` | The Brevo API key from step 3 |
| `FLARIA_MAIL_FROM` | `Flaria <your-verified-address@gmail.com>`, using the exact sender you verified |
| `FLARIA_BOOTSTRAP_ADMIN_EMAIL` | `admin123@gmail.com`, or whichever inbox should own the admin account |
| `FLARIA_BOOTSTRAP_ADMIN_NAME` | `ADMIN_FLOURISH` |
| `FLARIA_SAME_AS` | Optional for now: your profile links, comma-separated, e.g. `https://github.com/you,https://www.linkedin.com/in/you` |
| `FLARIA_GOOGLE_SITE_VERIFICATION` | Leave empty for now (step 7 fills it in) |

4. Click **Apply**. The static site builds in a few minutes. The app's first Docker build takes roughly 10–15 minutes.

### If Render gives a service a different address

Service addresses are first come, first served. If `flaria` or `flaria-app` is already taken, Render adds a suffix, for example `flaria-app-k3x9.onrender.com`. Each service's page shows its real address. If either one differs, edit `render.yaml` in your repository:

- the app's address goes in `VITE_APP_URL` and in the `routes` destination, both on the `flaria` static site;
- the landing page's address goes in `VITE_SITE_URL` on **both** services.

Commit the change. Render redeploys both services automatically.

## 5. Sign in as the admin

1. When `flaria-app` shows **Live**, check the inbox from `FLARIA_BOOTSTRAP_ADMIN_EMAIL`, including spam, for **"Set up your Flaria admin account"**.
2. Open the link and choose a password. The link works once and expires in 30 minutes. If it expired, use **Forgot your password?** on the sign-in page.
3. Sign in at `https://flaria-app.onrender.com/login`. Admins land in the admin console.

The setup email is sent only while no admin exists, so restarts don't send it again.

## 6. Check the landing page

Open `https://flaria.onrender.com`. You should see the landing page, and **Sign in** should take you to the app. Check these too:

- `https://flaria.onrender.com/sitemap.xml` lists the landing page;
- `https://flaria.onrender.com/robots.txt` points to the sitemap;
- pasting the address into WhatsApp or LinkedIn shows the Flaria share card.

## 7. Tell Google and Bing about the site

1. Go to search.google.com/search-console, choose **Add property → URL prefix**, and enter `https://flaria.onrender.com/`.
2. Choose the **HTML tag** method. Copy only the `content="..."` value, for example `AbC123xyz...`.
3. On Render, open the `flaria` static site, go to **Environment**, set `FLARIA_GOOGLE_SITE_VERIFICATION` to that value, and click **Save, rebuild and deploy**.
4. When the deploy finishes, click **Verify** in Search Console.
5. In Search Console, open **Sitemaps** and submit `sitemap.xml`. Then use **URL inspection** on `https://flaria.onrender.com/` and click **Request indexing**.
6. At bing.com/webmasters, use **Import from Google Search Console**. Bing also powers DuckDuckGo and Yahoo results.

Indexing usually takes days to a few weeks. Search Console's **Pages** report shows when the landing page is indexed.

## 8. Link to Flaria from your own profiles

Search engines learn which site "Flaria" means from links. Add a link to `https://flaria.onrender.com`, with the text "Flaria", to:

- your portfolio site (a project card with a short description);
- the GitHub repository (set the repository **Website** field and add the link to the README);
- your LinkedIn profile (Featured section or a project entry).

Then put those profile addresses in `FLARIA_SAME_AS` on the static site and redeploy, so the structured data connects them.

## Research mode settings

Research mode is on in `render.yaml`. On the `flaria-app` service, open **Environment** and fill in:

| Setting | Value |
|---|---|
| `FLARIA_STUDY_INVESTIGATOR` | Your name as lead investigator |
| `FLARIA_STUDY_INSTITUTION` | Your university or organisation |
| `FLARIA_STUDY_CONTACT_EMAIL` | The email participants should contact |
| `FLARIA_STUDY_ETHICS_REFERENCE` | Leave empty until an ethics committee approves the study, then enter its approval number |

Click **Save, rebuild and deploy**. Participants see these on the consent page, and every change asks them to consent again.

## Staying within the free plans

- **Render:** a free web service sleeps when idle, so the first visit afterwards takes about a minute. The landing page is unaffected. Free instance hours (750 a month) cover one always-busy service.
- **Neon:** 0.5 GB of storage. Telemetry is stored at one row per second of session, roughly 2 MB per hour of sessions, so a demo stays far below the limit. The admin console's **System** page shows the database size.
- **Brevo:** the free plan has a daily sending limit, far more than password resets need.

## Troubleshooting

| What you see | What to do |
|---|---|
| Static site build fails with `VITE_SITE_URL must be set` | Set `VITE_SITE_URL` on the static site to its address (step 4). |
| App deploy fails with a database error | Re-copy the Neon string; make sure it starts with `postgresql://` and the Neon project isn't deleted. |
| App deploy fails mentioning `FLARIA_BREVO_API_KEY` or `FLARIA_MAIL_FROM` | Fill both in under the app's **Environment** tab. |
| No setup email | Check spam. In Brevo, check **Transactional → Logs**. Make sure `FLARIA_MAIL_FROM` uses the verified sender. You can always use **Forgot your password?** with the admin address. |
| A reset link opens slowly | The app was asleep. Wait a minute; the link still works for 30 minutes. |
| Camera works but no face is found | Allow camera access and use good light. On slow devices, tracking runs slower and the screen says so. |
