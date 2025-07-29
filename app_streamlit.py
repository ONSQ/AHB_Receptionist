import streamlit as st
import requests
from app_v3 import chat

#user_input = st.text_input("you")
#if user_input:
#    response = chat(user_input)
 #   st.write(response)

# Set the backend URL (Flask app from app_v3.py)
BACKEND_URL = "http://localhost:8080/chat"
RESET_URL = "http://localhost:8080/reset"

st.set_page_config(page_title="Austin Hybrid Battery Receptionist", page_icon="🔋")
st.title("🔋 Austin Hybrid Battery")
st.subheader("AI Receptionist Chat")

if "history" not in st.session_state:
    st.session_state.history = []

# Show chat history
for msg in st.session_state.history:
    with st.chat_message("user" if msg["role"] == "user" else "assistant"):
        st.markdown(msg["content"])

# User input
if prompt := st.chat_input("Type your message..."):
    st.session_state.history.append({"role": "user", "content": prompt})

    try:
        bot_msg = chat(prompt)  # 👈 Call chat() directly from app_v2
    except Exception as e:
        bot_msg = f"⚠️ Internal error: {e}"

    st.session_state.history.append({"role": "assistant", "content": bot_msg})
    st.rerun()

# Reset session
if st.button("🔄 Reset Conversation"):
   st.session_state.clear()
   st.rerun()
