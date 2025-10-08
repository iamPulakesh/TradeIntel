import discord
import pandas as pd
import os
import boto3
from botocore.exceptions import ClientError
from discord.ext import commands, tasks
from forex_factory import PyEcoCal
import pytz
from datetime import datetime, timezone
from collections import defaultdict

def get_discord_token_from_ssm(param_name: str, region: str = None) -> str:
    
    try:
        ssm = boto3.client('ssm', region_name=region)
        response = ssm.get_parameter(Name=param_name, WithDecryption=True)
        return response['Parameter']['Value']
    except ClientError as e:
        print(f"[ERROR] Failed to fetch Discord token from SSM: {e}")
        return None

SSM_PARAM_NAME = os.getenv('DISCORD_BOT_TOKEN_PARAM', '/tradeintel/discord/token')
AWS_REGION = os.getenv('AWS_REGION', 'ap-south-1')
DISCORD_BOT_TOKEN = get_discord_token_from_ssm(SSM_PARAM_NAME, AWS_REGION)
if not DISCORD_BOT_TOKEN:
    raise RuntimeError("DISCORD_BOT_TOKEN could not be retrieved from AWS SSM Parameter Store.")
CHANNEL_ID = 1417468571637776397

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="/", intents=intents)

# Toggle for verbose logging to trace filtering issues
DEBUG_EVENTS = bool(int(os.getenv("DEBUG_EVENTS", "0")))

# =========================
# Timezones
eastern = pytz.timezone('US/Eastern')
ist = pytz.timezone('Asia/Kolkata')

def _resolve_timezone(name: str) -> pytz.timezone:
    """Resolve common aliases like 'IST' to a pytz timezone string.
    Falls back to Asia/Kolkata if resolution fails.
    """
    try:
        if not name:
            return pytz.timezone('Asia/Kolkata')
        key = name.strip()
        aliases = {
            'IST': 'Asia/Kolkata',
            'INDIA': 'Asia/Kolkata',
            'IN': 'Asia/Kolkata',
            'EDT': 'US/Eastern',
            'EST': 'US/Eastern',
            'ET': 'US/Eastern',
            'EASTERN': 'US/Eastern',
        }
        tz_name = aliases.get(key.upper(), key)
        return pytz.timezone(tz_name)
    except Exception:
        return pytz.timezone('Asia/Kolkata')

# Use one target timezone for display + filtering. Default to IST (Asia/Kolkata).
TARGET_TZ = _resolve_timezone(os.getenv('EVENTS_TZ', 'Asia/Kolkata'))
if DEBUG_EVENTS:
    print(f"[config] Using target timezone: {TARGET_TZ.zone}")

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

    df_high = eco.GetEconomicCalendar("calendar?week=this")
    print("[DEBUG] df_high from daily_events:")
    print(df_high)
    if df_high.empty:
        await channel.send("No high-impact events found for the current week.")
        return

    df_high['Time_Eastern'] = df_high['Time_Eastern'].replace('', pd.NA).ffill()

    # Group events by their date
    grouped = defaultdict(list)
    now_tz = datetime.now(TARGET_TZ)


    def parse_event_datetime(row):
        # Try UTC timestamp first
        ts = row.get('Timestamp_UTC')
        if ts and str(ts).isdigit():
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TARGET_TZ)
            except Exception:
                pass
        # Fallback: parse from Date and Time_Eastern
        date_str = str(row.get('Date', '')).strip()
        time_str = str(row.get('Time_Eastern', '')).strip().replace(' ', '')
        if not date_str or not time_str or time_str.lower() == 'tentative':
            return None
        try:
            # Example: 'Wed Oct 08', '6:30am' or '8:30pm'
            dt_str = f"{date_str} {time_str.upper()}"
            dt = datetime.strptime(dt_str, "%a %b %d %I:%M%p")
            # Assume current year
            dt = dt.replace(year=datetime.now().year)
            return TARGET_TZ.localize(dt)
        except Exception:
            return None

    df_high['__dt_obj'] = df_high.apply(parse_event_datetime, axis=1)
    # Filter for today's events only
    today = now_tz.date()
    df_today = df_high[df_high['__dt_obj'].apply(lambda d: d and d.date() == today)].copy()

    if df_today.empty:
        await channel.send(f"No high-impact events for today ({now_tz.strftime('%a %b %d')}).")
        return

    # Group and display today's events
    grouped = defaultdict(list)
    for _, row in df_today.iterrows():
        dt_obj = row['__dt_obj']
        grouped[today.strftime('%a %b %d')].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_obj': dt_obj,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    tz_abbr = now_tz.strftime('%Z') or os.getenv('EVENTS_TZ', 'Asia/Kolkata')
    embed = discord.Embed(
        title=f"🔴 Today's High-Impact Forex Events ({today.strftime('%a %b %d')}) — Times in {tz_abbr}",
        color=discord.Color.red()
    )

    events = grouped[today.strftime('%a %b %d')]
    events.sort(key=lambda e: e['time_obj'])
    section_text = ""
    for ev in events:
        time_str = ev['time_obj'].strftime("%I:%M %p")
        flag = flag_map.get(ev['currency'], '')
        section_text += (
            f"{flag} **{ev['currency']} - {ev['event']}**\n"
            f"⏰ {time_str}\n"
            f"📊 Forecast: {ev['forecast']} | 📈 Previous: {ev['previous']}\n"
        )
        if ev['actual']:
            section_text += f"✅ Actual: {ev['actual']}\n"
        section_text += "\n"
    embed.add_field(
        name=f"📌 {today.strftime('%a %b %d')}",
        value=section_text.strip(),
        inline=False
    )
    await channel.send(embed=embed)


# =========================
# Slash command: /events
# =========================
@bot.slash_command(name="events", description="Show this week's high-impact ForexFactory events")
async def events(ctx):
    eco = PyEcoCal()

    df_high = eco.GetEconomicCalendar("calendar?week=this")
    print("[DEBUG] df_high from /events command:")
    print(df_high)
    if df_high.empty:
        await ctx.send("No high-impact events found for the current week.")
        return

    df_high['Time_Eastern'] = df_high['Time_Eastern'].replace('', pd.NA).ffill()

    grouped = defaultdict(list)
    now_tz = datetime.now(TARGET_TZ)


    def parse_event_datetime(row):
        ts = row.get('Timestamp_UTC')
        if ts and str(ts).isdigit():
            try:
                return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TARGET_TZ)
            except Exception:
                pass
        date_str = str(row.get('Date', '')).strip()
        time_str = str(row.get('Time_Eastern', '')).strip().replace(' ', '')
        if not date_str or not time_str or time_str.lower() == 'tentative':
            return None
        try:
            dt_str = f"{date_str} {time_str.upper()}"
            dt = datetime.strptime(dt_str, "%a %b %d %I:%M%p")
            dt = dt.replace(year=datetime.now().year)
            return TARGET_TZ.localize(dt)
        except Exception:
            return None

    df_high['__dt_obj'] = df_high.apply(parse_event_datetime, axis=1)
    # Filter for today's events only
    today = now_tz.date()
    df_today = df_high[df_high['__dt_obj'].apply(lambda d: d and d.date() == today)].copy()

    if df_today.empty:
        await ctx.send(f"No high-impact events for today ({now_tz.strftime('%a %b %d')}).")
        return

    grouped = defaultdict(list)
    for _, row in df_today.iterrows():
        dt_obj = row['__dt_obj']
        grouped[today.strftime('%a %b %d')].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_obj': dt_obj,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    tz_abbr = now_tz.strftime('%Z') or os.getenv('EVENTS_TZ', 'Asia/Kolkata')
    embed = discord.Embed(
        title=f"🔴 Today's High-Impact Forex Events ({today.strftime('%a %b %d')}) — Times in {tz_abbr}",
        color=discord.Color.red()
    )

    events = grouped[today.strftime('%a %b %d')]
    events.sort(key=lambda e: e['time_obj'])
    section_text = ""
    for ev in events:
        time_str = ev['time_obj'].strftime("%I:%M %p")
        flag = flag_map.get(ev['currency'], '')
        section_text += (
            f"{flag} **{ev['currency']} - {ev['event']}**\n"
            f"⏰ {time_str}\n"
            f"📊 Forecast: {ev['forecast']} | 📈 Previous: {ev['previous']}\n"
        )
        if ev['actual']:
            section_text += f"✅ Actual: {ev['actual']}\n"
        section_text += "\n"
    embed.add_field(
        name=f"📌 {today.strftime('%a %b %d')}",
        value=section_text.strip(),
        inline=False
    )
    await ctx.send(embed=embed)


# =========================
bot.run(DISCORD_BOT_TOKEN)
