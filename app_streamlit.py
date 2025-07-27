import streamlit as st
import yaml, re, json
from difflib import get_close_matches
import openai
from datetime import datetime, timedelta
from google.oauth2 import service_account
from googleapiclient.discovery import build

# --- API keys & secrets ---
openai.api_key = st.secrets.get("OPENAI_API_KEY")
google_api_key = st.secrets["GOOGLE_API_KEY"]
# For Google service account JSON:
import json
google_creds = json.loads(st.secrets["GOOGLE_SERVICE_ACCOUNT_JSON"])


# --- Google Calendar setup ---
SCOPES = ['https://www.googleapis.com/auth/calendar']
credentials = service_account.Credentials.from_service_account_info(
    google_creds, scopes=SCOPES
)
calendar_service = build('calendar', 'v3', credentials=credentials)

def create_calendar_event(start_datetime, end_datetime):
    event = {
        'summary': 'Hybrid Battery Appointment',
        'start': {'dateTime': start_datetime.isoformat(), 'timeZone': 'America/Chicago'},
        'end': {'dateTime': end_datetime.isoformat(), 'timeZone': 'America/Chicago'},
    }
    event = calendar_service.events().insert(calendarId='primary', body=event).execute()
    return event.get('htmlLink')

# --- Knowledge base ---
@st.cache_data
def load_knowledge_base(path="knowledge_base.txt"):
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["vehicles"]

vehicles = load_knowledge_base()

def extract_keywords(user_input):
    user_input = user_input.lower()
    words = re.findall(r'\b\w+\b', user_input)
    year = next((w for w in words if w.isdigit() and 1980 <= int(w) <= 2050), None)
    return {"words": words, "year": int(year) if year else None}

def match_vehicle(user_input, vehicles):
    keywords = extract_keywords(user_input)
    model_candidates = list(set(v["model"].lower() for v in vehicles))
    matched_models = []
    for word in keywords["words"]:
        matches = get_close_matches(word, model_candidates, n=1, cutoff=0.7)
        if matches:
            matched_models.append(matches[0])
    if not matched_models:
        return None
    selected_model = matched_models[0]
    model_matches = [v for v in vehicles if v["model"].lower() == selected_model]
    if keywords["year"]:
        year_matches = [v for v in model_matches if v["year"] == keywords["year"]]
        if year_matches:
            if len(year_matches) == 1:
                return year_matches[0]
            else:
                return {"ambiguous": year_matches}
    model_matches.sort(key=lambda x: x["year"] or 0, reverse=True)
    return model_matches[0] if model_matches else None

def process_with_llm(history):
    try:
        latest_user_message = next((msg["content"] for msg in reversed(history) if msg["role"] == "user"), "")
        result = match_vehicle(latest_user_message, vehicles)
        if result is None:
            fallback_prompt = (
                "You are a helpful receptionist for Austin Hybrid Battery. "
                "The customer asked about battery service, but we couldn't determine the vehicle. "
                "Kindly ask for the year and model."
            )
            messages = [{"role": "system", "content": fallback_prompt}] + history
            response = openai.chat.completions.create(model="gpt-4o", messages=messages)
            return response.choices[0].message.content
        elif isinstance(result, dict) and "ambiguous" in result:
            options = result["ambiguous"]
            option_list = "\n".join(
                f"- {v.get('year', 'Unknown Year')} {v['make']} {v['model']} ({v['type']})"
                for v in options
            )
            return (
                "I found more than one version of that vehicle. "
                "Could you let me know which one you have?\n\n" + option_list
            )
        else:
            hours = result.get("service_time_hours", "some")
            model = result.get("model", "Unknown Model")
            make = result.get("make", "Unknown Make")
            year = result.get("year")
            vehicle_desc = f"{year} {make} {model}" if year else f"{make} {model}"
            return (
                f"For the {vehicle_desc}, battery replacement typically takes about {hours} hours."
            )
    except Exception as e:
        return f"Error: {str(e)}"

# --- STREAMLIT APP ---
st.set_page_config(page_title="Austin Hybrid Battery Receptionist", page_icon="🔋")
st.title("🔋 Austin Hybrid Battery AI Receptionist 🔋")
st.write("Ask about battery service for your vehicle, or schedule an appointment.")

if "history" not in st.session_state:
    st.session_state.history = []

for msg in st.session_state.history:
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        st.markdown(msg["content"])

if prompt := st.chat_input("Type your message..."):
    st.session_state.history.append({"role": "user", "content": prompt})

    # If user asks for appointment, trigger calendar widget
    if "appointment" in prompt.lower() or "schedule" in prompt.lower():
        st.chat_message("assistant").markdown("Please select a date and time for your appointment:")
        
        # Show date & time picker inline
        selected_date = st.date_input("Select a date", min_value=datetime.now().date())
        selected_time = st.time_input("Select a time")
        
        if st.button("📅 Confirm Appointment"):
            start_dt = datetime.combine(selected_date, selected_time)
            end_dt = start_dt + timedelta(hours=1)
            event_link = create_calendar_event(start_dt, end_dt)
            confirmation = f"✅ Appointment booked for {start_dt.strftime('%I:%M %p on %B %d, %Y')}.\n[View on Google Calendar]({event_link})"
            st.session_state.history.append({"role": "assistant", "content": confirmation})
            st.experimental_rerun()

    else:
        response = process_with_llm(st.session_state.history)
        st.session_state.history.append({"role": "assistant", "content": response})
        st.rerun()
