import discord
import pandas as pd
import os
from dotenv import load_dotenv
from discord.ext import commands, tasks
from forex_factory import PyEcoCal
import pytz
from datetime import datetime
from collections import defaultdict

load_dotenv()
DISCORD_BOT_TOKEN = os.getenv('DISCORD_BOT_TOKEN')
CHANNEL_ID = 1417468571637776397

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)

# =========================
# Timezones
eastern = pytz.timezone('US/Eastern')
ist = pytz.timezone('Asia/Kolkata')

# Flag map
flag_map = {
    'USD': ':flag_us:', 'EUR': ':flag_eu:', 'GBP': ':flag_gb:', 'JPY': ':flag_jp:',
    'AUD': ':flag_au:', 'NZD': ':flag_nz:', 'CAD': ':flag_ca:', 'CHF': ':flag_ch:',
    'CNY': ':flag_cn:', 'INR': ':flag_in:'
}
# =========================


@bot.event
async def on_ready():
    print(f"Logged in as {bot.user}")
    daily_events.start()


# =========================
# Daily auto-post task
# =========================
@tasks.loop(time=datetime.strptime("22:30", "%H:%M").time())
async def daily_events():
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        print(f"Channel ID {CHANNEL_ID} not found.")
        return

    eco = PyEcoCal()
    df_high = eco.GetEconomicCalendar("calendar")
    if df_high.empty:
        await channel.send("No high-impact events found or impact detection failed.")
        return

    df_high['Time_Eastern'] = df_high['Time_Eastern'].replace('', pd.NA).ffill()

    # Group events by their date
    grouped = defaultdict(list)
    today_str = datetime.now().strftime('%a %b %d')
    df_today = df_high[df_high['Date'] == today_str].reset_index(drop=True)

    if df_today.empty:
        await channel.send(f"No high-impact events for today ({today_str}).")
        return

    # Parse each row into event objects
    for _, row in df_today.iterrows():
        try:
            dt_naive = datetime.strptime(row['Time_Eastern'], "%I:%M%p")
        except:
            continue

        today_eastern = datetime.now(eastern).date()
        dt_eastern = eastern.localize(datetime.combine(today_eastern, dt_naive.time()))

        grouped[row['Date']].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_eastern': dt_eastern,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    embed = discord.Embed(
        title=f"🔴 High-Impact Forex Events ({today_str})",
        color=discord.Color.red()
    )

    # Sort and add to embed
    for date_str, events in grouped.items():
        events.sort(key=lambda e: e['time_eastern'])
        section_text = ""
        for ev in events:
            ist_time = ev['time_eastern'].astimezone(ist)
            est_str = ev['time_eastern'].strftime("%I:%M %p")
            ist_str = ist_time.strftime("%I:%M %p %a, %b %d")
            flag = flag_map.get(ev['currency'], '')

            section_text += (
                f"{flag} **{ev['currency']} - {ev['event']}**\n"
                f"⏰ {est_str} (IST {ist_str})\n"
                f"📊 Forecast: {ev['forecast']} | 📈 Previous: {ev['previous']}\n"
            )
            if ev['actual']:
                section_text += f"✅ Actual: {ev['actual']}\n"
            section_text += "\n"

        embed.add_field(
            name=f"📌 {date_str}",
            value=section_text.strip(),
            inline=True
        )

    await channel.send(embed=embed)


# =========================
# Slash command: /events
# =========================
@bot.slash_command(name="events", description="Show today's high-impact ForexFactory events with IST time")
async def events(ctx):
    eco = PyEcoCal()
    df_high = eco.GetEconomicCalendar("calendar")
    if df_high.empty:
        await ctx.send("No high-impact events found or impact detection failed.")
        return

    df_high['Time_Eastern'] = df_high['Time_Eastern'].replace('', pd.NA).ffill()

    grouped = defaultdict(list)
    today_str = datetime.now().strftime('%a %b %d')
    df_today = df_high[df_high['Date'] == today_str].reset_index(drop=True)

    if df_today.empty:
        await ctx.send(f"No high-impact events for today ({today_str}).")
        return

    for _, row in df_today.iterrows():
        try:
            dt_naive = datetime.strptime(row['Time_Eastern'], "%I:%M%p")
        except:
            continue

        today_eastern = datetime.now(eastern).date()
        dt_eastern = eastern.localize(datetime.combine(today_eastern, dt_naive.time()))

        grouped[row['Date']].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_eastern': dt_eastern,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    embed = discord.Embed(
        title=f"🔴 High-Impact Forex Events ({today_str})",
        color=discord.Color.red()
    )

    for date_str, events in grouped.items():
        events.sort(key=lambda e: e['time_eastern'])
        section_text = ""
        for ev in events:
            ist_time = ev['time_eastern'].astimezone(ist)
            est_str = ev['time_eastern'].strftime("%I:%M %p")
            ist_str = ist_time.strftime("%I:%M %p %a, %b %d")
            flag = flag_map.get(ev['currency'], '')

            section_text += (
                f"{flag} **{ev['currency']} - {ev['event']}**\n"
                f"⏰ {est_str} (IST {ist_str})\n"
                f"📊 Forecast: {ev['forecast']} | 📈 Previous: {ev['previous']}\n"
            )
            if ev['actual']:
                section_text += f"✅ Actual: {ev['actual']}\n"
            section_text += "\n"

        embed.add_field(
            name=f"📌 {date_str}",
            value=section_text.strip(),
            inline=True
        )

    await ctx.send(embed=embed)


# =========================
bot.run(DISCORD_BOT_TOKEN)
