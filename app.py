import os
import json
import base64
from io import BytesIO
import tempfile
from threading import Lock
from urllib import error as urllib_error
from urllib import request as urllib_request
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, jsonify, request, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError, PyMongoError

try:
    from faster_whisper import WhisperModel
except ImportError:
    WhisperModel = None

try:
    from PIL import Image
    from rapidocr_onnxruntime import RapidOCR
except ImportError:
    Image = None
    RapidOCR = None


load_dotenv()

app = Flask(__name__, static_folder=".", static_url_path="")
# Enable CORS for all routes, including file:// origins
CORS(app, origins="*", supports_credentials=True)


@app.errorhandler(400)
def bad_request(error):
    return jsonify({"error": "bad request", "details": str(error)}), 400


@app.errorhandler(403)
def forbidden(error):
    return jsonify({"error": "forbidden", "details": str(error)}), 403


@app.errorhandler(404)
def not_found(error):
    return jsonify({"error": "not found", "details": str(error)}), 404


@app.errorhandler(405)
def method_not_allowed(error):
    return jsonify({"error": "method not allowed", "details": str(error)}), 405

mongo_uri = os.getenv("MONGO_URI") or os.getenv("mongourli") or os.getenv("mongo_url")
if not mongo_uri:
    raise RuntimeError("MONGO_URI is missing from the environment")

mongo_client = MongoClient(mongo_uri, serverSelectionTimeoutMS=5000)
database = mongo_client[os.getenv("MONGO_DB_NAME", "campusfix_ai")]
users_collection = database.users
conversations_collection = database.conversations
tickets_collection = database.tickets

APP_DESCRIPTION = (
    "CampusFix AI is an AI-powered campus IT support agent that helps students "
    "and faculty solve Wi-Fi, login, password, software, printer, and network "
    "issues. It asks diagnostic questions, provides step-by-step troubleshooting, "
    "and escalates unresolved issues to the IT team."
)
provider_alias = os.getenv("qroq_url", "").strip()
GROQ_API_URL = os.getenv("GROQ_API_URL") or (
    provider_alias if provider_alias.startswith("http") else "https://api.groq.com/openai/v1/chat/completions"
)
LLM_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")
WHISPER_MODEL_SIZE = os.getenv("WHISPER_MODEL_SIZE", "tiny")
whisper_model = None
whisper_model_lock = Lock()
ocr_engine = None
ocr_engine_lock = Lock()
LLM_SCHEMA = {
    "type": "object",
    "properties": {
        "answer": {"type": "string"},
        "category": {"type": "string"},
        "disposal_method": {"type": "string"},
        "next_step": {"type": "string"},
        "resolved": {"type": "boolean"},
        "needs_escalation": {"type": "boolean"},
        "ticket_summary": {"type": "string"},
    },
    "required": [
        "answer", "category", "disposal_method", "next_step", "resolved",
        "needs_escalation", "ticket_summary",
    ],
    "additionalProperties": False,
}
IMAGE_SCHEMA = {
    "type": "object",
    "properties": {
        "error_message": {"type": "string"},
        "problem_type": {"type": "string"},
        "likely_cause": {"type": "string"},
        "recommended_solution": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "string"}},
        "needs_escalation": {"type": "boolean"},
        "ticket_summary": {"type": "string"},
    },
    "required": [
        "error_message", "problem_type", "likely_cause", "recommended_solution",
        "steps", "needs_escalation", "ticket_summary",
    ],
    "additionalProperties": False,
}


def utc_now():
    return datetime.now(timezone.utc)


@app.get("/")
def frontend():
    return send_from_directory(app.static_folder, "index.html")


def ask_llm(message, user, history):
    api_key = (
        os.getenv("GROQ_API_KEY")
        or os.getenv("groq_api_key")
        or os.getenv("groq_token")
        or (provider_alias if not provider_alias.startswith("http") else "")
    )
    if not api_key:
        raise RuntimeError("GROQ_API_KEY, groq_api_key, or groq_token is missing from the environment")

    system_prompt = f"""You are CampusFix AI, a practical and accurate campus IT support assistant.
{APP_DESCRIPTION}
The current user is {user['name']}, a {user['user_type']}, with registration number {user['registration_number']}.
Answer the user's question directly. Stay relevant to Wi-Fi, login, password, software, printer,
network, and other campus IT support issues. Ask a concise diagnostic question when needed and give
safe, numbered troubleshooting steps. If a question is unrelated, briefly say so and offer to help
with CampusFix AI. Set resolved to true only when the user confirms the issue is fixed.
Return valid JSON matching the supplied schema. Put the natural-language response in answer.
For category, use an IT issue category, otherwise use "general IT".
For disposal_method, provide relevant troubleshooting or safety guidance. For next_step, give one concise actionable step.
Set needs_escalation to true when the issue remains unresolved after reasonable troubleshooting.
When escalation is needed, write a concise ticket_summary; otherwise use an empty string."""

    messages = [{"role": "system", "content": system_prompt}]
    messages.extend({"role": item["role"], "content": item["content"]} for item in history[-8:])
    messages.append({"role": "user", "content": message})
    body = json.dumps({
        "model": LLM_MODEL,
        "messages": messages,
        "temperature": 0.2,
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": "campusfix_response", "schema": LLM_SCHEMA},
        },
    }).encode("utf-8")
    request = urllib_request.Request(
        GROQ_API_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=45) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"].get("content", "").strip()
        if not content:
            raise RuntimeError("The LLM returned an empty response. Try again.")
        if content.startswith("```"):
            content = content.strip("`").replace("json\n", "", 1).strip()
        try:
            parsed = json.loads(content)
        except json.JSONDecodeError as error:
            raise RuntimeError("The LLM returned an invalid JSON response. Try again.") from error
        if not isinstance(parsed, dict) or not parsed.get("answer"):
            raise RuntimeError("The LLM response was missing its answer. Try again.")
        return {
            "answer": str(parsed.get("answer", "")),
            "category": str(parsed.get("category", "not applicable")),
            "disposal_method": str(parsed.get("disposal_method", "See local recycling guidance.")),
            "next_step": str(parsed.get("next_step", "Reply with what happened after that step.")),
            "resolved": bool(parsed.get("resolved", False)),
            "needs_escalation": bool(parsed.get("needs_escalation", False)),
            "ticket_summary": str(parsed.get("ticket_summary", "")),
        }
    except urllib_error.HTTPError as error:
        response_body = error.read().decode("utf-8", errors="replace")
        if error.code == 403 and "1010" in response_body:
            raise RuntimeError(
                "Groq access is blocked by the current network (HTTP 403, edge error 1010). "
                "Try another network or VPN, then restart Flask."
            ) from error
        if error.code in {401, 403}:
            raise RuntimeError(
                f"Groq rejected the LLM token (HTTP {error.code}). Check groq_token in .env and restart Flask."
            ) from error
        raise RuntimeError(f"LLM request failed with HTTP {error.code}") from error
    except (urllib_error.URLError, KeyError, json.JSONDecodeError) as error:
        raise RuntimeError(f"LLM request failed: {error}") from error


def groq_api_key():
    return (
        os.getenv("GROQ_API_KEY")
        or os.getenv("groq_api_key")
        or os.getenv("groq_token")
        or (provider_alias if not provider_alias.startswith("http") else "")
    )



def vision_diagnosis(image_data, mime_type, user):
    api_key = groq_api_key()
    if not api_key:
        raise RuntimeError("GROQ_API_KEY, groq_api_key, or groq_token is missing from the environment")
    prompt = f"""You are CampusFix AI diagnosing a campus IT error screenshot for {user['name']}.
Identify visible error text or code, classify the IT problem, explain the likely cause, and provide
safe, numbered troubleshooting steps. Do not invent text that is not visible; use 'Not clearly visible'
when necessary. Do not ask for passwords or one-time codes. Return JSON matching the supplied schema.
Set needs_escalation true only when the screenshot indicates the issue needs IT intervention."""
    content = [
        {"type": "text", "text": prompt},
        {"type": "image_url", "image_url": {"url": f"data:{mime_type};base64,{base64.b64encode(image_data).decode('ascii')}"}},
    ]
    body = json.dumps({
        "model": os.getenv("GROQ_VISION_MODEL", "meta-llama/llama-4-scout-17b-16e-instruct"),
        "messages": [
            {"role": "system", "content": "Return only valid JSON for the CampusFix image diagnosis schema."},
            {"role": "user", "content": content},
        ],
        "temperature": 0.1,
        "response_format": {"type": "json_schema", "json_schema": {"name": "campusfix_image_diagnosis", "schema": IMAGE_SCHEMA}},
    }).encode("utf-8")
    request = urllib_request.Request(
        GROQ_API_URL,
        data=body,
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib_request.urlopen(request, timeout=60) as response:
            result = json.loads(response.read().decode("utf-8"))
        content = result["choices"][0]["message"].get("content", "").strip()
        parsed = json.loads(content)
        if not isinstance(parsed, dict) or not parsed.get("problem_type"):
            raise RuntimeError("The vision model returned an incomplete diagnosis")
        return {
            "error_message": str(parsed.get("error_message", "Not clearly visible")),
            "problem_type": str(parsed["problem_type"]),
            "likely_cause": str(parsed.get("likely_cause", "Not determined")),
            "recommended_solution": str(parsed.get("recommended_solution", "Contact campus IT support.")),
            "steps": [str(step) for step in parsed.get("steps", [])],
            "needs_escalation": bool(parsed.get("needs_escalation", False)),
            "ticket_summary": str(parsed.get("ticket_summary", "")),
        }
    except urllib_error.HTTPError as error:
        details = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Vision service returned HTTP {error.code}: {details}") from error
    except (urllib_error.URLError, json.JSONDecodeError, KeyError) as error:
        raise RuntimeError(f"Vision analysis failed: {error}") from error


def local_ocr_engine():
    global ocr_engine
    if Image is None or RapidOCR is None:
        raise RuntimeError("local OCR is not installed; run '.venv\\Scripts\\python.exe -m pip install -r requirements.txt'")
    if ocr_engine is None:
        with ocr_engine_lock:
            if ocr_engine is None:
                ocr_engine = RapidOCR()
    return ocr_engine


IT_IMAGE_KNOWLEDGE_BASE = [
    ({"wifi", "wi-fi", "internet", "network", "dns", "connection"}, "Wi-Fi/network error", "The device may be disconnected, using incorrect network settings, or unable to reach campus services.", "Reconnect to the campus network and refresh the device network settings.", ["Turn Wi-Fi off and on.", "Forget the campus network and connect again.", "Restart the device and test another campus service.", "If other devices also fail, contact campus IT."], True),
    ({"login", "sign in", "log in", "credential", "account", "authentication"}, "Login/account error", "The account may be locked, the username may be incorrect, or campus authentication may be unavailable.", "Verify the campus username and use the official account recovery page if needed.", ["Check the username and Caps Lock.", "Try the official campus password-reset or account-recovery page.", "Complete multi-factor authentication.", "Contact campus IT if the account remains locked."], True),
    ({"password", "passcode", "one-time", "mfa"}, "Password/account error", "The password may be expired, incorrect, or blocked by account security policy.", "Use the official campus password reset process; never share the password or one-time code.", ["Open the official campus password-reset page.", "Complete identity verification and multi-factor authentication.", "Wait a few minutes, then try signing in again.", "Contact campus IT if reset access fails."], True),
    ({"printer", "print", "paper", "queue", "spooler"}, "Printer error", "The printer may be offline, out of paper, or have a stuck print queue.", "Reconnect the printer and clear the pending print job.", ["Check power, paper, and the printer connection.", "Cancel stuck jobs in the print queue.", "Select the correct campus printer.", "Send a one-page test print and contact IT if it still fails."], True),
    ({"install", "installation", "software", "setup", "application", "app"}, "Software installation error", "The installer may lack permissions, be incompatible, or be blocked by campus security policy.", "Use the approved campus software portal and install a compatible version.", ["Save work and close the application.", "Download the installer only from the approved campus portal.", "Check operating-system compatibility and available storage.", "Contact campus IT if administrator approval is required."], True),
    ({"windows", "system", "blue screen", "update", "boot", "driver"}, "Windows/system error", "A system update, driver, startup service, or device resource may be failing.", "Restart safely and apply approved Windows updates.", ["Record the visible error code.", "Restart the computer once.", "Install pending approved Windows updates.", "Contact campus IT before changing managed system settings."], True),
    ({"browser", "chrome", "edge", "firefox", "website", "certificate", "cache"}, "Browser error", "The browser cache, extension, certificate, or campus website session may be causing the failure.", "Refresh the session and test the service in a private window.", ["Reload the page and check the campus URL.", "Try a private window or another approved browser.", "Disable only nonessential extensions and clear site data.", "Contact campus IT if the same error appears everywhere."], True),
]


def local_ocr_diagnosis(image_data):
    Image.open(BytesIO(image_data)).verify()
    result, _ = local_ocr_engine()(image_data)
    extracted_text = " ".join(item[1] for item in (result or []) if len(item) > 1).strip()
    searchable_text = extracted_text.lower()
    for keywords, problem_type, cause, solution, steps, escalate in IT_IMAGE_KNOWLEDGE_BASE:
        if any(keyword in searchable_text for keyword in keywords):
            return {
                "error_message": extracted_text or "Text detected, but no exact error code was clear.",
                "problem_type": problem_type,
                "likely_cause": cause,
                "recommended_solution": solution,
                "steps": steps,
                "needs_escalation": escalate,
                "ticket_summary": f"{problem_type}: {extracted_text[:300]}",
                "source": "local OCR knowledge base",
            }
    if not extracted_text:
        raise RuntimeError("no readable text was found; upload a clearer screenshot with the error visible")
    return {
        "error_message": extracted_text[:500],
        "problem_type": "Campus IT error",
        "likely_cause": "The screenshot contains an error that is not in the local troubleshooting knowledge base.",
        "recommended_solution": "Contact campus IT with the exact error and affected device.",
        "steps": ["Reproduce the error and record its exact wording.", "Restart the affected application or device.", "Contact campus IT with the screenshot and error text."],
        "needs_escalation": True,
        "ticket_summary": f"Unclassified campus IT error: {extracted_text[:300]}",
        "source": "local OCR knowledge base",
    }


@app.post("/api/analyze-image")
def analyze_image():
    image = request.files.get("image")
    user_id = str(request.form.get("user_id", "")).strip()
    allowed_types = {"image/png", "image/jpeg", "image/webp"}
    if not image or not image.filename:
        return jsonify({"error": "an image is required"}), 400
    if image.mimetype not in allowed_types:
        return jsonify({"error": "unsupported image type; use PNG, JPG, JPEG, or WEBP"}), 415
    image_bytes = image.read()
    if not image_bytes:
        return jsonify({"error": "the image is empty"}), 400
    if len(image_bytes) > 10 * 1024 * 1024:
        return jsonify({"error": "the image must be smaller than 10 MB"}), 413
    try:
        user = users_collection.find_one({"_id": user_id}) if user_id else {"name": "CampusFix user"}
        if user_id and not user:
            return jsonify({"error": "user was not found"}), 404
        try:
            diagnosis = vision_diagnosis(image_bytes, image.mimetype, user)
        except RuntimeError as vision_error:
            app.logger.warning("Vision analysis unavailable; using local OCR fallback: %s", vision_error)
            try:
                diagnosis = local_ocr_diagnosis(image_bytes)
            except Exception as ocr_error:
                app.logger.error("Local OCR fallback failed: %s", ocr_error)
                return jsonify({
                    "error": "We could not read this screenshot. Please upload a clearer image with the error message visible.",
                    "details": str(ocr_error),
                    "fallback": "local OCR",
                }), 422
        return jsonify({"diagnosis": diagnosis}), 200
    except RuntimeError as error:
        app.logger.error("Image diagnosis failed: %s", error)
        return jsonify({"error": "image diagnosis failed", "details": str(error)}), 502


def local_whisper_model():
    global whisper_model
    if WhisperModel is None:
        raise RuntimeError(
            "faster-whisper is not installed. Run '.venv\\Scripts\\python.exe -m pip install -r requirements.txt'."
        )
    if whisper_model is None:
        with whisper_model_lock:
            if whisper_model is None:
                app.logger.info("Loading local Whisper model: %s", WHISPER_MODEL_SIZE)
                whisper_model = WhisperModel(
                    WHISPER_MODEL_SIZE,
                    device=os.getenv("WHISPER_DEVICE", "cpu"),
                    compute_type=os.getenv("WHISPER_COMPUTE_TYPE", "int8"),
                )
    return whisper_model


@app.post("/api/transcribe")
def transcribe_audio():
    audio = request.files.get("audio")
    if not audio or not audio.filename:
        return jsonify({"error": "an audio recording is required"}), 400
    audio_bytes = audio.read()
    app.logger.info(
        "Transcription upload received: filename=%s mimetype=%s bytes=%d",
        audio.filename,
        audio.mimetype,
        len(audio_bytes),
    )
    if not audio_bytes:
        return jsonify({"error": "the audio recording was empty"}), 400
    if len(audio_bytes) > 25 * 1024 * 1024:
        return jsonify({"error": "the audio recording must be smaller than 25 MB"}), 413

    suffix = os.path.splitext(audio.filename)[1].lower()
    if suffix not in {".webm", ".ogg", ".wav", ".mp3", ".mp4", ".m4a"}:
        suffix = ".webm"
    temporary_path = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temporary_file:
            temporary_file.write(audio_bytes)
            temporary_path = temporary_file.name
        segments, _ = local_whisper_model().transcribe(
            temporary_path,
            language="en",
            vad_filter=True,
        )
        text = " ".join(segment.text.strip() for segment in segments if segment.text.strip()).strip()
        if not text:
            return jsonify({"error": "local Whisper detected no speech in the recording"}), 422
        return jsonify({"text": text}), 200
    except RuntimeError as error:
        app.logger.error("Local transcription setup failed: %s", error)
        return jsonify({"error": "local speech transcription is unavailable", "details": str(error)}), 503
    except Exception as error:
        app.logger.exception("Local transcription failed")
        return jsonify({"error": "local speech transcription failed", "details": str(error)}), 502
    finally:
        if temporary_path:
            try:
                os.unlink(temporary_path)
            except OSError:
                app.logger.warning("Could not remove temporary audio file: %s", temporary_path)


def local_campusfix_response(message):
    """Provide useful offline guidance while the remote provider is unavailable."""
    text = message.lower()
    responses = [
        ("wifi", "Turn Wi-Fi off and on, then reconnect to the campus network. If it still fails, forget the network and sign in again.", "Wi-Fi", "Never share your campus password in chat.", "Tell me the exact error or whether other devices can connect."),
        ("login", "Check that your campus username is correct and Caps Lock is off. If login still fails, use the official password-reset page.", "Login", "Never send your password or one-time code to support staff.", "Tell me which campus service is rejecting the login."),
        ("password", "Use the campus password-reset process and complete multi-factor authentication. A reset may take a few minutes to reach every service.", "Password", "CampusFix staff will never need your password.", "Try the service again and report the exact message if it fails."),
        ("printer", "Check that the printer is powered on, has paper, and is selected. Cancel stuck jobs, then send a one-page test print.", "Printer", "Do not open or repair electrical components yourself.", "Tell me the printer name and error shown on your computer."),
        ("software", "Restart the application and check for updates from the approved campus software portal. Save your work before reinstalling anything.", "Software", "Install software only from approved campus or vendor sources.", "Tell me the application name, version, and operating system."),
        ("network", "Restart your device and check whether the problem affects one service or the whole campus network. Capture the exact error.", "Network", "Do not change managed network settings without IT guidance.", "Tell me your location and whether nearby users have the same issue."),
    ]
    for keyword, answer, category, disposal_method, next_step in responses:
        if keyword in text:
            return {"answer": answer, "category": category, "disposal_method": disposal_method, "next_step": next_step, "resolved": False, "needs_escalation": False, "ticket_summary": "", "offline": True}
    return {
        "answer": "I can help with campus Wi-Fi, login, password, software, printer, and network issues. Tell me what stopped working and the exact error.",
        "category": "general IT",
        "disposal_method": "Do not share passwords, one-time codes, or sensitive personal data in chat.",
        "next_step": "Tell me the affected service, device, and exact error message.",
        "resolved": False,
        "needs_escalation": False,
        "ticket_summary": "",
        "offline": True,
    }


def serialize_user(user):
    return {
        "id": str(user["_id"]),
        "name": user["name"],
        "user_type": user["user_type"],
        "registration_number": user["registration_number"],
        "description": user.get("description", APP_DESCRIPTION),
        "created_at": user["created_at"].isoformat(),
    }


@app.get("/api/health")
def health():
    try:
        mongo_client.admin.command("ping")
        return jsonify({"status": "ok", "database": "connected"})
    except PyMongoError as error:
        return jsonify({"status": "error", "message": str(error)}), 503


@app.get("/api/app")
def app_info():
    return jsonify({"name": "CampusFix AI", "description": APP_DESCRIPTION})


@app.post("/api/users")
def create_user():
    payload = request.get_json(silent=True) or {}
    name = str(payload.get("name", "")).strip()
    user_type = str(payload.get("user_type", "")).strip().lower()
    user_type = {"students": "student", "employees": "employee", "employe": "employee", "employes": "employee"}.get(user_type, user_type)
    registration_number = str(payload.get("registration_number", "")).strip()

    if not name or user_type not in {"student", "employee"} or not registration_number:
        return jsonify({
            "error": "name, user_type (student or employee), and registration_number are required"
        }), 400

    user = {
        "_id": registration_number,
        "name": name,
        "user_type": user_type,
        "registration_number": registration_number,
        "description": payload.get("description", APP_DESCRIPTION),
        "created_at": utc_now(),
    }

    try:
        users_collection.insert_one(user)
        return jsonify({"user": serialize_user(user)}), 201
    except DuplicateKeyError:
        return jsonify({"error": "registration_number already exists"}), 409
    except PyMongoError as error:
        return jsonify({"error": "could not save user", "details": str(error)}), 503


@app.get("/api/users")
def list_users():
    try:
        users = [serialize_user(user) for user in users_collection.find().sort("created_at", -1)]
        return jsonify({"users": users})
    except PyMongoError as error:
        return jsonify({"error": "could not load users", "details": str(error)}), 503


@app.get("/api/users/<registration_number>")
def get_user(registration_number):
    try:
        user = users_collection.find_one({"_id": registration_number})
        if not user:
            return jsonify({"error": "user was not found"}), 404
        return jsonify({"user": serialize_user(user)})
    except PyMongoError as error:
        return jsonify({"error": "could not load user", "details": str(error)}), 503


@app.post("/api/chat")
def save_chat_message():
    payload = request.get_json(silent=True) or {}
    message = str(payload.get("message", "")).strip()
    user_id = str(payload.get("user_id", "")).strip()

    if not message:
        return jsonify({"error": "message is required"}), 400

    try:
        if not user_id:
            return jsonify({"error": "complete onboarding before sending a message"}), 400
        user = users_collection.find_one({"_id": user_id})
        if not user:
            return jsonify({"error": "user was not found"}), 404
        prior_messages = list(conversations_collection.find(
            {"user_id": user_id}, {"role": 1, "content": 1, "_id": 0}
        ).sort("created_at", 1).limit(8))
        try:
            answer = ask_llm(message, user, prior_messages)
        except RuntimeError:
            answer = local_campusfix_response(message)
        now = utc_now()
        conversation = {
            "user_id": user_id,
            "role": "user",
            "content": message,
            "created_at": now,
        }
        conversations_collection.insert_one(conversation)
        conversations_collection.insert_one({
            "user_id": user_id,
            "role": "assistant",
            "content": answer["answer"],
            "structured_response": answer,
            "created_at": utc_now(),
        })
        return jsonify({
            "message": message,
            "status": "answered",
            "assistant_response": answer,
            "mode": "offline-fallback" if answer.get("offline") else "llm",
        }), 201
    except RuntimeError as error:
        return jsonify({"error": str(error)}), 502
    except PyMongoError as error:
        return jsonify({"error": "could not save conversation", "details": str(error)}), 503


@app.post("/api/tickets")
def create_ticket():
    payload = request.get_json(silent=True) or {}
    user_id = str(payload.get("user_id", "")).strip()
    issue = str(payload.get("issue", "")).strip()
    category = str(payload.get("category", "general IT")).strip()

    if not user_id or not issue:
        return jsonify({"error": "user_id and issue are required"}), 400

    try:
        user = users_collection.find_one({"_id": user_id})
        if not user:
            return jsonify({"error": "user was not found"}), 404
        ticket = {
            "user_id": user_id,
            "issue": issue,
            "category": category,
            "status": "open",
            "created_at": utc_now(),
        }
        result = tickets_collection.insert_one(ticket)
        return jsonify({
            "ticket": {
                "id": str(result.inserted_id),
                "user_id": user_id,
                "issue": issue,
                "category": category,
                "status": ticket["status"],
                "created_at": ticket["created_at"].isoformat(),
            }
        }), 201
    except PyMongoError as error:
        return jsonify({"error": "could not create ticket", "details": str(error)}), 503


@app.get("/api/users/<registration_number>/tickets")
def list_user_tickets(registration_number):
    try:
        if not users_collection.find_one({"_id": registration_number}):
            return jsonify({"error": "user was not found"}), 404
        tickets = [{
            "id": str(ticket["_id"]),
            "user_id": ticket["user_id"],
            "issue": ticket["issue"],
            "category": ticket.get("category", "general IT"),
            "status": ticket.get("status", "open"),
            "created_at": ticket["created_at"].isoformat(),
        } for ticket in tickets_collection.find({"user_id": registration_number}).sort("created_at", -1)]
        return jsonify({"tickets": tickets})
    except PyMongoError as error:
        return jsonify({"error": "could not load tickets", "details": str(error)}), 503


if __name__ == "__main__":
    app.run(host="127.0.0.1", port=int(os.getenv("PORT", "5000")), debug=True)