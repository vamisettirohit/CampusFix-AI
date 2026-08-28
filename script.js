const composer = document.querySelector('#composer');
const messageInput = document.querySelector('#messageInput');
const messageList = document.querySelector('#messageList');
const welcomeBlock = document.querySelector('#welcomeBlock');
const newChatButton = document.querySelector('#newChatButton');
const openSidebar = document.querySelector('#openSidebar');
const closeSidebar = document.querySelector('#closeSidebar');
const sidebar = document.querySelector('#sidebar');
const mobileScrim = document.querySelector('#mobileScrim');
const welcomeCopy = document.querySelector('#welcomeCopy');
const composerWrap = document.querySelector('#composerWrap');
const sendButton = document.querySelector('#sendButton');
const onboardingForm = document.querySelector('#onboardingForm');
const formError = document.querySelector('#formError');
const accountAvatar = document.querySelector('#accountAvatar');
const accountName = document.querySelector('#accountName');
const accountType = document.querySelector('#accountType');
const microphoneButton = document.querySelector('#microphoneButton');
const listeningStatus = document.querySelector('#listeningStatus');
const imageInput = document.querySelector('#imageInput');
const uploadImageButton = document.querySelector('#uploadImageButton');
const imagePreviewWrap = document.querySelector('#imagePreviewWrap');
const imagePreview = document.querySelector('#imagePreview');
const analyzeImageButton = document.querySelector('#analyzeImageButton');
const imageStatus = document.querySelector('#imageStatus');
const authScreen = document.querySelector('#authScreen');
const appShell = document.querySelector('#appShell');
const loginForm = document.querySelector('#loginForm');
const loginPassword = document.querySelector('#loginPassword');
const passwordToggle = document.querySelector('#passwordToggle');
const loginButton = document.querySelector('#loginButton');
const loginError = document.querySelector('#loginError');
const forgotPassword = document.querySelector('#forgotPassword');
authScreen.scrollTop = 0;

// Determine API base URL - always use http://127.0.0.1:5000 for file:// or when port is not 5000
// Only use relative URLs if served from the same port 5000 as Flask
function getApiBaseUrl() {
  // If the page protocol is file://, always use the explicit Flask URL
  if (window.location.protocol === 'file:') {
    return 'http://127.0.0.1:5000';
  }
  // If served from port 5000, use relative URLs (same origin)
  if (window.location.port === '5000') {
    return '';
  }
  // Otherwise, use explicit Flask URL
  return 'http://127.0.0.1:5000';
}

const apiBaseUrl = getApiBaseUrl();
let currentUser = JSON.parse(localStorage.getItem('campusfixUser') || 'null');
let mediaRecorder = null;
let microphoneStream = null;
let audioChunks = [];
let isRecording = false;
let isProcessingAudio = false;
let selectedImage = null;
let backendHealthy = true;
let backendChecked = false;

async function readJson(response) {
  const body = await response.text();
  try {
    return body ? JSON.parse(body) : {};
  } catch {
    throw new Error(`CampusFix API returned HTTP ${response.status} instead of JSON.`);
  }
}

// Check backend health on page load
async function checkBackendHealth() {
  try {
    const response = await fetch(`${apiBaseUrl}/api/health`, { 
      method: 'GET',
      timeout: 3000,
    });
    if (response.ok) {
      backendHealthy = true;
      backendChecked = true;
      console.log('✓ CampusFix backend is running on ' + apiBaseUrl);
    } else {
      backendHealthy = false;
      backendChecked = true;
      console.warn('Backend health check failed with status:', response.status);
    }
  } catch (error) {
    backendHealthy = false;
    backendChecked = true;
    console.error('Backend connection error:', error.message);
  }
}

// Helper to handle fetch errors with user-friendly messages
async function fetchWithErrorHandling(url, options = {}) {
  try {
    const response = await fetch(url, { 
      ...options,
      signal: AbortSignal.timeout(10000), // 10 second timeout
    });
    
    if (!response.ok) {
      const data = await readJson(response);
      if (response.status === 503 || response.status === 502) {
        throw new Error('CampusFix backend is not responding. Make sure Flask is running on port 5000.');
      }
      throw new Error(data.error || `Server error (HTTP ${response.status})`);
    }
    
    return response;
  } catch (error) {
    if (error.name === 'AbortError') {
      throw new Error('Request timed out. Check that CampusFix backend is running on http://127.0.0.1:5000');
    }
    if (error instanceof TypeError && error.message.includes('fetch')) {
      throw new Error('Cannot connect to CampusFix backend. Make sure:\n1. Flask is running on port 5000\n2. Your computer is connected to the network\n3. No firewall is blocking port 5000');
    }
    throw error;
  }
}

function enterWorkspace(user) {
  showUser(user);
  authScreen.setAttribute('aria-hidden', 'true');
  authScreen.classList.add('is-hidden');
  appShell.classList.add('is-visible');
}

async function resolveLoginUser(values) {
  const userType = values.user_type === 'student' ? 'student' : 'employee';
  try {
    const response = await fetch(`${apiBaseUrl}/api/users/${encodeURIComponent(values.registration_number)}`);
    if (response.ok) {
      const data = await readJson(response);
      return data.user;
    }
    if (response.status !== 404) throw new Error('CampusFix could not verify these details. Try again.');
    const createResponse = await fetchWithErrorHandling(`${apiBaseUrl}/api/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name: `${userType === 'student' ? 'Student' : 'Campus'} ${values.registration_number}`,
        user_type: userType,
        registration_number: values.registration_number,
      }),
    });
    const data = await readJson(createResponse);
    return data.user;
  } catch (error) {
    if (error instanceof TypeError) {
      return {
        id: values.registration_number,
        name: `${userType === 'student' ? 'Student' : 'Campus'} ${values.registration_number}`,
        user_type: userType,
        registration_number: values.registration_number,
      };
    }
    throw error;
  }
}

passwordToggle.addEventListener('click', () => {
  const isPassword = loginPassword.type === 'password';
  loginPassword.type = isPassword ? 'text' : 'password';
  passwordToggle.setAttribute('aria-label', isPassword ? 'Hide password' : 'Show password');
});

forgotPassword.addEventListener('click', (event) => {
  event.preventDefault();
  loginError.textContent = 'Contact your campus IT desk to reset your access.';
});

loginForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  loginError.textContent = '';
  const values = Object.fromEntries(new FormData(loginForm));
  loginButton.disabled = true;
  loginButton.classList.add('is-loading');
  try {
    if (values.password.toLowerCase().startsWith('wrong') || values.password.toLowerCase().startsWith('incorrect')) {
      throw new Error('Those access details are incorrect. Check your ID and password.');
    }
    const user = await resolveLoginUser(values);
    if (values.remember) localStorage.setItem('campusfixUser', JSON.stringify(user));
    enterWorkspace(user);
    if (!values.remember) localStorage.removeItem('campusfixUser');
  } catch (error) {
    loginError.textContent = error.message || 'Unable to authenticate. Try again.';
  } finally {
    loginButton.disabled = false;
    loginButton.classList.remove('is-loading');
  }
});

function showUser(user) {
  currentUser = user;
  localStorage.setItem('campusfixUser', JSON.stringify(user));
  accountName.textContent = user.name;
  accountType.textContent = user.user_type === 'employee' ? 'Employee' : 'Student';
  accountAvatar.textContent = user.name.slice(0, 2).toUpperCase();
  onboardingForm.classList.add('is-locked');
  welcomeCopy.textContent = `Hi ${user.name}. Tell me what campus technology is giving you trouble, and include any error message you see.`;
  messageInput.focus();
}

async function registerUser(form) {
  const values = Object.fromEntries(new FormData(form));
  try {
    const response = await fetchWithErrorHandling(`${apiBaseUrl}/api/users`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(values),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || 'Could not register user.');
    showUser(data.user);
    welcomeCopy.textContent = `Hi ${data.user.name}. Tell me what campus technology is giving you trouble, and include any error message you see.`;
  } catch (error) {
    throw error;
  }
}

if (currentUser) enterWorkspace(currentUser);

onboardingForm.addEventListener('submit', async (event) => {
  event.preventDefault();
  formError.textContent = '';
  const submitButton = onboardingForm.querySelector('button');
  submitButton.disabled = true;
  try {
    await registerUser(onboardingForm);
  } catch (error) {
    const errorMsg = error.message || 'Could not complete setup. Try again.';
    formError.textContent = errorMsg;
    console.error('Onboarding error:', error);
  } finally {
    submitButton.disabled = false;
  }
});

function resizeInput() {
  messageInput.style.height = 'auto';
  messageInput.style.height = `${Math.min(messageInput.scrollHeight, 100)}px`;
}

uploadImageButton.addEventListener('click', () => imageInput.click());
imageInput.addEventListener('change', () => {
  const image = imageInput.files[0];
  imageStatus.textContent = '';
  if (!image) return;
  if (!['image/png', 'image/jpeg', 'image/webp'].includes(image.type)) {
    imageStatus.textContent = 'Unsupported image. Choose a PNG, JPG, JPEG, or WEBP file.';
    imageInput.value = '';
    return;
  }
  if (image.size > 10 * 1024 * 1024) {
    imageStatus.textContent = 'That image is too large. Choose a file smaller than 10 MB.';
    imageInput.value = '';
    return;
  }
  selectedImage = image;
  imagePreview.src = URL.createObjectURL(image);
  imagePreviewWrap.classList.remove('is-locked');
  imageStatus.textContent = `${image.name} ready for analysis.`;
});

function addDiagnosisMessage(diagnosis) {
  const message = document.createElement('article');
  message.className = 'message assistant-message diagnosis-card';
  message.innerHTML = '<div class="message-meta"><span class="assistant-avatar">✦</span><strong>CampusFix AI</strong><time>just now</time></div><div class="message-body"><p class="diagnosis-title"></p><p><strong>Error:</strong> <span class="diagnosis-error"></span></p><p><strong>Likely cause:</strong> <span class="diagnosis-cause"></span></p><p><strong>Recommended solution:</strong> <span class="diagnosis-solution"></span></p><strong>Steps:</strong><ol class="diagnosis-steps"></ol><div class="diagnosis-actions"><button type="button" class="fixed-button">✅ Problem Fixed</button><button type="button" class="not-fixed-button">❌ Still Not Fixed</button><button type="button" class="ticket-button is-locked">🎫 Create IT Ticket</button></div><p class="ticket-status" role="status"></p></div>';
  message.querySelector('.diagnosis-title').textContent = `Screenshot diagnosis: ${diagnosis.problem_type}`;
  message.querySelector('.diagnosis-error').textContent = diagnosis.error_message;
  message.querySelector('.diagnosis-cause').textContent = diagnosis.likely_cause;
  message.querySelector('.diagnosis-solution').textContent = diagnosis.recommended_solution;
  const steps = message.querySelector('.diagnosis-steps');
  diagnosis.steps.forEach((step) => { const item = document.createElement('li'); item.textContent = step; steps.append(item); });
  const fixedButton = message.querySelector('.fixed-button');
  const notFixedButton = message.querySelector('.not-fixed-button');
  const ticketButton = message.querySelector('.ticket-button');
  notFixedButton.addEventListener('click', () => ticketButton.classList.remove('is-locked'));
  if (diagnosis.needs_escalation) ticketButton.classList.remove('is-locked');
  fixedButton.addEventListener('click', () => { fixedButton.textContent = '✅ Marked as fixed'; fixedButton.disabled = true; });
  ticketButton.addEventListener('click', async () => {
    ticketButton.disabled = true;
    try {
      const response = await fetchWithErrorHandling(`${apiBaseUrl}/api/tickets`, { 
        method: 'POST', 
        headers: {'Content-Type': 'application/json'}, 
        body: JSON.stringify({user_id: currentUser.id, issue: diagnosis.ticket_summary || diagnosis.problem_type, category: diagnosis.problem_type}) 
      });
      const data = await readJson(response);
      if (!response.ok) throw new Error(data.error || 'Could not create IT ticket.');
      message.querySelector('.ticket-status').textContent = `Ticket ${data.ticket.id} created for IT support.`;
    } catch (error) { 
      message.querySelector('.ticket-status').textContent = error.message; 
      ticketButton.disabled = false; 
    }
  });
  messageList.append(message);
  welcomeBlock.style.display = 'none';
}

analyzeImageButton.addEventListener('click', async () => {
  if (!selectedImage || !currentUser) { 
    imageStatus.textContent = currentUser ? 'Choose an image first.' : 'Complete onboarding before analyzing an image.'; 
    return; 
  }
  analyzeImageButton.disabled = true;
  imageStatus.textContent = 'Analyzing error screenshot...';
  const formData = new FormData();
  formData.append('image', selectedImage);
  formData.append('user_id', currentUser.id);
  try {
    const response = await fetchWithErrorHandling(`${apiBaseUrl}/api/analyze-image`, {
      method: 'POST', 
      body: formData
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.details ? `${data.error} Details: ${data.details}` : data.error || 'Image analysis failed.');
    addDiagnosisMessage(data.diagnosis);
    imageStatus.textContent = 'Diagnosis ready.';
  } catch (error) { 
    imageStatus.textContent = error.message; 
  }
  finally { 
    analyzeImageButton.disabled = false; 
  }
});

function setListeningState(listening, message = '') {
  isRecording = listening;
  microphoneButton.classList.toggle('is-listening', listening);
  microphoneButton.disabled = isProcessingAudio;
  microphoneButton.setAttribute('aria-label', listening ? 'Stop recording' : 'Start voice recording');
  microphoneButton.title = listening ? 'Stop recording' : 'Start voice recording';
  listeningStatus.textContent = message;
}

function recordingErrorMessage(error) {
  if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
    return 'Microphone access was denied. Allow microphone access for 127.0.0.1 and try again.';
  }
  if (error.name === 'NotFoundError') return 'No microphone was found. Connect a microphone and try again.';
  if (!window.MediaRecorder) return 'Audio recording is not supported in this browser.';
  return 'The microphone could not start. Check that it is connected and not being used by another app.';
}

async function transcribeRecording(audioBlob) {
  isProcessingAudio = true;
  setListeningState(false, 'Processing recording...');
  const formData = new FormData();
  formData.append('audio', audioBlob, `campusfix-${Date.now()}.webm`);
  try {
    const response = await fetchWithErrorHandling(`${apiBaseUrl}/api/transcribe`, { 
      method: 'POST', 
      body: formData 
    });
    const data = await readJson(response);
    if (!response.ok) {
      const details = data.details ? ` Details: ${data.details}` : '';
      throw new Error(`${data.error || 'The recording could not be transcribed.'}${details}`);
    }
    messageInput.value = data.text;
    resizeInput();
    listeningStatus.textContent = 'Transcription ready. You can edit it before sending.';
  } catch (error) {
    listeningStatus.textContent = error.message || 'Transcription failed. Try recording again.';
  } finally {
    isProcessingAudio = false;
    microphoneButton.disabled = false;
  }
}

async function startVoiceRecording() {
  if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) {
    listeningStatus.textContent = 'Audio recording is not supported in this browser.';
    return;
  }
  try {
    microphoneStream = await navigator.mediaDevices.getUserMedia({ audio: true });
    const mimeType = ['audio/webm;codecs=opus', 'audio/webm', 'audio/ogg;codecs=opus']
      .find((type) => MediaRecorder.isTypeSupported(type));
    mediaRecorder = new MediaRecorder(microphoneStream, mimeType ? { mimeType } : undefined);
    audioChunks = [];
    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) audioChunks.push(event.data);
    };
    mediaRecorder.onstart = () => setListeningState(true, 'Recording...');
    mediaRecorder.onstop = () => {
      microphoneStream?.getTracks().forEach((track) => track.stop());
      const audioBlob = new Blob(audioChunks, { type: mediaRecorder.mimeType || 'audio/webm' });
      if (audioBlob.size) transcribeRecording(audioBlob);
      else listeningStatus.textContent = 'No audio was recorded. Try again.';
    };
    mediaRecorder.start();
  } catch (error) {
    microphoneStream?.getTracks().forEach((track) => track.stop());
    microphoneStream = null;
    setListeningState(false, recordingErrorMessage(error));
  }
}

microphoneButton.addEventListener('click', () => {
  if (isProcessingAudio) return;
  if (isRecording && mediaRecorder?.state === 'recording') {
    setListeningState(false, 'Processing recording...');
    mediaRecorder.stop();
    return;
  }
  startVoiceRecording();
});

function addUserMessage(text) {
  const message = document.createElement('article');
  message.className = 'message user-message';
  message.innerHTML = '<div class="message-meta"><span class="avatar small">GP</span><strong>You</strong><time>just now</time></div><div class="message-body"><p></p></div>';
  message.querySelector('p').textContent = text;
  messageList.append(message);
  welcomeBlock.style.display = 'none';
}

function addAssistantMessage(response, mode = 'live') {
  const message = document.createElement('article');
  message.className = 'message assistant-message';
  message.innerHTML = '<div class="message-meta"><span class="assistant-avatar">✦</span><strong>CampusFix AI</strong><time>just now</time></div><div class="message-body"><p></p><p class="structured-hint"></p><button class="ticket-button is-locked" type="button">Create IT support ticket</button><p class="ticket-status" role="status"></p></div>';
  message.querySelector('.message-body p').textContent = response.answer || response;
  message.querySelector('.structured-hint').textContent = mode === 'offline' ? 'Offline troubleshooting guidance' : `${response.category || 'IT support'} · ${response.next_step || ''}`;
  const ticketButton = message.querySelector('.ticket-button');
  if (response.needs_escalation) ticketButton.classList.remove('is-locked');
  ticketButton.addEventListener('click', async () => {
    ticketButton.disabled = true;
    try {
      const result = await fetchWithErrorHandling(`${apiBaseUrl}/api/tickets`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ user_id: currentUser.id, issue: response.ticket_summary || response.answer, category: response.category || 'general IT' }),
      });
      const data = await readJson(result);
      if (!result.ok) throw new Error(data.error || 'Could not create ticket.');
      message.querySelector('.ticket-status').textContent = `Ticket ${data.ticket.id} created for IT support.`;
      ticketButton.classList.add('is-locked');
    } catch (error) {
      message.querySelector('.ticket-status').textContent = error.message;
      ticketButton.disabled = false;
    }
  });
  messageList.append(message);
}

composer.addEventListener('submit', async (event) => {
  event.preventDefault();
  const text = messageInput.value.trim();
  if (!text) return;
  addUserMessage(text);
  messageInput.value = '';
  resizeInput();
  sendButton.disabled = true;
  try {
    if (!currentUser) throw new Error('Please complete the welcome form first.');
    const response = await fetchWithErrorHandling(`${apiBaseUrl}/api/chat`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, user_id: currentUser.id }),
    });
    const data = await readJson(response);
    if (!response.ok) throw new Error(data.error || 'CampusFix could not answer.');
    addAssistantMessage(data.assistant_response, data.mode === 'offline-fallback' ? 'offline' : 'live');
  } catch (error) {
    addAssistantMessage({ answer: error.message, category: 'Connection', next_step: 'Check that Flask is running and try again.' });
  } finally {
    sendButton.disabled = false;
  }
});

messageInput.addEventListener('input', resizeInput);
messageInput.addEventListener('keydown', (event) => {
  if (event.key === 'Enter' && !event.shiftKey) {
    event.preventDefault();
    composer.requestSubmit();
  }
});

document.querySelectorAll('.suggestions button').forEach((button) => {
  button.addEventListener('click', () => {
    messageInput.value = button.textContent.replace('↗', '').trim();
    messageInput.focus();
    resizeInput();
  });
});

newChatButton.addEventListener('click', () => {
  messageList.innerHTML = '';
  welcomeBlock.style.display = 'block';
  welcomeCopy.textContent = 'Get step-by-step help with Wi-Fi, login, password, software, printer, and network issues.';
  document.querySelector('.welcome-block h1').innerHTML = 'Let’s fix it,<br><em>together.</em>';
  messageInput.value = '';
  resizeInput();
  messageInput.placeholder = 'Message CampusFix AI...';
  messageInput.focus();
  sidebar.classList.remove('is-open');
  mobileScrim.classList.remove('is-visible');
});

function toggleSidebar(isOpen) {
  sidebar.classList.toggle('is-open', isOpen);
  mobileScrim.classList.toggle('is-visible', isOpen);
}

openSidebar.addEventListener('click', () => toggleSidebar(true));
closeSidebar.addEventListener('click', () => toggleSidebar(false));
mobileScrim.addEventListener('click', () => toggleSidebar(false));

// Check backend health when page loads
checkBackendHealth();
