# CampusFix AI Chatbot

A responsive chatbot frontend with a Flask and MongoDB backend for CampusFix AI.

## Run locally

1. Create and activate a virtual environment:

	```powershell
	py -m venv .venv
	.venv\Scripts\Activate.ps1
	```

2. Install the backend dependencies:

	```powershell
	pip install -r requirements.txt
	```

3. Start Flask:

	```powershell
	py app.py
	```

4. Open `http://127.0.0.1:5000` in your browser. Flask serves the frontend and API from the same origin.

## Current scope

- Responsive desktop and mobile chat layout
- Conversation history sidebar
- First-entry user onboarding with name, account type, and registration number
- Message composer with Enter-to-submit behavior
- Flask health, app info, user registration, and chat message endpoints
- MongoDB persistence for users and chat messages
- Groq LLM responses with structured IT troubleshooting guidance
- Local faster-whisper transcription for microphone recordings
- AI screenshot diagnosis for campus IT errors

## Environment

The backend reads `MONGO_URI`, `mongourli`, or the existing `mongo_url` variable from `.env`. `MONGO_DB_NAME` is optional and defaults to `campusfix_ai`. Keep `.env` private; the repository ignores it.

## API shape

`POST /api/users` accepts `name`, `user_type` (`student` or `employee`), and `registration_number`. The registration number is stored directly as MongoDB's `_id` primary key.

`POST /api/chat` accepts `message` and `user_id`, sends the question to Groq, stores both turns in MongoDB, and returns a structured CampusFix response.

`POST /api/tickets` accepts `user_id`, `issue`, and an optional `category`, then creates an open escalation ticket. `GET /api/users/<registration_number>/tickets` lists that user's tickets.

`POST /api/transcribe` accepts the MediaRecorder audio upload as `audio` and runs local `faster-whisper` inference. The default `tiny` model is downloaded once and cached locally. Set `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, or `WHISPER_COMPUTE_TYPE` in `.env` to change the local model settings. If the package or model cannot load, the endpoint returns the actual setup error as JSON with HTTP 503.

`POST /api/analyze-image` accepts a multipart `image` and optional `user_id`. PNG, JPEG, and WEBP images up to 10 MB are analyzed by the configured vision-capable model and returned as structured JSON. Set `GROQ_VISION_MODEL` to override the default model. The API key is read only by Flask from `GROQ_API_KEY`, `groq_api_key`, or `groq_token`; it is never sent from browser code.

The LLM configuration accepts `GROQ_API_KEY`, `groq_api_key`, or `groq_token` from `.env`. `GROQ_MODEL` is optional and defaults to `llama-3.3-70b-versatile`.
