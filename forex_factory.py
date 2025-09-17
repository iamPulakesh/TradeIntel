from bs4 import BeautifulSoup
import urllib.request
import ssl
import pandas as pd
import pytz
from datetime import datetime, timezone

class PyEcoCal:
    def __init__(self):
        # any init-stuff if needed
        pass

    def GetEconomicCalendar(self, date_path="calendar"):
        """Fetches ForexFactory calendar for given date path, returns DataFrame of high-impact events with date assigned."""
        baseURL = "https://www.forexfactory.com/"
        ssl._create_default_https_context = ssl._create_unverified_context
        url = baseURL + date_path
        opener = urllib.request.build_opener()
        opener.addheaders = [('User-agent', 'Mozilla/5.0')]
        response = opener.open(url)
        html = response.read().decode('utf-8', errors='replace')
        soup = BeautifulSoup(html, "html.parser")

        # Find all rows (including date rows)
        all_rows = soup.find("table", class_="calendar__table").find_all("tr")
        data = []
        current_date = None
        for row in all_rows:
            # Check if this is a date row
            if "calendar__row--day-breaker" in row.get("class", []):
                date_td = row.find("td", class_="calendar__cell")
                if date_td:
                    weekday = date_td.contents[0].strip().replace('"', '').strip()
                    date_span = date_td.find("span")
                    date_str = date_span.text.strip() if date_span else ""
                    current_date = f"{weekday} {date_str}".strip()
                continue
            cur_td = row.find("td", class_="calendar__currency")
            if not cur_td:
                continue
            currency = cur_td.text.strip()
            if currency == "":
                continue
            event_td = row.find("td", class_="calendar__event")
            event = event_td.text.strip() if event_td else ""
            time_td = row.find("td", class_="calendar__time")
            time_label = time_td.text.strip() if time_td else ""
            # Use timezone-agnostic unix timestamp provided by FF
            timestamp_utc = None
            if time_td:
                # Common attribute on FF
                if time_td.has_attr("data-timestamp"):
                    try:
                        timestamp_utc = int(time_td.get("data-timestamp"))
                    except Exception:
                        timestamp_utc = None
                # Fallback: look for any plausible epoch-like attribute
                if timestamp_utc is None:
                    try:
                        # Check own attributes
                        for k, v in time_td.attrs.items():
                            if isinstance(v, list):
                                v = v[0]
                            if k and ("timestamp" in k or "sort" in k or "epoch" in k):
                                if str(v).isdigit() and len(str(v)) >= 10:
                                    timestamp_utc = int(str(v)[:10])
                                    break
                        # Search descendants for similar attributes
                        if timestamp_utc is None:
                            for el in time_td.descendants:
                                if hasattr(el, 'attrs'):
                                    for k, v in el.attrs.items():
                                        if isinstance(v, list):
                                            v = v[0]
                                        if k and ("timestamp" in k or "sort" in k or "epoch" in k):
                                            sv = str(v)
                                            if sv.isdigit() and len(sv) >= 10:
                                                timestamp_utc = int(sv[:10])
                                                break
                                    if timestamp_utc is not None:
                                        break
                    except Exception:
                        timestamp_utc = None
            impact = ""
            impact_td = row.find("td", class_="calendar__impact")
            if impact_td:
                span = impact_td.find("span")
                if span and 'class' in span.attrs:
                    classes = span['class']
                    if any("icon--ff-impact-red" in cls for cls in classes):
                        impact = "High"
                    elif any("icon--ff-impact-orange" in cls for cls in classes):
                        impact = "Medium"
                    elif any("icon--ff-impact-yellow" in cls for cls in classes):
                        impact = "Low"
                    else:
                        impact = ""
            actual = ""
            forecast = ""
            previous = ""
            act_td = row.find("td", class_="calendar__actual")
            if act_td:
                actual = act_td.text.strip()
            fcast_td = row.find("td", class_="calendar__forecast")
            if fcast_td:
                forecast = fcast_td.text.strip()
            prev_td = row.find("td", class_="calendar__previous")
            if prev_td:
                previous = prev_td.text.strip()
            data.append({
                "Date": current_date,                     # As shown by site (may vary by env)
                "Currency": currency,
                "Event": event,
                "Time_Eastern": time_label,               # Kept for backward compatibility/display
                "Timestamp_UTC": timestamp_utc,           # New: robust reference time
                "Impact": impact,
                "Actual": actual,
                "Forecast": forecast,
                "Previous": previous
            })
        df = pd.DataFrame(data)
        print("Unique Impact levels found:", df["Impact"].unique())
        df_high = df[df["Impact"] == "High"].reset_index(drop=True)
        return df_high

if __name__ == "__main__":
    eco = PyEcoCal()
    df_high = eco.GetEconomicCalendar("calendar")
    if df_high.empty:
        print("No high-impact events found or impact detection failed.")
    else:
        # Forward fill missing label times for readability
        df_high['Time_Eastern'] = df_high['Time_Eastern'].replace('', pd.NA).ffill()

        # Demonstrate normalization using UTC timestamp -> Eastern
        eastern = pytz.timezone('US/Eastern')
        now_et = datetime.now(eastern)
        today_et = now_et.date()

        def to_et_str(ts):
            if pd.isna(ts):
                return None
            try:
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(eastern)
                return dt.strftime('%I:%M %p')
            except Exception:
                return None

        df_high['Time_ET_fromUTC'] = df_high['Timestamp_UTC'].apply(to_et_str)

        # Filter to today's events by converting timestamp to Eastern date
        def is_today(ts):
            if pd.isna(ts):
                return False
            try:
                dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone(eastern)
                return dt.date() == today_et
            except Exception:
                return False

        df_today = df_high[df_high['Timestamp_UTC'].apply(is_today)].reset_index(drop=True)
        today_str = now_et.strftime('%a %b %d')
        print(f"Today's high-impact events ({today_str}) [normalized to ET]:")
        print(df_today)
