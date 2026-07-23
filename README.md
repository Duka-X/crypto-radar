<p align="center">
  <h1 align="center">🚀 CryptoRadar</h1>
  <p align="center">
    <strong>Real-Time Multi-Signal Cryptocurrency Ranking Tool</strong>
    <br>
    Score trending coins across 5 dimensions: Trending · Price · Momentum · Volume · Community
    <br>
    <a href="https://cryptoradar.dev/"><strong>cryptoradar.dev »</strong></a>
  </p>
</p>

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.13-blue?logo=python" alt="Python 3.13">
  <img src="https://img.shields.io/badge/FastAPI-0.110-teal?logo=fastapi" alt="FastAPI">
  <img src="https://img.shields.io/badge/license-MIT-green" alt="MIT License">
</p>

---

## 📡 What is CryptoRadar?

CryptoRadar is a **real-time cryptocurrency ranking dashboard** that scores the top trending coins using a weighted multi-signal model. Data is collected hourly from CoinGecko and Reddit, normalized, and combined into a single 0–100 **Signal Score** per coin.

**Live at:** [https://cryptoradar.dev](https://cryptoradar.dev)

## 🔥 Key Features

- **5-Dimension Scoring** — T(20%) P(15%) M(20%) V(25%) C(20%)
- **Hourly Auto-Refresh** — Background task fetches fresh data every hour
- **Token Detail Pages** — Score breakdown, price chart, score history, community description
- **Trending & Volatility Pages** — [`/trending`](https://cryptoradar.dev/trending) shows signal score gainers, [`/most-volatile`](https://cryptoradar.dev/most-volatile) shows momentum leaders
- **Reddit Sentiment** — Mention tracking across 5 crypto subreddits
- **Responsive Dark Theme** — Mobile-friendly, designed for scanning
- **[Read the full scoring methodology »](https://cryptoradar.dev/methodology)**

## 📊 Signal Breakdown

| Signal | Weight | Data Source |
|--------|--------|-------------|
| **T** — Trending | 20% | CoinGecko `/search/trending` API |
| **P** — 24h Price | 15% | CoinGecko markets API |
| **M** — Momentum | 20% | 7-day sparkline volatility expansion |
| **V** — Volume | 25% | Volume-to-market-cap ratio |
| **C** — Community | 20% | GitHub commits, Twitter, Telegram, Reddit |

## 🛠 Tech Stack

- **Backend:** Python 3.13 + FastAPI
- **Templating:** Jinja2 + Chart.js
- **Database:** SQLite
- **Data Sources:** CoinGecko API, Reddit JSON API
- **Infrastructure:** Nginx + Let\'s Encrypt (Vultr Japan)

## 🚦 Getting Started

```bash
git clone https://github.com/Duka-X/crypto-radar.git
cd crypto-radar
pip install -r requirements.txt
python -m uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open [http://localhost:8000](http://localhost:8000).

## 📈 SEO & Discoverability

CryptoRadar is fully optimized for search engines:
- Dynamic sitemap (`/sitemap.xml`) with hourly changefreq
- Structured data (WebApplication, Product, BreadcrumbList, AggregateRating)
- Open Graph / Twitter Card meta tags for social sharing
- Dedicated trend pages (`/trending`, `/most-volatile`) with unique content
- Scoring methodology page (`/methodology`) with keyword-rich explanations

## 📄 License

MIT
