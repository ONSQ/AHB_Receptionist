from flask import Flask, request, jsonify, session, render_template, send_from_directory
from flask_session import Session
#from twilio.twiml.messaging_response import MessagingResponse
#from twilio.twiml.voice_response import VoiceResponse
from google.oauth2 import service_account
from googleapiclient.discovery import build
import os
import openai
from io import BytesIO
import dateparser
from datetime import timedelta
import requests

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
SERVICE_ACCOUNT_FILE = os.environ.get('GOOGLE_CREDENTIALS_PATH', 'austin-hybrid-battery-chatbot-bc7d3e26797d.json')
credentials = service_account.Credentials.from_service_account_file(SERVICE_ACCOUNT_FILE, scopes=SCOPES)
calendar_service = build('calendar', 'v3', credentials=credentials)

# Load knowledge base
with open('knowledge_base.txt', 'r') as f:
    knowledge_base = f.read()

# OpenAI setup
#openai.api_key = os.environ.get('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')
openai.api_key = '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'

import openai
import yaml
import re
from difflib import get_close_matches

# Load YAML once if running in a persistent app (e.g. Flask)
def load_knowledge_base(file_path="knowledge_base.txt"):
    with open(file_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return data["vehicles"]

# Extract model and year from user input
def extract_keywords(user_input):
    user_input = user_input.lower()
    words = re.findall(r'\b\w+\b', user_input)
    year = next((w for w in words if w.isdigit() and 1980 <= int(w) <= 2050), None)
    return {"words": words, "year": int(year) if year else None}

# Match user input to vehicle entry
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

    # Fallback: most recent year
    model_matches.sort(key=lambda x: x["year"], reverse=True)
    return model_matches[0] if model_matches else None


# Main function: combines vehicle matching and optional LLM fallback
'''
def process_with_llm(messages):
    try:
        vehicles = load_knowledge_base()
        result = match_vehicle(user_message, vehicles)

        if result is None:
            # No match at all → use LLM fallback
            fallback_prompt = (
                "You are a helpful receptionist for Austin Hybrid Battery. "
                "The customer asked about battery service, but we couldn't determine the vehicle. "
                "Kindly ask for the year and model."
            )
            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=[
                    {"role": "system", "content": fallback_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            return response.choices[0].message.content

        elif isinstance(result, dict) and "ambiguous" in result:
            # Multiple matches (e.g. Prius and Prius Prime in same year)
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
            # Matched exactly
            hours = result.get("service_time_hours", "some")
            model = result.get("model", "Unknown Model")
            make = result.get("make", "Unknown Make")
            year = result.get("year")

            if year:
                vehicle_desc = f"{year} {make} {model}"
            else: 
                vehicle_desc = f"{make} {model}"

            return (
                f"For the {vehicle_desc}, battery replacement typically takes about {hours} hours."
            )

    except Exception as e:
        return f"Error: {str(e)}"
'''
def process_with_llm(history):
    try:
        vehicles = load_knowledge_base()
        # Extract latest user content for matching vehicle info
        latest_user_message = None
        # Find last message with role 'user'
        for msg in reversed(history):
            if msg["role"] == "user":
                latest_user_message = msg["content"]
                break

        if not latest_user_message:
            latest_user_message = ""

        result = match_vehicle(latest_user_message, vehicles) #if latest_user_message else None

        if result is None:
            # No match → use LLM fallback, prepend system prompt
            fallback_prompt = (
                "You are a helpful receptionist for Austin Hybrid Battery. "
                "The customer asked about battery service, but we couldn't determine the vehicle. "
                "Kindly ask for the year and model."
            )
            # Insert system prompt as the first message
            messages = [{"role": "system", "content": fallback_prompt}] + history

            response = openai.chat.completions.create(
                model="gpt-4o",
                messages=messages
            )
            return response.choices[0].message.content

        elif isinstance(result, dict) and "ambiguous" in result:
            # Multiple vehicle matches → ask user to clarify
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
            # Exact match → give estimated service time
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



'''
def process_with_llm(message):
    try:
        response = openai.chat.completions.create(
            model="gpt-4o-mini",
            messages=[
                {"role": "system", "content": f"Knowledge base: {knowledge_base}\nYou are a helpful receptionist for Austin Hybrid Battery."},
                {"role": "user", "content": message}
            ]
        )
        return response.choices[0].message.content
    except Exception as e:
        return f"Error with LLM: {str(e)}"
'''
'''
def transcribe_recording(recording_url):
    try:
        audio_response = requests.get(recording_url)
        audio_file = BytesIO(audio_response.content)
        audio_file.name = "recording.wav"
        transcription = openai.Audio.transcriptions.create(
            model="whisper-1",
            file=audio_file
        )
        return transcription.text
    except Exception as e:
        return f"Error transcribing: {str(e)}"
'''
'''
def text_to_speech(text):
    try:
        response = openai.Audio.speech.create(
            model="tts-1",
            voice="alloy",
            input=text
        )
        audio_path = "static/response.mp3"
        response.stream_to_file(audio_path)
        return "/static/response.mp3"
    except Exception as e:
        return f"Error generating speech: {str(e)}"
'''
def handle_appointment(message):
    parsed_date = dateparser.parse(message, settings={'PREFER_DATES_FROM': 'future'})
    if not parsed_date:
        return "Please specify a date and time for the appointment."
    start_time = parsed_date.isoformat()
    end_time = (parsed_date + timedelta(hours=1)).isoformat()
    event = {
        'summary': 'Hybrid Battery Appointment',
        'start': {'dateTime': start_time, 'timeZone': 'America/Chicago'},
        'end': {'dateTime': end_time, 'timeZone': 'America/Chicago'},
    }
    event = calendar_service.events().insert(calendarId='primary', body=event).execute()
    return f"Appointment booked for {parsed_date.strftime('%I:%M %p on %B %d, %Y')}. Event ID: {event.get('id')}"
@app.route("/", methods=["GET"])
def home():
    return render_template("index.html")

@app.route("/chat", methods=["POST", "GET"])
#def home():
    #return "Welcome to Austin Hybrid Battery AI Assistant!"
def chat():
    if request.method == "GET":
        return "✅ Chat endpoint is live. Send a POST request with JSON."

    # Handle POST request
    user_message = str(request.json.get("message") or "")
    session.permanent = True
    history = session.get("history", [])
    print("DEBUG - current session history length:", len(history), flush=True) #DEBUG
    
    history.append({"role": "user", "content": user_message})
    
    if "appointment" in user_message.lower() or "schedule" in user_message.lower():
        response = handle_appointment(user_message)
    else:
        print("DEBUG - session history:" , history, flush=True) #DEBUG
        response = process_with_llm(history)
        print("DEBUG - history passed to LLM:" , history, flush=True) #DEBUG
    
    history.append({"role": "assistant", "content": response})
    session["history"] = history
    session.modified = True

    response = jsonify({"response": response})
    print("DEBUG - response headers:", response.headers)
    return response
    #return jsonify({"response": response})


'''
def chat():
    print("Chat endpoint hit!")
    data = request.get_json()
    print("Data received:", data)
    return jsonify({"response": "Received your message"})
'''

'''
def chat():
    user_message = request.json['message']
    if "appointment" in user_message.lower() or "schedule" in user_message.lower():
        response = handle_appointment(user_message)
    else:
        response = process_with_llm(user_message)
    return jsonify({'response': response})
'''
'''
@app.route('/sms', methods=['POST'])
def sms():
    user_message = request.values.get('Body', '')
    if "appointment" in user_message.lower() or "schedule" in user_message.lower():
        response = handle_appointment(user_message)
    else:
        response = process_with_llm(user_message)
        twiml = MessagingResponse()
        twiml.message(response)
    return str(twiml)
'''
'''
@app.route('/voice', methods=['POST'])
def voice():
    resp = VoiceResponse()
    resp.say("Welcome to Austin Hybrid Battery. How can I assist you today?", voice='alice')
    resp.record(action='/handle-recording', method='POST')
    return str(resp)
'''
'''
@app.route('/handle-recording', methods=['POST'])
def handle_recording():
    recording_url = request.values.get('RecordingUrl', '')
    transcription = transcribe_recording(recording_url)
    if "appointment" in transcription.lower() or "schedule" in transcription.lower():
        response = handle_appointment(transcription)
    else:
        response = process_with_llm(transcription)
    speech_url = text_to_speech(response)
    resp = VoiceResponse()
    resp.play(speech_url)
    return str(resp)
'''
'''
@app.route('/static/<path:filename>')
def static_files(filename):
    return send_from_directory('static', filename)
'''
if __name__ == '__main__':
    port = int(os.environ.get('PORT', 8080))
    app.run(host='0.0.0.0', port=port, debug=True)
