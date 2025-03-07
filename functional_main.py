import os
import json
import base64
import asyncio
import argparse
from fastapi import FastAPI, WebSocket, BackgroundTasks, Form, Request
from fastapi.responses import JSONResponse, HTMLResponse
from fastapi.websockets import WebSocketDisconnect
from twilio.rest import Client
import websockets
from dotenv import load_dotenv, dotenv_values
import uvicorn
import re
import time
import sys
from twilio.twiml.voice_response import VoiceResponse, Connect

load_dotenv()

# Configuration
TWILIO_ACCOUNT_SID = os.getenv('TWILIO_ACCOUNT_SID')
TWILIO_AUTH_TOKEN = os.getenv('TWILIO_AUTH_TOKEN')
TWILIO_PHONE_NUMBER = os.getenv('TWILIO_PHONE_NUMBER')
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY')

# Get domain directly from .env file to ensure correct value
env_config = dotenv_values(".env")
raw_domain = env_config.get('DOMAIN', '')
print(f"Domain from .env file: {raw_domain}")
DOMAIN = re.sub(r'(^\w+:|^)\/\/|\/+$', '', raw_domain) # Strip protocols and trailing slashes from DOMAIN
print(f"Processed domain for WebSocket URL: {DOMAIN}")

PORT = int(os.getenv('PORT', 5050))
SYSTEM_MESSAGE = (
    "You are a helpful and bubbly AI assistant who loves to chat about "
    "anything the user is interested in and is prepared to offer them facts. "
    "You have a penchant for dad jokes, owl jokes, and rickrolling – subtly. "
    "Always stay positive, but work in a joke when appropriate."
)
VOICE = 'alloy'
LOG_EVENT_TYPES = [
    'error', 'response.content.done', 'rate_limits.updated', 'response.done',
    'input_audio_buffer.committed', 'input_audio_buffer.speech_stopped',
    'input_audio_buffer.speech_started', 'session.created'
]

app = FastAPI()

if not (TWILIO_ACCOUNT_SID and TWILIO_AUTH_TOKEN and TWILIO_PHONE_NUMBER and OPENAI_API_KEY):
    raise ValueError('Missing Twilio and/or OpenAI environment variables. Please set them in the .env file.')

# Initialize Twilio client
client = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)

@app.get('/', response_class=HTMLResponse)
async def index_page():
    """Render a simple UI for making outbound calls and instructions."""
    return """
    <html>
        <head>
            <title>Twilio + OpenAI Voice Assistant</title>
            <style>
                body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
                h1 { color: #1a73e8; }
                .section { margin-bottom: 30px; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }
                form { margin-top: 15px; }
                input, button { padding: 8px; margin: 5px 0; }
                button { background-color: #1a73e8; color: white; border: none; cursor: pointer; }
                pre { background-color: #f5f5f5; padding: 10px; border-radius: 5px; overflow-x: auto; }
            </style>
        </head>
        <body>
            <h1>Twilio + OpenAI Voice Assistant</h1>
            
            <div class="section">
                <h2>Make an Outbound Call</h2>
                <form action="/web-make-call" method="post">
                    <label for="to">Phone Number to Call:</label><br>
                    <input type="tel" id="to" name="to" placeholder="+1234567890" required><br>
                    <button type="submit">Make Call</button>
                </form>
            </div>
            
            <div class="section">
                <h2>Command Line Usage</h2>
                <p>You can also make calls from the command line:</p>
                <pre>python functional_main.py --call +1234567890</pre>
            </div>
        </body>
    </html>
    """

@app.websocket('/media-stream')
async def handle_media_stream(websocket: WebSocket):
    """Handle WebSocket connections between Twilio and OpenAI."""
    print("WebSocket connection attempt received at /media-stream")
    await websocket.accept()
    print("WebSocket connection accepted")
    stream_sid = None  # Define stream_sid at this scope level

    try:
        url = 'wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview-2024-12-17'
        print(f"Connecting to OpenAI at {url}")
        
        async with websockets.connect(
            url,
            additional_headers={
                "Authorization": f"Bearer {OPENAI_API_KEY}",
                "OpenAI-Beta": "realtime=v1"
            }
        ) as openai_ws:
            print("Connected to OpenAI WebSocket")
            
            # Initialize session and wait for confirmation
            await initialize_session(openai_ws)
            print("Session initialized with OpenAI")
            
            # Define helper functions with proper access to stream_sid
            async def receive_from_twilio():
                nonlocal stream_sid
                try:
                    async for message in websocket.iter_text():
                        data = json.loads(message)
                        if data['event'] == 'media':
                            try:
                                audio_append = {
                                    "type": "input_audio_buffer.append",
                                    "audio": data['media']['payload']
                                }
                                await openai_ws.send(json.dumps(audio_append))
                                # Uncomment for verbose logging
                                # print("Sent audio to OpenAI")
                            except websockets.exceptions.ConnectionClosed:
                                print("OpenAI WebSocket connection is closed")
                                break
                        elif data['event'] == 'start':
                            stream_sid = data['start']['streamSid']
                            print(f"Incoming stream has started {stream_sid}")
                        elif data['event'] == 'stop':
                            print(f"Stream {stream_sid} has stopped")
                except WebSocketDisconnect:
                    print("Twilio client disconnected")
                except Exception as e:
                    print(f"Error in receive_from_twilio: {e}")

            async def send_to_twilio():
                try:
                    async for openai_message in openai_ws:
                        response = json.loads(openai_message)
                        if response['type'] in LOG_EVENT_TYPES:
                            print(f"Received event: {response['type']}")
                        
                        # Add handling for speech detection to interrupt AI
                        if response["type"] == "input_audio_buffer.speech_started":
                            print('Speech Start:', response['type'])
                            
                            # Clear Twilio buffer
                            if stream_sid:
                                clear_twilio = {
                                    "streamSid": stream_sid,
                                    "event": "clear"
                                }
                                await websocket.send_json(clear_twilio)
                                print('Cleared Twilio buffer.')
                            
                            # Send interrupt message to OpenAI
                            interrupt_message = {
                                "type": "response.cancel"
                            }
                            await openai_ws.send(json.dumps(interrupt_message))
                            print('Cancelling AI speech from the server.')
                        
                        if response['type'] == 'session.updated':
                            print("Session updated successfully:", response)
                        
                        if response['type'] == 'response.audio.delta' and response.get('delta'):
                            if not stream_sid:
                                print("Warning: No stream_sid available yet")
                                continue
                                
                            try:
                                audio_payload = base64.b64encode(base64.b64decode(response['delta'])).decode('utf-8')
                                audio_delta = {
                                    "event": "media",
                                    "streamSid": stream_sid,
                                    "media": {
                                        "payload": audio_payload
                                    }
                                }
                                await websocket.send_json(audio_delta)
                                # Uncomment for verbose logging
                                # print("Sent audio to Twilio")
                            except Exception as e:
                                print(f"Error processing audio data: {e}")
                except websockets.exceptions.ConnectionClosed:
                    print("OpenAI WebSocket connection closed")
                except Exception as e:
                    print(f"Error in send_to_twilio: {e}")

            # Run both tasks concurrently
            await asyncio.gather(receive_from_twilio(), send_to_twilio())
            
    except Exception as e:
        print(f"Failed to connect to OpenAI: {e}")
    finally:
        print("Closing WebSocket connection")
        try:
            await websocket.close()
        except:
            pass

async def send_initial_conversation_item(openai_ws):
    """Send initial conversation so AI talks first."""
    initial_conversation_item = {
        "type": "conversation.item.create",
        "item": {
            "type": "message",
            "role": "user",
            "content": [
                {
                    "type": "input_text",
                    "text": (
                        "Greet the user with 'Hello there! I am an AI voice assistant powered by "
                        "Twilio and the OpenAI Realtime API. You can ask me for facts, jokes, or "
                        "anything you can imagine. How can I help you?'"
                    )
                }
            ]
        }
    }
    await openai_ws.send(json.dumps(initial_conversation_item))
    await openai_ws.send(json.dumps({"type": "response.create"}))

async def initialize_session(openai_ws):
    """Control initial session with OpenAI."""
    session_update = {
        "type": "session.update",
        "session": {
            "turn_detection": {"type": "server_vad"},
            "input_audio_format": "g711_ulaw",
            "output_audio_format": "g711_ulaw",
            "voice": VOICE,
            "instructions": SYSTEM_MESSAGE,
            "modalities": ["text", "audio"],
            "temperature": 0.8,
        }
    }
    print('Sending session update:', json.dumps(session_update))
    await openai_ws.send(json.dumps(session_update))

    # Have the AI speak first
    await send_initial_conversation_item(openai_ws)

async def check_number_allowed(to):
    """Check if a number is allowed to be called."""
    try:
        # Uncomment these lines to test numbers. Only add numbers you have permission to call
        # OVERRIDE_NUMBERS = ['+18005551212'] 
        # if to in OVERRIDE_NUMBERS:             
          # return True

        incoming_numbers = client.incoming_phone_numbers.list(phone_number=to)
        if incoming_numbers:
            return True

        outgoing_caller_ids = client.outgoing_caller_ids.list(phone_number=to)
        if outgoing_caller_ids:
            return True

        return False
    except Exception as e:
        print(f"Error checking phone number: {e}")
        return False

async def make_call(phone_number_to_call: str):
    """Make an outbound call."""
    if not phone_number_to_call:
        raise ValueError("Please provide a phone number to call.")

    is_allowed = await check_number_allowed(phone_number_to_call)
    if not is_allowed:
        raise ValueError(f"The number {phone_number_to_call} is not recognized as a valid outgoing number or caller ID.")

    # Ensure compliance with applicable laws and regulations
    # All of the rules of TCPA apply even if a call is made by AI.
    # Do your own diligence for compliance.

    # Make sure the WebSocket URL is correctly formatted
    websocket_url = f"wss://{DOMAIN}/media-stream"
    print(f"Setting up call with WebSocket URL: {websocket_url}")

    outbound_twiml = (
        f'<?xml version="1.0" encoding="UTF-8"?>'
        f'<Response>'
        f'<Say>Hello! You are about to start a conversation with an AI assistant.</Say>'
        f'<Pause length="1"/>'
        f'<Connect timeout="20">'
        f'<Stream url="{websocket_url}" />'
        f'</Connect>'
        f'<Say>I\'m sorry, but we couldn\'t establish a connection. Please try again later.</Say>'
        f'</Response>'
    )
    
    print(f"Generated TwiML: {outbound_twiml}")

    call = client.calls.create(
        from_=TWILIO_PHONE_NUMBER,
        to=phone_number_to_call,
        twiml=outbound_twiml
    )

    print(f"Call initiated with SID: {call.sid}")
    await log_call_sid(call.sid)

async def log_call_sid(call_sid):
    """Log the call SID."""
    print(f"Call started with SID: {call_sid}")

@app.post("/web-make-call")
async def web_make_call(to: str = Form(...)):
    """Handle web form submission to make an outbound call."""
    try:
        await make_call(to)
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Call Initiated</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                        h1 {{ color: #1a73e8; }}
                        .section {{ margin-bottom: 30px; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                        a {{ color: #1a73e8; text-decoration: none; }}
                        a:hover {{ text-decoration: underline; }}
                    </style>
                </head>
                <body>
                    <h1>Call Initiated</h1>
                    <div class="section">
                        <p>A call has been initiated to {to}.</p>
                        <p><a href="/">Back to home</a></p>
                    </div>
                </body>
            </html>
            """
        )
    except Exception as e:
        return HTMLResponse(
            content=f"""
            <html>
                <head>
                    <title>Error</title>
                    <style>
                        body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }}
                        h1 {{ color: #e81a1a; }}
                        .section {{ margin-bottom: 30px; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }}
                        a {{ color: #1a73e8; text-decoration: none; }}
                        a:hover {{ text-decoration: underline; }}
                    </style>
                </head>
                <body>
                    <h1>Error</h1>
                    <div class="section">
                        <p>An error occurred: {str(e)}</p>
                        <p><a href="/">Back to home</a></p>
                    </div>
                </body>
            </html>
            """
        )

async def test_websocket_connection():
    """Test the WebSocket connection to the media-stream endpoint."""
    url = f"ws://localhost:{PORT}/media-stream"
    print(f"Connecting to {url}...")
    
    try:
        async with websockets.connect(url) as ws:
            print("Connected successfully!")
            
            # Simulate the 'start' event from Twilio
            start_event = {
                "event": "start",
                "start": {
                    "streamSid": "test-stream-sid-123",
                    "accountSid": "test-account-sid",
                    "callSid": "test-call-sid"
                }
            }
            
            print("Sending start event...")
            await ws.send(json.dumps(start_event))
            
            # Wait for a moment to see if we get any response
            print("Waiting for responses...")
            try:
                # Set a timeout for receiving messages
                for _ in range(5):  # Try to receive up to 5 messages
                    response = await asyncio.wait_for(ws.recv(), timeout=5.0)
                    print(f"Received: {response}")
            except asyncio.TimeoutError:
                print("No response received within timeout period")
            
            # Simulate the 'stop' event from Twilio
            stop_event = {
                "event": "stop",
                "streamSid": "test-stream-sid-123"
            }
            
            print("Sending stop event...")
            await ws.send(json.dumps(stop_event))
            
            print("Test completed successfully")
    
    except websockets.exceptions.ConnectionClosed as e:
        print(f"WebSocket connection closed: {e}")
    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")

async def test_audio_websocket():
    """Test the WebSocket connection with audio data."""
    url = f"ws://localhost:{PORT}/media-stream"
    print(f"Connecting to {url}...")
    
    # Sample audio data (this is just a placeholder - empty WAV file in base64)
    SAMPLE_AUDIO = "UklGRiQAAABXQVZFZm10IBAAAAABAAEARKwAAIhYAQACABAAZGF0YQAAAAA="
    
    try:
        async with websockets.connect(url) as ws:
            print("Connected successfully!")
            
            # Simulate the 'start' event from Twilio
            start_event = {
                "event": "start",
                "start": {
                    "streamSid": "test-stream-sid-123",
                    "accountSid": "test-account-sid",
                    "callSid": "test-call-sid"
                }
            }
            
            print("Sending start event...")
            await ws.send(json.dumps(start_event))
            
            # Wait a moment for initialization
            await asyncio.sleep(2)
            
            # Send some audio data
            for i in range(5):
                media_event = {
                    "event": "media",
                    "media": {
                        "payload": SAMPLE_AUDIO
                    }
                }
                print(f"Sending audio chunk {i+1}...")
                await ws.send(json.dumps(media_event))
                await asyncio.sleep(0.5)  # Simulate real-time audio chunks
            
            # Listen for responses
            print("Listening for responses...")
            
            # Create a task to listen for responses
            async def listen_for_responses():
                try:
                    while True:
                        response = await asyncio.wait_for(ws.recv(), timeout=1.0)
                        print(f"Received: {response}")
                except asyncio.TimeoutError:
                    print("No more responses received")
                except Exception as e:
                    print(f"Error in response listener: {e}")
            
            response_task = asyncio.create_task(listen_for_responses())
            
            # Wait for a while to collect responses
            await asyncio.sleep(10)
            
            # Simulate the 'stop' event from Twilio
            stop_event = {
                "event": "stop",
                "streamSid": "test-stream-sid-123"
            }
            
            print("Sending stop event...")
            await ws.send(json.dumps(stop_event))
            
            # Wait for the response task to complete
            response_task.cancel()
            
            print("Test completed")
    
    except websockets.exceptions.ConnectionClosed as e:
        print(f"WebSocket connection closed: {e}")
    except Exception as e:
        print(f"Error connecting to WebSocket: {e}")

async def run_websocket_tests():
    """Run all WebSocket tests."""
    print("Starting WebSocket connection test...")
    await test_websocket_connection()
    
    print("\n" + "="*50 + "\n")
    
    # print("Starting WebSocket audio test...")
    # await test_audio_websocket()

@app.get('/test-websocket')
async def test_websocket_page():
    """Render a simple page to test WebSocket connection."""
    return HTMLResponse(
        content="""
        <html>
            <head>
                <title>WebSocket Test</title>
                <script>
                    function connectWebSocket() {
                        const wsUrl = document.getElementById('wsUrl').value;
                        const outputDiv = document.getElementById('output');
                        
                        outputDiv.innerHTML += `<p>Connecting to ${wsUrl}...</p>`;
                        
                        const ws = new WebSocket(wsUrl);
                        
                        ws.onopen = function() {
                            outputDiv.innerHTML += '<p style="color:green">Connection established!</p>';
                            
                            // Send a test message
                            const testMessage = {
                                event: "start",
                                start: {
                                    streamSid: "test-stream-sid-" + Date.now(),
                                    accountSid: "test-account-sid",
                                    callSid: "test-call-sid"
                                }
                            };
                            
                            ws.send(JSON.stringify(testMessage));
                            outputDiv.innerHTML += `<p>Sent test message: ${JSON.stringify(testMessage)}</p>`;
                        };
                        
                        ws.onmessage = function(event) {
                            outputDiv.innerHTML += `<p>Received: ${event.data}</p>`;
                        };
                        
                        ws.onerror = function(error) {
                            outputDiv.innerHTML += `<p style="color:red">Error: ${error}</p>`;
                        };
                        
                        ws.onclose = function() {
                            outputDiv.innerHTML += '<p>Connection closed</p>';
                        };
                    }
                </script>
                <style>
                    body { font-family: Arial, sans-serif; max-width: 800px; margin: 0 auto; padding: 20px; }
                    h1 { color: #1a73e8; }
                    .section { margin-bottom: 30px; padding: 20px; border: 1px solid #ddd; border-radius: 5px; }
                    input, button { padding: 8px; margin: 5px 0; }
                    button { background-color: #1a73e8; color: white; border: none; cursor: pointer; }
                    #output { background-color: #f5f5f5; padding: 10px; border-radius: 5px; min-height: 200px; }
                </style>
            </head>
            <body>
                <h1>WebSocket Connection Test</h1>
                
                <div class="section">
                    <p>Test your WebSocket connection:</p>
                    <input type="text" id="wsUrl" value="ws://localhost:5050/media-stream" style="width:400px;">
                    <button onclick="connectWebSocket()">Connect</button>
                    
                    <div id="output" style="margin-top: 20px;">
                        <p>Connection logs will appear here...</p>
                    </div>
                </div>
            </body>
        </html>
        """
    )

@app.api_route("/incoming-call", methods=["GET", "POST"])
async def handle_incoming_call(request: Request):
    """Handle incoming call and return TwiML response to connect to Media Stream."""
    response = VoiceResponse()
    # <Say> punctuation to improve text-to-speech flow
    response.say("Please wait while we connect your call to the AI")
    response.pause(length=1)
    response.say("O.K. you can start talking!")
    host = request.url.hostname
    connect = Connect()
    connect.stream(url=f'wss://{DOMAIN}/media-stream')
    response.append(connect)
    return HTMLResponse(content=str(response), media_type="application/xml")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run the Twilio AI voice assistant server.")
    parser.add_argument('--call', help="The phone number to call, e.g., '--call=+18005551212'")
    parser.add_argument('--testwbs', action='store_true', help="Run WebSocket connection tests")
    args = parser.parse_args()

    if args.testwbs:
        print("Running WebSocket tests...")
        # Run the tests directly
        asyncio.run(run_websocket_tests())
        sys.exit(0)
    
    if args.call:
        phone_number = args.call
        print(
            'Our recommendation is to always disclose the use of AI for outbound or inbound calls.\n'
            'Reminder: All of the rules of TCPA apply even if a call is made by AI.\n'
            'Check with your counsel for legal and compliance advice.'
        )
        loop = asyncio.get_event_loop()
        loop.run_until_complete(make_call(phone_number))
    
    uvicorn.run(app, host="0.0.0.0", port=PORT)
