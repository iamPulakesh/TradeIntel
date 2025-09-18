# 💹 TradeIntel
## A Discord bot built with Python that automatically fetches and posts high-impact forex news/events from the Forex Factory economic calendar.

## 📌 Features
- 📰 Scrapes high-impact (red folder) events directly from Forex Factory using BeautifulSoup.
- 🕐 Automatically posts a daily embed of upcoming events in your target timezone.
- ⚡ Provides a /events slash command to show today's high-impact events on demand.
- 🇺🇸  Displays currency flags and event details (forecast, previous, actual values).

## ⚙️ Tech Stack
- 🖥️ Pycord (discord, discord.ext.commands, discord.ext.tasks)
- 📄 BeautifulSoup (HTML parsing)
- 📊 pandas (data handling)

## 📁 Project Structure

```
TradeIntel/
│
├── bot.py                # Main Discord bot logic
├── forex_factory.py      # Scraper for Forex Factory calendar
├── .env                  
├── requirements.txt
└── README.md
```

