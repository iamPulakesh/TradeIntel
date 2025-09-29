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
@tasks.loop(time=datetime.strptime("09:04", "%H:%M").time())
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

    # Group events by their date (today only)
    grouped = defaultdict(list)
    now_tz = datetime.now(TARGET_TZ)
    today_str = now_tz.strftime('%a %b %d')

    # Use only robust UTC timestamp for filtering and conversion
    def to_et(ts):
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TARGET_TZ)
        except Exception:
            return None

    df_high['__et_dt'] = df_high['Timestamp_UTC'].apply(to_et)
    if DEBUG_EVENTS:
        print("[daily_events] sample timestamps -> ET:")
        print(df_high[['Date','Currency','Event','Timestamp_UTC']].head(10))
        print(df_high['__et_dt'].head(10))
    # Strict: compute mask in one pass to guard against None
    mask_today = df_high['__et_dt'].apply(lambda d: (d is not None) and (d.date() == now_tz.date()))
    df_today = df_high[mask_today].reset_index(drop=True)
    if DEBUG_EVENTS:
        print(f"[daily_events] now_tz date: {now_tz.date()}  rows today: {len(df_today)} / {len(df_high)}")

    # Fallback: if nothing matched via UTC timestamps, try page labels for today's date only
    if df_today.empty:
        def parse_label_et(s: str):
            try:
                dt_naive = datetime.strptime(str(s).strip(), "%I:%M%p")
                return TARGET_TZ.localize(datetime.combine(now_tz.date(), dt_naive.time()))
            except Exception:
                return None
        df_lbl = df_high[df_high['Date'] == today_str].copy()
        df_lbl['__et_dt'] = df_lbl['Time_Eastern'].apply(parse_label_et)
        df_today = df_lbl[df_lbl['__et_dt'].notna()].reset_index(drop=True)
        if DEBUG_EVENTS:
            print(f"[daily_events] fallback label-based rows today: {len(df_today)}")

    if df_today.empty:
        await channel.send(f"No high-impact events for today ({today_str}).")
        return

    # Parse each row into event objects
    for _, row in df_today.iterrows():
        dt_eastern = row['__et_dt']
        if dt_eastern is None:
            continue

        # Force grouping under today's label only
        grouped[today_str].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_eastern': dt_eastern,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    tz_abbr = now_tz.strftime('%Z') or os.getenv('EVENTS_TZ', 'Asia/Kolkata')
    embed = discord.Embed(
        title=f"🔴 High-Impact Forex Events ({today_str}) — Times in {tz_abbr}",
        color=discord.Color.red()
    )

    # Sort and add to embed
    for date_str, events in grouped.items():
        events.sort(key=lambda e: e['time_eastern'])
        section_text = ""
        for ev in events:
                est_str = ev['time_eastern'].strftime("%I:%M %p")
                flag = flag_map.get(ev['currency'], '')

                section_text += (
                    f"{flag} **{ev['currency']} - {ev['event']}**\n"
                    f"⏰ {est_str}\n"
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
    now_tz = datetime.now(TARGET_TZ)
    today_str = now_tz.strftime('%a %b %d')

    def to_et(ts):
        try:
            return datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(TARGET_TZ)
        except Exception:
            return None

    df_high['__et_dt'] = df_high['Timestamp_UTC'].apply(to_et)
    if DEBUG_EVENTS:
        print("[/events] sample timestamps -> ET:")
        print(df_high[['Date','Currency','Event','Timestamp_UTC']].head(10))
        print(df_high['__et_dt'].head(10))
    mask_today = df_high['__et_dt'].apply(lambda d: (d is not None) and (d.date() == now_tz.date()))
    df_today = df_high[mask_today].reset_index(drop=True)
    if DEBUG_EVENTS:
        print(f"[/events] now_tz date: {now_tz.date()}  rows today: {len(df_today)} / {len(df_high) if isinstance(df_high, pd.DataFrame) else 'NA'}")

    # Fallback: if nothing matched via UTC timestamps, try page labels for today's date only
    if df_today.empty:
        def parse_label_et(s: str):
            try:
                dt_naive = datetime.strptime(str(s).strip(), "%I:%M%p")
                return TARGET_TZ.localize(datetime.combine(now_tz.date(), dt_naive.time()))
            except Exception:
                return None
        df_lbl = df_high[df_high['Date'] == today_str].copy()
        df_lbl['__et_dt'] = df_lbl['Time_Eastern'].apply(parse_label_et)
        df_today = df_lbl[df_lbl['__et_dt'].notna()].reset_index(drop=True)
        if DEBUG_EVENTS:
            print(f"[/events] fallback label-based rows today: {len(df_today)}")

    if df_today.empty:
        await ctx.send(f"No high-impact events for today ({today_str}).")
        return

    for _, row in df_today.iterrows():
        dt_eastern = row['__et_dt']
        if dt_eastern is None:
            continue

        grouped[today_str].append({
            'currency': row['Currency'],
            'event': row['Event'],
            'time_eastern': dt_eastern,
            'forecast': row.get('Forecast', 'N/A'),
            'previous': row.get('Previous', 'N/A'),
            'actual': row.get('Actual', '')
        })

    tz_abbr = now_tz.strftime('%Z') or os.getenv('EVENTS_TZ', 'Asia/Kolkata')
    embed = discord.Embed(
        title=f"🔴 High-Impact Forex Events ({today_str}) — Times in {tz_abbr}",
        color=discord.Color.red()
    )

    for date_str, events in grouped.items():
        events.sort(key=lambda e: e['time_eastern'])
        section_text = ""
        for ev in events:
                est_str = ev['time_eastern'].strftime("%I:%M %p")
                flag = flag_map.get(ev['currency'], '')

                section_text += (
                    f"{flag} **{ev['currency']} - {ev['event']}**\n"
                    f"⏰ {est_str}\n"
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
