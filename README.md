# Patch Pulse

Pick any Android app, optionally a competitor, and see how users feel about crashes, speed, pricing, ads, customer support, design and login, release by release.

Instead of one star rating, Patch Pulse reads the latest Play Store reviews, splits each into sentences, tags each sentence with the part of the app it talks about, scores the sentiment, and lays it out as a version-by-category table. It flags releases where a category got significantly better or worse, and every cell links to the reviews behind it.

## Run locally

```bash
pip install -r requirements.txt
uvicorn main:app --reload
```

Open http://localhost:8000.

## Deploy

**Render (free):** push this folder to a GitHub repo, then in Render choose New, Blueprint, and select the repo. `render.yaml` sets everything up. Free instances sleep after inactivity, so the first visit after a while takes about 30 seconds to wake.

**Hugging Face Spaces (free):** create a new Space with the Docker SDK and push this folder to it. The Dockerfile serves on port 7860, which Spaces expects.

**Any Docker host:** `docker build -t patch-pulse . && docker run -p 7860:7860 patch-pulse`

Run a single process (`--workers 1`). Running analyses and the 6-hour results cache are kept in memory, so multiple workers would not see each other's jobs.

## How it works

| File | Role |
|---|---|
| `engine.py` | App search, review scraping, release grouping, category tagging, sentiment, spike detection |
| `main.py` | FastAPI server: `/api/search`, `/api/analyze` (starts a background job), `/api/jobs/{id}` (progress and results) |
| `static/index.html` | The whole frontend |

**Finding apps.** Search uses the Play Store search page for ranking and `google-play-scraper` for app details. Pasting a Play Store link or a package name also works.

**Grouping into releases.** Apps format versions differently (Instagram 449.0.0.52.84, Duolingo 6.95.4, WhatsApp 2.26.38.73). The engine tries one, two and three version parts and keeps whichever gives the most releases with enough reviews. Releases with under 2% of the pulled reviews are dropped. Apps that don't report versions are grouped by week.

**Categories.** Seven app-agnostic categories, each defined by a vocabulary list in `engine.py` (`crash*` matches crashes and crashing). A review can count toward several categories, one sentence at a time.

**Sentiment.** Each category mention is scored from -1 to 1: 60% the sentence's VADER sentiment (with app terms like "crashing", "laggy" and "overpriced" added to its lexicon) and 40% the review's star rating.

**Spikes.** For each category, the share of negative mentions is compared between consecutive releases. Moves of 10+ points are kept, and a two-proportion z-test separates significant shifts (p < 0.05) from ones that need more reviews.

## Limitations

Google Play exposes a rolling window of recent reviews, so history depends on how many reviews are pulled and how busy the app is. Keyword tagging misses complaints phrased without any category word. Mixed-language reviews such as Hinglish are scored less reliably. Heavy use from a single server can get rate-limited by the Play Store; the cache keeps repeat lookups from hitting it again.
