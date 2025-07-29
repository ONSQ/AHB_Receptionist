from flask import Flask, request, jsonify, render_template, session
from flask_session import Session
import openai
import os
import yaml
import re
from difflib import get_close_matches
from datetime import datetime, timedelta
import dateparser
from google.oauth2 import service_account
from googleapiclient.discovery import build
from zoneinfo import ZoneInfo

# Constants
SHOP_CLOSING_HOUR = 18  # 6 PM
SHOP_TIMEZONE = "America/Chicago"

app = Flask(__name__)
app.secret_key = "2d10ce70ef3a5a756901e7ca4644719f3ce491a940bcf1f2a7de55fddaf9ddda"

app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_FILE_DIR"] = "./flask_session"
app.config["SESSION_PERMANENT"] = True
app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(hours=24)
app.config["SESSION_USE_SIGNER"] = True #app.secret_key dependant
session_dir = "./flask_session"
if not os.path.exists(session_dir):
    os.makedirs(session_dir)
Session(app)

# Google Calendar setup
SCOPES = ['https://www.googleapis.com/auth/calendar']
SERVICE_ACCOUNT_FILE = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'JSON_KEY')
CALENDAR_ID = '63515b4cafca7044234174496771b4287a0b94542d15c02d41b45c19bff9e7f5@group.calendar.google.com'
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)

calendar_service = build('calendar', 'v3', credentials=credentials)

openai.api_key = 'Open_AI_Key'

# ---------------------- UTILS ----------------------
def load_knowledge_base(file_path="knowledge_base.txt"):
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["vehicles"]

def extract_keywords(text):
    text = text.lower()
    words = re.findall(r'\b\w+\b', text)
    year = next((w for w in words if w.isdigit() and 1980 <= int(w) <= 2050), None)
    return {"words": words, "year": int(year) if year else None}

def match_vehicle(text, vehicles):
    keywords = extract_keywords(text)
    models = list(set(v["model"].lower() for v in vehicles))
    matched_models = [get_close_matches(w, models, n=1, cutoff=0.7)[0]
                      for w in keywords["words"] if get_close_matches(w, models, n=1, cutoff=0.7)]

    if not matched_models:
        return None

    selected_model = matched_models[0]
    candidates = [v for v in vehicles if v["model"].lower() == selected_model]

    if keywords["year"]:
        year_matches = [v for v in candidates if v["year"] == keywords["year"]]
        if year_matches:
            return year_matches[0] if len(year_matches) == 1 else {"ambiguous": year_matches}

    candidates.sort(key=lambda x: x["year"], reverse=True)
    return candidates[0] if candidates else None

def extract_datetime(text):
    return dateparser.parse(text, settings={'PREFER_DATES_FROM': 'future'})

def is_time_slot_available(start_dt, end_dt, calendar_id=CALENDAR_ID):
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ZoneInfo(SHOP_TIMEZONE))
    if end_dt.tzinfo is None:
        end_dt = end_dt.replace(tzinfo=ZoneInfo(SHOP_TIMEZONE))

    events_result = calendar_service.events().list(
        calendarId=calendar_id,
        timeMin=start_dt.isoformat(),
        timeMax=end_dt.isoformat(),
        singleEvents=True,
        orderBy='startTime'
    ).execute()

    events = events_result.get('items', [])

    # Extra safety: check for actual time overlap
    for event in events:
        ev_start = dateparser.parse(event["start"].get("dateTime", event["start"].get("date")))
        ev_end = dateparser.parse(event["end"].get("dateTime", event["end"].get("date")))
        if start_dt < ev_end and end_dt > ev_start:
            return False  # Overlap found

    return True  # No conflicts

#ADDED FOR TESTING 28JUL
def find_next_available_slots(service_hours, num_slots=3, calendar_id=CALENDAR_ID):
    now = datetime.now(ZoneInfo(SHOP_TIMEZONE))
    current_day = now.replace(hour=10, minute=0, second=0, microsecond=0)

    slots = []
    days_checked = 0

    while len(slots) < num_slots and days_checked < 30:  # Limit search to next 30 days
        # Skip Sundays
        if current_day.weekday() != 6:
            for hour in range(10, 18):  # Check every hour from 10 AM to 5 PM
                start_dt = current_day.replace(hour=hour, minute=0)
                end_dt = start_dt + timedelta(hours=service_hours)

                if end_dt.hour > 18 or (end_dt.hour == 18 and end_dt.minute > 0):
                    continue  # Skip if it would end after 6 PM

                if start_dt < now:
                    continue

                if within_shop_hours(start_dt, service_hours) and is_time_slot_available(start_dt, end_dt, calendar_id):
                    slots.append(start_dt)
                    if len(slots) == num_slots:
                        break

        current_day += timedelta(days=1)
        days_checked += 1

    return slots


def get_datetime_prompt(service_hours):
    available = find_next_available_slots(service_hours)
    if not available:
        return (
            "When would you like to bring it in? Please specify a date and time. "
            "Unfortunately, we couldn't find open time slots in the next few weeks, so please try again later or contact the shop."
        )

    suggestions = [dt.strftime("%B %d at %I:%M %p") for dt in available]
    suggestion_text = ", ".join(suggestions)

    return (
        "When would you like to bring it in? Please specify a date and time. "
        "Use this format: MONTH DAY TIME (e.g., August 3 at 2 PM).\n\n"
        f"📅 Our soonest available appointments are: {suggestion_text}. "
        "You can also check availability for other days by typing 'Try MONTH DAY' (e.g., Try August 10)"
    )


def get_available_times_for_date(date_str, service_hours, calendar_id=CALENDAR_ID):
    try:
        target_date = dateparser.parse(date_str, settings={'PREFER_DATES_FROM': 'future'})
        if not target_date:
            return []

        shop_tz = ZoneInfo(SHOP_TIMEZONE)
        day = target_date.astimezone(shop_tz).replace(hour=10, minute=0, second=0, microsecond=0)

        if day.weekday() == 6:  # Sunday
            return []

        available_times = []
        for hour in range(10, 18):
            start_dt = day.replace(hour=hour)
            end_dt = start_dt + timedelta(hours=service_hours)

            if end_dt.hour > 18 or (end_dt.hour == 18 and end_dt.minute > 0):
                continue

            if start_dt > datetime.now(shop_tz) and is_time_slot_available(start_dt, end_dt, calendar_id):
                available_times.append(start_dt)

        return available_times

    except Exception:
        return []



def handle_try_date_request(message, service_hours):
    match = re.search(r"\btry\s+([a-zA-Z]+\s+\d{1,2})\b", message.lower())
    if not match:
        return None  # It's not a "Try ..." request

    date_str = match.group(1).strip()  # Extract just the date part (e.g., "August 5")
    dt = dateparser.parse(date_str, settings={'PREFER_DATES_FROM': 'future'})

    if not dt:
        return "Sorry, I couldn’t understand that date. Try a format like 'Try August 5'."

    available_times = get_available_times_for_date(date_str, service_hours)
    if not available_times:
        return f"Sorry, there are no available appointment times on {dt.strftime('%B %d')}."

    times_list = "\n".join(f"🕒 {t.strftime('%I:%M %p')}" for t in available_times)
    return (
        f"Here are the available times for {dt.strftime('%B %d')}:\n\n{times_list}\n\n"
        "Please type the full date and time you'd like in this format: MONTH DAY TIME. Or, check availability for another day using the same 'Try MONTH DAY' format you just used."
    )



def within_shop_hours(start_dt, duration_hrs):
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=ZoneInfo(SHOP_TIMEZONE))

    end_dt = start_dt + timedelta(hours=duration_hrs)

    # Sunday check: weekday() == 6 means Sunday (Monday is 0)
    if start_dt.weekday() == 6:
        return False

    # Time check
    if start_dt.hour < 10 or end_dt.hour > 18 or (end_dt.hour == 18 and end_dt.minute > 0):
        return False

    return True


# ---------------------- MODES ----------------------

def handle_chat_mode(history):
    latest_msg = next((m["content"] for m in reversed(history) if m["role"] == "user"), "")
    vehicles = load_knowledge_base()
    match = match_vehicle(latest_msg, vehicles)

    if isinstance(match, dict) and "ambiguous" in match:
        options = "\n".join(f"- {v['year']} {v['make']} {v['model']} ({v['type']})" for v in match["ambiguous"])
        return f"I found multiple vehicle types. Could you clarify?\n{options}"

    if match:
        system_prompt = f"""
        You are a helpful assistant for Austin Hybrid Battery.
        Customer can enter Booking Mode at any time by typing 'Lets book'- you must tell them this if service options are being discussed
        The customer is asking about a {match['year']} {match['make']} {match['model']}.
        Our service history data shows that a battery replacement for customer vehicle should take approximately {match['service_time_hours']} hours.
        """
    else:
        system_prompt = (
            "You are a helpful assistant for Austin Hybrid Battery. "
            "The customer asked about service, but their vehicle was unclear. Ask them for year/make/model."
            "If the customer asks a question or statement that has nothing to do with vehicle maintenance, cleverly steer their input back towards the fact that you are here to help with their hybrid battery needs."
            "Always try to guide customer towards scheduling a battery replacement with us."
            "You do not have the ability to find available appointment times unless customer wants to enter booking mode."
            "Customer can enter 'Booking Mode' at any time by saying 'Lets book' and you must inform them of this if any services are being discussed."
        )

    messages = [{"role": "system", "content": system_prompt}] + history
    response = openai.chat.completions.create(model="gpt-4o", messages=messages)
    return response.choices[0].message.content

def handle_booking_mode(message, state):
    response = None
    vehicles = load_knowledge_base()

    if not state.get("vehicle"):
        match = match_vehicle(message, vehicles)
        if isinstance(match, dict) and "make" in match:
            state["vehicle"] = f"{match['year']} {match['make']} {match['model']}"
            state["duration"] = match.get("service_time_hours", 1)
        else:
            return "Okay, lets get you booked! Please confirm your vehicle info using this input format: YEAR MAKE MODEL"

    elif not state.get("datetime"):
        try_response = handle_try_date_request(message, state.get("duration", 1)) #TEST
        if try_response:#TEST
            return try_response #TEST
        dt = extract_datetime(message)
        if dt and within_shop_hours(dt, state["duration"]):
            end_dt = dt + timedelta(hours=state["duration"])
            if is_time_slot_available(dt, end_dt):
                state["datetime"] = dt
            else:
                return "That time is already booked. Please choose another."
        else:
            return "Please provide a valid time during shop hours (Monday-Saturday 10 AM-6 PM) in the format: MONTH DAY TIME."
    
    


    elif not state.get("name"):
        match = re.search(r"^\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)\s*$", message, re.I)
        if match:
            state["name"] = match.group(1).strip()
        else:
            return "I'm sorry, but I did not get your name! Please provide your full name in the format: FIRST LAST"

    elif not state.get("phone"):
        match = re.search(r"\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}", message)
        if match:
            state["phone"] = match.group(0)
        else:
            return "So sorry, but I did not get your phone number! Please provide your phone number in the format:(xxx) xxx-xxxx"

    if all(state.values()):
        if not state.get("confirmation_requested"):
            state["confirmation_requested"] = True
            dt_str = state["datetime"].strftime('%I:%M %p on %B %d, %Y')
            return (
                f"Here is your appointment info:\n\n"
                f"📅 Date & Time: {dt_str}\n"
                f"🚗 Vehicle: {state['vehicle']}\n"
                f"👤 Name: {state['name']}\n"
                f"📞 Phone: {state['phone']}\n\n"
                f"If everything looks good, type BOOK NOW to confirm your appointment."
            )

        if message.strip().upper() == "BOOK NOW":
            event = {
                'summary': f'Hybrid Battery Appointment - {state["name"]}',
                'description': f'Vehicle: {state["vehicle"]}\nPhone: {state["phone"]}',
                'start': {'dateTime': state["datetime"].isoformat(), 'timeZone': SHOP_TIMEZONE},
                'end': {'dateTime': (state["datetime"] + timedelta(hours=state["duration"])).isoformat(), 'timeZone': SHOP_TIMEZONE},
            }
            calendar_service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
            session["mode"] = "chat"  # Reset to chat mode after booking
            return f"✅ Appointment booked for {state['datetime'].strftime('%I:%M %p on %B %d, %Y')}. We are looking forward to seeing you!"
        else:
            return "Please type BOOK NOW to confirm, or let me know if something needs to be changed."


    return None

# ---------------------- ROUTES ----------------------

@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/reset", methods=["GET"])
def reset():
    session.clear()
    return "Session cleared."

@app.route("/chat", methods=["POST"])
def chat():
    user_msg = request.json.get("message", "").strip()
    session.setdefault("mode", "chat")
    session.setdefault("history", [])
    session.setdefault("booking_state", {
        "vehicle": None,
        "datetime": None,
        "name": None,
        "phone": None,
        "duration": None
    })

    session["history"].append({"role": "user", "content": user_msg})

    # Transition to booking mode
    if "lets book" in user_msg.lower() or "let's book" in user_msg.lower():
        session["mode"] = "booking"

    if session["mode"] == "booking":
        response = handle_booking_mode(user_msg, session["booking_state"])
        if not response:
            # If partial info gathered
            for key, val in session["booking_state"].items():
                if val is None:
                    prompts = {
                        "vehicle": "Okay, lets get you booked! Please provide your full vehicle information so our staff can properly book you. Use this format: YEAR MAKE MODEL",
                        "datetime": get_datetime_prompt(session["booking_state"]["duration"]),
                        "name": "Almost there! Can I have your full name? Use this format: FIRST LAST",
                        "phone": "Last thing! What’s your phone number? Use this format: (xxx) xxx-xxxx"
                    }
                    response = prompts[key]
                    break
    else:
        response = handle_chat_mode(session["history"])

    session["history"].append({"role": "assistant", "content": response})
    session.modified = True

    return jsonify({"response": response})

if __name__ == "__main__":
    app.run(debug=False, port=8080)
